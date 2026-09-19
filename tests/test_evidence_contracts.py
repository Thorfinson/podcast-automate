"""Adversarial receipt checks; these test gates, not model scientific accuracy."""
import copy
import io
import json
import unittest

from pydantic import ValidationError
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from podcast_automate.errors import AppError
from podcast_automate.evidence_models import ResearchObjection, SynthesisRelation
from podcast_automate.question_dependencies import ordered_tasks, invalidate_dependents
from podcast_automate.question_research import answer_errors
from podcast_automate.question_scope import QuestionScopeReview, scoped_plan
from podcast_automate.research_evidence import support_errors, concentration, validate_synthesis, validate_objection, claim_changes
from podcast_automate.research_ledger import read_value, save_value
from podcast_automate.research_models import ResearchDossier
from podcast_automate.research_patches import DossierPatch, apply_patch
from podcast_automate.research_reader import SourceReader
from podcast_automate.research_review import SourceReview
from podcast_automate.research_tasks import AnswerReview, QuestionPlan, ResearchDecision
from podcast_automate.script_evidence import validate_claim_checks
from podcast_automate.script_models import ScriptReview
from podcast_automate.sources import extract
from podcast_automate.storage import digest
from tests.question_fixtures import answer_for, task_value, support_receipts, script_checks
from tests.script_fixtures import example_script
from tests import test_question_research as fixtures


class EvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self.findings = answer_for("src_one#s_one").findings
        self.context = [{"source_id": "src_one", "sections": [{"reference": "src_one#s_one",
            "text": "Models assign an energy to each configuration in this example."}]}]
        self.receipts = support_receipts([f.model_dump() for f in self.findings], self.context)

    def review(self, **changes):
        return SourceReview(issues=[], limitations=[], **{**copy.deepcopy(self.receipts), **changes})

    def dossier(self, synthesis=()):
        return ResearchDossier(topic="Fixture", scope_note="Synthetic evidence, no research claim.",
            findings=self.findings, coverage=[], open_questions=[], synthesis=list(synthesis))

    def test_missing_duplicate_and_unknown_finding_receipts_fail_closed(self):
        for rows in ([], self.receipts["finding_support"] * 2,
                     [{**self.receipts["finding_support"][0], "finding_id": "f_unknown"}]):
            with self.subTest(rows=rows), self.assertRaises(AppError):
                support_errors(self.findings, self.review(finding_support=rows), self.context)

    def test_valid_anchor_does_not_cancel_unsupported_clause(self):
        review = self.review()
        review.finding_support[0].verdict = "partially_supported"
        review.finding_support[0].unsupported_clauses = ["The method is effective in all populations."]
        self.assertTrue(support_errors(self.findings, review, self.context))

    def test_aggregate_pass_cannot_override_semantic_failure(self):
        review = AnswerReview(criteria=[dict(index=0, passed=True, reason="Fixture criterion.")],
            supported=True, source_adequacy=True, issues=[], **self.receipts)
        review.finding_support[0].contract_preserved = False
        self.assertTrue(support_errors(self.findings, review, self.context))

    def test_definition_can_pass_without_independence_or_doi(self):
        self.assertEqual(support_errors(self.findings, self.review(), self.context), [])

    def test_unknown_and_unread_source_assessments_cannot_pass(self):
        for field, value in (("source_id", "src_other"), ("evidence_refs", ["src_one#s_unread"])):
            review = self.review()
            setattr(review.source_assessments[0], field, value)
            with self.subTest(field=field), self.assertRaises(AppError):
                support_errors(self.findings, review, self.context)

    def test_duplicate_work_is_not_independent_confirmation(self):
        self.findings[0].evidence.append(self.findings[0].evidence[0].model_copy(update={"reference": "src_two#s_two"}))
        self.context.append({"source_id": "src_two", "sections": [{"reference": "src_two#s_two", "text": "Same study in a repository."}]})
        self.receipts = support_receipts([f.model_dump() for f in self.findings], self.context)
        review = self.review()
        for source in review.source_assessments:
            source.roles = ["empirical_test"]
            source.work_id, source.evidence_family, source.independence = "doi:one", "dataset_one", "shared"
        receipt = review.finding_support[0]
        receipt.empirical_status = "independently_tested"
        receipt.independent_evidence_refs = list(receipt.references)
        self.assertTrue(support_errors(self.findings, review, self.context))
        for n, source in enumerate(review.source_assessments):
            source.work_id, source.evidence_family, source.independence = f"doi:{n}", f"dataset_{n}", "independent"
        self.assertEqual(support_errors(self.findings, review, self.context), [])
        review.source_assessments[0].work_id = "https://doi.org/10.123/TEST"
        review.source_assessments[1].work_id = "doi:10.123/test"
        self.assertTrue(support_errors(self.findings, review, self.context))
        review.source_assessments[1].work_id = "doi:10.123/other"
        for context in self.context:
            context["text_hash"] = "identical_text"
        self.assertTrue(support_errors(self.findings, review, self.context))

    def test_lost_scope_and_causality_upgrade_are_recorded(self):
        before = copy.deepcopy(self.findings)
        self.findings[0].claim_contract.relation = "causal"
        self.findings[0].claim_contract.scope = ["All contexts"]
        changes = claim_changes(before, self.findings)
        self.assertIn("claim_contract", changes[0]["changed_fields"])
        review = self.review()
        review.finding_support[0].contract_preserved = False
        self.assertTrue(support_errors(self.findings, review, self.context))

    def test_concentration_reports_unknown_denominators_without_blocking(self):
        review = self.review()
        second = review.source_assessments[0].model_copy(update={"source_id": "src_two", "geography": "Region A"})
        report = concentration([review.source_assessments[0], second])
        self.assertEqual(report["dimensions"]["geography"]["unknown"], 1)
        self.assertEqual(report["dimensions"]["geography"]["largest_share_of_known"], 1.0)
        self.assertTrue(report["advisory_only"])

    def relation(self, **changes):
        self.findings = [self.findings[0], self.findings[0].model_copy(update={"id": "f_two"})]
        return SynthesisRelation.model_validate(dict(id="rel_one", finding_ids=["f_energy", "f_two"], dimension="Response",
            relation="conditional_difference", comparability="different_conditions", conditions="Different time scales.",
            evidence_refs=["src_one#s_one"], resolution="unresolved", explanation="Timescales differ; no direct contradiction.",
            basis="editorial_synthesis", **changes))

    def test_different_time_scales_are_not_a_direct_contradiction(self):
        relation = self.relation()
        validate_synthesis(self.dossier([relation]), self.context)
        relation.relation = "contradiction"
        with self.assertRaises(AppError):
            validate_synthesis(self.dossier([relation]), self.context)

    def test_synthesis_needs_known_findings_and_their_read_evidence(self):
        relation = self.relation()
        relation.evidence_refs = ["src_unread#s_one"]
        with self.assertRaises(AppError):
            validate_synthesis(self.dossier([relation]), self.context)

    def test_patch_cannot_rewrite_unrelated_synthesis(self):
        relation = self.relation()
        patch = DossierPatch(updates=[], additions=[], coverage_updates=[], resolved_open_questions=[],
                             new_open_questions=[], removed_synthesis_ids=[relation.id])
        with self.assertRaises(AppError):
            apply_patch(self.dossier([relation]), patch, {"f_unrelated"})

    def test_objection_requires_evidence_or_specific_missing_evidence(self):
        with self.assertRaises(ValidationError):
            ResearchObjection(id="obj_one", rule="support", task_id="", finding_ids=["f_energy"],
                evidence_refs=[], missing_evidence="", reason="More research", correction="Research",
                closure_condition="Good enough", resolution="research")

    def test_objection_cannot_reopen_unrelated_task_by_shared_requirement(self):
        tasks = QuestionPlan(tasks=[task_value(), task_value("task_other")]).tasks
        objection = ResearchObjection(id="obj_one", rule="support", task_id="task_other", finding_ids=["f_energy"],
            evidence_refs=["src_one#s_one"], missing_evidence="", reason="Unsupported clause", correction="Remove clause",
            closure_condition="Full remaining claim is supported", resolution="revise")
        with self.assertRaises(AppError):
            validate_objection(objection, tasks, self.findings, self.context,
                               target_task="task_other", owners={"f_energy": ["task_definition"]})

    def test_script_requires_every_segment_and_real_quotes(self):
        script = example_script()
        review = ScriptReview(issues=[], limitations=[])
        with self.assertRaises(AppError):
            validate_claim_checks(review, script, self.findings)
        review = ScriptReview(issues=[], limitations=[], claim_checks=script_checks(json.dumps({"script": script.model_dump()})))
        self.assertEqual(validate_claim_checks(review, script, self.findings), [])
        review.claim_checks[0].quote = "Invented quotation"
        with self.assertRaises(AppError):
            validate_claim_checks(review, script, self.findings)

    def test_script_drift_creates_actionable_issue_but_spoken_numbers_are_allowed(self):
        script = example_script()
        script.segments[1].text = "A quarter of this fixture group, twenty-five percent."
        review = ScriptReview(issues=[], limitations=[], claim_checks=script_checks(json.dumps({"script": script.model_dump()})))
        self.assertEqual(validate_claim_checks(review, script, self.findings), [])
        review.claim_checks[1].verdict = "drift"
        review.claim_checks[1].changed_fields = ["quantities", "scope"]
        self.assertEqual(validate_claim_checks(review, script, self.findings)[0].segment_ids, ["seg_002"])

    def test_pdf_reports_blank_table_and_equation_pages(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 30 700 Td (Table 1. x = y. " + "Synthetic page text. " * 30 + ") Tj ET").encode())
        page[NameObject("/Contents")] = stream
        writer.add_blank_page(width=600, height=800)
        buffer = io.BytesIO()
        writer.write(buffer)
        coverage = extract(buffer.getvalue(), "application/pdf", "fixture.pdf")[2]["extraction_coverage"]
        self.assertEqual((coverage["pages_total"], coverage["pages_with_text"]), (2, 1))
        self.assertEqual(coverage["empty_pages"], [2])
        self.assertEqual(coverage["suspected_table_pages"], [1])
        self.assertEqual(coverage["suspected_equation_pages"], [1])


class SingleGroupTests(unittest.TestCase):
    """Descriptive only: this advisory names claims with one known source of authority."""

    def assessments(self, **groups):
        from podcast_automate.evidence_models import SourceAssessment
        return [SourceAssessment(source_id=source_id, roles=["original_definition"],
                                 evidence_refs=[f"{source_id}#sec_one"],
                                 rationale="Fixture.", work_id="", version="", evidence_family="",
                                 independence="unknown", method="", research_group=group, population="",
                                 geography="", period="", limitations=[])
                for source_id, group in groups.items()]

    def finding(self, identifier, kind, *sources):
        from podcast_automate.research_models import Evidence, Finding
        return Finding(id=identifier, kind=kind, statement="A synthetic statement.",
                       evidence=[Evidence(reference=f"{s}#sec_one", excerpt="A quote") for s in sources])

    def test_a_claim_backed_only_by_one_group_is_listed_with_its_sources(self):
        from podcast_automate.research_evidence import single_group_findings
        rows = single_group_findings(
            [self.finding("f_one", "claim", "src_a", "src_b")],
            self.assessments(src_a="Vendor Labs", src_b="Vendor Labs"))
        self.assertEqual(rows, [{"finding_id": "f_one", "research_group": "Vendor Labs",
                                 "source_ids": ["src_a", "src_b"], "unknown_group_source_ids": []}])

    def test_two_groups_are_not_reported(self):
        from podcast_automate.research_evidence import single_group_findings
        self.assertEqual(single_group_findings(
            [self.finding("f_one", "claim", "src_a", "src_b")],
            self.assessments(src_a="Vendor Labs", src_b="A university")), [])

    def test_an_unknown_group_is_reported_as_unknown_rather_than_independent(self):
        from podcast_automate.research_evidence import single_group_findings
        rows = single_group_findings([self.finding("f_one", "claim", "src_a", "src_b")],
                                     self.assessments(src_a="Vendor Labs", src_b=""))
        self.assertEqual(rows[0]["research_group"], "Vendor Labs")
        self.assertEqual(rows[0]["unknown_group_source_ids"], ["src_b"])
        nothing_known = single_group_findings([self.finding("f_one", "claim", "src_a")],
                                              self.assessments(src_a=""))
        self.assertIsNone(nothing_known[0]["research_group"])

    def test_definitions_and_limitations_are_not_effect_claims(self):
        from podcast_automate.research_evidence import single_group_findings
        for kind in ("definition", "limitation", "example"):
            with self.subTest(kind=kind):
                self.assertEqual(single_group_findings([self.finding("f_one", kind, "src_a")],
                                                       self.assessments(src_a="Vendor Labs")), [])

    def test_the_advisory_never_gates_and_reaches_the_quality_report(self):
        from podcast_automate.research_evidence import evidence_summary, single_group_findings
        from podcast_automate.research_review import SourceReview
        findings = [self.finding("f_one", "claim", "src_a")]
        review = SourceReview(issues=[], limitations=[], finding_support=[],
                              source_assessments=self.assessments(src_a="Vendor Labs"))
        summary = evidence_summary(findings, review)
        self.assertEqual(summary["single_group_findings"], single_group_findings(findings, review.source_assessments))
        self.assertTrue(summary["concentration"]["advisory_only"])


class PrerequisiteTests(unittest.TestCase):
    def tasks(self):
        return QuestionPlan(tasks=[{**task_value("task_synthesis", "synthesis"), "depends_on": ["task_definition"]},
                                   task_value(), task_value("task_unrelated")]).tasks

    def test_topological_order_and_invalid_graphs(self):
        self.assertLess([t.id for t in ordered_tasks(self.tasks())].index("task_definition"),
                        [t.id for t in ordered_tasks(self.tasks())].index("task_synthesis"))
        for deps in (["missing"], ["task_definition"], ["task_synthesis"]):
            tasks = self.tasks()
            tasks[1].depends_on = deps
            with self.subTest(deps=deps), self.assertRaises(AppError):
                ordered_tasks(tasks)

    def test_transitive_invalidation_preserves_unrelated_answers(self):
        tasks = self.tasks()
        state = {"plan": {"tasks": [t.model_dump() for t in tasks]}, "tasks": {
            t.id: dict(status="verified", answer={"summary": t.id}, reopenings=[]) for t in tasks}}
        untouched = copy.deepcopy(state["tasks"]["task_unrelated"])
        affected = invalidate_dependents(state, ["task_definition"])
        self.assertEqual(affected, ["task_definition", "task_synthesis"])
        self.assertEqual(state["tasks"]["task_unrelated"], untouched)
        self.assertEqual(state["tasks"]["task_synthesis"]["status"], "researching")

    def test_scope_split_replaces_parent_dependency_with_all_children(self):
        tasks = self.tasks()
        review = QuestionScopeReview(decisions=[dict(task_id=t.id, reason="Fixture", parts=[] if t.id != "task_definition" else [
            dict(question=q, criterion_indices=[0], acceptance=[q], queries=["energy"], key_terms=["energy"])
            for q in ("Definition A", "Definition B")]) for t in tasks])
        plan, groups = scoped_plan(QuestionPlan(tasks=tasks), review)
        self.assertEqual(plan.tasks[0].depends_on, groups["task_definition"])


class EvidenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.QuestionResearchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_verified_prerequisite_answer_and_hash_are_supplied(self):
        captured = []
        def hook(prompt, schema, data, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[{**task_value("task_synthesis", "synthesis"), "depends_on": ["task_definition"]}, task_value()])
            if schema is ResearchDecision:
                captured.append((data["task"]["id"], data["prerequisite_answers"]))
        self.fixture.hook = hook
        engine = self.fixture.engine()
        engine.run(self.fixture.discovery, self.fixture.index)
        self.assertEqual([t for t, _ in captured], ["task_definition", "task_synthesis"])
        self.assertEqual(captured[1][1][0]["task_id"], "task_definition")
        self.assertEqual(engine.state["tasks"]["task_synthesis"]["verification"]["prerequisite_hashes"]["task_definition"],
                         digest(engine.state["tasks"]["task_definition"]["answer"]))

    def test_supported_uncertainty_is_a_complete_answer_not_operational_block(self):
        answer = answer_for(self.fixture.ref)
        answer.outcome, answer.limits = "supported_uncertainty", ["The fixture explicitly leaves this question unresolved."]
        answer.findings[0].claim_contract.relation = "uncertainty"
        task = QuestionPlan(tasks=[task_value()]).tasks[0]
        self.assertEqual(answer_errors(answer, task, SourceReader(self.fixture.index), {self.fixture.ref}), [])
        answer.limits = []
        self.assertTrue(answer_errors(answer, task, SourceReader(self.fixture.index), {self.fixture.ref}))

    def test_legacy_receipt_is_preserved_and_rechecked_without_redownloading(self):
        engine = self.fixture.engine()
        engine.run(self.fixture.discovery, self.fixture.index)
        state = read_value(engine.folder / "state.json")
        state.pop("evidence_version")
        row = state["tasks"]["task_definition"]
        for finding in row["answer"]["findings"]:
            finding.pop("claim_contract")
            finding.pop("supporting_contracts")
        row["verification"]["answer_hash"] = digest(row["answer"])
        save_value(engine.folder / "state.json", state)
        downloads, calls = self.fixture.download.call_count, len(self.fixture.calls)
        resumed = self.fixture.engine()
        resumed.run(self.fixture.discovery, self.fixture.index)
        self.assertTrue((engine.folder / "legacy_evidence_state.json").exists())
        self.assertGreater(len(self.fixture.calls), calls)
        self.assertEqual(self.fixture.download.call_count, downloads)
        self.assertEqual(resumed.state["tasks"]["task_definition"]["status"], "verified")
