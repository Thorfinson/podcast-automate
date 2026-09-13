import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.studio_progress import script_progress, watch
from podcast_automate.storage import write_json


class StudioProgressTests(unittest.TestCase):
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
