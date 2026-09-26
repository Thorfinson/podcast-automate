"""The plan gate: a projection before the first task call, run-bound approvals, caps and calibration."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.models import RunManifest
from podcast_automate.question_budget import (DEFAULT_CALLS_PER_TASK, DEFAULT_SECONDS_PER_CALL, affordable_tasks,
                                              expected_calls_per_task, plan_review_message, review_parts_per_round, seconds_per_call,
                                              write_calibration)
from podcast_automate.question_scope import QuestionScopeReview
from podcast_automate.research import run_research
from podcast_automate.research_ledger import read_value, save_value
from podcast_automate.research_models import ResearchDiscovery, ResearchDossier
from podcast_automate.research_quality import ResearchAssessment
from podcast_automate.research_review import SourceReview
from podcast_automate.research_tasks import AnswerReview, QuestionPlan, ResearchDecision
from podcast_automate.run_budget import approve_research_plan, plan_approval_for, read_plan_approval
from podcast_automate.storage import digest, file_hash, write_json, write_yaml
from podcast_automate.studio_progress import research_progress
from podcast_automate.studio_worker import perform
from tests import research_fixtures as fixtures
from tests.question_fixtures import task_value

MODEL = "podcast_automate.research.CodexAdapter.structured"
PLANNING = [ResearchDiscovery, QuestionPlan, QuestionScopeReview]


class PlanGateTests(fixtures.ResearchProjectCase):
    def work(self, run):
        return self.root / "runs" / run.run_id

    def projection(self, run):
        return json.loads((self.work(run) / "question_research/plan_projection.json").read_text(encoding="utf-8"))

    def planner(self, tasks):
        """A fixture model whose plan has ``tasks(cap)`` tasks for the cap the allowance names."""
        def model(prompt, output_type, directory, **kwargs):
            if output_type is QuestionPlan:
                self.calls.append(output_type)
                payload = json.loads(prompt.splitlines()[-1])
                count = tasks(payload["planning_budget"]["max_tasks"])
                return QuestionPlan(tasks=[task_value(f"task_{n}") for n in range(count)]), {}
            return self.model(prompt, output_type, directory, **kwargs)
        return model

    def test_run_stops_at_the_gate_with_a_projection_before_the_first_task_call(self):
        with patch(MODEL, side_effect=self.model):
            first = run_research(self.root, plan_review="required")
        self.assertEqual(first.status, "blocked")
        error = first.stages["dossier"].error
        self.assertEqual(error.code, "research_plan_review")
        self.assertIn("1 Teilfragen, voraussichtlich 11 Aufrufe, etwa", error.message)
        self.assertIn("Stunden bei", error.message)
        self.assertIn(f"--research-plan {first.run_id}", error.message)
        # Discovery, plan and scope were spent; no reading or review call before the approval.
        self.assertEqual(self.calls, PLANNING)
        self.assertEqual(json.loads((self.work(first) / "budget.json").read_text())["model_calls"], 3)
        projection = self.projection(first)
        state = read_value(self.work(first) / "question_research/state.json")
        self.assertEqual(state["phase"], "awaiting_plan_approval")
        self.assertEqual(projection["plan_hash"], digest(state["plan"]))
        self.assertEqual((projection["tasks"], projection["tasks_pending"]), (1, 1))
        # One task at the default rate plus the three identifiable closing calls (dossier, grounding, assessment).
        self.assertEqual((projection["expected_calls_per_task"], projection["expected_calls_source"]), (DEFAULT_CALLS_PER_TASK, "default"))
        self.assertEqual((projection["closing_calls"], projection["closing_reserve"], projection["projected_calls"]), (3, 4, 11))
        # The approved limit is the project's own allowance.
        self.assertEqual((projection["approved_limit"], projection["used"], projection["within_limit"]),
                         (self.config.research_limits.model_calls, 3, True))
        # Three answered calls carry timings, so the hours come from this run's own median.
        self.assertEqual(projection["seconds_per_call_source"], "run")
        self.assertEqual(len(state["call_timings"]), 3)
        self.assertEqual([row["task"] for row in state["call_timings"]], [None, None, None])
        self.assertEqual(projection["projected_hours"], round(11 * projection["seconds_per_call"] / 3600, 1))
        ledger = json.loads((self.work(first) / "research_questions.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["phase"], "awaiting_plan_approval")
        self.assertIn("wartet auf Freigabe", (self.work(first) / "research_questions.md").read_text(encoding="utf-8"))
        review = research_progress(self.root, first.model_dump(mode="json"))["plan_review"]
        self.assertEqual((review["awaiting"], review["approved"], review["approval"]), (True, False, None))
        self.assertEqual(review["projection"]["plan_hash"], projection["plan_hash"])
        # A resume without an approval blocks again and spends nothing.
        with patch(MODEL, side_effect=self.model):
            again = run_research(self.root, resume=True, plan_review="required")
        self.assertEqual((again.status, again.stages["dossier"].error.code), ("blocked", "research_plan_review"))
        self.assertEqual(self.calls, PLANNING)

    def test_an_approval_receipt_resumes_without_spending_a_call_and_publishes_the_calibration(self):
        with patch(MODEL, side_effect=self.model):
            first = run_research(self.root, plan_review="required")
            approval = approve_research_plan(self.root, first.run_id)
            self.assertEqual(approval.plan_hash, self.projection(first)["plan_hash"])
            resumed = run_research(self.root, resume=True, plan_review="required")
        self.assertEqual(resumed.status, "completed", resumed.model_dump())
        self.assertEqual(resumed.run_id, first.run_id)
        self.assertEqual(self.calls, PLANNING + [ResearchDecision, AnswerReview, ResearchDossier, SourceReview,
                                                 ResearchAssessment])
        self.assertEqual(self.calls.count(QuestionPlan), 1)
        self.assertEqual(self.calls.count(QuestionScopeReview), 1)
        state = read_value(self.work(first) / "question_research/state.json")
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["plan_approval"]["plan_hash"], approval.plan_hash)
        self.assertEqual([row["task"] for row in state["call_timings"]][:5], [None, None, None, "task_definition", "task_definition"])
        review = research_progress(self.root, resumed.model_dump(mode="json"))["plan_review"]
        self.assertEqual((review["awaiting"], review["approved"]), (False, True))
        calibration = json.loads((self.root / "research/calibration.json").read_text(encoding="utf-8"))
        self.assertEqual((calibration["run_id"], calibration["tasks"], calibration["verified_tasks"]), (first.run_id, 1, 1))
        # The one verified task spent exactly its reading decision and its independent review.
        self.assertEqual(calibration["calls_per_task"], 2)
        self.assertIsInstance(calibration["seconds_per_call"], float)
        self.assertEqual(calibration["measured_calls"], 8)
        self.assertIn("research/calibration.json", resumed.stages["publish"].outputs)

    def test_a_run_planned_before_the_gate_is_gated_on_resume_while_no_task_is_finished(self):
        paused = []

        def model(prompt, output_type, directory, **kwargs):
            if output_type is ResearchDecision and not paused:
                paused.append(1)
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.model(prompt, output_type, directory, **kwargs)

        # The bare API never gates: this is the shape of every run planned before the gate existed.
        with patch(MODEL, side_effect=model):
            first = run_research(self.root)
        self.assertEqual(first.status, "waiting_for_quota")
        state = read_value(self.work(first) / "question_research/state.json")
        self.assertEqual(state["phase"], "questions")
        self.assertNotIn("plan_approval", state)
        spent = list(self.calls)
        # Resuming through the Studio or CLI path projects the untouched plan and waits, spending nothing.
        with patch(MODEL, side_effect=self.model):
            resumed = run_research(self.root, resume=True, run_id=first.run_id, plan_review="required")
        self.assertEqual((resumed.status, resumed.stages["dossier"].error.code), ("blocked", "research_plan_review"))
        self.assertEqual(self.calls, spent)
        self.assertEqual(self.projection(first)["tasks"], 1)
        self.assertEqual(read_value(self.work(first) / "question_research/state.json")["phase"], "awaiting_plan_approval")
        with patch(MODEL, side_effect=self.model):
            approve_research_plan(self.root, first.run_id)
            done = run_research(self.root, resume=True, run_id=first.run_id, plan_review="required")
        self.assertEqual(done.status, "completed", done.model_dump())
        self.assertEqual(self.calls, spent + [ResearchDecision, AnswerReview, ResearchDossier, SourceReview, ResearchAssessment])

    def test_a_resume_that_does_not_replan_changes_only_the_budget_files(self):
        with patch(MODEL, side_effect=self.model):
            first = run_research(self.root, plan_review="required")
            approve_research_plan(self.root, first.run_id)
            completed = run_research(self.root, resume=True, plan_review="required")
        self.assertEqual(completed.status, "completed")
        work = self.work(first)
        # Counters and projections are live status; the manifest is the run's own status record.
        volatile = {"budget.json", "budget_projection.json", "run_manifest.yaml"}
        before = {p.relative_to(work).as_posix(): file_hash(p) for p in sorted(work.rglob("*"))
                  if p.is_file() and p.name not in volatile}
        calls = len(self.calls)
        with patch(MODEL, side_effect=self.model):
            again = run_research(self.root, resume=True, plan_review="required")
        self.assertEqual((again.status, len(self.calls)), ("completed", calls))
        after = {p.relative_to(work).as_posix(): file_hash(p) for p in sorted(work.rglob("*"))
                 if p.is_file() and p.name not in volatile}
        self.assertEqual(after, before)

    def test_a_lower_cap_replans_once_and_blocks_again_for_the_new_plan(self):
        model = self.planner(lambda cap: min(3, cap))
        with patch(MODEL, side_effect=model):
            first = run_research(self.root, plan_review="required")
            self.assertEqual((first.status, self.projection(first)["tasks"]), ("blocked", 3))
            self.assertIn("3 Teilfragen", first.stages["dossier"].error.message)
            capped = approve_research_plan(self.root, first.run_id, max_tasks=1)
            self.assertEqual(capped.max_tasks, 1)
            second = run_research(self.root, resume=True, plan_review="required")
        # One planning call and one scope pass under the cap; the smaller plan waits for its own approval.
        self.assertEqual((second.status, second.stages["dossier"].error.code), ("blocked", "research_plan_review"))
        self.assertIn("1 Teilfragen", second.stages["dossier"].error.message)
        self.assertEqual(self.calls, PLANNING + [QuestionPlan, QuestionScopeReview])
        work = self.work(first)
        state = read_value(work / "question_research/state.json")
        self.assertEqual((state["phase"], len(state["plan"]["tasks"]), state["plan_caps"]), ("awaiting_plan_approval", 1, [1]))
        self.assertEqual(state["plan_revisions"][0]["cap"], 1)
        self.assertEqual(state["plan_revisions"][0]["previous_plan_hash"], capped.plan_hash)
        self.assertNotEqual(state["plan_revisions"][0]["plan_hash"], capped.plan_hash)
        self.assertEqual(read_value(work / "question_research/planning_budget_cap1.json")["max_tasks"], 1)
        self.assertTrue((work / "question_research/plan_cap1.json").exists())
        self.assertTrue((work / "question_research/plan.json").exists(), "the first plan's receipt is retained")
        # The old receipt approves the old plan only; nothing runs until the shown plan is approved.
        self.assertIsNone(plan_approval_for(work, first.input_hash, self.projection(first)["plan_hash"]))
        with patch(MODEL, side_effect=model):
            blocked = run_research(self.root, resume=True, plan_review="required")
            self.assertEqual(blocked.stages["dossier"].error.code, "research_plan_review")
            self.assertEqual(len(self.calls), 5)
            approve_research_plan(self.root, first.run_id)
            done = run_research(self.root, resume=True, plan_review="required")
        self.assertEqual(done.status, "completed", done.model_dump())
        self.assertEqual(self.calls.count(ResearchDecision), 1)
        self.assertEqual(len(read_value(work / "question_research/state.json")["tasks"]), 1)

    def test_a_cap_the_planner_cannot_meet_is_tried_once_then_needs_an_explicit_decision(self):
        model = self.planner(lambda cap: 3)
        with patch(MODEL, side_effect=model):
            first = run_research(self.root, plan_review="required")
            approve_research_plan(self.root, first.run_id, max_tasks=1)
            second = run_research(self.root, resume=True, plan_review="required")
        self.assertEqual((second.status, second.stages["dossier"].error.code), ("blocked", "research_plan_review"))
        self.assertIn("Obergrenze von 1 Teilfragen wurde bereits angefordert", second.stages["dossier"].error.message)
        # Two rejections with the cap named, then the final attempt keeps the plan; one scope pass follows.
        self.assertEqual(self.calls.count(QuestionPlan), 4)
        self.assertEqual(self.calls.count(QuestionScopeReview), 2)
        work = self.work(first)
        self.assertTrue((work / "question_research/plan_cap1_rejected_01.json").exists())
        state = read_value(work / "question_research/state.json")
        self.assertEqual((len(state["plan"]["tasks"]), state["plan_caps"]), (3, [1]))
        calls = len(self.calls)
        with patch(MODEL, side_effect=model):
            third = run_research(self.root, resume=True, plan_review="required")
            # The same cap is not tried again; only a new decision moves the run.
            self.assertEqual((third.stages["dossier"].error.code, len(self.calls)), ("research_plan_review", calls))
            approve_research_plan(self.root, first.run_id)
            done = run_research(self.root, resume=True, plan_review="required")
        self.assertEqual(done.status, "completed", done.model_dump())
        self.assertEqual(self.calls.count(ResearchDecision), 3)

    def test_tampered_and_foreign_receipts_are_refused_without_a_call(self):
        with patch(MODEL, side_effect=self.model):
            first = run_research(self.root, plan_review="required")
            approve_research_plan(self.root, first.run_id)
            work = self.work(first)
            receipt = json.loads((work / "plan_approval.json").read_text(encoding="utf-8"))
            forged = json.loads(json.dumps(receipt))
            forged["value"]["max_tasks"] = 1
            write_json(work / "plan_approval.json", forged)
            with self.assertRaises(AppError) as tampered:
                read_plan_approval(work)
            self.assertEqual(tampered.exception.code, "invalid_plan_approval")
            resumed = run_research(self.root, resume=True, plan_review="required")
            self.assertEqual((resumed.status, resumed.stages["dossier"].error.code), ("blocked", "invalid_plan_approval"))
            self.assertEqual(self.calls, PLANNING)
            # A second run of the same project waits at its own gate; the first run's receipt does not serve it.
            other = run_research(self.root, plan_review="required")
            self.assertEqual(other.stages["dossier"].error.code, "research_plan_review")
            write_json(self.work(other) / "plan_approval.json", receipt)
            foreign = run_research(self.root, resume=True, run_id=other.run_id, plan_review="required")
        self.assertEqual((foreign.status, foreign.stages["dossier"].error.code), ("blocked", "invalid_plan_approval"))
        self.assertEqual(self.calls, PLANNING * 2)
        with self.assertRaises(AppError) as rejected:
            plan_approval_for(self.work(other), other.input_hash, self.projection(other)["plan_hash"])
        self.assertEqual(rejected.exception.code, "invalid_plan_approval")

    def test_approval_arguments_are_checked_before_anything_is_written(self):
        with patch(MODEL, side_effect=self.model):
            first = run_research(self.root, plan_review="required")
        work = self.work(first)
        for cap in (0, -1, True, "2", 2.0):
            with self.subTest(cap=cap), self.assertRaises(AppError):
                approve_research_plan(self.root, first.run_id, max_tasks=cap)
        self.assertFalse((work / "plan_approval.json").exists())
        fresh = self.root / "runs/run_fresh"
        write_yaml(fresh / "run_manifest.yaml", RunManifest(run_id="run_fresh", kind="research", project_hash="0" * 64,
                                                            input_hash="a" * 64, stages={}).model_dump(mode="json"))
        with self.assertRaises(AppError) as missing:
            approve_research_plan(self.root, "run_fresh")
        self.assertEqual(missing.exception.code, "invalid_plan_approval")
        write_yaml(fresh / "run_manifest.yaml", RunManifest(run_id="run_fresh", kind="script", project_hash="0" * 64,
                                                            input_hash="a" * 64, stages={}).model_dump(mode="json"))
        with self.assertRaises(AppError):
            approve_research_plan(self.root, "run_fresh")
        # Once the plan runs, a cap has nothing left to cut.
        approve_research_plan(self.root, first.run_id)
        with patch(MODEL, side_effect=self.model):
            self.assertEqual(run_research(self.root, resume=True, plan_review="required").status, "completed")
        with self.assertRaises(AppError):
            approve_research_plan(self.root, first.run_id, max_tasks=1)

    def test_the_cli_flag_records_an_automatic_approval_that_sticks_for_resumes(self):
        paused = []

        def model(prompt, output_type, directory, **kwargs):
            if output_type is QuestionScopeReview and not paused:
                paused.append(1)
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.model(prompt, output_type, directory, **kwargs)

        with patch(MODEL, side_effect=model):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["research", str(self.root), "--approve-plan", "--json"])
            self.assertEqual(code, 2)
            first = json.loads(output.getvalue())["run"]
            self.assertEqual(first["status"], "waiting_for_quota")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["resume", str(self.root), "--json"])
        self.assertEqual(code, 0)
        resumed = json.loads(output.getvalue())["run"]
        self.assertEqual((resumed["run_id"], resumed["status"]), (first["run_id"], "completed"))
        work = self.root / "runs" / first["run_id"]
        self.assertEqual(json.loads((work / "research_request.json").read_text(encoding="utf-8"))["plan_review"], "auto")
        approval = read_plan_approval(work)
        self.assertIn("auto", approval.source)
        self.assertEqual(approval.plan_hash, digest(read_value(work / "question_research/state.json")["plan"]))
        self.assertTrue((work / "question_research/plan_projection.json").exists())

    def test_the_cli_approves_a_waiting_plan_with_an_optional_cap(self):
        with patch(MODEL, side_effect=self.planner(lambda cap: min(2, cap))):
            first = run_research(self.root, plan_review="required")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["approve", str(self.root), "--research-plan", first.run_id, "--max-tasks", "1", "--json"])
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual((data["status"], data["plan_approval"]["max_tasks"]), ("approved", 1))
            self.assertEqual(data["plan_approval"]["source"], "pla approve --research-plan")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["approve", str(self.root), "--max-tasks", "1", "--json"])
            self.assertEqual((code, json.loads(output.getvalue())["code"]), (1, "invalid_request"))
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["resume", str(self.root), "--json"])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["run"]["stages"]["dossier"]["error"]["code"], "research_plan_review")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["approve", str(self.root), "--research-plan", "--json"])
            self.assertEqual(code, 0)
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["resume", str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(len(read_value(self.root / "runs" / first.run_id / "question_research/state.json")["tasks"]), 1)

    def test_the_studio_quota_resume_never_bypasses_the_gate(self):
        paused = []

        def model(prompt, output_type, directory, **kwargs):
            if output_type is QuestionScopeReview and not paused:
                paused.append(1)
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.model(prompt, output_type, directory, **kwargs)

        text = {"provider": "codex_cli"}
        with patch(MODEL, side_effect=model):
            started = perform(self.root, {"action": "research", "text": text})["run"]
            self.assertEqual(started["status"], "waiting_for_quota")
            # The scheduler's automatic resume carries a count, never an approval.
            resumed = perform(self.root, {"action": "resume", "run_id": started["run_id"], "text": text,
                                          "auto_resume_count": 1})["run"]
        self.assertEqual((resumed["status"], resumed["stages"]["dossier"]["error"]["code"]), ("blocked", "research_plan_review"))
        self.assertEqual(self.calls, PLANNING)
        work = self.root / "runs" / started["run_id"]
        self.assertFalse((work / "plan_approval.json").exists())
        self.assertEqual(json.loads((work / "research_request.json").read_text(encoding="utf-8"))["plan_review"], "required")

    def test_planning_and_the_question_report_use_the_calibrated_rate(self):
        write_json(self.root / "research/calibration.json", {"calls_per_task": 6.4, "seconds_per_call": 300, "tasks": 12})
        with patch(MODEL, side_effect=self.model):
            first = run_research(self.root, plan_review="required")
        work = self.work(first)
        allowance = read_value(work / "question_research/planning_budget.json")
        self.assertEqual((allowance["expected_calls_per_task"], allowance["expected_calls_source"]), (6, "project"))
        self.assertEqual(allowance["max_tasks"], affordable_tasks(1, self.config.research_limits.model_calls, 6))
        projection = self.projection(first)
        self.assertEqual((projection["expected_calls_per_task"], projection["expected_calls_source"], projection["projected_calls"]), (6, "project", 9))
        self.assertIn("(6 je offener Teilfrage, Erfahrungswert des Projekts)", (work / "research_questions.md").read_text(encoding="utf-8"))
        self.assertEqual(read_value(work / "question_research/state.json")["budget_projection"]["expected_calls_per_task"], 6)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / "runs/run_test"

    def state(self, counts, verified):
        timings = [{"name": "reader", "task": task, "seconds": 10.0 * (n + 1)}
                   for task, count in counts.items() for n in range(count)]
        return {"call_timings": timings, "tasks": {task: {"status": "verified" if task in verified else "pending"}
                                                   for task in counts}, "plan": {"tasks": []}}

    def test_default_without_history_then_project_history_then_the_run_itself(self):
        self.assertEqual(expected_calls_per_task(self.work, self.root), (DEFAULT_CALLS_PER_TASK, "default"))
        self.assertEqual(seconds_per_call(self.work, self.root), (DEFAULT_SECONDS_PER_CALL, "default"))
        write_json(self.root / "research/calibration.json", {"calls_per_task": 6.4, "seconds_per_call": 271.4})
        self.assertEqual(expected_calls_per_task(self.work, self.root), (6, "project"))
        self.assertEqual(seconds_per_call(self.work, self.root), (271.4, "project"))
        # Two verified tasks are not yet a measurement; the third makes the run's own median win.
        two = self.state({"a": 5, "b": 9, "c": 7}, verified={"a", "b"})
        self.assertEqual(expected_calls_per_task(self.work, self.root, two), (6, "project"))
        three = self.state({"a": 5, "b": 9, "c": 7, "d": 4}, verified={"a", "b", "c"})
        self.assertEqual(expected_calls_per_task(self.work, self.root, three), (7, "run"))
        # 25 timings of 10, 20, ... seconds per task; the 13th of the sorted values is 40.
        self.assertEqual(seconds_per_call(self.work, self.root, three), (40.0, "run"))
        # A saved ledger serves the same answer without the caller holding the state.
        save_value(self.work / "question_research/state.json", three)
        self.assertEqual(expected_calls_per_task(self.work, self.root), (7, "run"))
        # Corrupt or boolean calibration values fall through instead of steering the plan.
        write_json(self.root / "research/calibration.json", {"calls_per_task": True, "seconds_per_call": "fast"})
        self.assertEqual(expected_calls_per_task(self.work, self.root, two), (DEFAULT_CALLS_PER_TASK, "default"))
        few = {"call_timings": [{"name": "plan", "task": None, "seconds": 5.0}, {"name": "scope_0", "task": None, "seconds": 7.0}],
               "tasks": {}, "plan": {"tasks": []}}
        self.assertEqual(seconds_per_call(self.work, self.root, few), (DEFAULT_SECONDS_PER_CALL, "default"))

    def test_affordable_tasks_follows_the_per_task_rate(self):
        # The 19 September run: 150 approved calls, one spent, planned at 5 per task, gave 29 tasks.
        self.assertEqual(affordable_tasks(1, 150, 5), 29)
        self.assertEqual(affordable_tasks(1, 150, DEFAULT_CALLS_PER_TASK), 18)
        self.assertEqual(affordable_tasks(1, 150), 18)
        self.assertEqual(affordable_tasks(10, 12, 8), 1)

    def test_the_message_names_calls_hours_and_the_approval_command(self):
        projection = {"tasks": 29, "tasks_pending": 29, "expected_calls_per_task": 5, "expected_calls_source": "project",
                      "closing_reserve": 4, "closing_calls": 3, "projected_calls": 148, "used": 7, "approved_limit": 150,
                      "within_limit": True, "seconds_per_call": 270.0, "seconds_per_call_source": "run",
                      "projected_hours": round(148 * 270 / 3600, 1), "plan_hash": "0" * 64, "plan_caps": []}
        message = plan_review_message(projection, "run_x")
        self.assertIn("29 Teilfragen, voraussichtlich 148 Aufrufe, etwa 11 Stunden bei 4,5 Minuten je Aufruf", message)
        self.assertIn("5 Aufrufe je Teilfrage, Erfahrungswert des Projekts", message)
        self.assertIn("pla approve <projekt> --research-plan run_x [--max-tasks N]", message)
        self.assertNotIn("reicht dafür voraussichtlich nicht", message)
        self.assertNotIn("Modellfenster", message)
        large = plan_review_message({**projection, "large_run": True, "review_parts_per_round": 41}, "run_x")
        self.assertIn("Ab 6 Teilfragen passt das Dossier nicht mehr in ein Modellfenster", large)
        self.assertIn("etwa 41 Prüfteile je Runde", large)
        self.assertEqual((review_parts_per_round(5), review_parts_per_round(6), review_parts_per_round(16)), (0, 9, 23))
        short = plan_review_message({**projection, "tasks": 2, "projected_calls": 19, "within_limit": False,
                                     "projected_hours": round(19 * 270 / 3600, 1), "plan_caps": [1]})
        self.assertIn("2 Teilfragen, voraussichtlich 19 Aufrufe, etwa 1,4 Stunden", short)
        self.assertIn("reicht dafür voraussichtlich nicht", short)
        self.assertIn("Obergrenze von 1 Teilfragen wurde bereits angefordert", short)

    def test_calibration_records_the_medians_of_the_run(self):
        state = self.state({"a": 5, "b": 9, "c": 7}, verified={"a", "b", "c"})
        state["call_timings"].append({"name": "dossier", "task": None, "seconds": 100.0})
        path = write_calibration(self.root, self.work, "run_test", state)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((data["run_id"], data["tasks"], data["verified_tasks"], data["calls_per_task"]), ("run_test", 3, 3, 7))
        self.assertEqual(data["measured_calls"], 22)
        self.assertIsNone(write_calibration(self.root, self.root / "runs/run_none", "run_none"))


if __name__ == "__main__":
    unittest.main()
