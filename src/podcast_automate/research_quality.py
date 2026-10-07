"""Close the original research brief against retrieved evidence before planning."""
from __future__ import annotations

import json
from collections import Counter
from typing import Literal

from pydantic import Field

from .content_text import catalog_label, text as wording
from .errors import AppError
from .models import Contract, Identifier, NonEmpty
from .research_models import ResearchDiscovery, ResearchDossier, SourceIndex, is_idea
from .storage import digest

QUALITY_VERSION = "research_quality.v1"
CRITERIA = {
    "direct_answer": "Leitfrage vollständig beantwortet",
    "explanation": "Grundlagen, Mechanismus und nachvollziehbares Beispiel",
    "evidence": "Inhaltliche Belege aus gelesenen unabhängigen Quellen",
    "cross_check": "Gegenpositionen und unabhängige Prüfungen berücksichtigt",
    "boundaries": "Geltungsgrenzen, Unsicherheit und Verbindungen erklärt",
}


class RequirementAssessment(Contract):
    requirement_id: Identifier
    finding_ids: list[Identifier]
    direct_answer: bool
    explanation: bool
    evidence: bool
    cross_check: bool
    boundaries: bool
    reason: NonEmpty
    missing: list[NonEmpty]
    search_queries: list[NonEmpty]


class ResearchAssessment(Contract):
    requirements: list[RequirementAssessment] = Field(min_length=1)
    issues: list[NonEmpty]


# What closes a gap: new reading or a corrected answer (research), or nothing the run can do, because it is a property
# of the available sources that the script states (limit). A follow-up assessment names it for every row and issue.
REMEDY = ("research: new reading or a corrected answer can close it; limit: it is a property of the available "
          "sources (a work only as an abstract, a third-party copy or not freely available, figures only a vendor or "
          "the authors report, independence no source establishes) that the script must state")


class FollowUpRequirement(RequirementAssessment):
    remedy: Literal["research", "limit", "none"] = Field(description="For a requirement not fully met, " + REMEDY +
                                                                     "; none when nothing is missing.")


class AssessmentIssue(Contract):
    text: NonEmpty
    remedy: Literal["research", "limit"] = Field(description=REMEDY + ".")
    task_ids: list[NonEmpty] = Field(description="The tasks whose answers the issue concerns.")


class FollowUpAssessment(Contract):
    """The assessment from the second round of an assembled dossier on (question_synthesis.follow_up_assessment)."""
    requirements: list[FollowUpRequirement]
    issues: list[AssessmentIssue]


def requirements_for(config):
    questions = list(dict.fromkeys([config.central_question or config.topic, *config.focus_questions]))
    return [{"id": f"rq_{i:03d}", "question": question} for i, question in enumerate(questions, 1)]


def quality_brief(config):
    brief = {"topic": config.topic, "central_question": config.central_question,
             "depth_request": config.depth_request, "prior_knowledge": config.prior_knowledge,
             "audience_level": config.audience_level, "excluded_topics": config.excluded_topics,
             "requirements": requirements_for(config)}
    # Only when set, so a brief without them binds its research ledger exactly as before (2026-09-30).
    for key in ("series_goal", "recency_months"):
        if getattr(config, key) is not None:
            brief[key] = getattr(config, key)
    return brief


def cite_findings(dossier, assessment):
    """An assessment that names a synthesis relation where a finding belongs relies on the findings the
    relation compares: those replace it, in place (Asimov, 2026-09-27: thirteen ``syn_…`` IDs refused)."""
    relations = {relation.id: relation.finding_ids for relation in dossier.synthesis}
    for row in assessment.requirements:
        if any(fid in relations for fid in row.finding_ids):
            row.finding_ids = list(dict.fromkeys(f for fid in row.finding_ids for f in relations.get(fid, [fid])))
    return assessment


