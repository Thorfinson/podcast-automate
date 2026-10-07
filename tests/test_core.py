import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript, Failure, RunManifest, StageRecord, TopicBrief, TextProbeOutput
from podcast_automate.runner import manifest_path, probe_script, run_probe, status
from podcast_automate.storage import atomic_text, init_project, project_lock, read_yaml, write_json, write_yaml


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Projekt mit Leerzeichen"
        self.config = TopicBrief(topic="Energiebasierte Modelle")
        init_project(self.root, self.config)


class ProjectTests(ProjectCase):
    def test_init_preserves_existing_project(self):
        before = (self.root / "project.yaml").read_bytes()
        with self.assertRaises(AppError):
            init_project(self.root, TopicBrief(topic="Andere Frage"))
        self.assertEqual(before, (self.root / "project.yaml").read_bytes())

    def test_no_implicit_series_duration_or_episode_limit(self):
        self.assertIsNone(self.config.target_total_minutes)
        self.assertEqual(self.config.max_episode_minutes, 30)
        long_project = TopicBrief(topic="Ausführlich", target_total_minutes=1200)
        self.assertEqual(long_project.target_total_minutes, 1200)
        for change in ({"topic": "   "}, {"max_episode_minutes": 31},
                       {"target_total_minutes": 0}, {"voice_profile": {"host_a": "Ryan"}}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                TopicBrief.model_validate({**self.config.model_dump(), **change})

    def test_invalid_script_order_or_duplicate_ids_rejected(self):
        original = probe_script().model_dump()
        original["segments"][1]["segment_id"] = original["segments"][0]["segment_id"]
        with self.assertRaises(ValidationError):
            EpisodeScript.model_validate(original)
        original = probe_script().model_dump()
        original["segments"][0]["chapter_id"] = "pronunciation"
        with self.assertRaises(ValidationError):
            EpisodeScript.model_validate(original)

    def test_atomic_write_preserves_previous_file_on_failure(self):
        path = self.root / "durable.json"
        atomic_text(path, "previous")
        with patch("podcast_automate.storage.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                atomic_text(path, "incomplete")
        self.assertEqual(path.read_text(), "previous")
        self.assertFalse(list(self.root.glob(".pla-*.tmp")))

    def test_parallel_project_write_rejected_and_lock_released(self):
        with project_lock(self.root):
            with self.assertRaises(AppError):
                with project_lock(self.root):
                    pass
        with project_lock(self.root):
            pass


class ResumeTests(ProjectCase):
    def result(self):
        return TextProbeOutput(topic=self.config.topic, focus_questions=["Wie funktioniert es?"],
                               note="Keine Recherche"), {"research_performed": False}

    def test_quota_pause_resume_and_completed_result_reuse(self):
        quota = AppError("Kontingent erreicht", code="quota_exhausted", status="waiting_for_quota")
        with patch("podcast_automate.runner.CodexAdapter.probe", side_effect=quota):
            first = run_probe(self.root, kind="text_probe")
        self.assertEqual(first.status, "waiting_for_quota")
        self.assertEqual(first.stages["codex_probe"].outputs, {})
        with patch("podcast_automate.runner.CodexAdapter.probe", return_value=self.result()) as invoke:
            second = run_probe(self.root, resume=True)
            third = run_probe(self.root, resume=True)
        self.assertEqual(second.run_id, first.run_id)
        self.assertEqual(third.status, "completed")
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(third.stages["codex_probe"].attempts, 2)

    def test_changed_result_is_detected_and_recomputed(self):
        with patch("podcast_automate.runner.CodexAdapter.probe", return_value=self.result()):
            first = run_probe(self.root, kind="text_probe")
        output = self.root / next(iter(first.stages["codex_probe"].outputs))
        output.write_text("corrupted")
        self.assertEqual(status(self.root)["invalid_completed_stages"], ["codex_probe"])
        with patch("podcast_automate.runner.CodexAdapter.probe", return_value=self.result()) as invoke:
            result = run_probe(self.root, resume=True)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(result.status, "completed")
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])

    def test_changed_input_cannot_reuse_old_run(self):
        with patch("podcast_automate.runner.CodexAdapter.probe", return_value=self.result()):
            run_probe(self.root, kind="text_probe")
        changed = self.config.model_copy(update={"topic": "Neues Thema"})
        write_yaml(self.root / "project.yaml", changed.model_dump(mode="json"))
        with self.assertRaises(AppError) as error:
            run_probe(self.root, resume=True)
        self.assertEqual(error.exception.code, "inputs_changed")

    def test_interrupt_is_persisted_and_resumable(self):
        with patch("podcast_automate.runner.CodexAdapter.probe", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_probe(self.root, kind="text_probe")
        self.assertEqual(status(self.root)["status"], "pending")
        with patch("podcast_automate.runner.CodexAdapter.probe", return_value=self.result()):
            self.assertEqual(run_probe(self.root, resume=True).status, "completed")

    def test_audio_requires_explicit_approval(self):
        with self.assertRaises(AppError) as error:
            run_probe(self.root, kind="audio_probe")
        self.assertEqual(error.exception.code, "audio_approval_required")
        self.assertFalse((self.root / "runs/latest.json").exists())

    def test_run_path_traversal_is_rejected(self):
        with self.assertRaises(AppError):
            status(self.root, "../../other")

    def test_a_quota_pause_keeps_its_reset_facts_and_other_stops_dump_as_before(self):
        until = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        quota = AppError("Kontingent erreicht", code="quota_exhausted", status="waiting_for_quota",
                         details={"provider": "codex_cli", "blocked_until": until, "payload": {"answer": "model output"},
                                  "snapshots": {"codex_cli": {}}})
        with patch("podcast_automate.runner.CodexAdapter.probe", side_effect=quota):
            paused = run_probe(self.root, kind="text_probe")
        error = paused.stages["codex_probe"].error
        # Only the allowlisted plain facts, never model output or provider snapshots.
        self.assertEqual(error.details, {"provider": "codex_cli", "blocked_until": until})
        saved = read_yaml(manifest_path(self.root, paused.run_id))
        self.assertEqual(saved["stages"]["codex_probe"]["error"]["details"]["blocked_until"], until)
        blocked = AppError("Codex fehlt", code="codex_missing", status="blocked", details={"provider": "codex_cli"})
        with patch("podcast_automate.runner.CodexAdapter.probe", side_effect=blocked):
            stopped = run_probe(self.root, resume=True)
        self.assertNotIn("details", read_yaml(manifest_path(self.root, stopped.run_id))["stages"]["codex_probe"]["error"])

    def test_a_budget_stop_keeps_the_limit_it_reached_and_nothing_else(self):
        # D-152: the Studio named the spent limit by matching "Rechercherunden" in the German message.
        cases = [(AppError("Limit erreicht", code="research_budget_exhausted", status="blocked",
                           details={"limit": "search_rounds", "payload": {"answer": "model output"}}), {"limit": "search_rounds"}),
                 (AppError("Limit erreicht", code="research_budget_exhausted", status="blocked",
                           details={"limit": "model_calls"}), {"limit": "model_calls"}),
                 # Only the two limit names, and only on the budget stop.
                 (AppError("Limit erreicht", code="research_budget_exhausted", status="blocked",
                           details={"limit": "Rechercherunden"}), None),
                 (AppError("Zu knapp", code="research_budget_insufficient", status="blocked",
                           details={"limit": "model_calls"}), None)]
        for error, expected in cases:
            with self.subTest(details=error.details, code=error.code):
                with patch("podcast_automate.runner.CodexAdapter.probe", side_effect=error):
                    stopped = run_probe(self.root, kind="text_probe")
                self.assertEqual(stopped.stages["codex_probe"].error.details, expected)
                saved = read_yaml(manifest_path(self.root, stopped.run_id))["stages"]["codex_probe"]["error"]
                if expected:
                    self.assertEqual(saved["details"], expected)
                else:
                    self.assertNotIn("details", saved)

    def test_a_manifest_written_before_failure_details_keeps_its_bytes(self):
        manifest = RunManifest(run_id="run_old", kind="text_probe", project_hash="p", input_hash="i",
                               stages={"codex_probe": StageRecord(status="blocked", error=Failure(code="x", message="y"))})
        path = manifest_path(self.root, "run_old")
        write_yaml(path, manifest.model_dump(mode="json"))
        before = path.read_bytes()
        write_yaml(path, RunManifest.model_validate(read_yaml(path)).model_dump(mode="json"))
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn(b"details", before)


class WorkerProcessTests(ProjectCase):
    """``studio_worker.main``: the job names its process, a pause resumes at the failing provider's reset, and the
    computer is kept awake only outside tests."""

    def run_worker(self, result, job=None):
        from podcast_automate import studio_worker
        from podcast_automate.logs import release_logging
        write_json(self.root / "studio/job.json", {"id": "job_one", "status": "running", **(job or {})})
        request = {"action": "check", "text": {}}
        awake = []
        with patch.dict(os.environ, {"PLA_SUBSCRIPTIONS_STORE": str(Path(self.temp.name) / "subscriptions.json")}), \
                patch.object(studio_worker, "perform", return_value=result), \
                patch.object(studio_worker, "keep_awake", return_value=lambda: awake.append("released")), \
                patch.object(studio_worker.sys, "argv", ["studio_worker", str(self.root)]), \
                patch.object(studio_worker.sys, "stdin", io.StringIO(json.dumps(request))):
            try:
                studio_worker.main()
            finally:
                release_logging(self.root / "studio/worker.log")
        self.assertEqual(awake, ["released"])
        return json.loads((self.root / "studio/job.json").read_text(encoding="utf-8"))

    def paused_run(self, details=None):
        error = Failure(code="quota_exhausted", message="Kontingent erreicht", details=details)
        return RunManifest(run_id="run_paused", kind="research", project_hash="p", input_hash="i", status="waiting_for_quota",
                           stages={"discovery": StageRecord(status="waiting_for_quota", error=error)}).model_dump(mode="json")

    def test_the_job_names_its_process_and_resumes_at_the_named_reset(self):
        until = (datetime.now(timezone.utc) + timedelta(hours=5)).replace(microsecond=0)
        job = self.run_worker({"run": self.paused_run({"provider": "codex_cli", "blocked_until": until.isoformat()})})
        self.assertEqual(job["pid"], os.getpid())
        started = datetime.fromisoformat(job["started_process_at"])
        self.assertLess(abs(datetime.now(timezone.utc) - started), timedelta(days=1))
        self.assertEqual(job["retry_at"], until.isoformat())

    def test_without_a_known_reset_each_automatic_resume_waits_longer(self):
        waits = []
        for count in (0, 1, 2):
            job = self.run_worker({"run": self.paused_run({"provider": "codex_cli"})}, {"auto_resume_count": count})
            waits.append(datetime.fromisoformat(job["retry_at"]) - datetime.now(timezone.utc))
        for wait, minutes in zip(waits, (30, 60, 120)):
            self.assertLess(abs(wait - timedelta(minutes=minutes)), timedelta(minutes=2))

    def test_keep_awake_asks_windows_and_is_a_no_op_in_tests_and_ci(self):
        from podcast_automate import studio_worker
        calls = []

        class Function:
            """kernel32.SetThreadExecutionState as ctypes exposes it: settable argtypes, called with the flags."""

            def __call__(self, flags):
                calls.append(flags)
                return 1

        class Kernel:
            SetThreadExecutionState = Function()

        with patch.dict(os.environ, {"PLA_KEEP_AWAKE": "0"}), \
                patch("ctypes.WinDLL", side_effect=AssertionError("no system call"), create=True):
            studio_worker.keep_awake()()
        with patch.dict(os.environ, {"CI": "true", "PLA_KEEP_AWAKE": ""}), \
                patch("ctypes.WinDLL", side_effect=AssertionError("no system call"), create=True):
            studio_worker.keep_awake()()
        environment = {key: value for key, value in os.environ.items() if key not in {"CI", "PLA_KEEP_AWAKE"}}
        with patch.dict(os.environ, environment, clear=True), patch.object(studio_worker.os, "name", "nt"), \
                patch("ctypes.WinDLL", return_value=Kernel(), create=True):
            allow_sleep = studio_worker.keep_awake()
            self.assertEqual(calls, [studio_worker.ES_CONTINUOUS | studio_worker.ES_SYSTEM_REQUIRED])
            allow_sleep()
        self.assertEqual(calls[-1], studio_worker.ES_CONTINUOUS)


if __name__ == "__main__":
    unittest.main()
