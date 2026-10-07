import json
import unittest
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.research import reconcile_budget, reserve_call, settle_call, settle_external
from podcast_automate.run_budget import BudgetApproval, approve_model_call_limit, effective_limits
from podcast_automate.script_budget import calls_per_episode
from podcast_automate.script_models import ScriptIssue, ScriptReview
from podcast_automate.scripting import outline_hash, run_script
from podcast_automate.storage import file_hash, load_project, read_yaml, write_json, write_yaml
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

    def test_a_source_limit_is_raised_explicitly_and_kept_when_another_limit_follows(self):
        baseline = self.fixture.config.research_limits
        approve_model_call_limit(self.root, self.run.run_id, search_rounds=baseline.search_rounds + 6)
        approve_model_call_limit(self.root, self.run.run_id, sources=baseline.sources + 40)
        limits = effective_limits(self.work, baseline, self.run.input_hash)
        self.assertEqual((limits.model_calls, limits.search_rounds, limits.sources),
                         (baseline.model_calls, baseline.search_rounds + 6, baseline.sources + 40))
        # Raising the calls later keeps both raised limits; nothing in the project changes.
        approve_model_call_limit(self.root, self.run.run_id, baseline.model_calls + 10)
        limits = effective_limits(self.work, baseline, self.run.input_hash)
        self.assertEqual((limits.model_calls, limits.search_rounds, limits.sources),
                         (baseline.model_calls + 10, baseline.search_rounds + 6, baseline.sources + 40))
        self.assertEqual(load_project(self.root).research_limits, baseline)
        for value in (True, baseline.sources + 39, 0, "200", 200.5):
            with self.subTest(sources=value), self.assertRaises(AppError):
                approve_model_call_limit(self.root, self.run.run_id, sources=value)

    def test_project_limits_raised_above_a_run_approval_apply_instead_of_stopping_the_run(self):
        # Limits left the resumed run's hash (storage.bound_brief, 2026-10-02), so project.yaml may now be raised while a
        # run with an earlier approval waits; the higher limit applies, and the approval stays bound to its own run.
        baseline = self.fixture.config.research_limits
        approve_model_call_limit(self.root, self.run.run_id, baseline.model_calls + 10,
                                 search_rounds=baseline.search_rounds + 2)
        raised = baseline.model_copy(update={"model_calls": baseline.model_calls + 500, "sources": baseline.sources + 7})
        limits = effective_limits(self.work, raised, self.run.input_hash)
        self.assertEqual((limits.model_calls, limits.search_rounds, limits.sources),
                         (baseline.model_calls + 500, baseline.search_rounds + 2, baseline.sources + 7))
        with self.assertRaises(AppError):
            effective_limits(self.work, raised, "0" * 64)

    def test_limit_must_be_an_explicit_integer_increase(self):
        for limit in (True, 39, 0, "120", 120.5):
            with self.subTest(limit=limit), self.assertRaises(AppError):
                approve_model_call_limit(self.root, self.run.run_id, limit)
        self.assertFalse((self.work / "budget_approval.json").exists())

    def test_new_projects_get_the_raised_defaults_without_overwriting_explicit_limits(self):
        from podcast_automate.models import TopicBrief
        limits = TopicBrief(topic="New project").research_limits
        # The two 18-question runs of 2026-09-26/27 needed 600 to 750 calls and up to 46 search rounds.
        self.assertEqual((limits.model_calls, limits.search_rounds, limits.sources), (750, 48, 150))
        self.assertEqual(TopicBrief(topic="Existing project", research_limits={"model_calls": 40}).research_limits.model_calls, 40)


class FreshAttemptOfferTests(unittest.TestCase):
    def test_fresh_attempts_are_offered_only_where_the_approval_would_set_something_aside(self):
        """2026-10-02: "Mit neuen Anläufen fortsetzen" stood on research stops without a stuck call, and again after
        an allowance had reset the repairs; both times the backend refused it."""
        import tempfile
        from pathlib import Path
        from podcast_automate.models import RunManifest, StageRecord
        from podcast_automate.research_patches import MAX_REJECTIONS
        from podcast_automate.run_budget import approve_fresh_attempts, fresh_attempts_available
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        work = root / "runs/run_r"

        def manifest(status):
            write_yaml(work / "run_manifest.yaml", RunManifest(run_id="run_r", kind="research", status=status,
                       project_hash="p", input_hash="i", stages={"dossier": StageRecord(status=status)}).model_dump(mode="json"))
        manifest("blocked")
        self.assertFalse(fresh_attempts_available(root, "run_r"))
        for number in range(MAX_REJECTIONS + 1):
            write_json(work / f"question_research/tasks/t1/patch_rejected_{number:02d}.json", {"code": "rejected_output"})
        self.assertTrue(fresh_attempts_available(root, "run_r"))
        manifest("running")
        self.assertFalse(fresh_attempts_available(root, "run_r"), "a running run is never touched")
        manifest("blocked")
        approve_fresh_attempts(root, "run_r")
        self.assertFalse(fresh_attempts_available(root, "run_r"), "once set aside there is nothing left to offer")
        self.assertFalse(fresh_attempts_available(root, "run_missing"))


