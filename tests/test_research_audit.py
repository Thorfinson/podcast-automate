"""The audit loop of an assembled dossier (prompt generation 3): follow-up scope, objection routing, reworks, resumes.

Regressions from the review of 2026-10-02 (Transformer: 10 rounds, 57 of 60 tasks reworked; Asimov: 7 rounds, 83 noted
points): the loop ended because reworks ran out, not because points were settled.
"""
import copy
import json
import unittest
from unittest.mock import patch

import tests.test_question_research as base
from podcast_automate.errors import AppError
from podcast_automate.evidence_models import ResearchObjection
from podcast_automate.prompts import instructions as prompt_text
from podcast_automate.question_research import QuestionResearch
from podcast_automate.question_synthesis import SynthesisMixin, complete_context
from podcast_automate.research_evidence import collapse_assessments, scope_assessments
from podcast_automate.research_ledger import public_ledger, read_value, save_value
from podcast_automate.research_models import Evidence, Finding, ResearchDossier
from podcast_automate.research_patches import DossierPatch, cached_call, retire_stale_receipt
from podcast_automate.research_quality import FollowUpAssessment, ResearchAssessment, render_quality
from podcast_automate.research_reader import SourceReader
from podcast_automate.research_review import SourceReview, SourceReviewIssue
from podcast_automate.research_tasks import QuestionPlan, ReopenPlan, ResearchDecision
from podcast_automate.question_dependencies import revalidate
from podcast_automate.storage import digest, write_json
from tests import research_fixtures as fixtures
from tests.question_fixtures import answer_for, claim_contract, complete_fixture_response, decision, support_receipts, task_value


def anchor(task_id, text, kind="support"):
    """A routing anchor: a support defect of the task's own finding, or a completeness objection to its criterion 0."""
    if kind == "criterion":
        return ResearchObjection(id="obj_test", rule="criterion", task_id=task_id, criterion_index=0, finding_ids=[],
                                 evidence_refs=[], missing_evidence=text, reason=text, correction="Complete the criterion.",
                                 closure_condition="The criterion is met in full by read evidence.", resolution="research")
    return ResearchObjection(id="obj_test", rule="support", task_id=task_id, criterion_index=None,
                             finding_ids=[f"{task_id}__f_energy"], evidence_refs=[], missing_evidence=text, reason=text,
                             correction="Supply the missing support.",
                             closure_condition="The missing support is supplied by checked evidence.", resolution="research")


def failing(rid, reason, finding):
    return dict(requirement_id=rid, finding_ids=[finding], direct_answer=True, explanation=False, evidence=True,
                cross_check=True, boundaries=True, reason=reason, missing=[], search_queries=["mechanism"])


def met(rid, finding):
    return dict(requirement_id=rid, finding_ids=[finding], direct_answer=True, explanation=True, evidence=True,
                cross_check=True, boundaries=True, reason="Met.", missing=[], search_queries=[])


class AuditCase(unittest.TestCase):
    """The question-workflow harness of test_question_research, with routing answered here (fixtures fill no anchors)."""

    def setUp(self):
        self.fixture = base.QuestionResearchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.work, self.config = self.fixture.root, self.fixture.work, self.fixture.config
        self.discovery, self.index, self.ref = self.fixture.discovery, self.fixture.index, self.fixture.ref
        self.hook = lambda prompt, schema, payload, kwargs: None
        self.routes = []
        self.reworks = []

    def model(self, prompt, schema, version="test", **kwargs):
        payload = json.loads(prompt.splitlines()[-1])
        value = self.hook(prompt, schema, payload, kwargs)
        if schema is ReopenPlan and value is not None:
            self.routes.append((payload, version, prompt))
        if schema in (ReopenPlan, SourceReview) and value is not None:
            # Taken as given: the fixture would replace the anchors these tests choose.
            self.fixture.calls.append((schema, version))
            return value, {}
        if schema is ResearchDecision and payload.get("reopening") and value is None:
            # A rework brings a changed answer, so the next follow-up sees the question as changed.
            self.reworks.append(payload["task"]["id"])
            answer = answer_for(self.ref)
            answer.summary += f" Reworked {len(self.reworks)}."
            value = decision("answer", answer=answer)
        if value is not None:
            self.fixture.calls.append((schema, version))
            return complete_fixture_response(value, payload), {}
        return self.fixture.model(prompt, schema, version, **kwargs)

    def engine(self, work=None):
        work = work or self.work
        return QuestionResearch(self.root, work, self.config, self.model,
                                lambda activity: write_json(work / "research_activity.json", {"activity": activity}))

    def calls(self, schema, since=0):
        return sum(1 for s, _ in self.fixture.calls[since:] if s is schema)

    def gate(self, work=None):
        return json.loads(((work or self.work) / "research_quality_gate.json").read_text(encoding="utf-8"))

    def quality(self, work=None):
        return ((work or self.work) / "research_quality.md").read_text(encoding="utf-8")


