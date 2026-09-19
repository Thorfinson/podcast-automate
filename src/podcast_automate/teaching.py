"""Source-grounded lesson design and evidence-backed checks of the actual dialogue."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from .prompts import instructions
from .errors import AppError
from .editorial import TERMINOLOGY, TEACHING_SCOPE, CONTINUITY, EPISODE_FRAMING
from .models import Contract, Identifier, NonEmpty
from .script_models import ScriptIssue
from .storage import atomic_text, digest, write_json

TEACHING_VERSION = "teaching.v3"
DESIGN_VERSION = "teaching_design.v2"
EDITORIAL_REVIEW_VERSION = "editorial_review.v3-series-context"
CRITERIA = ("orientation", "progression", "worked_example", "synthesis", "dialogue", "depth", "spoken_clarity")


class LearningObjective(Contract):
    objective_id: Identifier
    ability: NonEmpty
    question: NonEmpty
    expected_reasoning: list[NonEmpty] = Field(min_length=2)
    finding_ids: list[Identifier] = Field(min_length=1)


class Concept(Contract):
    concept_id: Identifier
    meaning: NonEmpty
    introduced_in: Identifier
    requires: list[Identifier]
    finding_ids: list[Identifier] = Field(min_length=1)


class TeachingScene(Contract):
    scene_id: Identifier
    entry_question: NonEmpty
    builds_on: list[Identifier]
    reasoning_steps: list[NonEmpty] = Field(min_length=1)
    listener_can_now: NonEmpty


class WorkedExample(Contract):
    scene_ids: list[Identifier] = Field(min_length=1)
    setup: NonEmpty
    reasoning_steps: list[NonEmpty] = Field(min_length=2)
    misconception: NonEmpty
    correction: NonEmpty
    limits: NonEmpty


class Synthesis(Contract):
    premise_concept_ids: list[Identifier] = Field(min_length=2)
    reasoning_steps: list[NonEmpty] = Field(min_length=2)
    conclusion: NonEmpty
    transfer_question: NonEmpty


class ResearchGap(Contract):
    question: NonEmpty
    why_needed: NonEmpty
    kind: Literal["evidence", "editorial_context"] = "evidence"


class TeachingPlan(Contract):
    episode_id: Identifier
    learner_start: NonEmpty
    opening_problem: NonEmpty
    relevance: NonEmpty
    destination: NonEmpty
    objectives: list[LearningObjective] = Field(min_length=1)
    concepts: list[Concept] = Field(min_length=2)
    scenes: list[TeachingScene] = Field(min_length=1)
    worked_example: WorkedExample
    synthesis: Synthesis
    research_gaps: list[ResearchGap]


class GapAssessment(Contract):
    gap: NonEmpty
    required_for_objective: bool
    reason: NonEmpty


class DesignGapAssessment(GapAssessment):
    kind: Literal["evidence", "editorial_context"] = "evidence"


class TeachingPlanReview(Contract):
    issues: list[NonEmpty]
    research_gaps: list[ResearchGap]
    gap_assessments: list[DesignGapAssessment]


class TeachingCorrection(Contract):
    issue: NonEmpty
    revised_passages: list[NonEmpty] = Field(min_length=1)


class TeachingPlanRepair(Contract):
    design: TeachingPlan
    corrections: list[TeachingCorrection] = Field(min_length=1)


class Passage(Contract):
    segment_id: Identifier
    quote: NonEmpty


class LearnerAnswer(Contract):
    objective_id: Identifier
    answer: NonEmpty
    reasoning_steps: list[NonEmpty]
    evidence: list[Passage]
    missing_explanations: list[NonEmpty]


class ListenerReadback(Contract):
    answers: list[LearnerAnswer]


class TeachingCheck(Contract):
    criterion: Literal["orientation", "progression", "worked_example", "synthesis", "dialogue", "depth", "spoken_clarity"]
    verdict: Literal["pass", "fail"]
    reason: NonEmpty
    evidence: list[Passage]


class ObjectiveCheck(Contract):
    objective_id: Identifier
    verdict: Literal["pass", "fail"]
    reason: NonEmpty
    evidence: list[Passage]
    gap_assessments: list[GapAssessment]


class TeachingReview(Contract):
    checks: list[TeachingCheck]
    objectives: list[ObjectiveCheck]
    limitations: list[str]


class EditorialReview(Contract):
    checks: list[TeachingCheck]
    limitations: list[str]


TEACHING_SCHEMAS = {"teaching_plan": TeachingPlan, "teaching_plan_review": TeachingPlanReview,
                    "teaching_plan_repair": TeachingPlanRepair, "listener_readback": ListenerReadback,
                    "teaching_review": TeachingReview, "editorial_review": EditorialReview}


def validate_teaching_plan(design, entry):
    errors = []
    scene_ids = [s.scene_id for s in entry.scenes]
    positions = {key: i for i, key in enumerate(scene_ids)}
    findings = set(entry.finding_ids)
    concepts = [c.concept_id for c in design.concepts]
    goals = [g.objective_id for g in design.objectives]
    if design.episode_id != entry.episode_id or [s.scene_id for s in design.scenes] != scene_ids:
        errors.append("Keep episode and scene IDs in the supplied order.")
    if len(set(concepts)) != len(concepts) or len(set(goals)) != len(goals):
        errors.append("Concept and objective IDs must be unique.")
    introduced = {}
    for concept in design.concepts:
        if concept.introduced_in not in positions or not set(concept.finding_ids) <= findings:
            errors.append(f"{concept.concept_id}: unknown scene or finding.")
        for required in concept.requires:
            if required not in introduced or positions.get(introduced[required], -1) > positions.get(concept.introduced_in, -1):
                errors.append(f"{concept.concept_id}: prerequisite {required} must be introduced first.")
        introduced[concept.concept_id] = concept.introduced_in
    for scene in design.scenes:
        if any(key not in positions or positions[key] >= positions.get(scene.scene_id, -1) for key in scene.builds_on):
            errors.append(f"{scene.scene_id}: build only on earlier scenes.")
        if positions.get(scene.scene_id, 0) > 0 and not scene.builds_on:
            errors.append(f"{scene.scene_id}: explain how this scene builds on earlier reasoning.")
    if any(not set(goal.finding_ids) <= findings for goal in design.objectives):
        errors.append("Learning objectives need known supporting findings.")
    if not set(design.worked_example.scene_ids) <= set(scene_ids):
        errors.append("Worked example refers to an unknown scene.")
    premises = design.synthesis.premise_concept_ids
    if len(set(premises)) < 2 or not set(premises) <= set(concepts):
        errors.append("Synthesis must connect at least two distinct taught concepts.")
    return errors


def prerequisite_context(plan, entry, work):
    """Use reviewed plans from this run; never substitute a future or unreviewed episode."""
    previous = {}
    for candidate in plan.episodes:
        if candidate.episode_id == entry.episode_id:
            break
        previous[candidate.episode_id] = candidate
    required = set(entry.prerequisite_episodes)
    pending = list(required)
    while pending:
        identifier = pending.pop()
        if identifier in previous:
            for prerequisite in previous[identifier].prerequisite_episodes:
                if prerequisite not in required:
                    required.add(prerequisite)
                    pending.append(prerequisite)
    rows = []
    for identifier, earlier in previous.items():
        if identifier not in required:
            continue
        row = {"episode_id": identifier, "title": earlier.title, "status": "outline_only",
               "outline": earlier.model_dump()}
        folder = work / "teaching" / identifier
        try:
            design = TeachingPlan.model_validate_json((folder / "plan.json").read_text(encoding="utf-8"))
            review = TeachingPlanReview.model_validate_json((folder / "review.json").read_text(encoding="utf-8"))
            saved = json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))
            if (TeachingPlan.model_validate(saved["design"]) == design and
                TeachingPlanReview.model_validate(saved["review"]) == review and
                not validate_teaching_plan(design, earlier) and not review.issues and not review.research_gaps and
                not any(g.required_for_objective for g in review.gap_assessments)):
                row.update(status="reviewed_teaching_plan", teaching_design={
                    "destination": design.destination, "objectives": [g.model_dump() for g in design.objectives],
                    "concepts": [c.model_dump() for c in design.concepts],
                    "worked_example": design.worked_example.model_dump()})
        except (OSError, ValueError, KeyError, TypeError):
            pass
        rows.append(row)
    return rows


def design_prompt(config, entry, dossier, sources, continuity=None, *, series_context=None):
    return (
        TERMINOLOGY + TEACHING_SCOPE + (CONTINUITY if continuity else "") + EPISODE_FRAMING +
        instructions("teaching_design") + "\n" + json.dumps({
            "brief": {"language": config.language, "audience": config.audience_level,
                      "prior_knowledge": config.prior_knowledge, "depth": config.depth_request},
            "episode": entry.model_dump(), "series_context": series_context,
            "findings": [f.model_dump() for f in dossier.findings if f.id in entry.finding_ids],
            "synthesis": [r.model_dump() for r in dossier.synthesis if set(r.finding_ids) & set(entry.finding_ids)],
            "sources": sources, **({"prerequisite_context": continuity} if continuity else {})}, ensure_ascii=False))


def render_teaching_plan(design):
    lines = [f"# Lehrplan: {design.episode_id}", "", "## Ausgangspunkt", "", design.learner_start,
             "", design.opening_problem, "", design.relevance, "", design.destination, "", "## Lernziele", ""]
    for goal in design.objectives:
        lines.extend([f"### {goal.objective_id}: {goal.ability}", "", goal.question, "",
                      *[f"- {step}" for step in goal.expected_reasoning], ""])
    lines.extend(["## Gedankengang", ""])
    for scene in design.scenes:
        lines.extend([f"### {scene.scene_id}: {scene.entry_question}", "",
                      *[f"- {step}" for step in scene.reasoning_steps], "", scene.listener_can_now, ""])
    lines.extend(["## Durchgearbeitetes Beispiel", "", design.worked_example.setup, "",
                  *[f"- {step}" for step in design.worked_example.reasoning_steps], "",
                  "Mögliche Fehlvorstellung: " + design.worked_example.misconception, "",
                  design.worked_example.correction, "", "Grenze: " + design.worked_example.limits,
                  "", "## Synthese und Übertragung", "",
                  *[f"- {step}" for step in design.synthesis.reasoning_steps], "",
                  design.synthesis.conclusion, "", design.synthesis.transfer_question, ""])
    return "\n".join(lines)


def build_teaching_plan(config, entry, dossier, sources, invoke, work, *, continuity=None, series_context=None):
    prompt = design_prompt(config, entry, dossier, sources, continuity, series_context=series_context)
    signature = digest({"version": DESIGN_VERSION, "prompt": prompt})
    checkpoint = work / "checkpoint.json"
    design, review, repairs = None, None, 0
    focused_repair = False
    reused_draft = False
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved.get("input_hash") == signature:
            design = TeachingPlan.model_validate(saved["design"])
            review = TeachingPlanReview.model_validate(saved["review"]) if saved["review"] else None
            repairs = saved["repairs"]
            focused_repair = saved.get("focused_repair", False)
        elif saved.get("design", {}).get("episode_id") == entry.episode_id:
            # A changed source/prompt requires a fresh independent review, not a wholesale rewrite.
            # The previous design is only a candidate; no old verdict or repair allowance is reused.
            design = TeachingPlan.model_validate(saved["design"])
            reused_draft = True

    def save():
        write_json(checkpoint, {"input_hash": signature, "design": design.model_dump(),
                               "review": review.model_dump() if review else None, "repairs": repairs,
                               "focused_repair": focused_repair})

    if design is None:
        design = invoke(prompt, TeachingPlan, "teaching_design.v1")
        save()
    elif reused_draft:
        save()
    while True:
        errors = validate_teaching_plan(design, entry)
        if review is None and not errors:
            review = invoke(
                TERMINOLOGY + TEACHING_SCOPE + CONTINUITY + EPISODE_FRAMING +
                instructions("teaching_design_review") + "\n" + json.dumps({"brief": {"audience": config.audience_level,
                    "prior_knowledge": config.prior_knowledge, "depth": config.depth_request},
                    "episode": entry.model_dump(), "design": design.model_dump(), "series_context": series_context,
                    "findings": [f.model_dump() for f in dossier.findings if f.id in entry.finding_ids],
                    "sources": sources, "prerequisite_context": continuity or []}, ensure_ascii=False),
                TeachingPlanReview, "teaching_design_review.v4-framing")
            save()
        if review is not None:
            reported = {g.question for g in design.research_gaps}
            assessed = [g.gap for g in review.gap_assessments]
            if set(assessed) != reported or len(assessed) != len(set(assessed)):
                raise AppError("Lehrplanprüfung muss jede Recherchefrage einordnen.", code="invalid_teaching_review", status="blocked")
        required = {g.gap: g for g in review.gap_assessments if g.required_for_objective} if review else {}
        assessed_gaps = [g.model_copy(update={"kind": required[g.question].kind})
                         for g in design.research_gaps if g.question in required]
        reported_gaps = [*assessed_gaps, *(review.research_gaps if review else [])]
        gaps = [g for g in reported_gaps if g.kind == "evidence"]
        if gaps:
            write_json(work / "research_needed.json", {"episode_id": entry.episode_id,
                       "questions": [g.model_dump() for g in gaps]})
            atomic_text(work / "research_needed.md", "# Recherche für die Erklärung ergänzen\n\n" +
                        "\n\n".join(f"- {g.question}\n\n  {g.why_needed}" for g in gaps) + "\n")
            raise AppError(f"Erforderliche Erklärgrundlagen fehlen: {work / 'research_needed.md'}",
                           code="teaching_research_required", status="blocked")
        issues = errors + (review.issues if review else []) + [
            "Redaktionellen Anschluss ergänzen: " + g.question + " " + g.why_needed
            for g in reported_gaps if g.kind == "editorial_context"]
        if not issues:
            break
        if repairs >= 2:
            if focused_repair:
                raise AppError("Das Lehrkonzept hat auch nach der gezielten automatischen Korrektur noch offene Punkte: " +
                               " ".join(issues), code="teaching_design_failed", status="blocked")
            repaired = invoke(prompt + "\n" + instructions("teaching_design_focused_repair") + "\n" +
                json.dumps({"design": design.model_dump(), "issues": issues}, ensure_ascii=False),
                TeachingPlanRepair, "teaching_design_focused_repair.v1")
            focused_repair = True
            save()
            write_json(work / "focused_repair.json", repaired.model_dump())
            reported = [correction.issue for correction in repaired.corrections]
            def strings(value):
                if isinstance(value, str):
                    yield value
                elif isinstance(value, dict):
                    for item in value.values():
                        yield from strings(item)
                elif isinstance(value, list):
                    for item in value:
                        yield from strings(item)
            passages = list(strings(repaired.design.model_dump()))
            if (sorted(reported) != sorted(issues) or
                any(not any(quote in text for text in passages)
                    for correction in repaired.corrections for quote in correction.revised_passages)):
                raise AppError("Die gezielte Korrektur muss jeden Kritikpunkt mit Text aus dem überarbeiteten "
                               "Lehrkonzept belegen.", code="invalid_teaching_repair", status="blocked")
            design, review = repaired.design, None
            save()
            continue
        design = invoke(prompt + "\n" + instructions("teaching_design_repair") + "\n" +
                        json.dumps({"design": design.model_dump(), "issues": issues}, ensure_ascii=False),
                        TeachingPlan, "teaching_design_repair.v1")
        repairs += 1
        review = None
        save()
    write_json(work / "plan.json", design.model_dump())
    write_json(work / "review.json", review.model_dump())
    limits = [g.reason for g in review.gap_assessments if not g.required_for_objective]
    atomic_text(work / "plan.md", render_teaching_plan(design) +
                ("\n## Eingeordnete Forschungsgrenzen\n\n" + "\n".join(f"- {s}" for s in limits) + "\n" if limits else ""))
    gap_path = work / "research_needed.json"
    if gap_path.exists():
        gaps = json.loads(gap_path.read_text(encoding="utf-8"))
        gaps["resolved"] = True
        write_json(gap_path, gaps)
        atomic_text(work / "research_needed.md", "# Recherchefragen geklärt\n\n"
                    "Die Lehrplanung wurde mit den verfügbaren Quellen erneut geprüft und angenommen.\n\n" +
                    "\n".join(f"- {g['question']}" for g in gaps["questions"]) + "\n")
    files = [work / "plan.json", work / "review.json", work / "plan.md", checkpoint]
    if focused_repair:
        files.append(work / "focused_repair.json")
    return design, files


def _check_passages(script, passages):
    texts = {s.segment_id: s.text for s in script.segments}
    if any(p.segment_id not in texts or p.quote not in texts[p.segment_id] for p in passages):
        raise AppError("Lehrprüfung zitiert Text, der im Skript nicht vorkommt.", code="invalid_teaching_evidence", status="blocked")


def validate_readback(readback, design, script):
    expected = {g.objective_id for g in design.objectives}
    ids = [a.objective_id for a in readback.answers]
    if set(ids) != expected or len(ids) != len(expected):
        raise AppError("Leseprüfung muss jedes Lernziel genau einmal prüfen.", code="invalid_teaching_review", status="blocked")
    for answer in readback.answers:
        _check_passages(script, answer.evidence)
        if not answer.missing_explanations and (not answer.evidence or not answer.reasoning_steps):
            raise AppError("Leseprüfung meldet Verständnis ohne Textbeleg oder Denkweg.", code="invalid_teaching_review", status="blocked")


def validate_teaching_review(review, design, script, reader=None):
    ids = [c.criterion for c in review.checks]
    goals = [c.objective_id for c in review.objectives]
    expected = {g.objective_id for g in design.objectives}
    if set(ids) != set(CRITERIA) or len(ids) != len(CRITERIA) or set(goals) != expected or len(goals) != len(expected):
        raise AppError("Lehrprüfung lässt Kriterien oder Lernziele aus.", code="invalid_teaching_review", status="blocked")
    for item in [*review.checks, *review.objectives]:
        _check_passages(script, item.evidence)
        if item.verdict == "pass" and not item.evidence:
            raise AppError("Bestandene Lehrprüfung benötigt konkrete Textbelege.", code="invalid_teaching_evidence", status="blocked")
    if reader is not None:
        gaps = {a.objective_id: a.missing_explanations for a in reader.answers}
        for item in review.objectives:
            assessed = [g.gap for g in item.gap_assessments]
            if set(assessed) != set(gaps[item.objective_id]) or len(assessed) != len(set(assessed)):
                raise AppError("Jede gemeldete Erklärungslücke benötigt eine begründete Einordnung.",
                               code="invalid_teaching_review", status="blocked")


def assess_teaching(script, design, invoke, directory: Path, *, audience, prior_knowledge, depth, series_context=None):
    """Fresh reader has only dialogue and questions. Examiner sees expected reasoning too."""
    signature = digest({"version": TEACHING_VERSION, "script": script.model_dump(), "design": design.model_dump(),
                        "audience": audience, "prior_knowledge": prior_knowledge, "depth": depth,
                        "editorial": TERMINOLOGY + TEACHING_SCOPE + EPISODE_FRAMING,
                        "series_context": series_context})
    work = directory / signature

    def cached(name, output_type, prompt, version):
        path = work / f"{name}.json"
        prompt_hash = digest({"prompt": prompt, "version": version})
        if path.exists():
            stamp = work / f"{name}.checkpoint.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            if stamp.exists() and json.loads(stamp.read_text(encoding="utf-8")) == {
                    "digest": digest(raw), "prompt_hash": prompt_hash}:
                return output_type.model_validate(raw)
        result = invoke(prompt, output_type, version)
        write_json(path, result.model_dump())
        write_json(work / f"{name}.checkpoint.json", {"digest": digest(result.model_dump()), "prompt_hash": prompt_hash})
        return result

    reader_payload = {"audience": audience, "prior_knowledge": prior_knowledge,
                      "script": script.model_dump(),
                      "questions": [{"objective_id": g.objective_id, "question": g.question} for g in design.objectives]}
    reader = cached("listener", ListenerReadback,
        TERMINOLOGY + TEACHING_SCOPE +
        instructions("listener_readback") + "\n" +
        json.dumps(reader_payload, ensure_ascii=False), "listener_readback.v2")
    validate_readback(reader, design, script)
    editorial = cached("editorial", EditorialReview,
        TERMINOLOGY + TEACHING_SCOPE + EPISODE_FRAMING +
        instructions("editorial_review") + "\n" + json.dumps({"audience": audience, "prior_knowledge": prior_knowledge,
            "depth": depth, "series_context": series_context, "script": script.model_dump()}, ensure_ascii=False),
        EDITORIAL_REVIEW_VERSION)
    criteria = [c.criterion for c in editorial.checks]
    if set(criteria) != set(CRITERIA) or len(criteria) != len(CRITERIA):
        raise AppError("Redaktionelle Prüfung lässt Kriterien aus.", code="invalid_teaching_review", status="blocked")
    for item in editorial.checks:
        _check_passages(script, item.evidence)
        if item.verdict == "pass" and not item.evidence:
            raise AppError("Redaktionelles Urteil benötigt Textbelege.", code="invalid_teaching_evidence", status="blocked")
    review = cached("review", TeachingReview,
        TERMINOLOGY + TEACHING_SCOPE + EPISODE_FRAMING +
        instructions("teaching_review") + "\n" +
        json.dumps({"audience": audience, "prior_knowledge": prior_knowledge, "depth": depth,
                    "design": design.model_dump(), "script": script.model_dump(), "series_context": series_context,
                    "listener": reader.model_dump()}, ensure_ascii=False), "teaching_review.v3-framing")
    validate_teaching_review(review, design, script, reader)
    issues = []
    for item in [*editorial.checks, *review.checks, *review.objectives]:
        if item.verdict == "fail":
            name = getattr(item, "criterion", None) or item.objective_id
            issues.append(ScriptIssue(category="depth", segment_ids=list(dict.fromkeys(p.segment_id for p in item.evidence)),
                                      reason=f"{name}: {item.reason}"))
    for objective in review.objectives:
        essential = [g.gap for g in objective.gap_assessments if g.required_for_objective]
        if essential:
            issues.append(ScriptIssue(category="clarity", segment_ids=list(dict.fromkeys(p.segment_id for p in objective.evidence)),
                                      reason=f"{objective.objective_id}: " + "; ".join(essential)))
    report = {"version": TEACHING_VERSION, "status": "passed" if not issues else "needs_revision",
              "script_digest": digest(script.model_dump()), "design_digest": digest(design.model_dump()),
              "listener": reader.model_dump(), "review": review.model_dump(),
              "editorial": editorial.model_dump(),
              "human_learning_validated": False, "issues": [i.model_dump() for i in issues]}
    write_json(work / "result.json", report)
    return issues, report, list(work.glob("*.json"))