class ReaskedStopTests(unittest.TestCase):
    """D-155: a script stop whose correction loop keeps no rejection is resumed by asking anew, so fresh attempts (and an
    allowance of them) cover it; a stop that would replay a saved state does not take them."""

    def test_a_spent_writing_stop_takes_fresh_attempts_and_the_resume_writes_the_episode_anew(self):
        from podcast_automate.models import EpisodeScript
        from podcast_automate.run_budget import REASKED_MARKER, approve_fresh_attempts, fresh_attempts_available
        fixture = fixtures.script_project(self)
        root = fixture.root
        drafts = []

        def model(prompt, schema, directory, **kwargs):
            if schema is EpisodeScript and len(drafts) < 4:
                # The draft and its three repairs keep the wrong episode ID (validate_script), so writing stops.
                drafts.append(prompt)
                return fixtures.example_script().model_copy(update={"episode_id": "ep_999"}), {}
            return fixture.model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=fixture.model):
            planned = run_script(root, plan_only=True)
        work = root / "runs" / planned.run_id
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            stopped = run_script(root, resume=True, approved_plan_hash=outline_hash(work))
        error = stopped.stages["writing"].error
        self.assertEqual((stopped.status, error.code, len(drafts)), ("blocked", "invalid_script", 4))
        self.assertIn(REASKED_MARKER, error.message)
        self.assertFalse((work / "drafts/ep_001.json").exists(), "a rejected draft is never kept for a resume")
        self.assertTrue(fresh_attempts_available(root, planned.run_id))
        record = approve_fresh_attempts(root, planned.run_id)
        self.assertEqual((record["supplements"], record["reviews"], record["series_repair"]), ([], [], False))
        # Nothing is set aside; the record names the stop the resume asks anew (D-155).
        self.assertEqual(record["reasked"]["code"], "invalid_script")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            done = run_script(root, resume=True, run_id=planned.run_id)
        self.assertEqual(done.status, "completed", done.model_dump())
        self.assertEqual(json.loads((work / "drafts/ep_001.json").read_text(encoding="utf-8"))["episode_id"], "ep_001")

    def test_a_stop_that_replays_a_saved_state_takes_no_fresh_attempts(self):
        import tempfile
        from pathlib import Path
        from podcast_automate.models import Failure, RunManifest, StageRecord
        from podcast_automate.run_budget import REASKED_MARKER, fresh_attempts_available
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)

        def stopped(kind, stage, code, message):
            write_yaml(root / "runs/run_s/run_manifest.yaml", RunManifest(
                run_id="run_s", kind=kind, status="blocked", project_hash="p", input_hash="i",
                stages={stage: StageRecord(status="blocked", error=Failure(code=code, message=message))}).model_dump(mode="json"))
            return fresh_attempts_available(root, "run_s")
        spent = f"Lehrprüfung lässt Kriterien aus. Der Aufruf wurde 2 Mal mit Korrekturhinweis wiederholt; {REASKED_MARKER}."
        self.assertTrue(stopped("script", "review", "invalid_teaching_review", spent))
        self.assertTrue(stopped("script", "teaching", "invalid_supplement", "Give issue_basis for every issue. " + spent))
        # A changed review checkpoint is read again on every resume; a stop the user decides keeps its own card.
        self.assertFalse(stopped("script", "review", "invalid_script", "Gespeicherter Review-Entwurf ist ungültig."))
        self.assertFalse(stopped("script", "teaching", "teaching_design_failed", spent))
        # Rejections a research run saved are replayed until fresh attempts set them aside (stuck_calls), whatever the code.
        self.assertFalse(stopped("research", "dossier", "invalid_model_output", spent))
        self.assertFalse(stopped("script", "writing", "invalid_script", "Gespeicherte Nachrecherche: die abgewiesenen "
                                 "Antworten sind gespeichert."))

    def test_the_marker_is_what_the_correction_loops_without_a_receipt_store_end_with(self):
        """The marker is matched against stop messages; these are the functions that write it."""
        import tempfile
        from pathlib import Path
        from podcast_automate.research_patches import cached_call, corrected_call, re_asked
        from podcast_automate.run_budget import REASKED_MARKER
        from podcast_automate.script_models import ScriptReview

        def check(answer):
            raise AppError("Lehrprüfung lässt Kriterien aus.", code="invalid_teaching_review", status="blocked")
        with self.assertRaises(AppError) as corrected:
            corrected_call(lambda prompt, schema, version: "answer", "task\n{}", ScriptReview, "v1", check)
        self.assertEqual(corrected.exception.code, "invalid_teaching_review")
        self.assertIn(REASKED_MARKER, str(corrected.exception))

        def rejected(prompt):
            raise AppError("Falsches Format.", code="rejected_output", details={"payload": {}})
        with self.assertRaises(AppError) as asked:
            re_asked(rejected, "task\n{}")
        self.assertIn(REASKED_MARKER, str(asked.exception))
        # A receipt store keeps its rejections and replays them: its stop does not carry the marker.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with self.assertRaises(AppError) as stored:
            cached_call(Path(temp.name), "review", ScriptReview, "task\n{}", lambda prompt, schema: rejected(prompt))
        self.assertEqual(stored.exception.code, "rejected_output")
        self.assertNotIn(REASKED_MARKER, str(stored.exception))