def check_follow_up(dossier, assessment, scope, task_ids):
    """Deterministic shape of a follow-up assessment: exactly the requirements in scope, real finding and task IDs."""
    if Counter(r.requirement_id for r in assessment.requirements) != Counter(scope["requirements"]):
        raise AppError("Die Folgebewertung beurteilt genau die Leitfragen aus requirements_in_scope, jede einmal: "
                       + ", ".join(scope["requirements"]) + ".", code="invalid_research_assessment", status="blocked")
    unknown = sorted({fid for r in assessment.requirements for fid in r.finding_ids} - {f.id for f in dossier.findings})
    if unknown:
        raise AppError("Die Folgebewertung verweist auf unbekannte Befunde: " + ", ".join(unknown[:12])
                       + ". finding_ids nennt nur Befund-IDs aus findings.", code="invalid_research_assessment", status="blocked")
    strange = sorted({tid for issue in assessment.issues for tid in issue.task_ids} - set(task_ids))
    if strange:
        raise AppError("Die Folgebewertung nennt unbekannte Teilfragen: " + ", ".join(strange[:12])
                       + ". task_ids nennt nur Kennungen aus answers.", code="invalid_research_assessment", status="blocked")


def check_assessment(config, dossier, assessment):
    """Deterministic shape of an assessment: every requirement once, only real finding IDs."""
    expected = {r["id"] for r in requirements_for(config)}
    if Counter(r.requirement_id for r in assessment.requirements) != Counter(expected):
        raise AppError("Die Qualitätsprüfung muss jede ursprüngliche Leitfrage genau einmal bewerten.",
                       code="invalid_research_assessment", status="blocked")
    findings = {f.id for f in dossier.findings}
    unknown = sorted({fid for r in assessment.requirements for fid in r.finding_ids} - findings)
    if unknown:
        raise AppError("Die Qualitätsprüfung verweist auf unbekannte Befunde: " + ", ".join(unknown[:12])
                       + ". finding_ids nennt nur Befund-IDs aus dossier.findings.",
                       code="invalid_research_assessment", status="blocked")


def quality_report(config, dossier, discovery, index, assessment, grounding_issues=(), *, accepted=None,
                   gap_probes=(), review_limitations=()):
    """The gate report. ``accepted`` maps task ids to explicitly accepted gaps; their coverage rows
    are listed as accepted, not blocking. Requirement verdicts stay honest either way.

    ``gap_probes`` are the corpus-probe rows. A gap whose candidate sections were never read
    blocks; a gap confirmed after reading them does not. A hit alone is not a contradiction.

    ``review_limitations`` are per-question rows of what the independent answer review confirmed
    only with a stated limit. They are recorded, never a gate: the answer passed."""
    check_assessment(config, dossier, cite_findings(dossier, assessment))
    accepted = accepted or {}
    requirements = requirements_for(config)
    findings = {f.id: f for f in dossier.findings}
    external = {s.id for s in index.sources if not is_idea(s)}
    rows = []
    for requirement in requirements:
        result = next(r for r in assessment.requirements if r.requirement_id == requirement["id"])
        evidence_backed = any(f.kind != "limitation" and any(e.reference.split("#")[0] in external for e in f.evidence)
                              for f in (findings[fid] for fid in result.finding_ids))
        missing, noted = list(result.missing), []
        criteria_met = all(getattr(result, key) for key in CRITERIA)
        if dossier.assembled and criteria_met and evidence_backed:
            # All five criteria met: what the assessment still lists is a limit of the answer, not missing research
            # (the user's choice, 2026-10-02: Ontologies failed 6 of 9 requirements only on such lists, such as
            # person-hours no source reports, and went from 2 to 1 to 0 passed requirements in three rounds).
            missing, noted = [], missing
        if not evidence_backed:
            missing.append("Es fehlt eine inhaltliche Antwort mit unabhängig abgerufenem Textbeleg.")
        passed = criteria_met and not missing
        gap_tasks = sorted(tid for tid, gap in accepted.items() if requirement["id"] in gap.get("requirement_ids", []))
        rows.append({**requirement, **result.model_dump(), "passed": passed, "missing": missing,
                     **({"noted": noted} if noted else {}), "accepted_gap_tasks": gap_tasks})
    questions = {q.id: q.question for q in discovery.questions}
    accepted_questions = {qid for gap in accepted.values() for qid in gap.get("question_ids", [])}
    gaps, tolerated = [], []
    for row in dossier.coverage:
        if row.status != "answered" or row.gap:
            text = f"{questions[row.question_id]}: {row.gap}"
            (tolerated if row.question_id in accepted_questions else gaps).append(text)
    gaps.extend(dossier.open_questions)
    gaps.extend(assessment.issues)
    gaps.extend(grounding_issues)
    probes = [dict(row) for row in gap_probes]
    gaps.extend(f"{row['text']} Dazu gibt es ungelesene Abschnitte im Korpus: "
                + ", ".join(hit["reference"] for hit in row["hits"]) + "."
                for row in probes if row["status"] == "hits_unread")
    return {"version": QUALITY_VERSION, "passed": all(r["passed"] for r in rows) and not gaps,
            "gap_probes": probes,
            "criteria": CRITERIA, "requirements": rows, "closed": sum(r["passed"] for r in rows),
            "total": len(rows), "blocking_gaps": list(dict.fromkeys(gaps)),
            "accepted_gaps": [{"task_id": tid, "question": gap.get("question", ""), "reason": gap.get("reason", ""),
                               "question_ids": gap.get("question_ids", []), "requirement_ids": gap.get("requirement_ids", [])}
                              for tid, gap in sorted(accepted.items())],
            "accepted_coverage_gaps": list(dict.fromkeys(tolerated)),
            "review_limitations": [dict(row) for row in review_limitations if row.get("limitations")],
            "dossier_hash": digest(dossier.model_dump()), "brief_hash": digest(requirements),
            "scope": "Alle vereinbarten Leitfragen; keine Behauptung abschließenden Wissens über das gesamte Fachgebiet."}


