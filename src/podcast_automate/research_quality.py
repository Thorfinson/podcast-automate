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


def quality_report(config, dossier, discovery, index, assessment, grounding_issues=()):
    requirements = requirements_for(config)
    expected = {r["id"] for r in requirements}
    if Counter(r.requirement_id for r in assessment.requirements) != Counter(expected):
        raise AppError("Die Qualitätsprüfung muss jede ursprüngliche Leitfrage genau einmal bewerten.",
                       code="invalid_research_assessment", status="blocked")
    findings = {f.id: f for f in dossier.findings}
    external = {s.id for s in index.sources if s.url and s.final_url}
    rows = []
    for requirement in requirements:
        result = next(r for r in assessment.requirements if r.requirement_id == requirement["id"])
        if not set(result.finding_ids) <= findings.keys():
            raise AppError("Die Qualitätsprüfung verweist auf unbekannte Befunde.",
                           code="invalid_research_assessment", status="blocked")
        evidence_backed = any(f.kind != "limitation" and any(e.reference.split("#")[0] in external for e in f.evidence)
                              for f in (findings[fid] for fid in result.finding_ids))
        missing = list(result.missing)
        if not evidence_backed:
            missing.append("Es fehlt eine inhaltliche Antwort mit unabhängig abgerufenem Textbeleg.")
        passed = all(getattr(result, key) for key in CRITERIA) and not missing
        rows.append({**requirement, **result.model_dump(), "passed": passed, "missing": missing})
    questions = {q.id: q.question for q in discovery.questions}
    gaps = [f"{questions[c.question_id]}: {c.gap}" for c in dossier.coverage if c.status != "answered" or c.gap]
    gaps.extend(dossier.open_questions)
    gaps.extend(assessment.issues)
    gaps.extend(grounding_issues)
    return {"version": QUALITY_VERSION, "passed": all(r["passed"] for r in rows) and not gaps,
            "criteria": CRITERIA, "requirements": rows, "closed": sum(r["passed"] for r in rows),
            "total": len(rows), "blocking_gaps": list(dict.fromkeys(gaps)),
            "dossier_hash": digest(dossier.model_dump()), "brief_hash": digest(requirements),
            "scope": "Alle vereinbarten Leitfragen; keine Behauptung abschließenden Wissens über das gesamte Fachgebiet."}


def render_quality(report):
    lines = ["# Recherchequalität", "", f"{report['closed']} von {report['total']} Leitfragen erfüllen alle Qualitätsmerkmale.",
             "", *[f"- {title}" for title in CRITERIA.values()], ""]
    if report.get("assessment_status") == "pending_after_source_review":
        lines += ["Die Quellenprüfung hat fehlende Belege gefunden. Zuerst wird gezielt nachrecherchiert; "
                  "die Bewertung aller Leitfragen wird danach erneuert. Die bisherigen Einzelbewertungen "
                  "sind noch kein Urteil über den ergänzten Entwurf.", ""]
    for row in report["requirements"]:
        lines += [f"## {'Erfüllt' if row['passed'] else 'Offen'}: {row['question']}", "", row["reason"], ""]
        lines += [f"- {CRITERIA[key]}: {'erfüllt' if row[key] else 'offen'}" for key in CRITERIA]
        lines += [f"- Noch benötigt: {gap}" for gap in row["missing"]]
        lines += [""]
    if report["blocking_gaps"]:
        lines += ["## Weitere offene Punkte", "", *[f"- {gap}" for gap in report["blocking_gaps"]], ""]
    return "\n".join(lines)


def load_complete_research(work):
    folder = work / "complete_research"
    return (ResearchDiscovery.model_validate_json((folder / "discovery.json").read_text(encoding="utf-8")),
            SourceIndex.model_validate_json((folder / "source_index.json").read_text(encoding="utf-8")),
            json.loads((folder / "source_context.json").read_text(encoding="utf-8")),
            ResearchDossier.model_validate_json((folder / "dossier.json").read_text(encoding="utf-8")))
