import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.call_activity import CallActivity
from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.process import run_process
from podcast_automate.status_summary import (ProgressDigest, evidence_snapshot, summary_view,
                                            update_summary, watch_summaries)
from podcast_automate.storage import init_project, write_json


class StatusSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Projekt"
        init_project(self.root, TopicBrief(topic="Test topic"))
        self.work = self.root / "runs/run_test"
        self.job = {"id": "job_one", "status": "running", "run": {"run_id": "run_test", "kind": "research",
                    "status": "running", "stages": {"discovery": {"status": "completed"}, "dossier": {"status": "running"}}}}
        write_json(self.root / "studio/job.json", self.job)
        write_json(self.work / "research_request.json", {"text_generation": {"provider": "codex_cli", "model": "gpt-6-astra"}})
        write_json(self.work / "research_activity.json", {"activity": "Dossier wird ausgearbeitet", "updated_at": "2026-09-16T12:00:00Z"})
        write_json(self.work / "budget.json", {"model_calls": 4, "search_rounds": 1})
        write_json(self.work / "calls/call_004/output_schema.json", {"title": "ResearchDossier"})
        self.seconds = 1000
        self.calls = []

    def result(self, adapter, prompt, output_type, directory, **kwargs):
        self.calls.append((adapter, prompt, directory, kwargs))
        return ProgressDigest(summary="Die Quellen sind gespeichert. Das Dossier wird noch ausgearbeitet.", evidence_ids=["stage"]), {}

    def update(self):
        update_summary(self.root, self.job, clock=lambda: self.seconds)

    def test_cadence_deduplicates_and_does_not_touch_production_budget(self):
        with patch("podcast_automate.status_summary.CodexAdapter.structured", autospec=True, side_effect=self.result):
            self.update()
            self.seconds += 179
            self.update()
            self.assertEqual(len(self.calls), 1)
            self.seconds += 1
            self.update()
            self.assertEqual(summary_view(self.work)["status"], "unchanged")
            write_json(self.work / "calls/call_004/response.json", {"findings": [1, 2], "open_questions": ["Was fehlt?"]})
            self.seconds += 180
            self.update()
        self.assertEqual(len(self.calls), 2)
        adapter = self.calls[0][0]
        self.assertEqual(adapter.settings.codex_model, "gpt-5.6-luna")
        self.assertEqual(adapter.reasoning_effort, "low")
        self.assertEqual(adapter.settings.text_timeout_seconds, 90)
        self.assertIn("2 Befunde im Entwurf", self.calls[-1][1])
        self.assertEqual(json.loads((self.work / "budget.json").read_text()), {"model_calls": 4, "search_rounds": 1})
        self.assertEqual(summary_view(self.work)["calls"], 2)
        self.assertEqual(len(summary_view(self.work)["history"]), 2)
        self.assertIsNone(summary_view(self.work, "other_job"))
        self.assertEqual(json.loads((self.work / "research_activity.json").read_text())["updated_at"], "2026-09-16T12:00:00Z")

    def test_openrouter_uses_deepseek_and_keeps_key_out_of_prompt_and_receipts(self):
        self.job["run"]["kind"] = "script"
        write_json(self.work / "script_request.json", {"text_generation": {"provider": "openrouter", "model": "openai/gpt-6-astra-pro"}})
        secret = "test-private-key"
        with patch("podcast_automate.status_summary.OpenRouterAdapter.structured", autospec=True, side_effect=self.result), \
                patch("podcast_automate.status_summary.CodexAdapter.structured") as codex:
            update_summary(self.root, self.job, api_key=secret, clock=lambda: self.seconds)
        codex.assert_not_called()
        adapter, prompt, directory, _ = self.calls[0]
        self.assertEqual(adapter.model, "deepseek/deepseek-v4.1-flash")
        self.assertEqual(adapter.reasoning_effort, "low")
        self.assertEqual(adapter._key.get_secret_value(), secret)
        self.assertNotIn(secret, prompt)
        self.assertNotIn(secret, (self.work / "status_reports/state.json").read_text())
        self.assertIn("status_reports", str(directory))

    def test_research_uses_project_openrouter_choice_when_it_has_no_saved_text_choice(self):
        write_json(self.work / "research_request.json", {"text_generation": None})
        write_json(self.root / "studio/text.json", {"provider": "openrouter"})
        with patch("podcast_automate.status_summary.OpenRouterAdapter.structured", autospec=True, side_effect=self.result):
            update_summary(self.root, self.job, api_key="test-private-key", clock=lambda: self.seconds)
        self.assertEqual(self.calls[0][0].model, "deepseek/deepseek-v4.1-flash")

    def test_report_can_cite_absence_of_live_events(self):
        with patch("podcast_automate.status_summary.CodexAdapter.structured", return_value=(ProgressDigest(
                summary="Zwischenmeldungen liegen nicht vor.", evidence_ids=["live_events_available"]), {})):
            self.update()
        self.assertEqual(summary_view(self.work)["status"], "ready")

    def test_status_provider_failure_is_bounded_and_does_not_fail_job(self):
        with patch("podcast_automate.status_summary.CodexAdapter.structured", side_effect=AppError("private-provider-details", code="quota_exhausted")) as model:
            for _ in range(5):
                self.update()
                self.seconds += 180
        self.assertEqual(model.call_count, 3)
        self.assertEqual(summary_view(self.work)["status"], "paused")
        self.assertEqual(json.loads((self.root / "studio/job.json").read_text())["status"], "running")
        self.assertNotIn("private-provider-details", (self.work / "status_reports/state.json").read_text())

    def test_model_is_not_called_after_status_budget_limit(self):
        write_json(self.work / "status_reports/state.json", {"calls": 100})
        with patch("podcast_automate.status_summary.CodexAdapter.structured") as model:
            self.update()
        model.assert_not_called()
        self.assertEqual(summary_view(self.work)["status"], "paused")

    def test_old_live_stream_is_not_invented_and_raw_prompts_never_reach_summary(self):
        write_json(self.work / "calls/call_004/response.json", {"findings": [1], "raw_prompt": "PRIVATE", "reasoning": "PRIVATE"})
        snapshot = evidence_snapshot(self.root, self.job["run"])
        self.assertFalse(snapshot["live_events_available"])
        self.assertNotIn("PRIVATE", json.dumps(snapshot))
        self.assertIn("1 Befunde im Entwurf", json.dumps(snapshot, ensure_ascii=False))

    def test_source_gap_handoff_does_not_present_old_assessment_as_current(self):
        write_json(self.work / "research_quality_gate.json", {"closed": 0, "total": 10,
            "assessment_status": "pending_after_source_review", "requirements": [],
            "source_review": {"issues": [{"reason": "Original comparison is missing."}]}})
        snapshot = evidence_snapshot(self.root, self.job["run"])
        text = json.dumps(snapshot, ensure_ascii=False)
        self.assertIn("Gesamtbewertung wurde noch nicht erneuert", text)
        self.assertIn("Original comparison is missing", text)
        self.assertNotIn("0 von 10", text)

    def test_a_waiting_plan_is_a_fact_with_its_projection(self):
        write_json(self.work / "research_questions.json", {"closed": 0, "total": 29, "phase": "awaiting_plan_approval",
                   "active_task": None, "questions": []})
        write_json(self.work / "question_research/plan_projection.json", {"tasks": 29, "projected_calls": 148,
                   "projected_hours": 11.1, "seconds_per_call": 270.0})
        text = json.dumps(evidence_snapshot(self.root, self.job["run"]), ensure_ascii=False)
        self.assertIn("Wartet auf Freigabe des Rechercheplans", text)
        self.assertIn("29 Teilfragen, voraussichtlich 148 Aufrufe, etwa 11 Stunden bei 4,5 Minuten je Aufruf", text)
        write_json(self.work / "research_questions.json", {"closed": 0, "total": 29, "phase": "questions", "questions": []})
        self.assertNotIn("Freigabe des Rechercheplans", json.dumps(evidence_snapshot(self.root, self.job["run"]), ensure_ascii=False))

    def test_question_progress_and_new_sources_replace_stale_global_summary_facts(self):
        write_json(self.work / "source_index.json", {"sources": [1], "failures": []})
        ledger = {"closed": 2, "total": 4, "phase": "questions", "active_task": "task_test", "source_count": 7,
            "source_failures": 1, "questions": [{"id": "task_test", "question": "Independent test?",
            "status": "researching", "activity": "Read the original experiment", "read_sections": 6}]}
        write_json(self.work / "research_questions.json", ledger)
        write_json(self.work / "research_quality_gate.json", {"closed": 0, "total": 10,
            "requirements": [{"passed": False, "question": "STALE GAP"}]})
        snapshot = evidence_snapshot(self.root, self.job["run"])
        text = json.dumps(snapshot, ensure_ascii=False)
        self.assertIn("2 von 4 Teilfragen", text)
        self.assertIn("7 Quellen eingelesen", text)
        self.assertIn("Read the original experiment", text)
        self.assertNotIn("STALE GAP", text)
        self.assertNotIn("0 von 10", text)
        with patch("podcast_automate.status_summary.CodexAdapter.structured", autospec=True, side_effect=self.result):
            self.update()
        envelope = json.loads((self.work / "research_activity.json").read_text())
        self.assertEqual(envelope["research_questions"], ledger)

    def test_several_active_tasks_are_one_fact_each_plus_a_count(self):
        ledger = {"closed": 0, "total": 4, "phase": "questions", "active_task": "task_a",
                  "active_tasks": ["task_a", "task_b", "task_c"],
                  "questions": [{"id": task, "question": f"Question {task}?", "status": "researching",
                                 "activity": f"Reading for {task}", "read_sections": 1}
                                for task in ("task_a", "task_b", "task_c", "task_d")]}
        write_json(self.work / "research_questions.json", ledger)
        facts = {fact["id"]: fact["text"] for fact in evidence_snapshot(self.root, self.job["run"])["facts"]}
        self.assertEqual(facts["active_tasks"], "3 Teilfragen in Arbeit: Question task_a?; Question task_b?; Question task_c?")
        self.assertEqual({key for key in facts if re.fullmatch(r"question_\d+", key)}, {"question_0", "question_1", "question_2"})
        self.assertNotIn("Reading for task_d", json.dumps(facts))
        # One task at a time, as before: no count line; the legacy field alone still selects its task.
        write_json(self.work / "research_questions.json", {**ledger, "active_tasks": ["task_b"], "active_task": "task_b"})
        facts = {fact["id"]: fact["text"] for fact in evidence_snapshot(self.root, self.job["run"])["facts"]}
        self.assertNotIn("active_tasks", facts)
        self.assertEqual({key for key in facts if re.fullmatch(r"question_\d+", key)}, {"question_1"})
        legacy = {key: value for key, value in ledger.items() if key != "active_tasks"}
        write_json(self.work / "research_questions.json", {**legacy, "active_task": "task_d"})
        facts = {fact["id"]: fact["text"] for fact in evidence_snapshot(self.root, self.job["run"])["facts"]}
        self.assertEqual({key for key in facts if re.fullmatch(r"question_\d+", key)}, {"question_3"})

    def test_public_events_are_allowlisted_redacted_and_visible_before_completion(self):
        directory = self.work / "calls/call_004"
        activity = CallActivity(directory, "ResearchDossier", "gpt-6-astra")
        for event in [
            {"type": "item.completed", "item": {"type": "reasoning", "text": "private-thought"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "unclassified-final"}},
            {"type": "error", "message": "private-error"},
            {"type": "item.completed", "item": {"type": "web_search", "query": "social learning sk-testsecret"}},
            {"type": "item.completed", "item": {"type": "agent_message", "phase": "commentary", "text": "Prüfe Quellen. token=secret"}},
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "result": "private-tool-output"}},
        ]:
            activity.observe(json.dumps(event))
        saved = (directory / "activity.json").read_text(encoding="utf-8")
        for forbidden in ("private-thought", "unclassified-final", "private-error", "sk-testsecret", "token=secret", "private-tool-output"):
            self.assertNotIn(forbidden, saved)
        self.assertIn("social learning", saved)
        self.assertTrue(evidence_snapshot(self.root, self.job["run"])["live_events_available"])

    def test_stopped_job_discards_inflight_summary_and_cancels_codex(self):
        def stop(adapter, *args, **kwargs):
            write_json(self.root / "studio/job.json", {**self.job, "status": "interrupted"})
            self.assertTrue(adapter.cancel_check())
            return self.result(adapter, *args, **kwargs)
        with patch("podcast_automate.status_summary.CodexAdapter.structured", autospec=True, side_effect=stop):
            self.update()
        self.assertIsNone(summary_view(self.work).get("summary"))

    def test_unknown_evidence_is_not_published(self):
        with patch("podcast_automate.status_summary.CodexAdapter.structured", return_value=(ProgressDigest(summary="Invented", evidence_ids=["unknown"]), {})):
            self.update()
        self.assertEqual(summary_view(self.work)["status"], "unavailable")
        self.assertIsNone(summary_view(self.work).get("summary"))

    def test_monitor_exits_on_job_change_without_starting_more_calls(self):
        def replace(_):
            write_json(self.root / "studio/job.json", {**self.job, "id": "different"})
        with patch("podcast_automate.status_summary.update_summary") as update, \
                patch("podcast_automate.status_summary.time.sleep", side_effect=replace):
            watch_summaries(self.root, "job_one")
        self.assertEqual(update.call_count, 1)


