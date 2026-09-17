import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.studio_progress import read, script_progress, watch
from podcast_automate.studio_progress import safe_script_progress
from podcast_automate.model_trace import ModelTrace
from podcast_automate.storage import write_json


class StudioProgressTests(unittest.TestCase):
    def test_trace_is_available_independently_of_optional_status_summary(self):
        trace = ModelTrace(self.work / "calls/call_004", "test")
        trace.record("reasoning", "A provider-visible progress note")
        trace.finish()
        progress = safe_script_progress(self.root, self.run)
        self.assertEqual(progress["model_trace"]["lines"][0]["text"], "A provider-visible progress note")
        self.assertNotIn("status_summary", progress)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / "runs/run_test"
        self.run = {"run_id": "run_test", "kind": "script", "status": "running",
                    "stages": {"planning": {"status": "completed"}, "teaching": {"status": "running"}}}
        write_json(self.work / "series_plan.json", {"episodes": [
            {"episode_id": "ep_001", "title": "First lesson"}, {"episode_id": "ep_002", "title": "Second lesson"}]})
        folder = self.work / "teaching/ep_001"
        review = {"issues": [], "research_gaps": []}
        plan = {"episode_id": "ep_001"}
        write_json(folder / "plan.json", plan)
        write_json(folder / "review.json", review)
        write_json(folder / "checkpoint.json", {"design": plan, "review": review})
        (folder / "plan.md").write_text("# First lesson\nAn accepted teaching design.", encoding="utf-8")
        write_json(self.work / "calls/call_004/output_schema.json", {"title": "TeachingPlanReview"})
        write_json(self.work / "budget.json", {"model_calls": 4})

    def test_reports_real_current_episode_and_readable_completed_result(self):
        progress = script_progress(self.root, self.run)
        self.assertEqual(progress["current_episode"], "ep_002")
        self.assertEqual(progress["completed_segments"], 1)
        self.assertEqual(progress["total_segments"], 2)
        self.assertIn("geprüft", progress["activity"])
        self.assertIn("accepted teaching design", progress["episodes"][0]["teaching_preview"])
        self.assertEqual(progress["episodes"][1]["teaching_preview"], "")

    def test_research_progress_exposes_quality_and_preserves_real_call_timing(self):
        write_json(self.work / "research_activity.json", {"phase": "research", "activity": "Read new evidence",
                   "model_call_limit": 150, "search_round_limit": 12})
        write_json(self.work / "research_quality_gate.json", {"closed": 1, "total": 3, "passed": False,
                   "requirements": [{"question": "Why?", "missing": ["Missing full chapter"]}]})
        write_json(self.work / "budget.json", {"model_calls": 4, "search_rounds": 2})
        run = {**self.run, "kind": "research", "status": "running"}
        progress = script_progress(self.root, run)
        self.assertEqual(progress["phase"], "research")
        self.assertEqual(progress["research_quality"]["closed"], 1)
        self.assertEqual(progress["search_rounds"], 2)
        self.assertIsNotNone(progress["model_call_started_at"])
        stopped = script_progress(self.root, {**run, "status": "pending"})
        self.assertIsNone(stopped["model_call_started_at"])

    def test_refresh_is_distinct_from_model_activity_and_saved_results(self):
        first = script_progress(self.root, self.run)
        second = script_progress(self.root, self.run)
        self.assertGreaterEqual(second["updated_at"], first["updated_at"])
        self.assertEqual(second["model_call_started_at"], first["activity_started_at"])
        self.assertIsNone(first["last_result_at"])
        write_json(self.work / "calls/call_004/response.json", {"issues": []})
        completed = script_progress(self.root, self.run)
        self.assertIsNone(completed["model_call_started_at"])
        self.assertIsNotNone(completed["last_result_at"])
        self.assertEqual(completed["activity_started_at"], first["activity_started_at"])

    def test_research_progress_uses_question_ledger_instead_of_stale_global_score(self):
        write_json(self.work / "research_activity.json", {"activity": "Eine Frage wird geprüft"})
        write_json(self.work / "research_quality_gate.json", {"closed": 0, "total": 2})
        ledger = {"closed": 3, "total": 7, "phase": "questions", "questions": [
            {"id": "definition", "status": "verified", "answer": "A supported definition"}]}
        write_json(self.work / "research_questions.json", ledger)
        progress = script_progress(self.root, {**self.run, "kind": "research"})
        self.assertEqual((progress["completed_segments"], progress["total_segments"]), (3, 7))
        self.assertEqual(progress["research_questions"], ledger)
        self.assertEqual(progress["research_quality"]["closed"], 0)

    def test_publisher_recovers_from_transient_job_read_and_progress_io_failures(self):
        job_path = self.root / "studio/job.json"
        job = {"id": "job_one", "status": "running", "run": self.run}
        write_json(job_path, job)
        attempts = []

        def flaky_read(path, default=None):
            if path == job_path and not attempts:
                attempts.append("job read")
                return default
            return read(path, default)

        def flaky_progress(*args):
            attempts.append("progress")
            if len(attempts) == 2:
                raise PermissionError("temporarily locked")
            return script_progress(*args)

        def flaky_write(path, data):
            attempts.append("write")
            if attempts.count("write") == 1:
                raise PermissionError("temporarily locked")
            write_json(path, data)

        def tick(_):
            if (self.work / "progress.json").exists():
                self.assertEqual(json.loads(job_path.read_text()), job)
                write_json(job_path, {**job, "status": "completed"})

        with patch("podcast_automate.studio_progress.read", side_effect=flaky_read), \
             patch("podcast_automate.studio_progress.script_progress", side_effect=flaky_progress), \
             patch("podcast_automate.studio_progress.write_json", side_effect=flaky_write), \
             patch("time.sleep", side_effect=tick) as sleep:
            watch(self.root, "job_one")
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(json.loads((self.work / "progress.json").read_text())["model_calls"], 4)

    def test_publisher_exits_if_job_stays_unreadable(self):
        with patch("time.sleep") as sleep:
            watch(self.root, "job_one")
        self.assertEqual(sleep.call_count, 29)

    def test_stale_accepted_file_does_not_mark_pending_review_complete(self):
        write_json(self.work / "teaching/ep_001/checkpoint.json", {"design": {"episode_id": "ep_001"}, "review": None})
        progress = script_progress(self.root, self.run)
        self.assertEqual(progress["completed_segments"], 0)
        self.assertEqual(progress["episodes"][0]["teaching_preview"], "")

    def test_respects_single_episode_selection_and_ignores_non_script_jobs(self):
        write_json(self.work / "script_request.json", {"episode": "ep_002"})
        self.assertEqual(script_progress(self.root, self.run)["total_segments"], 1)
        self.assertIsNone(script_progress(self.root, {**self.run, "kind": "episode_audio"}))

    def test_invalid_episode_paths_cannot_read_outside_the_run(self):
        write_json(self.work / "series_plan.json", {"episodes": [{"episode_id": "../../private", "title": "Untrusted"}]})
        self.assertEqual(script_progress(self.root, self.run)["episodes"], [])

    def test_blocked_job_retains_completed_results_and_explains_the_remaining_review(self):
        write_json(self.work / "teaching/ep_002/checkpoint.json", {"review": {"issues": ["Explain the missing mechanism."]}})
        run = {**self.run, "status": "blocked", "stages": {"teaching": {"status": "blocked", "error": {"code": "teaching_design_failed"}}}}
        progress = script_progress(self.root, run)
        self.assertEqual(progress["completed_segments"], 1)
        self.assertEqual(progress["review_issues"], ["Explain the missing mechanism."])
        self.assertTrue(progress["episodes"][0]["teaching_preview"])

    def test_blocked_final_review_exposes_actual_issues(self):
        issues = [{"category": "depth", "segment_ids": ["seg_001"], "reason": "Explain the conclusion."}]
        write_json(self.work / "reviews/ep_001_checkpoint.json", {"review": {"issues": issues}})
        run = {**self.run, "status": "blocked", "stages": {
            "review": {"status": "blocked", "error": {"code": "script_review_failed"}}}}
        progress = script_progress(self.root, run)
        self.assertEqual(progress["current_episode"], "ep_001")
        self.assertEqual(progress["review_issues"], issues)

    def test_compatibility_publisher_never_modifies_job_or_run_and_exits_on_job_change(self):
        job_path = self.root / "studio/job.json"
        job = {"id": "job_one", "status": "running", "run": self.run}
        write_json(job_path, job)
        def change_job(_):
            self.assertEqual(json.loads(job_path.read_text()), job)
            write_json(job_path, {**job, "id": "job_two"})
        with patch("time.sleep", side_effect=change_job) as sleep:
            watch(self.root, "job_one")
        self.assertEqual(sleep.call_count, 1)
        self.assertTrue((self.work / "progress.json").exists())