class RecordedLimitTests(AuditCase):
    def test_an_unchanged_requirement_whose_objection_was_noted_is_kept_as_a_recorded_limit(self):
        """Asimov, 2026-10-02: rq_005 and rq_011 failed in all seven rounds and were judged and routed again each round,
        though nothing they depend on had changed. Such a requirement keeps its verdict as a recorded limit."""
        self.config.focus_questions = ["How is energy measured?"]
        followups = []

        def flow(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), {**task_value("task_empirical", "empirical"), "requirement_ids": ["rq_002"]}])
            if schema is ResearchAssessment:
                return ResearchAssessment(requirements=[
                    failing("rq_001", "The second case is not quite complete.", "task_definition__f_energy"),
                    failing("rq_002", "The measurement is absent.", "task_empirical__f_energy")], issues=[])
            if schema is FollowUpAssessment:
                followups.append(payload["follow_up"])
            if schema is ReopenPlan:
                scopes = {row["index"]: row for row in payload["objection_scopes"]}
                # rq_001 is anchored on its treated criterion (noted), rq_002 on a support defect (reworked).
                return ReopenPlan(routes=[dict(index=i, task_ids=scopes[i]["task_ids"], reason="Routed.",
                                               anchors=[anchor(scopes[i]["task_ids"][0], text,
                                                               "criterion" if scopes[i]["requirement_id"] == "rq_001" else "support")])
                                          for i, text in enumerate(payload["objections"])])
        self.hook = flow
        engine = self.engine()
        outcomes, reopen = [], engine.reopen

        def recorded(*args):
            result = reopen(*args)
            outcomes.append(copy.deepcopy(engine.state["objection_outcomes"]))
            return result
        with patch.object(engine, "reopen", side_effect=recorded):
            engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(self.calls(ReopenPlan), 1, "rq_001 is not routed again")
        # Only the requirement a changed question serves is judged again; rq_001 keeps its verdict as a recorded limit.
        self.assertEqual([f["requirements_in_scope"] for f in followups], [["rq_002"]])
        scope = read_value(self.work / "question_research/synthesis/audit_01/assessment_scope.json")
        self.assertEqual(scope["recorded_limits"], ["rq_001"])
        self.assertEqual({row["requirement_id"]: row["tasks"] for row in outcomes[0]["rows"]}, {"rq_001": {"task_definition": "noted"}, "rq_002": {"task_empirical": "reworked"}})
        self.assertEqual({tid: len(row["reopenings"]) for tid, row in engine.state["tasks"].items()},
                         {"task_definition": 0, "task_empirical": 1})
        gate = self.gate()
        rows = {row["requirement_id"]: row for row in gate["requirements"]}
        self.assertEqual((rows["rq_001"]["passed"], rows["rq_001"].get("recorded_limit")), (False, True))
        self.assertTrue(rows["rq_002"]["passed"])
        # Finished on recorded limits, not on accepted gaps (2026-10-02: both completed runs said accepted gaps).
        self.assertEqual((gate["passed"], gate["passed_with_accepted_gaps"], gate["passed_with_noted_limits"]), (True, False, True))
        report = self.quality()
        self.assertIn("mit vermerkten Grenzen abgeschlossen: 1 von 2 Leitfragen", report)
        self.assertNotIn("akzeptierten Lücken", report)
        self.assertIn("Das Urteil bleibt, bis sich eine ihrer Antworten ändert", report)


