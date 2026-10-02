import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from podcast_automate.errors import AppError
from podcast_automate.models import ResearchLimits, RunManifest, StageRecord
from podcast_automate.script_budget import STAGE_CALLS, budget_projection, calls_per_episode, ensure_script_budget
from podcast_automate.storage import file_hash, write_json, write_yaml

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

    def project(self):
        """A project folder with earlier runs beside the current one."""
        root = Path(self.folder.name) / "project"
        work = root / "runs" / "run_20261002_090000_000000_cccccccc"
        work.mkdir(parents=True)
        return root, work

    def earlier_run(self, root, run_id, *, kind="script", status="completed", calls=62, episodes=2, episode=None,
                    updated_at="2026-10-01T10:00:00+00:00"):
        work = root / "runs" / run_id
        write_yaml(work / "run_manifest.yaml", {"run_id": run_id, "kind": kind, "status": status, "updated_at": updated_at})
        write_json(work / "budget.json", {"model_calls": calls, "search_rounds": 0})
        write_json(work / "script_request.json", {"episode": episode})
        write_json(work / "series_plan.json", {"episodes": [{"episode_id": f"ep_{n:03d}"} for n in range(1, episodes + 1)]})

    def test_the_expectation_follows_the_last_completed_script_run_and_the_minimum_still_gates(self):
        """Finding of 2026-10-02: the projection showed only the mandatory minimum, 9 calls per episode, while the
        completed runs needed 31 (Asimov) and 42.5 (Ontologies). The expectation now scales by the project's last
        completed script run; only the minimum decides whether a stage may start."""
        root, work = self.project()
        self.earlier_run(root, "run_20260930_080000_000000_aaaaaaaa", calls=85, episodes=2,
                         updated_at="2026-09-30T08:00:00+00:00")
        self.earlier_run(root, "run_20261001_080000_000000_bbbbbbbb", calls=62, episodes=2)
        # Neither a research run, an unfinished script run nor the current run is a calibration.
        self.earlier_run(root, "run_20261001_120000_000000_dddddddd", kind="research", calls=600,
                         updated_at="2026-10-01T12:00:00+00:00")
        self.earlier_run(root, "run_20261001_130000_000000_eeeeeeee", status="blocked", calls=500,
                         updated_at="2026-10-01T13:00:00+00:00")
        self.earlier_run(root, work.name, status="completed", calls=900, updated_at="2026-10-02T09:00:00+00:00")
        write_json(work / "budget.json", {"model_calls": 0, "search_rounds": 0})
        projection = budget_projection(work, self.manifest, ResearchLimits(model_calls=40), self.entries,
                                       series_review=True, root=root)
        self.assertEqual(projection["calibration"], {"run_id": "run_20261001_080000_000000_bbbbbbbb", "model_calls": 62,
                                                     "episodes": 2, "calls_per_episode": 31.0})
        self.assertEqual(projection["minimum_remaining_calls"], 1 + 2 * 9 + 1)
        # Each episode stage scaled by 31/9; planning and the series review stay single calls.
        self.assertEqual(projection["expected_breakdown"], {"planning": 1, "teaching": 14, "writing": 7,
                                                            "polishing": 14, "review": 28, "series_review": 1})
        self.assertEqual(projection["expected_remaining_calls"], 65)
        self.assertEqual((projection["feasible"], projection["shortfall"], projection["expected_shortfall"]),
                         (True, 0, 25))
        self.assertIn("62 Aufrufe für 2 Folgen, 31 je Folge", projection["expected_label"])
        self.assertIn("Untergrenze", projection["minimum_label"])
        write_json(work / "budget.json", {"model_calls": 25, "search_rounds": 0})
        with self.assertRaises(AppError) as caught:
            ensure_script_budget(work, self.manifest, ResearchLimits(model_calls=40), self.entries, series_review=True,
                                 root=root)
        self.assertIn("Mindestens 20 weitere Modellaufrufe", str(caught.exception))
        self.assertIn("etwa 65 zu erwarten", str(caught.exception))

    def test_a_single_episode_run_counts_as_one_episode_and_no_run_means_the_minimum(self):
        root, work = self.project()
        projection = budget_projection(work, self.manifest, ResearchLimits(model_calls=40), self.entries,
                                       series_review=False, root=root)
        self.assertIsNone(projection["calibration"])
        self.assertEqual((projection["expected_remaining_calls"], projection["expected_source"]),
                         (projection["minimum_remaining_calls"], "minimum"))
        self.earlier_run(root, "run_20261001_080000_000000_bbbbbbbb", calls=40, episodes=12, episode="ep_003")
        projection = budget_projection(work, self.manifest, ResearchLimits(model_calls=40), self.entries,
                                       series_review=False, root=root)
        self.assertEqual(projection["calibration"]["calls_per_episode"], 40.0)
        self.assertEqual(projection["expected_source"], "project")
        # Without the project folder (an older caller) nothing is read.
        self.assertIsNone(budget_projection(work, self.manifest, ResearchLimits(model_calls=40), self.entries,
                                            series_review=False)["calibration"])


if __name__ == "__main__":
    unittest.main()