if __name__ == "__main__":
    unittest.main()


class MoneyLimitTests(unittest.TestCase):
    """A run billed to the user's key needs a money limit and stops at it; its spending counts every attempt (D-146,
    D-148). Expectations follow from the rows written here, not from the estimate table."""

    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            self.run = run_script(self.root, plan_only=True)
        self.work = self.root / "runs" / self.run.run_id
        self.baseline = self.fixture.config.research_limits
        write_json(self.work / "budget.json", {"model_calls": 3, "search_rounds": 0})

    def budget(self):
        return json.loads((self.work / "budget.json").read_text(encoding="utf-8"))

    def bill(self, number, rows):
        write_json(self.work / "calls" / f"call_{number:03d}" / "billing.json", rows)

    def test_an_unset_money_limit_leaves_every_dump_and_hash_as_it_was(self):
        self.assertEqual(self.baseline.model_dump(), {"search_rounds": self.baseline.search_rounds,
                                                      "sources": self.baseline.sources,
                                                      "model_calls": self.baseline.model_calls})
        self.assertEqual(self.baseline.model_copy(update={"cost_usd": 25.0}).model_dump()["cost_usd"], 25.0)

    def test_a_billed_call_needs_the_limit_and_stops_when_it_is_spent(self):
        before = self.budget()
        with self.assertRaises(AppError) as missing:
            reserve_call(self.work, self.baseline, billed=True)
        self.assertEqual((missing.exception.code, missing.exception.status), ("cost_limit_required", "blocked"))
        # A subscription call never asks for money.
        self.assertEqual(reserve_call(self.work, self.baseline), 4)
        limited = self.baseline.model_copy(update={"cost_usd": 1.0})
        self.assertEqual(reserve_call(self.work, limited, billed=True), 5)
        self.bill(5, [{"provider": "claude_api", "model": "claude-sonnet-5-5", "state": "priced", "usd": 0.75},
                      {"provider": "claude_api", "model": "claude-sonnet-5-5", "state": "priced", "usd": 0.30}])
        self.assertTrue(settle_call(self.work, 5))
        self.assertFalse(settle_call(self.work, 5), "a call is counted once")
        spent = self.budget()
        self.assertEqual((spent["billed_usd"], spent["priced_attempts"], spent["model_calls"]), (1.05, 2, 5))
        with self.assertRaises(AppError) as reached:
            reserve_call(self.work, limited, billed=True)
        self.assertEqual((reached.exception.code, reached.exception.details["spent_usd"]), ("cost_limit_reached", 1.05))
        self.assertEqual(self.budget()["model_calls"], 5, "a refused call charges nothing")
        self.assertEqual(before["model_calls"], 3)

    def test_an_attempt_without_a_reported_cost_counts_at_the_runs_mean_and_a_free_one_not_at_all(self):
        self.bill(4, [{"provider": "openrouter", "model": "vendor/x", "state": "priced", "usd": 0.4},
                      {"provider": "openrouter", "model": "vendor/x", "state": "free", "usd": None}])
        self.bill(5, [{"provider": "openrouter", "model": "vendor/x", "state": "unpriced", "usd": None},
                      {"provider": "openrouter", "model": "vendor/x", "state": "started", "usd": None}])
        settle_call(self.work, 4)
        settle_call(self.work, 5)
        spent = self.budget()
        self.assertEqual((spent["billed_usd"], spent["estimated_usd"], spent["unpriced_attempts"]), (0.4, 0.8, 2))

    def test_a_resume_counts_what_a_killed_worker_left_and_changes_only_the_budget(self):
        self.bill(6, [{"provider": "claude_api", "model": "claude-sonnet-5-5", "state": "priced", "usd": 0.5},
                      {"provider": "claude_api", "model": "claude-sonnet-5-5", "state": "started", "usd": None}])
        before = (self.work / "calls/call_006/billing.json").read_bytes()
        reconcile_budget(self.work)
        reconcile_budget(self.work)
        spent = self.budget()
        self.assertEqual((spent["billed_usd"], spent["estimated_usd"], spent["settled"]), (0.5, 0.5, [6]))
        self.assertEqual((self.work / "calls/call_006/billing.json").read_bytes(), before)

    def test_a_subscription_runs_budget_gains_no_money_keys(self):
        write_json(self.work / "calls/call_004/response.json", {})
        self.assertFalse(settle_call(self.work, 4))
        reconcile_budget(self.work)
        self.assertEqual(set(self.budget()), {"model_calls", "search_rounds"})

    def test_money_outside_a_call_counts_once(self):
        self.assertTrue(settle_external(self.work, "jev_probe", 0.6))
        self.assertFalse(settle_external(self.work, "jev_probe", 0.6))
        self.assertEqual(self.budget()["external_usd"], 0.6)

    def test_a_money_limit_is_raised_explicitly_and_kept_when_the_calls_are_raised_later(self):
        approve_model_call_limit(self.root, self.run.run_id, cost_usd=40)
        approve_model_call_limit(self.root, self.run.run_id, self.baseline.model_calls + 10)
        limits = effective_limits(self.work, self.baseline, self.run.input_hash)
        self.assertEqual((limits.model_calls, limits.cost_usd), (self.baseline.model_calls + 10, 40.0))
        # A project limit above the receipt applies; nothing in the project changed.
        raised = self.baseline.model_copy(update={"cost_usd": 90.0})
        self.assertEqual(effective_limits(self.work, raised, self.run.input_hash).cost_usd, 90.0)
        self.assertEqual(load_project(self.root).research_limits, self.baseline)
        for value in (True, 39.5, 0, -1, "50", float("nan"), 200_000):
            with self.subTest(cost=value), self.assertRaises(AppError):
                approve_model_call_limit(self.root, self.run.run_id, cost_usd=value)
        # A receipt written before the money limit existed still reads.
        old = BudgetApproval.model_validate({"run_id": self.run.run_id, "input_hash": self.run.input_hash,
                                             "model_calls": 50, "approved_at": "2026-10-01T00:00:00+00:00"})
        self.assertIsNone(old.cost_usd)