class ScopedRoutingTests(AuditCase):
    def test_a_blocking_issue_may_reopen_only_the_changed_questions_it_names(self):
        """2026-10-02: only an issue's text reached the router, which could reopen unchanged questions too, and picked
        support anchors among finding ids it could not read. Each wrong pick spent one of two reworks."""
        followups = []

        def flow(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchAssessment:
                return ResearchAssessment(requirements=[failing("rq_001", "The mechanism is absent.", "task_definition__f_energy")],
                                          issues=[])
            if schema is FollowUpAssessment:
                followups.append(payload)
                issues = [] if len(followups) > 1 else [dict(text="The reworked definition lost its unit.", remedy="research",
                                                             task_ids=["task_definition", "task_empirical"])]
                return FollowUpAssessment(requirements=[{**met(rid, "task_definition__f_energy"), "remedy": "none"}
                                                        for rid in payload["follow_up"]["requirements_in_scope"]], issues=issues)
            if schema is ReopenPlan:
                first_attempt = "Rejections" not in prompt and "objection_scopes" in payload and \
                    payload["objection_scopes"][0]["requirement_id"] is None
                # In round two the router first reaches for the unchanged question, then corrects itself.
                target = "task_empirical" if first_attempt else "task_definition"
                return ReopenPlan(routes=[dict(index=i, task_ids=[target], reason="Routed.", anchors=[anchor(target, text)])
                                          for i, text in enumerate(payload["objections"])])
        self.hook = flow
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual({tid: len(row["reopenings"]) for tid, row in engine.state["tasks"].items()},
                         {"task_definition": 2, "task_empirical": 0})
        payload, version, prompt = self.routes[1]
        # Code fixed the scope before the call: only the changed question the issue names.
        self.assertEqual(payload["objection_scopes"], [{"index": 0, "task_ids": ["task_definition"], "requirement_id": None,
                                                        "finding_ids": [], "remedy": "research"}])
        self.assertIn("objection_scopes lists", prompt)
        self.assertTrue(version.endswith(".g3.scoped.routes"), version)
        # The router reads each answer's findings with their statements.
        self.assertEqual(payload["answers"]["task_empirical"]["findings"],
                         [{"id": "task_empirical__f_energy", "statement": "Configurations are assigned energies."}])
        rejected = json.loads((self.work / "question_research/synthesis/audit_01/routes_rejected_00.json").read_text(encoding="utf-8"))
        self.assertEqual(rejected["code"], "invalid_question_routing")
        self.assertIn("may reopen only task_definition", rejected["message"])
        # Round one's requirement may go to the questions that serve it.
        first = self.routes[0][0]["objection_scopes"][0]
        self.assertEqual((first["requirement_id"], first["task_ids"], first["remedy"]),
                         ("rq_001", ["task_definition", "task_empirical"], None))

    def test_an_objection_research_can_close_reopens_even_on_a_treated_criterion(self):
        """Every verified answer names findings for each criterion, so criterion_covered held for every criterion anchor
        and the router's rule label decided between rework and note. The assessment's remedy decides now; without one
        (a first assessment), the user's choice of 2026-10-01 stands and the point is noted."""
        engine = self.engine()
        engine.run(self.discovery, self.index)
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text(encoding="utf-8"))
        review = SourceReview(issues=[], limitations=[])
        saved = copy.deepcopy(engine.state)
        row = {"requirement_id": "rq_001", "reason": "A second case is missing.", "missing": [], "passed": False,
               "finding_ids": ["task_definition__f_energy"]}
        plan = ReopenPlan(routes=[dict(index=0, task_ids=["task_definition"], reason="Routed.",
                                       anchors=[anchor("task_definition", "A second case is missing.", "criterion")])])

        def routed(folder, name, schema, prompt, *, validate=None, **kwargs):
            validate(plan, True)
            return plan
        for remedy, expected in (("research", (["task_definition"], [])), (None, ([], ["task_definition"]))):
            with self.subTest(remedy=remedy):
                engine.state = copy.deepcopy(saved)
                report = {"requirements": [row], "blocking_gaps": [], "objection_checks": [],
                          "routing_scope": {"issues": [], "requirements": {"rq_001": {"task_ids": ["task_definition"],
                                                                                      "remedy": remedy}}}}
                with patch.object(engine, "call", side_effect=routed):
                    reopened, _ = engine.reopen(dossier, review, report)
                noted = [n["task_id"] for n in engine.state.get("noted_objections", {}).values()]
                self.assertEqual((reopened, noted), expected)


