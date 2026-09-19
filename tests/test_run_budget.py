import json
import unittest
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.research import reserve_call
from podcast_automate.run_budget import approve_model_call_limit, effective_limits
from podcast_automate.script_budget import calls_per_episode
from podcast_automate.script_models import ScriptIssue, ScriptReview
from podcast_automate.scripting import outline_hash, run_script
from podcast_automate.storage import file_hash, read_yaml, write_json, write_yaml
from podcast_automate.studio_progress import script_progress
from podcast_automate.teaching import TeachingPlan
from tests import script_fixtures as fixtures


class RunBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        # An existing project retains its explicit older allowance after a default change.
        self.fixture.config.research_limits.model_calls = 40
        write_yaml(self.root / "project.yaml", self.fixture.config.model_dump(mode="json"))
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            self.run = run_script(self.root, plan_only=True)
        self.work = self.root / "runs" / self.run.run_id

    def test_raise_resumes_same_approved_run_without_resetting_counts_or_changing_inputs(self):
        plan_hash = outline_hash(self.work)
        write_json(self.work / "budget.json", {"model_calls": 40, "search_rounds": 2})
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            blocked = run_script(self.root, resume=True, approved_plan_hash=plan_hash)
            # The used allowance equals the limit: the run stops before the first paid teaching call.
            self.assertEqual(blocked.stages["teaching"].error.code, "script_budget_insufficient")
            protected = [self.root / "project.yaml", self.work / "plan_approval.json", self.work / "inputs.json",
                         self.work / "script_request.json", self.work / "budget.json"]
            before = {p: file_hash(p) for p in protected}
            approve_model_call_limit(self.root, self.run.run_id, 120)
            self.assertEqual(before, {p: file_hash(p) for p in protected})
            completed = run_script(self.root, resume=True, run_id=self.run.run_id)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(outline_hash(self.work), plan_hash)
        counts = json.loads((self.work / "budget.json").read_text())
        self.assertGreater(counts["model_calls"], 40)
        self.assertEqual(counts["search_rounds"], 2)
        # Independent check of the projection table: the no-repair fixture spends exactly the projected
        # minimum per episode plus one series review. A new model call in any stage must update STAGE_CALLS.
        episodes = len(json.loads((self.work / "series_plan.json").read_text(encoding="utf-8"))["episodes"])
        self.assertEqual(counts["model_calls"], 40 + calls_per_episode() * episodes + 1)
        self.assertFalse(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])
        self.assertEqual(script_progress(self.root, completed.model_dump(mode="json"))["model_call_limit"], 120)
        fresh = self.root / "runs/run_fresh"
        self.assertEqual(effective_limits(fresh, self.fixture.config.research_limits, "0" * 64).model_calls, 40)

    def test_insufficient_allowance_blocks_before_any_paid_call_and_resumes_after_raise(self):
        write_json(self.work / "budget.json", {"model_calls": 39, "search_rounds": 0})
        calls = []
        def model(prompt, schema, directory, **kwargs):
            calls.append(schema.__name__)
            return self.fixture.model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            blocked = run_script(self.root, resume=True, approved_plan_hash=outline_hash(self.work))
        self.assertEqual(blocked.stages["teaching"].error.code, "script_budget_insufficient")
        self.assertEqual(calls, [], "no model call may be spent when the remaining allowance cannot finish the episodes")
        projection = json.loads((self.work / "budget_projection.json").read_text(encoding="utf-8"))
        self.assertFalse(projection["feasible"])
        self.assertEqual(projection["remaining"], 1)
        self.assertGreaterEqual(projection["minimum_remaining_calls"], 9)
        self.assertIn("Mindestens", blocked.stages["teaching"].error.message)
        self.assertEqual(json.loads((self.work / "budget.json").read_text())["model_calls"], 39)
        approve_model_call_limit(self.root, self.run.run_id, 120)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root, resume=True, run_id=self.run.run_id)
        self.assertEqual(run.status, "completed")
        self.assertIn("TeachingPlan", calls)
        self.assertGreater(json.loads((self.work / "budget.json").read_text())["model_calls"], 40)
        self.assertTrue(json.loads((self.work / "budget_projection.json").read_text(encoding="utf-8"))["feasible"])

    def test_running_worker_reads_a_raised_allowance_before_its_next_call(self):
        # 30 of 40 used leaves exactly the projected minimum (one episode plus the series review), so the
        # pre-stage gate passes. One review repair then needs more calls than the projection promised;
        # the allowance raised during the first teaching call must be honoured without a restart.
        write_json(self.work / "budget.json", {"model_calls": 30, "search_rounds": 0})
        reviews = []
        def model(prompt, schema, directory, **kwargs):
            if schema is TeachingPlan:
                approve_model_call_limit(self.root, self.run.run_id, 120)
            value, meta = self.fixture.model(prompt, schema, directory, **kwargs)
            if schema is ScriptReview:
                reviews.append(schema)
                if len(reviews) == 1:
                    value.issues = [ScriptIssue(category="depth", segment_ids=["seg_002"], reason="Repair once.")]
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root, resume=True, approved_plan_hash=outline_hash(self.work))
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(reviews), 2)
        self.assertGreater(json.loads((self.work / "budget.json").read_text())["model_calls"], 40)

    def test_approval_cannot_be_reused_for_another_run_and_does_not_raise_search_limit(self):
        approve_model_call_limit(self.root, self.run.run_id, 120)
        baseline = self.fixture.config.research_limits
        with self.assertRaises(AppError):
            effective_limits(self.work, baseline, "0" * 64)
        copied = self.root / "runs/run_copy"
        write_json(copied / "budget_approval.json", json.loads((self.work / "budget_approval.json").read_text()))
        with self.assertRaises(AppError):
            effective_limits(copied, baseline, self.run.input_hash)
        limits = effective_limits(self.work, baseline, self.run.input_hash)
        write_json(self.work / "budget.json", {"model_calls": 40, "search_rounds": baseline.search_rounds})
        with self.assertRaises(AppError):
            reserve_call(self.work, limits, search=True)
        self.assertEqual(reserve_call(self.work, limits), 41)
        write_json(self.work / "budget.json", {"model_calls": 120, "search_rounds": 0})
        with self.assertRaises(AppError):
            reserve_call(self.work, limits)

    def test_limit_must_be_an_explicit_integer_increase(self):
        for limit in (True, 39, 0, "120", 120.5):
            with self.subTest(limit=limit), self.assertRaises(AppError):
                approve_model_call_limit(self.root, self.run.run_id, limit)
        self.assertFalse((self.work / "budget_approval.json").exists())

    def test_new_projects_default_to_150_without_overwriting_explicit_limits(self):
        from podcast_automate.models import TopicBrief
        self.assertEqual(TopicBrief(topic="New project").research_limits.model_calls, 150)
        self.assertEqual(TopicBrief(topic="Existing project", research_limits={"model_calls": 40}).research_limits.model_calls, 40)


if __name__ == "__main__":
    unittest.main()