class BilledScriptRunTests(unittest.TestCase):
    """A script run on the user's Anthropic key end to end: the key reaches only its calls, the money of each call
    counts, the run stops at its limit before the next call and goes on after an explicit raise (D-145, D-146)."""

    KEY = "sk-ant-test-key-0123456789"

    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        self.fixture.config.research_limits.cost_usd = 1.0
        write_yaml(self.root / "project.yaml", self.fixture.config.model_dump(mode="json"))
        self.keys = []

    def claude(self, adapter, prompt, output_type, directory, **kwargs):
        self.keys.append((adapter.auth, adapter._key.get_secret_value()))
        output, _ = self.fixture.model(prompt, output_type, directory, **kwargs)
        return output, {"separately_billed_cost": 0.3}

    def test_a_run_on_the_key_stops_at_its_money_limit_and_goes_on_after_a_raise(self):
        from podcast_automate import subscriptions
        no_quota = AssertionError("a run on the key asks no quota")
        with patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", autospec=True, side_effect=self.claude), \
                patch.object(subscriptions, "claude_quota", side_effect=no_quota), \
                patch.object(subscriptions, "codex_quota", side_effect=no_quota), \
                patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("no Codex")):
            stopped = run_script(self.root, backend="claude_api", api_key=self.KEY)
            work = self.root / "runs" / stopped.run_id
            budget = json.loads((work / "budget.json").read_text(encoding="utf-8"))
            # Four calls of 0.30 USD reach the 1 USD limit; the fifth is refused before it is charged.
            self.assertEqual(stopped.status, "blocked")
            self.assertEqual([stage.error.code for stage in stopped.stages.values() if stage.error],
                             ["cost_limit_reached"])
            self.assertEqual((budget["model_calls"], budget["billed_usd"], budget["priced_attempts"]), (4, 1.2, 4))
            self.assertEqual(set(self.keys), {("api_key", self.KEY)})
            approve_model_call_limit(self.root, stopped.run_id, cost_usd=200)
            finished = run_script(self.root, resume=True, run_id=stopped.run_id, api_key=self.KEY)
        self.assertEqual(finished.status, "completed")
        budget = json.loads((work / "budget.json").read_text(encoding="utf-8"))
        self.assertEqual(budget["billed_usd"], round(0.3 * budget["model_calls"], 6))
        self.assertEqual(len(budget["settled"]), budget["model_calls"])
        request = json.loads((work / "script_request.json").read_text(encoding="utf-8"))
        self.assertEqual((request["text_generation"]["provider"], request["text_generation"]["adapter_version"]),
                         ("claude_api", "claude_api.v1"))
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.KEY.encode(), path.read_bytes(), path)