class StoppedRoundTests(AuditCase):
    def test_a_round_stopped_in_its_routing_resumes_after_an_update_of_its_prompts(self):
        """2026-10-02: a run stopped during the routing and resumed after an update that changed an assessment or
        routing prompt raised invalid_research_checkpoint, and the Studio offered only a new run. The round is rebuilt
        from its saved assessment, and a routing receipt of the earlier prompt is set aside and asked again."""
        state = {"stopped": False}

        def flow(prompt, schema, payload, kwargs):
            if schema is ResearchAssessment:
                return ResearchAssessment(requirements=[failing("rq_001", "The mechanism is absent.", "task_definition__f_energy")],
                                          issues=["A second case is missing."])
            if schema is ReopenPlan:
                if payload["objections"] == ["The mechanism is absent. "] and not state["stopped"]:
                    state["stopped"] = True
                    raise AppError("Kontingent erschöpft.", code="model_timeout", status="blocked")
                return ReopenPlan(routes=[dict(index=0, task_ids=["task_definition"], reason="Routed.",
                                               anchors=[anchor("task_definition", payload["objections"][0])])])
        self.hook = flow
        with patch("podcast_automate.question_synthesis.ROUTING_PART_SIZE", 1):
            with self.assertRaises(AppError) as stopped:
                self.engine().run(self.discovery, self.index)
            self.assertEqual(stopped.exception.code, "model_timeout")
            audit = self.work / "question_research/synthesis/audit_00"
            self.assertTrue((audit / "routes_part_000.json").exists())
            self.assertFalse((audit / "routes_part_001.json").exists())
            def updated(name, **values):
                text = prompt_text(name, **values)
                return text + " Updated wording." if name in {"research_assessment", "objection_routes"} else text
            count = len(self.fixture.calls)
            with patch("podcast_automate.question_synthesis.instructions", side_effect=updated):
                engine = self.engine()
                engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        # The round's assessment was not asked again; both routing parts were, the first after being set aside.
        self.assertEqual((self.calls(ResearchAssessment, count), self.calls(ReopenPlan, count)), (0, 2))
        self.assertTrue((audit / "routes_part_000_superseded_receipt_00.json").exists())
        self.assertEqual(len(engine.state["tasks"]["task_definition"]["reopenings"]), 1)

    def test_a_routing_receipt_of_an_earlier_prompt_is_set_aside_and_a_current_one_kept(self):
        folder = self.work / "receipts"
        folder.mkdir(parents=True)
        calls = []

        def generate(prompt, schema):
            calls.append(prompt)
            return DossierPatch(updates=[], additions=[], coverage_updates=[], resolved_open_questions=[], new_open_questions=[])
        cached_call(folder, "routes", DossierPatch, "Old prompt\n{}", generate)
        self.assertFalse(retire_stale_receipt(folder, "routes", DossierPatch, "Old prompt\n{}"))
        with self.assertRaises(AppError) as stale:
            cached_call(folder, "routes", DossierPatch, "New prompt\n{}", generate)
        self.assertEqual(stale.exception.code, "invalid_research_checkpoint", "cached_call alone still refuses it")
        self.assertTrue(retire_stale_receipt(folder, "routes", DossierPatch, "New prompt\n{}"))
        cached_call(folder, "routes", DossierPatch, "New prompt\n{}", generate)
        self.assertEqual(len(calls), 2)
        self.assertTrue((folder / "routes_superseded_receipt_00.json").exists())
        # A tampered receipt is not set aside: cached_call refuses it as before.
        saved = json.loads((folder / "routes.json").read_text(encoding="utf-8"))
        write_json(folder / "routes.json", {**saved, "sha256": "0" * 64})
        self.assertFalse(retire_stale_receipt(folder, "routes", DossierPatch, "Third prompt\n{}"))


