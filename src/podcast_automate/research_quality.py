"""Close the original research brief against retrieved evidence before planning."""
from __future__ import annotations

import json
from collections import Counter

from pydantic import Field

from .errors import AppError
from .models import Contract, Identifier, NonEmpty
from .research_models import ResearchDiscovery, ResearchDossier, SourceIndex
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


def requirements_for(config):
    questions = list(dict.fromkeys([config.central_question or config.topic, *config.focus_questions]))
    return [{"id": f"rq_{i:03d}", "question": question} for i, question in enumerate(questions, 1)]


def quality_brief(config):
    return {"topic": config.topic, "central_question": config.central_question,
            "depth_request": config.depth_request, "prior_knowledge": config.prior_knowledge,
            "audience_level": config.audience_level, "excluded_topics": config.excluded_topics,
            "requirements": requirements_for(config)}


def check_assessment(config, dossier, assessment):
    """Deterministic shape of an assessment: every requirement once, only real finding IDs."""
    expected = {r["id"] for r in requirements_for(config)}
    if Counter(r.requirement_id for r in assessment.requirements) != Counter(expected):
        raise AppError("Die Qualitätsprüfung muss jede ursprüngliche Leitfrage genau einmal bewerten.",
                       code="invalid_research_assessment", status="blocked")
    findings = {f.id for f in dossier.findings}
    if any(not set(r.finding_ids) <= findings for r in assessment.requirements):
        raise AppError("Die Qualitätsprüfung verweist auf unbekannte Befunde.",
                       code="invalid_research_assessment", status="blocked")


def quality_report(config, dossier, discovery, index, assessment, grounding_issues=(), *, accepted=None):
    """The gate report. ``accepted`` maps task ids to explicitly accepted gaps; their coverage rows
    are listed as accepted, not blocking. Requirement verdicts stay honest either way."""
    check_assessment(config, dossier, assessment)
    accepted = accepted or {}
    requirements = requirements_for(config)
    findings = {f.id: f for f in dossier.findings}
    external = {s.id for s in index.sources if s.url and s.final_url}
    rows = []
    for requirement in requirements:
        result = next(r for r in assessment.requirements if r.requirement_id == requirement["id"])
        evidence_backed = any(f.kind != "limitation" and any(e.reference.split("#")[0] in external for e in f.evidence)
                              for f in (findings[fid] for fid in result.finding_ids))
        missing = list(result.missing)
        if not evidence_backed:
            missing.append("Es fehlt eine inhaltliche Antwort mit unabhängig abgerufenem Textbeleg.")
        passed = all(getattr(result, key) for key in CRITERIA) and not missing
        gap_tasks = sorted(tid for tid, gap in accepted.items() if requirement["id"] in gap.get("requirement_ids", []))
        rows.append({**requirement, **result.model_dump(), "passed": passed, "missing": missing,
                     "accepted_gap_tasks": gap_tasks})
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
    return {"version": QUALITY_VERSION, "passed": all(r["passed"] for r in rows) and not gaps,
            "criteria": CRITERIA, "requirements": rows, "closed": sum(r["passed"] for r in rows),
            "total": len(rows), "blocking_gaps": list(dict.fromkeys(gaps)),
            "accepted_gaps": [{"task_id": tid, "question": gap.get("question", ""), "reason": gap.get("reason", ""),
                               "question_ids": gap.get("question_ids", []), "requirement_ids": gap.get("requirement_ids", [])}
                              for tid, gap in sorted(accepted.items())],
            "accepted_coverage_gaps": list(dict.fromkeys(tolerated)),
            "dossier_hash": digest(dossier.model_dump()), "brief_hash": digest(requirements),
            "scope": "Alle vereinbarten Leitfragen; keine Behauptung abschließenden Wissens über das gesamte Fachgebiet."}


def render_quality(report):
    lines = ["# Recherchequalität", "", f"{report['closed']} von {report['total']} Leitfragen erfüllen alle Qualitätsmerkmale.",
             "", *[f"- {title}" for title in CRITERIA.values()], ""]
    if report.get("assessment_status") == "pending_after_source_review":
        lines += ["Die Quellenprüfung hat fehlende Belege gefunden. Zuerst wird gezielt nachrecherchiert; "
                  "die Bewertung aller Leitfragen wird danach erneuert. Die bisherigen Einzelbewertungen "
                  "sind noch kein Urteil über den ergänzten Entwurf.", ""]
    if report.get("passed_with_accepted_gaps"):
        lines += ["Die Recherche wurde mit ausdrücklich akzeptierten Lücken abgeschlossen. Die betroffenen Teilfragen "
                  "und verbliebenen Einwände stehen unten; das Dossier behauptet für sie keine Antwort.", ""]
    for row in report["requirements"]:
        lines += [f"## {'Erfüllt' if row['passed'] else 'Offen'}: {row['question']}", "", row["reason"], ""]
        lines += [f"- {CRITERIA[key]}: {'erfüllt' if row[key] else 'offen'}" for key in CRITERIA]
        lines += [f"- Noch benötigt: {gap}" for gap in row["missing"]]
        if row.get("accepted_gap_tasks"):
            lines += [f"- Akzeptierte Lücke: Teilfrage {tid}" for tid in row["accepted_gap_tasks"]]
        lines += [""]
    if report["blocking_gaps"]:
        lines += ["## Weitere offene Punkte", "", *[f"- {gap}" for gap in report["blocking_gaps"]], ""]
    if report.get("accepted_gaps"):
        lines += ["## Akzeptierte Lücken", ""]
        for gap in report["accepted_gaps"]:
            lines += [f"- {gap['question'] or gap['task_id']}" + (f": {gap['reason']}" if gap.get("reason") else "")]
        lines += [f"- {gap}" for gap in report.get("accepted_coverage_gaps", [])]
        lines += [""]
    if report.get("residual_objections"):
        lines += ["## Verbliebene Prüfeinwände zu akzeptierten Lücken", "",
                  *[f"- {objection}" for objection in report["residual_objections"]], ""]
    return "\n".join(lines)


def load_complete_research(work):
    folder = work / "complete_research"
    return (ResearchDiscovery.model_validate_json((folder / "discovery.json").read_text(encoding="utf-8")),
            SourceIndex.model_validate_json((folder / "source_index.json").read_text(encoding="utf-8")),
            json.loads((folder / "source_context.json").read_text(encoding="utf-8")),
            ResearchDossier.model_validate_json((folder / "dossier.json").read_text(encoding="utf-8")))
