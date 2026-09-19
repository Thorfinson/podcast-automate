"""Independent research tasks side by side: overlap, prerequisites, an unchanged sequential mode,
drained failures with a clean resume, several active tasks in every view, and the plan gate."""
import json
import re
import threading
import unittest
from collections import Counter
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.execution import MAX_PARALLEL
from podcast_automate.question_research import QuestionResearch
from podcast_automate.question_scope import QuestionScopeReview, pending_task
from podcast_automate.research import run_research
from podcast_automate.research_ledger import public_ledger, read_value
from podcast_automate.research_models import ResearchDiscovery, ResearchDossier, SourceIndex
from podcast_automate.research_quality import ResearchAssessment, requirements_for
from podcast_automate.research_review import SourceReview
from podcast_automate.research_tasks import AnswerReview, QuestionPlan, ResearchDecision
from podcast_automate.run_budget import approve_research_plan
from podcast_automate.sources import import_source
from podcast_automate.status_summary import evidence_snapshot
from podcast_automate.storage import digest, init_project, write_json
from podcast_automate.studio_progress import research_progress
from tests import research_fixtures as fixtures
from tests.question_fixtures import decision, task_value

MODEL = "podcast_automate.research.CodexAdapter.structured"
PARALLEL = {"text": "parallel", "audio": "sequential"}
TASKS = ("task_a", "task_b", "task_c")


class Probe:
    """Counts fake model calls that are inside the model at the same time. No sleeps: the tasks
    named for a rendezvous meet at a barrier with a timeout, which proves the overlap or fails."""

    def __init__(self, parties, events):
        self.mutex = threading.Lock()
        self.barrier = threading.Barrier(parties) if parties > 1 else None
        self.met = set()
        self.active = self.peak = 0
        self.events = events

    def enter(self, schema, task):
        with self.mutex:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.events.append((schema.__name__, task))

    def leave(self):
        with self.mutex:
            self.active -= 1

    def rendezvous(self, task):
        with self.mutex:
            first = task not in self.met
            self.met.add(task)
        if first and self.barrier is not None:
            self.barrier.wait(timeout=5)


class ParallelResearchCase(fixtures.ResearchProjectCase):
    """A plan of ``tasks`` with ``depends`` between them, run in parallel mode unless a test says otherwise."""

    tasks = TASKS
    depends = {}

    def setUp(self):
        super().setUp()
        # The plan is sized to the worker cap: a rendezvous of every task needs a worker each, and a cap
        # that no longer matches must fail here, not as a barrier timeout five seconds into a test.
        self.assertEqual(len(TASKS), MAX_PARALLEL, "resize TASKS together with MAX_PARALLEL")
        write_json(self.root / "studio/execution.json", PARALLEL)
        self.work = self.root / "runs/run_test"
        self.index = None
        self.events = []
        self.probe = Probe(1, self.events)
        self.meeting = ()
        self.hook = lambda schema, task, payload: None

    def meet(self, *tasks):
        """The tasks whose first reading call must be inside the model at the same time."""
        self.meeting = tasks
        self.probe = Probe(len(tasks), self.events)

    def plan(self):
        rows = []
        for identifier in self.tasks:
            row = task_value(identifier)
            row["question"] = f"What is energy in {identifier}?"
            row["depends_on"] = list(self.depends.get(identifier, []))
            row["requirement_ids"] = [r["id"] for r in requirements_for(self.config)]
            rows.append(row)
        return QuestionPlan(tasks=rows)

    def model(self, prompt, output_type, directory, **kwargs):
        if output_type is QuestionPlan:
            self.calls.append(output_type)
            return self.plan(), {}
        if output_type not in (ResearchDecision, AnswerReview):
            return super().model(prompt, output_type, directory, **kwargs)
        payload = json.loads(prompt.splitlines()[-1])
        task = payload["task"]["id"]
        self.probe.enter(output_type, task)
        try:
            if output_type is ResearchDecision and task in self.meeting:
                self.probe.rendezvous(task)
            override = self.hook(output_type, task, payload)
            if override is not None:
                self.calls.append(output_type)
                return override, {}
            return super().model(prompt, output_type, directory, **kwargs)
        finally:
            self.probe.leave()

    def invoke(self, prompt, schema, version, *, search=False):
        return self.model(prompt, schema, self.work / "calls", prompt_version=version, search=search)

    def engine(self, workers):
        """The engine on a fixed index, as the production stage builds it, without budget receipts."""
        if self.index is None:
            document, _ = import_source(fixtures.discovery().candidates[0], self.root, self.work.name)
            self.index = SourceIndex(sources=[document], failures=[])
        return QuestionResearch(self.root, self.work, self.config, self.invoke,
                                lambda activity: write_json(self.work / "research_activity.json", {"activity": activity}),
                                workers=workers)

    def work_of(self, run):
        return self.root / "runs" / run.run_id

    def receipts(self, work):
        """Every task receipt with its prompt binding and value hash, keyed by its path in the task folder."""
        folder = work / "question_research/tasks"
        rows = {}
        for path in sorted(folder.rglob("*.json")):
            saved = json.loads(path.read_text(encoding="utf-8"))
            rows[path.relative_to(folder).as_posix()] = (saved.get("input_hash"), saved.get("sha256"))
        return rows

    def reader_receipt(self, work, task):
        return work / "question_research/tasks" / task / "attempt_0/step_000/reader.json"