class FailedReworkTests(AuditCase):
    def test_a_rework_that_ends_blocked_keeps_the_answer_verified_before_it(self):
        """2026-10-02: a reopening cleared the verified answer; when the rework blocked, the run stopped, and accepting
        the gap dropped that answer from the dossier. The answer comes back and the objection is noted as a limit:
        at once, right after the rework (QuestionResearch.run), and on resume for a run that stopped there before and
        whose gap the user then accepted."""
        for stopped_before in (False, True):
            with self.subTest(stopped_before=stopped_before):
                work = self.root / "runs" / f"run_rework_{stopped_before}"

                def flow(prompt, schema, payload, kwargs):
                    if schema is ResearchAssessment:
                        return ResearchAssessment(requirements=[failing("rq_001", "The mechanism is absent.",
                                                                        "task_definition__f_energy")], issues=[])
                    if schema is ReopenPlan:
                        return ReopenPlan(routes=[dict(index=0, task_ids=["task_definition"], reason="Routed.",
                                                       anchors=[anchor("task_definition", payload["objections"][0])])])
                    if schema is ResearchDecision and payload.get("reopening"):
                        return decision("blocked")
                self.hook = flow
                if stopped_before:
                    # The code before this fix restored the answer only on resume, so the run stopped once.
                    with patch.object(QuestionResearch, "keep_spent_answers", return_value=[]), \
                            self.assertRaises(AppError) as stopped:
                        self.engine(work).run(self.discovery, self.index)
                    self.assertEqual(stopped.exception.code, "research_questions_blocked")
                    path = work / "question_research/state.json"
                    state = read_value(path)
                    row = state["tasks"]["task_definition"]
                    self.assertEqual((row["status"], row["answer"]), ("blocked", None))
                    verified = row["reopenings"][-1]["previous_answer"]
                    row.update(outcome="accepted_gap", accepted_gap={"task_id": "task_definition", "reason": "",
                                                                     "approved_at": "2026-10-02T10:00:00Z"})
                    save_value(path, state)
                count = len(self.fixture.calls)
                engine = self.engine(work)
                engine.run(self.discovery, self.index)
                row = engine.state["tasks"]["task_definition"]
                if not stopped_before:
                    verified = row["reopenings"][-1]["previous_answer"]
                self.assertEqual((engine.state["phase"], row["status"], row["answer"]), ("completed", "verified", verified))
                self.assertNotIn("accepted_gap", row)
                if stopped_before:
                    # Nothing changed and the objection is noted: the requirement is a recorded limit, asked no further
                    # call.
                    self.assertEqual(len(self.fixture.calls), count)
                (noted,) = engine.state["noted_objections"].values()
                self.assertEqual((noted["basis"], noted["task_id"]), ("rework_blocked", "task_definition"))
                gate = self.gate(work)
                self.assertEqual([r.get("basis") for r in gate["noted_after_reworks"]], ["rework_blocked"])
                self.assertEqual((gate["passed_with_accepted_gaps"], gate["passed_with_noted_limits"]), (False, True))
                self.assertIn("## Einwände, die eine Nachbesserung nicht schließen konnte", self.quality(work))
                dossier = json.loads((work / "complete_research/dossier.json").read_text(encoding="utf-8"))
                self.assertEqual([f["id"] for f in dossier["findings"]], ["task_definition__f_energy"])


    def test_a_block_after_the_rework_ended_verified_does_not_bring_back_the_answer_before_it(self):
        """2026-10-02 review: revalidate() adds no reopening, so a question whose rework had verified A2 and whose later
        revalidation blocked went back to A1, the answer the audit had rejected."""
        first, second = {"summary": "A1"}, {"summary": "A2"}
        row = {"status": "verified", "answer": second, "verification": {"answer_hash": digest(second)},
               "reopenings": [{"reason": ["Objection."], "previous_answer": first,
                               "previous_verification": {"answer_hash": digest(first)}}]}
        blocked = {**copy.deepcopy(row), "status": "blocked", "answer": None, "outcome": "evidence_block"}
        self.assertEqual(SynthesisMixin.failed_rework(blocked)[0], first, "the rework itself blocked: A1 comes back")
        revalidate(row)
        row.update(status="blocked", outcome="evidence_block")
        self.assertIsNone(SynthesisMixin.failed_rework(row))

    def test_a_kept_answer_is_checked_again_when_its_prerequisite_changed(self):
        """2026-10-02 review: a prerequisite and its dependent were reopened in one round; the prerequisite's rework
        passed, the dependent's blocked, and the dependent came back verified against the prerequisite's old answer."""
        self.hook = lambda prompt, schema, payload, kwargs: QuestionPlan(tasks=[task_value(), {
            **task_value("task_synthesis", "synthesis"), "depends_on": ["task_definition"]}]) if schema is QuestionPlan else None
        engine = self.engine()
        engine.run(self.discovery, self.index)
        row = engine.state["tasks"]["task_synthesis"]
        kept = row["answer"]
        row["reopenings"].append({"reason": ["Objection."], "previous_answer": kept,
                                  "previous_verification": row["verification"]})
        row.update(status="blocked", answer=None, outcome="evidence_block", reason="Keine neuen Belege.")
        saved = copy.deepcopy(engine.state)
        self.assertEqual(engine.keep_spent_answers(), ["task_synthesis"])
        self.assertEqual(row["status"], "verified", "an unchanged prerequisite: the answer stands")
        engine.state = copy.deepcopy(saved)
        prerequisite = engine.state["tasks"]["task_definition"]
        prerequisite["answer"] = {**prerequisite["answer"], "summary": "Reworked."}
        engine.keep_spent_answers()
        row = engine.state["tasks"]["task_synthesis"]
        self.assertEqual((row["status"], row["draft_answer"], row["resubmit"]), ("researching", kept, digest(kept)))
        self.assertTrue(row["reopenings"][-1]["settled"], "a block of this check returns to no earlier answer")

    def test_an_access_gap_approved_for_a_blocked_rework_is_taken_up_before_its_answer_comes_back(self):
        """2026-10-02 review: the run restored the answer from before the rework first, and the access gap the editor
        had approved for that blocked question was never taken up."""
        def flow(prompt, schema, payload, kwargs):
            if schema is ResearchAssessment:
                return ResearchAssessment(requirements=[failing("rq_001", "The mechanism is absent.",
                                                                "task_definition__f_energy")], issues=[])
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=0, task_ids=["task_definition"], reason="Routed.",
                                               anchors=[anchor("task_definition", payload["objections"][0])])])
            if schema is ResearchDecision and payload.get("reopening"):
                return decision("blocked")
        self.hook = flow
        with patch.object(QuestionResearch, "keep_spent_answers", return_value=[]), self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        approval = {"task_id": "task_definition", "criterion": 0, "source": "src_closed", "evidence": "HTTP 403", "reason": ""}
        engine = QuestionResearch(self.root, self.work, self.config, self.model,
                                  lambda activity: write_json(self.work / "research_activity.json", {"activity": activity}),
                                  access_gaps=lambda: [approval])
        engine.run(self.discovery, self.index)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual((engine.state["phase"], row["status"], row.get("access_gaps")), ("completed", "verified", [approval]))

    def test_a_finish_on_disputed_objections_is_a_finish_on_noted_limits(self):
        """2026-10-02 review: every requirement met and the one remaining issue disputed as a review disagreement; the
        quality report said no_remaining_issues beside the objection it listed."""
        from types import SimpleNamespace
        engine = self.engine()
        engine.run(self.discovery, self.index)
        gate = self.gate()
        self.assertFalse(gate.get("passed_with_noted_limits"), "a clean pass")
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text(encoding="utf-8"))
        disputed = SimpleNamespace(issues=[SimpleNamespace(reason="The second case is disputed.")])
        report = engine.tolerate(dossier, disputed, {**gate, "blocking_gaps": []})
        self.assertEqual((report["residual_objections"], report["passed_with_noted_limits"]),
                         (["The second case is disputed."], True))


