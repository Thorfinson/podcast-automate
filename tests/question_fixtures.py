"""Small synthetic question answers for downstream workflow tests."""
import json

from podcast_automate.research_tasks import AnswerReview, QuestionAnswer, QuestionPlan, ResearchDecision
from podcast_automate.question_scope import QuestionScopeReview
from podcast_automate.evidence_models import ResearchObjection, ObjectionClosure
from podcast_automate.research_review import SourceReview
from podcast_automate.research_tasks import QuestionSearch, ReopenPlan
from podcast_automate.research_advisor import BlockAdvice


def claim_contract():
    return dict(basis="source_definition", relation="definition", scope=["Synthetic configuration example"],
                qualifications=[], quantities=[])


def support_receipts(findings, sources):
    refs = {e["reference"] for f in findings for e in f["evidence"]}
    return dict(finding_support=[dict(finding_id=f["id"], verdict="supported",
        references=list(dict.fromkeys(e["reference"] for e in f["evidence"])), reason="All clauses checked in synthetic fixture.",
        unsupported_clauses=[], suitability="suitable", suitability_reason="Original fixture definition.",
        contract_preserved=True, empirical_status="not_applicable", independent_evidence_refs=[]) for f in findings],
        source_assessments=[dict(source_id=s["source_id"], roles=["original_definition"],
            evidence_refs=[p["reference"] for p in s["sections"] if p["reference"] in refs],
            rationale="Synthetic fixture defines its terms.", work_id="", version="", evidence_family="",
            independence="unknown", method="", research_group="", population="", geography="", period="", limitations=[])
            for s in sources if any(p["reference"] in refs for p in s["sections"])])


def script_checks(prompt):
    payload = json.loads(prompt.splitlines()[-1])
    return [dict(segment_id=s["segment_id"], finding_ids=s["knowledge_refs"],
                 verdict="preserved" if s["knowledge_refs"] else "no_research_claim", quote=s["text"],
                 reason="Synthetic fixture preserves its assigned statement.", changed_fields=[])
            for s in payload["script"]["segments"]]


def complete_fixture_response(value, payload):
    """Expand existing workflow stubs to valid evidence receipts, never used by gate tests."""
    if isinstance(value, (AnswerReview, SourceReview)):
        findings = payload.get("answer", payload.get("dossier", {})).get("findings", [])
        if not value.finding_support:
            value = type(value).model_validate({**value.model_dump(), **support_receipts(findings, payload.get("sources", []))})
        if isinstance(value, SourceReview):
            value.objection_checks = [ObjectionClosure(objection_id=identifier, verdict="closed",
                references=[findings[0]["evidence"][0]["reference"]], reason="Synthetic fixture meets fixed closure condition.")
                for identifier in payload.get("open_objections", {})]
            for issue in value.issues:
                issue.objection = ResearchObjection(id="obj_fixture", rule="support", task_id="", criterion_index=None,
                    finding_ids=[issue.finding_id], evidence_refs=[], missing_evidence=issue.reason, reason=issue.reason,
                    correction=issue.reason, closure_condition="The stated missing evidence is supplied and checked.",
                    resolution=issue.resolution)
    elif isinstance(value, QuestionSearch):
        value = value.model_copy(update={"executed_queries": payload.get("queries", ["energy"]),
                                        "counterevidence": "No contrary result in this synthetic fixture."})
    elif isinstance(value, ReopenPlan):
        for route in value.routes:
            route.anchors = [ResearchObjection(id="obj_fixture", rule="criterion", task_id=task_id,
                criterion_index=0, finding_ids=[], evidence_refs=[], missing_evidence=payload["objections"][route.index],
                reason=route.reason, correction="Supply the required mechanism.",
                closure_condition="The missing criterion is met by checked evidence.", resolution="research")
                for task_id in route.task_ids]
    return value


def task_value(id="task_definition", kind="definition"):
    return dict(id=id, requirement_ids=["rq_001"], question_ids=["q_energy"],
                question="What is energy?", kind=kind, acceptance=["Explain energy in this bounded example."],
                queries=["energy configuration"], key_terms=["energy"], finding_ids=[], gap_ids=[])


def decision(action, **values):
    return ResearchDecision.model_validate(dict(action=action, reason="A concrete next step.",
        searches=values.get("searches", []), windows=values.get("windows", []),
        web_queries=values.get("web_queries", []), answer=values.get("answer")))


def answer_for(ref):
    return QuestionAnswer.model_validate(dict(summary="Configurations receive energies in this fixture.",
        findings=[dict(id="f_energy", kind="definition", statement="Configurations are assigned energies.",
                       claim_contract=claim_contract(),
                       evidence=[dict(reference=ref, excerpt="Models assign an energy")])],
        criteria=[dict(index=0, explanation="The source directly defines the assignment.", finding_ids=["f_energy"])], limits=[]))


def question_response(prompt, schema):
    payload = json.loads(prompt.splitlines()[-1])
    if schema is QuestionScopeReview:
        return QuestionScopeReview(decisions=[{"task_id": task["id"], "parts": [],
            "reason": "One definition and its evidence, no unrelated obligations."} for task in payload["tasks"]])
    if schema is QuestionPlan:
        task = task_value()
        task["gap_ids"] = list(payload["gaps"])
        task["finding_ids"] = [f["id"] for f in payload["existing_findings"]]
        return QuestionPlan(tasks=[task])
    if schema is ResearchDecision:
        ref = next(section["reference"] for source in payload["sources"] for section in source["sections"]
                   if "Models assign an energy" in section["text"])
        return decision("answer", answer=answer_for(ref))
    if schema is AnswerReview:
        return complete_fixture_response(AnswerReview(criteria=[dict(index=0, passed=True, reason="Supported by this fixture.")],
                            supported=True, source_adequacy=True, issues=[]), payload)
    if schema is SourceReview:
        return complete_fixture_response(SourceReview(issues=[], limitations=[]), payload)
    if schema is BlockAdvice:
        # The default advice starts nothing on its own, so a blocked fixture run still stops for its decisions.
        return BlockAdvice(diagnosis="Synthetische Beratung ohne neuen Zugang.", recommendation="accept_gap",
                           limit="none", hint="", sources=[])
    return None