class IndependentTasksTests(ParallelResearchCase):
    def test_independent_tasks_overlap_and_the_run_completes_with_an_exact_budget(self):
        self.meet(*TASKS)
        with patch(MODEL, side_effect=self.model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed", run.model_dump())
        self.assertEqual(self.probe.peak, MAX_PARALLEL, "every worker of the cap was inside the model at once")
        work = self.work_of(run)
        # Discovery, plan and scope; one reading decision and one review per task; dossier, source review, assessment.
        self.assertEqual(len(self.calls), 3 + 2 * 3 + 3)
        budget = json.loads((work / "budget.json").read_text(encoding="utf-8"))
        self.assertEqual((budget["model_calls"], budget["sequence"]), (len(self.calls), len(self.calls)))
        state = read_value(work / "question_research/state.json")
        self.assertEqual([row["status"] for row in state["tasks"].values()], ["verified"] * 3)
        self.assertEqual(state["active_tasks"], [])
        self.assertNotIn("active_task", state)
        self.assertEqual(Counter(row["task"] for row in state["call_timings"]),
                         Counter({None: 6, "task_a": 2, "task_b": 2, "task_c": 2}))
        ledger = json.loads((work / "research_questions.json").read_text(encoding="utf-8"))
        self.assertEqual([row["id"] for row in ledger["questions"]], list(TASKS))
        self.assertEqual((ledger["closed"], ledger["active_task"], ledger["active_tasks"]), (3, None, []))
        text = (work / "research_questions.md").read_text(encoding="utf-8")
        positions = [text.index(f"## What is energy in {task}?") for task in TASKS]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(json.loads((work / "research_request.json").read_text(encoding="utf-8"))["execution"], PARALLEL)
        self.assertEqual(research_progress(self.root, run.model_dump(mode="json"))["execution"], PARALLEL)
        self.assertEqual(json.loads((self.root / "research/questions.json").read_text(encoding="utf-8"))["closed"], 3)
        with patch(MODEL, side_effect=AssertionError("No repeat calls on resume")):
            resumed = run_research(self.root, resume=True)
        self.assertEqual((resumed.status, resumed.run_id), ("completed", run.run_id))


class PrerequisiteTests(ParallelResearchCase):
    depends = {"task_c": ["task_a"]}

    def test_a_dependent_task_starts_only_after_its_prerequisite_is_verified(self):
        self.meet("task_a", "task_b")
        prerequisites = {}

        def capture(schema, task, payload):
            if schema is ResearchDecision:
                prerequisites[task] = payload["prerequisite_answers"]
        self.hook = capture
        with patch(MODEL, side_effect=self.model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed", run.model_dump())
        self.assertEqual(self.probe.peak, 2)
        self.assertLess(self.events.index(("AnswerReview", "task_a")), self.events.index(("ResearchDecision", "task_c")))
        self.assertEqual([row["task_id"] for row in prerequisites["task_c"]], ["task_a"])
        self.assertEqual(prerequisites["task_b"], [])
        state = read_value(self.work_of(run) / "question_research/state.json")
        self.assertEqual(state["tasks"]["task_c"]["verification"]["prerequisite_hashes"],
                         {"task_a": digest(state["tasks"]["task_a"]["answer"])})

    def test_a_blocked_prerequisite_blocks_its_dependent_without_starting_it(self):
        self.meet("task_a", "task_b")
        self.hook = lambda schema, task, payload: decision("blocked") if schema is ResearchDecision and task == "task_a" else None
        with patch(MODEL, side_effect=self.model):
            run = run_research(self.root)
        self.assertEqual((run.status, run.stages["dossier"].error.code), ("blocked", "research_questions_blocked"))
        state = read_value(self.work_of(run) / "question_research/state.json")
        self.assertEqual([state["tasks"][task]["status"] for task in TASKS], ["blocked", "verified", "blocked"])
        self.assertEqual(state["tasks"]["task_c"]["outcome"], "prerequisite_block")
        self.assertNotIn(("ResearchDecision", "task_c"), self.events)
        self.assertEqual(state["active_tasks"], [])


class SequentialModeTests(ParallelResearchCase):
    def test_the_sequential_default_keeps_one_task_at_a_time_and_the_same_receipts(self):
        (self.root / "studio/execution.json").unlink()
        with patch(MODEL, side_effect=self.model):
            sequential = run_research(self.root)
        self.assertEqual(sequential.status, "completed", sequential.model_dump())
        self.assertEqual(self.probe.peak, 1)
        self.assertEqual(self.events, [(name, task) for task in TASKS for name in ("ResearchDecision", "AnswerReview")])
        # As in the one-task production test (8 calls), plus a reading decision and a review for each further task.
        self.assertEqual(self.calls, [ResearchDiscovery, QuestionPlan, QuestionScopeReview,
                                      ResearchDecision, AnswerReview, ResearchDecision, AnswerReview, ResearchDecision, AnswerReview,
                                      ResearchDossier, SourceReview, ResearchAssessment])
        work = self.work_of(sequential)
        self.assertEqual(json.loads((work / "budget.json").read_text(encoding="utf-8"))["model_calls"], 12)
        self.assertEqual(json.loads((work / "research_request.json").read_text(encoding="utf-8"))["execution"]["text"], "sequential")
        self.assertEqual(research_progress(self.root, sequential.model_dump(mode="json"))["execution"]["text"], "sequential")
        # The same brief in parallel mode writes the same receipts: same paths, prompt bindings and values.
        other = self.root.parent / "parallel"
        init_project(other, self.config)
        write_json(other / "studio/execution.json", PARALLEL)
        self.meet(*TASKS)
        self.calls.clear()
        with patch(MODEL, side_effect=self.model):
            parallel = run_research(other)
        self.assertEqual(parallel.status, "completed", parallel.model_dump())
        self.assertEqual(self.probe.peak, len(TASKS))
        self.assertEqual(len(self.calls), 12)
        self.assertEqual(self.receipts(other / "runs" / parallel.run_id), self.receipts(work))


class FailureTests(ParallelResearchCase):
    tasks = ("task_a", "task_b")

    def quota(self):
        return AppError("Kontingent erschöpft", code="quota_exhausted", status="waiting_for_quota")

    def test_a_failing_task_lets_the_other_finish_its_call_saves_and_stops_it_at_the_next_step(self):
        self.meet("task_a", "task_b")
        engine = self.engine(workers=2)

        def flow(schema, task, payload):
            if schema is ResearchDecision and task == "task_b":
                raise self.quota()
            if schema is ResearchDecision and task == "task_a":
                # Return only once the failure has been noticed, so the next step boundary is a stop.
                self.assertTrue(engine.stopping.wait(timeout=5))
        self.hook = flow
        with self.assertRaises(AppError) as raised:
            engine.run(fixtures.discovery(), self.index)
        self.assertEqual(raised.exception.status, "waiting_for_quota")
        self.assertEqual(Counter(self.calls), Counter({QuestionPlan: 1, QuestionScopeReview: 1, ResearchDecision: 1}))
        self.assertEqual(Counter(name for name, _ in self.events), Counter({"ResearchDecision": 2}))
        state = read_value(self.work / "question_research/state.json")
        self.assertEqual(state["active_tasks"], [])
        self.assertEqual((state["tasks"]["task_a"]["status"], state["tasks"]["task_a"]["step"]), ("reviewing", 1))
        self.assertEqual((state["tasks"]["task_b"]["status"], state["tasks"]["task_b"]["step"]), ("researching", 0))
        self.assertTrue(self.reader_receipt(self.work, "task_a").exists())
        self.assertFalse(self.reader_receipt(self.work, "task_b").exists())
        # A fresh engine continues each row at its saved step; nothing answered before is asked again.
        self.hook = lambda schema, task, payload: None
        self.meet()
        resumed = self.engine(workers=2)
        resumed.run(fixtures.discovery(), self.index)
        self.assertEqual(resumed.state["phase"], "completed")
        self.assertEqual(Counter(self.calls), Counter({QuestionPlan: 1, QuestionScopeReview: 1, ResearchDecision: 2, AnswerReview: 2,
                                                      ResearchDossier: 1, SourceReview: 1, ResearchAssessment: 1}))
        self.assertEqual([row["status"] for row in resumed.state["tasks"].values()], ["verified", "verified"])

    def test_a_quota_pause_in_production_refunds_the_failed_call_and_resumes_without_repeats(self):
        self.meet("task_a", "task_b")
        noticed = threading.Event()

        def flow(schema, task, payload):
            if schema is ResearchDecision and task == "task_b":
                noticed.set()
                raise self.quota()
            if schema is ResearchDecision and task == "task_a":
                self.assertTrue(noticed.wait(timeout=5))
        self.hook = flow
        with patch(MODEL, side_effect=self.model):
            first = run_research(self.root)
        self.assertEqual(first.status, "waiting_for_quota", first.model_dump())
        work = self.work_of(first)
        budget = json.loads((work / "budget.json").read_text(encoding="utf-8"))
        self.assertEqual((budget["model_calls"], len(budget["refunded"])), (len(self.calls), 1))
        self.assertTrue(self.reader_receipt(work, "task_a").exists())
        self.assertFalse(self.reader_receipt(work, "task_b").exists())
        state = read_value(work / "question_research/state.json")
        self.assertEqual((state["active_tasks"], state["tasks"]["task_b"]["status"]), ([], "researching"))
        self.assertIn(state["tasks"]["task_a"]["status"], {"reviewing", "verified"})
        self.hook = lambda schema, task, payload: None
        self.meet()
        with patch(MODEL, side_effect=self.model):
            resumed = run_research(self.root, resume=True)
        self.assertEqual((resumed.status, resumed.run_id), ("completed", first.run_id))
        self.assertEqual(Counter(self.calls), Counter({ResearchDiscovery: 1, QuestionPlan: 1, QuestionScopeReview: 1,
                                                      ResearchDecision: 2, AnswerReview: 2, ResearchDossier: 1,
                                                      SourceReview: 1, ResearchAssessment: 1}))
        self.assertEqual(Counter(name for name, _ in self.events), Counter({"ResearchDecision": 3, "AnswerReview": 2}))
        self.assertEqual(json.loads((work / "budget.json").read_text(encoding="utf-8"))["model_calls"], len(self.calls))


class ActiveTasksTests(ParallelResearchCase):
    def test_the_ledger_and_the_status_facts_name_every_task_in_flight(self):
        self.meet(*TASKS)
        seen, mutex = {}, threading.Lock()

        def capture(schema, task, payload):
            with mutex:
                if schema is not ResearchDecision or seen:
                    return
                run_id = json.loads((self.root / "runs/latest.json").read_text(encoding="utf-8"))["run_id"]
                work = self.root / "runs" / run_id
                seen["ledger"] = json.loads((work / "research_questions.json").read_text(encoding="utf-8"))
                seen["facts"] = evidence_snapshot(self.root, {"run_id": run_id, "kind": "research", "status": "running",
                                                              "stages": {"dossier": {"status": "running"}}})["facts"]
        self.hook = capture
        with patch(MODEL, side_effect=self.model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed", run.model_dump())
        ledger = seen["ledger"]
        self.assertEqual((ledger["active_task"], ledger["active_tasks"]), ("task_a", list(TASKS)))
        self.assertEqual([row["status"] for row in ledger["questions"]], ["researching"] * 3)
        facts = {fact["id"]: fact["text"] for fact in seen["facts"]}
        self.assertEqual(facts["active_tasks"], "3 Teilfragen in Arbeit: " + "; ".join(f"What is energy in {t}?" for t in TASKS))
        self.assertEqual({key for key in facts if re.fullmatch(r"question_\d+", key)}, {"question_0", "question_1", "question_2"})
        self.assertTrue(all(f"What is energy in {task}?" in facts[f"question_{n}"] for n, task in enumerate(TASKS)))

    def test_the_public_ledger_keeps_the_first_active_task_for_older_readers(self):
        plan = {"tasks": [task_value(task) for task in TASKS]}
        rows = {task: {**pending_task(), "status": "researching"} for task in TASKS}
        state = {"plan": plan, "tasks": rows, "phase": "questions", "active_tasks": ["task_a", "task_c"]}
        ledger = public_ledger(state)
        self.assertEqual((ledger["active_task"], ledger["active_tasks"]), ("task_a", ["task_a", "task_c"]))
        legacy = public_ledger({"plan": plan, "tasks": rows, "phase": "questions", "active_task": "task_b"})
        self.assertEqual((legacy["active_task"], legacy["active_tasks"]), ("task_b", ["task_b"]))
        idle = public_ledger({"plan": plan, "tasks": rows, "phase": "questions", "active_tasks": []})
        self.assertEqual((idle["active_task"], idle["active_tasks"]), (None, []))


class PlanGateTests(ParallelResearchCase):
    def test_the_plan_gate_blocks_before_any_task_starts_in_parallel_mode(self):
        self.meet(*TASKS)
        with patch(MODEL, side_effect=self.model):
            first = run_research(self.root, plan_review="required")
        self.assertEqual((first.status, first.stages["dossier"].error.code), ("blocked", "research_plan_review"))
        self.assertEqual(self.calls, [ResearchDiscovery, QuestionPlan, QuestionScopeReview])
        self.assertEqual(self.events, [])
        work = self.work_of(first)
        self.assertFalse((work / "question_research/tasks").exists())
        self.assertEqual(read_value(work / "question_research/state.json")["active_tasks"], [])
        self.assertEqual(json.loads((work / "research_request.json").read_text(encoding="utf-8"))["execution"], PARALLEL)
        approve_research_plan(self.root, first.run_id)
        with patch(MODEL, side_effect=self.model):
            resumed = run_research(self.root, resume=True, plan_review="required")
        self.assertEqual((resumed.status, resumed.run_id), ("completed", first.run_id))
        self.assertEqual(self.probe.peak, len(TASKS))
        self.assertEqual(len(self.calls), 12)


if __name__ == "__main__":
    unittest.main()