class RevalidationTests(AuditCase):
    def test_revalidations_of_dependents_are_counted_and_reported(self):
        """2026-10-02: one synthesis question was revalidated four times, against no limit and on no record."""
        def flow(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), {**task_value("task_synthesis", "synthesis"),
                                                          "depends_on": ["task_definition"]}])
            if schema is ResearchAssessment:
                return ResearchAssessment(requirements=[failing("rq_001", "The mechanism is absent.",
                                                                "task_definition__f_energy")], issues=[])
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=0, task_ids=["task_definition"], reason="Routed.",
                                               anchors=[anchor("task_definition", payload["objections"][0])])])
        self.hook = flow
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(engine.state["tasks"]["task_synthesis"]["revalidations"], 1)
        ledger = {row["id"]: row for row in public_ledger(engine.state)["questions"]}
        self.assertEqual((ledger["task_definition"]["revalidations"], ledger["task_synthesis"]["revalidations"]), (0, 1))
        self.assertEqual([(r["task_id"], r["count"]) for r in self.gate()["revalidations"]], [("task_synthesis", 1)])
        self.assertIn("## Nachprüfungen nach geänderten Voraussetzungen", self.quality())


class CorrectionLoopTests(AuditCase):
    def test_spent_validator_rejections_stop_as_a_correction_loop_not_as_an_unreadable_answer(self):
        """docs/research.md: only a missing or unreadable answer keeps invalid_model_output, and a resume asks again
        for it. Spent rejections are replayed without a call, so they stop with a correction-loop code."""
        folder = self.work / "receipts"
        folder.mkdir(parents=True)
        calls = []

        def generate(prompt, schema):
            calls.append(prompt)
            return DossierPatch(updates=[], additions=[], coverage_updates=[], resolved_open_questions=[], new_open_questions=[])

        def reject(value, final):
            raise AppError("Gesamtprüfung nennt unbekannte Befunde.", code="invalid_model_output", status="blocked")
        for attempt in range(2):
            with self.assertRaises(AppError) as spent:
                cached_call(folder, "check", DossierPatch, "Prompt\n{}", generate, validate=reject)
            self.assertEqual((spent.exception.code, len(calls)), ("rejected_output", 3))
        # The rejection itself keeps the check's code; a missing answer still stops as invalid_model_output.
        self.assertEqual(json.loads((folder / "check_rejected_02.json").read_text(encoding="utf-8"))["code"], "invalid_model_output")

        def unreadable(prompt, schema):
            raise AppError("Keine gültige strukturierte Antwort.", code="invalid_model_output", status="blocked")
        with self.assertRaises(AppError) as missing:
            cached_call(folder, "other", DossierPatch, "Prompt\n{}", unreadable)
        self.assertEqual(missing.exception.code, "invalid_model_output")


