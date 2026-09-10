import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript, TopicBrief, TextProbeOutput
from podcast_automate.runner import probe_script, run_probe, status
from podcast_automate.storage import atomic_text, init_project, project_lock, write_yaml


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


if __name__ == "__main__":
    unittest.main()