PROBE_LABELS = {"no_hits": "kein passender Abschnitt gefunden",
                "hits_unread": "Treffer noch ungelesen",
                "hits_unowned": "Treffer in Quellen, die keine Folge nutzt",
                "hits_read_confirmed": "Treffer gelesen, Lücke bestätigt",
                "resolved": "in den Quellen beantwortet"}


def render_quality(report, *, language="de-DE"):
    """quality.md, in the podcast's content language (D-153). The criteria and probe states keep their German words in
    CRITERIA and PROBE_LABELS, which the report records; content_text has the other languages' words."""
    say = lambda key, **values: wording(language, key, **values)
    criterion = lambda key: catalog_label(language, "criterion", key) or CRITERIA[key]
    probe_label = lambda status: (catalog_label(language, "probe_status", status)
                                  or PROBE_LABELS.get(status, status))
    lines = [f"# {say('quality_title')}", "", say("quality_closed", closed=report["closed"], total=report["total"]),
             "", *[f"- {criterion(key)}" for key in CRITERIA], ""]
    if report.get("assessment_status") == "pending_after_source_review":
        lines += [say("quality_pending"), ""]
    if report.get("passed_with_residual_objections"):
        note = f" ({report['residual_note']})" if report.get("residual_note") else ""
        lines += [say("quality_residual", note=note), ""]
    if report.get("passed_with_accepted_gaps"):
        lines += [say("quality_accepted"), ""]
    if report.get("passed_with_noted_limits"):
        # Not "complete": what is unmet or noted stays visible as a limit (2026-10-02: Ontologies passed with 6 of 9
        # requirements unmet and was published as if gaps had been accepted).
        lines += [say("quality_noted", closed=report["closed"], total=report["total"]), ""]
    for row in report["requirements"]:
        verdict = say("quality_requirement_met" if row["passed"] else "quality_requirement_open")
        lines += [f"## {verdict}: {row['question']}", "", row["reason"], ""]
        lines += [f"- {criterion(key)}: {say('quality_criterion_met' if row[key] else 'quality_criterion_open')}"
                  for key in CRITERIA]
        lines += [f"- {say('quality_missing', gap=gap)}" for gap in row["missing"]]
        lines += [f"- {say('quality_noted_item', item=item)}" for item in row.get("noted", [])]
        if row.get("source_limit"):
            lines += [f"- {say('quality_source_limit')}"]
        if row.get("recorded_limit"):
            lines += [f"- {say('quality_recorded_limit')}"]
        if row.get("accepted_gap_tasks"):
            lines += [f"- {say('quality_accepted_task', task=tid)}" for tid in row["accepted_gap_tasks"]]
        lines += [""]
    if report["blocking_gaps"]:
        lines += [f"## {say('quality_blocking')}", "", *[f"- {gap}" for gap in report["blocking_gaps"]], ""]
    if report.get("script_notes"):
        lines += [f"## {say('quality_script_notes')}", "", say("quality_script_notes_intro"), "",
                  *[f"- {note}" for note in report["script_notes"]], ""]
    rows = (report.get("advisories") or {}).get("single_group_findings") or []
    if rows:
        lines += [f"## {say('quality_single_group')}", "", say("quality_single_group_intro"), ""]
        for row in rows:
            group = row["research_group"] or say("quality_unknown_group")
            lines.append(f"- {row['finding_id']}: {group} ({', '.join(row['source_ids'])})")
        lines += [""]
    if report.get("gap_probes"):
        lines += [f"## {say('quality_probes')}", "", say("quality_probes_intro"), ""]
        for row in report["gap_probes"]:
            lines += [f"- {row['text']} — {probe_label(row['status'])}"
                      + (": " + ", ".join(hit["reference"] for hit in row["hits"]) if row["hits"] else "")]
        lines += [""]
    if report.get("review_limitations"):
        lines += [f"## {say('quality_review_limits')}", "", say("quality_review_limits_intro"), ""]
        for row in report["review_limitations"]:
            lines += [f"### {row['question']}", "", *[f"- {item['text']}" for item in row["limitations"]], ""]
    if report.get("accepted_gaps"):
        lines += [f"## {say('quality_accepted_gaps')}", ""]
        for gap in report["accepted_gaps"]:
            lines += [f"- {gap['question'] or gap['task_id']}" + (f": {gap['reason']}" if gap.get("reason") else "")]
        lines += [f"- {gap}" for gap in report.get("accepted_coverage_gaps", [])]
        lines += [""]
    if report.get("disputed_objections"):
        lines += [f"## {say('quality_disputed')}", "", say("quality_disputed_intro"), ""]
        for row in report["disputed_objections"]:
            side = say("quality_side_reviewer" if row["decision"] == "reviewer" else "quality_side_objection")
            lines += [f"- {say('quality_objection', text=(row.get('objection') or {}).get('reason', row['objection_id']))}",
                      f"  {say('quality_reviewer', text=row['review']['reason'])}",
                      f"  {say('quality_decision', side=side)}" + (f" ({row['note']})" if row.get("note") else "")]
        lines += [""]
    if report.get("noted_limits"):
        lines += [f"## {say('quality_noted_limits')}", "", say("quality_noted_limits_intro"), "",
                  *[f"- {objection}" for objection in report["noted_limits"]], ""]
    spent = [row for row in report.get("noted_after_reworks", []) if row.get("basis") != "rework_blocked"]
    blocked = [row for row in report.get("noted_after_reworks", []) if row.get("basis") == "rework_blocked"]
    if spent:
        lines += [f"## {say('quality_after_reworks')}", "", say("quality_after_reworks_intro"), "",
                  *[f"- {row['task_id']}: {row['objection']}" for row in spent], ""]
    if blocked:
        lines += [f"## {say('quality_rework_blocked')}", "", say("quality_rework_blocked_intro"), "",
                  *[f"- {row['task_id']}: {row['objection']}"
                    + (say("quality_rework_note", block=row["rework_block"]) if row.get("rework_block") else "")
                    for row in blocked], ""]
    if report.get("revalidations"):
        lines += [f"## {say('quality_revalidations')}", "", say("quality_revalidations_intro"), "",
                  *[f"- {row.get('question') or row['task_id']}: {row['count']}×" for row in report["revalidations"]], ""]
    if report.get("residual_objections"):
        if report.get("passed_with_residual_objections"):
            heading = say("quality_residual_heading")
        elif report.get("passed_with_accepted_gaps") and not report.get("passed_with_noted_limits"):
            heading = say("quality_residual_accepted")
        else:
            heading = say("quality_residual_noted")
        lines += [f"## {heading}", "", *[f"- {objection}" for objection in report["residual_objections"]], ""]
    return "\n".join(lines)

def load_complete_research(work):
    folder = work / "complete_research"
    return (ResearchDiscovery.model_validate_json((folder / "discovery.json").read_text(encoding="utf-8")),
            SourceIndex.model_validate_json((folder / "source_index.json").read_text(encoding="utf-8")),
            json.loads((folder / "source_context.json").read_text(encoding="utf-8")),
            ResearchDossier.model_validate_json((folder / "dossier.json").read_text(encoding="utf-8")))