class MergedReviewTests(AuditCase):
    def test_a_part_that_fails_only_under_the_other_parts_assessments_is_asked_again(self):
        """Generation 2: the parts of a split review passed on their own, the merged review did not (it keeps the
        assessment of a shared source claiming the least independence), and every resume replayed the failure."""
        findings = [Finding(id=f"f_{n}", kind="claim", statement=f"Claim {n}.", claim_contract=claim_contract(),
                            evidence=[Evidence(reference=f"a#s{n}", excerpt=f"Passage {n}")]) for n in range(2)]
        dossier = ResearchDossier(topic="Test topic", scope_note="Bounded.", findings=findings, coverage=[], open_questions=[])
        context = [{"source_id": "a", "title": "a", "url": "https://example.org/a",
                    "sections": [{"reference": f"a#s{n}", "text": f"Passage {n}"} for n in range(2)]}]
        folder = self.work / "audit"
        folder.mkdir(parents=True)
        asked = []

        def generate(prompt, schema):
            payload = json.loads(prompt.splitlines()[-1])
            (finding,) = payload["dossier"]["findings"]
            asked.append(finding["id"])
            receipts = support_receipts([finding], payload["sources"])
            if finding["id"] == "f_0" and "Rejections" not in prompt:
                receipts["finding_support"][0].update(empirical_status="independently_tested",
                                                      independent_evidence_refs=[finding["evidence"][0]["reference"]])
                receipts["source_assessments"][0].update(independence="independent", evidence_family="study a")
            return SourceReview(issues=[], limitations=[], **receipts)

        def well_formed(review, final, findings=None, objections=None):
            findings = list(dossier.findings) if findings is None else findings
            scope_assessments(review, findings, context)
            independence = {a.source_id: a.independence for a in collapse_assessments(review.source_assessments)}
            for row in review.finding_support:
                if row.empirical_status == "independently_tested" and independence.get("a") != "independent":
                    raise AppError(f"{row.finding_id}: independent testing is not established.",
                                   code="invalid_evidence_review", status="blocked")

        test = self

        class Engine:
            config = test.config
            state = {"plan": {"tasks": [{"id": "t", "question": "q"}]}, "verified_baseline": []}
            settled_findings = SynthesisMixin.settled_findings

            def save(self, activity):
                pass

            def call(self, folder, name, schema, prompt, validate):
                return cached_call(folder, name, schema, prompt, generate, validate=validate)

        with patch("podcast_automate.question_synthesis.PROMPT_BUDGET_CHARS", 1), \
                patch("podcast_automate.question_synthesis.ANSWER_BUDGET_CHARS", 1):
            review = SynthesisMixin.grounding_review(Engine(), folder, 0, dossier, context, {}, well_formed)
            self.assertEqual(asked, ["f_0", "f_1", "f_0"])
            rejected = json.loads((folder / "grounding_0_part_000_rejected_00.json").read_text(encoding="utf-8"))
            self.assertIn("other parts of this review assessed a: unknown", rejected["message"])
            self.assertEqual({r.finding_id: r.empirical_status for r in review.finding_support},
                             {"f_0": "not_applicable", "f_1": "not_applicable"})
            # A resume replays the corrected receipts without a call.
            SynthesisMixin.grounding_review(Engine(), folder, 0, dossier, context, {}, well_formed)
            self.assertEqual(len(asked), 3)

    def test_an_unanchored_review_disagreement_is_on_record_once_however_often_the_run_resumes(self):
        def disagree(prompt, schema, payload, kwargs):
            if schema is SourceReview:
                value = complete_fixture_response(SourceReview(issues=[SourceReviewIssue(
                    finding_id="f_energy", reason="An optional new topic.", resolution="research", search_queries=["energy"])],
                    limitations=[]), payload)
                value.issues[0].objection = value.issues[0].objection.model_copy(update={"resolution": "review_disagreement"})
                return value
        self.hook = disagree
        with fixtures.composed_generation():
            for attempt in range(2):
                with self.assertRaises(AppError) as stopped:
                    self.engine().run(self.discovery, self.index)
                self.assertEqual(stopped.exception.code, "review_disagreement")
        state = read_value(self.work / "question_research/state.json")
        self.assertEqual(len(state["review_disagreements"]), 1)


