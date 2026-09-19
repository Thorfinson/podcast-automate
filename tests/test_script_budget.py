import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from podcast_automate.errors import AppError
from podcast_automate.models import ResearchLimits, RunManifest, StageRecord
from podcast_automate.script_budget import STAGE_CALLS, budget_projection, calls_per_episode, ensure_script_budget
from podcast_automate.storage import file_hash, write_json

STAGES = ("planning", "teaching", "writing", "polishing", "review", "publish")


class ScriptBudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.work = Path(self.folder.name)
        self.manifest = RunManifest(run_id="run_x", kind="script", project_hash="a", input_hash="b",
                                    stages={name: StageRecord() for name in STAGES})
        self.entries = [SimpleNamespace(episode_id="ep_001"), SimpleNamespace(episode_id="ep_002")]

    def tearDown(self):
        self.folder.cleanup()

    def test_fresh_run_needs_planning_plus_nine_calls_per_episode_and_series_review(self):
        self.assertEqual(calls_per_episode(), 9)
        projection = budget_projection(self.work, self.manifest, ResearchLimits(model_calls=20), self.entries,
                                       series_review=True)
        self.assertEqual(projection["minimum_remaining_calls"], 1 + 2 * 9 + 1)
        self.assertEqual(projection["breakdown"]["review"], 2 * STAGE_CALLS["review"])
        self.assertTrue(projection["feasible"])

    def test_finished_checkpoints_and_completed_stages_are_not_counted(self):
        self.manifest.stages["planning"].status = "completed"
        self.manifest.stages["teaching"].status = "completed"
        draft = self.work / "drafts/ep_001.json"
        write_json(draft, {"episode_id": "ep_001"})
        write_json(draft.with_suffix(".checkpoint.json"), {"sha256": file_hash(draft)})
        write_json(self.work / "budget.json", {"model_calls": 5, "search_rounds": 0})
        projection = budget_projection(self.work, self.manifest, ResearchLimits(model_calls=20), self.entries,
                                       series_review=False)
        self.assertEqual(projection["breakdown"], {"writing": 1, "polishing": 4, "review": 8})
        self.assertEqual((projection["used"], projection["remaining"], projection["shortfall"]), (5, 15, 0))
        self.assertTrue(projection["feasible"])

    def test_insufficient_allowance_blocks_with_a_concrete_message_and_saved_projection(self):
        write_json(self.work / "budget.json", {"model_calls": 12, "search_rounds": 0})
        with self.assertRaises(AppError) as caught:
            ensure_script_budget(self.work, self.manifest, ResearchLimits(model_calls=20), self.entries, series_review=True)
        self.assertEqual(caught.exception.code, "script_budget_insufficient")
        self.assertEqual(caught.exception.status, "blocked")
        self.assertIn("Mindestens 20 weitere Modellaufrufe", str(caught.exception))
        self.assertIn("nur 8 von 20", str(caught.exception))
        saved = __import__("json").loads((self.work / "budget_projection.json").read_text(encoding="utf-8"))
        self.assertFalse(saved["feasible"])
        self.assertEqual(saved["shortfall"], 12)


if __name__ == "__main__":
    unittest.main()