class ProcessStreamingTests(unittest.TestCase):
    def test_stdout_callback_runs_while_child_is_running_and_stderr_is_drained(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "observed"
            script = """import pathlib, sys, time
print('event', flush=True)
print('x' * 100000, file=sys.stderr, flush=True)
deadline=time.monotonic()+4
while not pathlib.Path(sys.argv[1]).exists() and time.monotonic()<deadline:
    time.sleep(.02)
sys.exit(0 if pathlib.Path(sys.argv[1]).exists() else 7)
"""
            result = run_process([sys.executable, "-c", script, str(marker)], timeout=6,
                                 on_stdout_line=lambda line: marker.write_text(line))
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.strip(), "event")
            self.assertGreater(len(result.stderr), 100000)

    def test_callback_failure_does_not_break_model_execution(self):
        def fail(_):
            raise OSError("status unavailable")
        result = run_process([sys.executable, "-c", "print('event')"], timeout=4, on_stdout_line=fail)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "event")

    def test_streaming_timeout_and_cancellation_stop_the_process(self):
        for cancel in (None, lambda: True):
            with self.subTest(cancel=bool(cancel)), self.assertRaises(AppError) as error:
                run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1,
                            on_stdout_line=lambda _: None, cancel_check=cancel)
            self.assertEqual(error.exception.code, "interrupted" if cancel else "timeout")