class CompleteContextTests(AuditCase):
    def test_a_cited_passage_the_reader_cannot_supply_stops_with_its_name_instead_of_being_dropped(self):
        reader = SourceReader(self.index)
        refs = [f"{source.id}#{section.id}" for source in self.index.sources for section in source.sections]
        self.assertEqual(len(complete_context(reader, refs)[0]["sections"]), len(refs))
        real = base.read_context

        def short(reader, refs):
            context = real(reader, refs)
            context[0]["sections"] = context[0]["sections"][:-1]  # what the former 2 000 000-character cap left out
            return context
        with patch("podcast_automate.question_synthesis.read_context", side_effect=short):
            with self.assertRaises(AppError) as dropped:
                complete_context(reader, refs)
        self.assertEqual(dropped.exception.code, "research_context_incomplete")
        self.assertIn(refs[-1], str(dropped.exception))


class RenderTests(unittest.TestCase):
    def test_a_finish_on_accepted_gaps_alone_keeps_its_heading(self):
        report = {"closed": 1, "total": 1, "requirements": [], "blocking_gaps": [], "passed_with_accepted_gaps": True,
                  "residual_objections": ["Only the accepted gap."]}
        self.assertIn("## Verbliebene Prüfeinwände zu akzeptierten Lücken", render_quality(report))
        noted = render_quality({**report, "passed_with_accepted_gaps": False, "passed_with_noted_limits": True})
        self.assertIn("## Verbliebene Prüfeinwände, als Grenzen vermerkt oder strittig", noted)
        self.assertNotIn("akzeptierten Lücken", noted)


if __name__ == "__main__":
    unittest.main()
