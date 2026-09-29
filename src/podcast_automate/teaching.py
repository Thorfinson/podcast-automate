"""Source-grounded lesson design and evidence-backed checks of the actual dialogue."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field

from .prompts import instructions
from .errors import AppError
from .editorial import TERMINOLOGY, TEACHING_SCOPE, CONTINUITY, EPISODE_FRAMING
from .models import Contract, Identifier, NonEmpty
from .research_patches import corrected_call
from .script_advisories import humanised
from .script_models import ScriptIssue
from .storage import atomic_text, digest, write_json

TEACHING_VERSION = "teaching.v3"
DESIGN_VERSION = "teaching_design.v2"
DESIGN_PROMPT_VERSION = "teaching_design.v2-terms"
DESIGN_REVIEW_VERSION = "teaching_design_review.v5-terms"
# Stored in every script-review checkpoint: bumping it makes a resumed in-flight run re-run the
# editorial review of its saved draft (draft and repair count are kept). That is intended whenever
# a composed fragment such as episode_framing.txt changes meaning.
EDITORIAL_REVIEW_VERSION = "editorial_review.v4-audit"
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
    terms: list[NonEmpty] = Field(default_factory=list,
        description="Spoken names of this concept in the episode's language; the ID is not spoken.")
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


# Why an issue of a follow-up review may still block (see build_teaching_plan and teaching_design_review_followup).
IssueBasis = Literal["previous", "editor_note", "factual_error", "unsupported_claim", "source_contradiction",
                     "objective_unreachable"]


class TeachingPlanReview(Contract):
    issues: list[NonEmpty]
    research_gaps: list[ResearchGap]
    gap_assessments: list[DesignGapAssessment]
    # Follow-up reviews only: one basis per issue, in order; everything else new is an advisory for the writer.
    issue_basis: list[IssueBasis] = Field(default_factory=list)
    advisories: list[NonEmpty] = Field(default_factory=list)


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


def validate_design_review(review, design, previous=()):
    """``previous`` are the issues earlier reviews of the episode raised; with them the review is a follow-up,
    and every issue it still lets block names its basis."""
    reported = {g.question for g in design.research_gaps}
    assessed = [g.gap for g in review.gap_assessments]
    if set(assessed) != reported or len(assessed) != len(set(assessed)):
        raise AppError("Lehrplanprüfung muss jede Recherchefrage einordnen.", code="invalid_teaching_review", status="blocked")
    # A previous issue repeated word for word is its own basis; any new one needs every issue's basis.
    fresh = [issue for issue in review.issues if issue not in set(previous)]
    if previous and fresh and len(review.issue_basis) != len(review.issues):
        raise AppError("Give issue_basis for every issue, in the same order: previous, editor_note or a critical defect. "
                       "Move every other new observation to advisories.", code="invalid_teaching_review", status="blocked")


def review_scope(work):
    """Every issue earlier design reviews of this episode raised. A follow-up review may block only on these, on
    the editor's note or on a new critical defect, so the review converges instead of finding new details in each
    new draft (Ontologies, 2026-09-28: three redesigns of ep_001, each passing the last points and failing on new
    ones). Kept across redesigns; seeded once from the reviews of the designs a redesign set aside."""
    path = work / "review_scope.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    issues = []
    for archived in sorted(work.glob("redesign_*/checkpoint.json")):
        issues += (json.loads(archived.read_text(encoding="utf-8")).get("review") or {}).get("issues", [])
    return list(dict.fromkeys(issues))


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def validate_focused_repair(repaired, issues):
    passages = list(strings(repaired.design.model_dump()))
    if (sorted(c.issue for c in repaired.corrections) != sorted(issues) or
            any(not any(quote in text for text in passages)
                for correction in repaired.corrections for quote in correction.revised_passages)):
        raise AppError("Die gezielte Korrektur muss jeden Kritikpunkt mit Text aus dem überarbeiteten "
                       "Lehrkonzept belegen.", code="invalid_teaching_repair", status="blocked")


def spoken_terms(concepts):
    """What an earlier episode actually called its concepts; older plans fall back to the ID."""
    found = [term for concept in concepts for term in (concept.terms or [humanised(concept.concept_id)])]
    return list(dict.fromkeys(term for term in (t.strip() for t in found) if term))


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
                    "worked_example": design.worked_example.model_dump()},
                    established_terms=spoken_terms(design.concepts))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        rows.append(row)
    return rows


def design_prompt(config, entry, dossier, sources, continuity=None, *, series_context=None, editor_note=None):
    """``editor_note`` is the editor's instruction for a new design after the corrections failed
    (run_budget.request_teaching_redesign); without it the prompt is exactly what it was before."""
    return (
        TERMINOLOGY + TEACHING_SCOPE + (CONTINUITY if continuity else "") + EPISODE_FRAMING +
        instructions("teaching_design") + (" " + instructions("teaching_editor_note") if editor_note else "") + "\n" + json.dumps({
            "brief": {"language": config.language, "audience": config.audience_level,
                      "prior_knowledge": config.prior_knowledge, "depth": config.depth_request},
            "episode": entry.model_dump(), "series_context": series_context,
            "findings": [f.model_dump() for f in dossier.findings if f.id in entry.finding_ids],
            "synthesis": [r.model_dump() for r in dossier.synthesis if set(r.finding_ids) & set(entry.finding_ids)],
            "sources": sources, **({"prerequisite_context": continuity} if continuity else {}),
            **({"editor_note": editor_note} if editor_note else {})}, ensure_ascii=False))


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


def build_teaching_plan(config, entry, dossier, sources, invoke, work, *, continuity=None, series_context=None,
                        editor_note=None):
    prompt = design_prompt(config, entry, dossier, sources, continuity, series_context=series_context,
                           editor_note=editor_note)
    noted = "+note" if editor_note else ""
    signature = digest({"version": DESIGN_VERSION, "prompt": prompt})
    checkpoint = work / "checkpoint.json"
    design, review, repairs = None, None, 0
    focused_repair = False
    reused_draft = False
    scope = review_scope(work)
    # The earlier issues the saved review was given; a review saved before this rule is judged against the scope.
    review_previous = list(scope)
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved.get("input_hash") == signature:
            design = TeachingPlan.model_validate(saved["design"])
            review = TeachingPlanReview.model_validate(saved["review"]) if saved["review"] else None
            repairs = saved["repairs"]
            focused_repair = saved.get("focused_repair", False)
            review_previous = saved.get("review_previous", review_previous)
        elif saved.get("design", {}).get("episode_id") == entry.episode_id:
            # A changed source/prompt requires a fresh independent review, not a wholesale rewrite.
            # The previous design is only a candidate; no old verdict or repair allowance is reused.
            design = TeachingPlan.model_validate(saved["design"])
            reused_draft = True

    def save():
        write_json(checkpoint, {"input_hash": signature, "design": design.model_dump(),
                               "review": review.model_dump() if review else None, "repairs": repairs,
                               "focused_repair": focused_repair, "review_previous": review_previous})

    if design is None:
        design = invoke(prompt, TeachingPlan, DESIGN_PROMPT_VERSION + noted)
        save()
    elif reused_draft:
        save()
    while True:
        errors = validate_teaching_plan(design, entry)
        if review is not None:
            try:
                validate_design_review(review, design, review_previous)
            except AppError:
                # Saved before its check ran: reviewed again instead of stopping every resume here.
                review = None
        if review is None and not errors:
            review_previous = list(scope)
            followup = "+followup" if review_previous else ""
            review = corrected_call(invoke,
                TERMINOLOGY + TEACHING_SCOPE + CONTINUITY + EPISODE_FRAMING +
                instructions("teaching_design_review") +
                (" " + instructions("teaching_editor_note_review") if editor_note else "") +
                (" " + instructions("teaching_design_review_followup") if review_previous else "") + "\n" +
                json.dumps({"brief": {"audience": config.audience_level,
                    "prior_knowledge": config.prior_knowledge, "depth": config.depth_request},
                    "episode": entry.model_dump(), "design": design.model_dump(), "series_context": series_context,
                    "findings": [f.model_dump() for f in dossier.findings if f.id in entry.finding_ids],
                    "sources": sources, "prerequisite_context": continuity or [],
                    **({"editor_note": editor_note} if editor_note else {}),
                    **({"previous_issues": review_previous} if review_previous else {})}, ensure_ascii=False),
                TeachingPlanReview, DESIGN_REVIEW_VERSION + noted + followup,
                lambda answer, previous=review_previous: validate_design_review(answer, design, previous))
            scope = list(dict.fromkeys([*scope, *review.issues]))
            write_json(work / "review_scope.json", scope)
            save()
        required ={g.gap: g for g in review.gap_assessments if g.required_for_objective} if review else {}
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
            repaired = corrected_call(invoke, prompt + "\n" + instructions("teaching_design_focused_repair") + "\n" +
                json.dumps({"design": design.model_dump(), "issues": issues}, ensure_ascii=False),
                TeachingPlanRepair, "teaching_design_focused_repair.v1" + noted,
                lambda answer: validate_focused_repair(answer, issues))
            focused_repair = True
            save()
            write_json(work / "focused_repair.json", repaired.model_dump())
            design, review = repaired.design, None
            save()
            continue
        design = invoke(prompt + "\n" + instructions("teaching_design_repair") + "\n" +
                        json.dumps({"design": design.model_dump(), "issues": issues}, ensure_ascii=False),
                        TeachingPlan, "teaching_design_repair.v1" + noted)
        repairs += 1
        review = None
        save()
    write_json(work / "plan.json", design.model_dump())
    write_json(work / "review.json", review.model_dump())
    write_json(work / "dismissed_gaps.json", dismissed_design_gaps(review))
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
    files = [work / "plan.json", work / "review.json", work / "dismissed_gaps.json", work / "plan.md", checkpoint]
    if focused_repair:
        files.append(work / "focused_repair.json")
    return design, files


def dismissed_design_gaps(review):
    """Research questions the design review judged inessential, with the reason it gave."""
    return [{"stage": "teaching_design", "objective_id": None, "gap": g.gap, "reason": g.reason}
            for g in review.gap_assessments if not g.required_for_objective]


def dismissed_objective_gaps(review):
    """Explanation gaps the listener reported that the examiner judged inessential."""
    return [{"stage": "teaching_review", "objective_id": objective.objective_id, "gap": g.gap, "reason": g.reason}
            for objective in review.objectives for g in objective.gap_assessments if not g.required_for_objective]


def _check_passages(script, passages):
    texts = {s.segment_id: s.text for s in script.segments}
    wrong = [p for p in passages if p.segment_id not in texts or p.quote not in texts[p.segment_id]]
    if wrong:
        raise AppError("Lehrprüfung zitiert Text, der im Skript nicht vorkommt. Nicht wörtlich enthalten: " +
                       "; ".join(f"{p.segment_id}: „{p.quote[:80]}“" for p in wrong[:3]) + ".",
                       code="invalid_teaching_evidence", status="blocked")


def id_defects(given, expected):
    """What a list of IDs lacks or repeats against the IDs it must name exactly once, for a correction."""
    counts = Counter(given)
    parts = [(label, items) for label, items in (
        ("Fehlend", [key for key in expected if key not in counts]),
        ("Doppelt", sorted(key for key, n in counts.items() if n > 1)),
        ("Unbekannt", sorted(key for key in counts if key not in set(expected))),
    ) if items]
    return "".join(f" {label}: {', '.join(items).rstrip('.')}." for label, items in parts)


def validate_readback(readback, design, script):
    expected = [g.objective_id for g in design.objectives]
    ids = [a.objective_id for a in readback.answers]
    if set(ids) != set(expected) or len(ids) != len(expected):
        raise AppError("Leseprüfung muss jedes Lernziel genau einmal prüfen." + id_defects(ids, expected),
                       code="invalid_teaching_review", status="blocked")
    for answer in readback.answers:
        _check_passages(script, answer.evidence)
        if not answer.missing_explanations and (not answer.evidence or not answer.reasoning_steps):
            raise AppError(f"Leseprüfung meldet Verständnis ohne Textbeleg oder Denkweg ({answer.objective_id}).",
                           code="invalid_teaching_review", status="blocked")


def validate_editorial(editorial, script):
    criteria = [c.criterion for c in editorial.checks]
    if set(criteria) != set(CRITERIA) or len(criteria) != len(CRITERIA):
        raise AppError("Redaktionelle Prüfung lässt Kriterien aus." + id_defects(criteria, CRITERIA),
                       code="invalid_teaching_review", status="blocked")
    for item in editorial.checks:
        _check_passages(script, item.evidence)
        if item.verdict == "pass" and not item.evidence:
            raise AppError(f"Redaktionelles Urteil benötigt Textbelege ({item.criterion}).",
                           code="invalid_teaching_evidence", status="blocked")


def validate_teaching_review(review, design, script, reader=None):
    ids = [c.criterion for c in review.checks]
    goals = [c.objective_id for c in review.objectives]
    expected = [g.objective_id for g in design.objectives]
    if set(ids) != set(CRITERIA) or len(ids) != len(CRITERIA) or set(goals) != set(expected) or len(goals) != len(expected):
        raise AppError("Lehrprüfung lässt Kriterien oder Lernziele aus." + id_defects(ids, CRITERIA) +
                       id_defects(goals, expected), code="invalid_teaching_review", status="blocked")
    for item in [*review.checks, *review.objectives]:
        _check_passages(script, item.evidence)
        if item.verdict == "pass" and not item.evidence:
            raise AppError("Bestandene Lehrprüfung benötigt konkrete Textbelege "
                           f"({getattr(item, 'criterion', None) or item.objective_id}).",
                           code="invalid_teaching_evidence", status="blocked")
    if reader is not None:
        gaps = {a.objective_id: a.missing_explanations for a in reader.answers}
        for item in review.objectives:
            assessed = [g.gap for g in item.gap_assessments]
            if set(assessed) != set(gaps[item.objective_id]) or len(assessed) != len(set(assessed)):
                raise AppError(f"Jede gemeldete Erklärungslücke benötigt eine begründete Einordnung ({item.objective_id})."
                               + id_defects(assessed, gaps[item.objective_id]),
                               code="invalid_teaching_review", status="blocked")


def assess_teaching(script, design, invoke, directory: Path, *, audience, prior_knowledge, depth, series_context=None):
    """Fresh reader has only dialogue and questions. Examiner sees expected reasoning too."""
    signature = digest({"version": TEACHING_VERSION, "script": script.model_dump(), "design": design.model_dump(),
                        "audience": audience, "prior_knowledge": prior_knowledge, "depth": depth,
                        "editorial": TERMINOLOGY + TEACHING_SCOPE + EPISODE_FRAMING,
                        "series_context": series_context})
    work = directory / signature

    def cached(name, output_type, prompt, version, check):
        """A stored answer ``check`` rejects was saved before the check ran: it is asked again, not replayed."""
        path = work / f"{name}.json"
        prompt_hash = digest({"prompt": prompt, "version": version})
        if path.exists():
            stamp = work / f"{name}.checkpoint.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            if stamp.exists() and json.loads(stamp.read_text(encoding="utf-8")) == {
                    "digest": digest(raw), "prompt_hash": prompt_hash}:
                result = output_type.model_validate(raw)
                try:
                    check(result)
                    return result
                except AppError:
                    pass
        result = corrected_call(invoke, prompt, output_type, version, check)
        write_json(path, result.model_dump())
        write_json(work / f"{name}.checkpoint.json", {"digest": digest(result.model_dump()), "prompt_hash": prompt_hash})
        return result

    reader_payload = {"audience": audience, "prior_knowledge": prior_knowledge,
                      "script": script.model_dump(),
                      "questions": [{"objective_id": g.objective_id, "question": g.question} for g in design.objectives]}
    reader = cached("listener", ListenerReadback,
        TERMINOLOGY + TEACHING_SCOPE +
        instructions("listener_readback") + "\n" +
        json.dumps(reader_payload, ensure_ascii=False), "listener_readback.v2",
        lambda answer: validate_readback(answer, design, script))
    editorial = cached("editorial", EditorialReview,
        TERMINOLOGY + TEACHING_SCOPE + EPISODE_FRAMING +
        instructions("editorial_review") + "\n" + json.dumps({"audience": audience, "prior_knowledge": prior_knowledge,
            "depth": depth, "series_context": series_context, "script": script.model_dump()}, ensure_ascii=False),
        EDITORIAL_REVIEW_VERSION, lambda answer: validate_editorial(answer, script))
    review = cached("review", TeachingReview,
        TERMINOLOGY + TEACHING_SCOPE + EPISODE_FRAMING +
        instructions("teaching_review") + "\n" +
        json.dumps({"audience": audience, "prior_knowledge": prior_knowledge, "depth": depth,
                    "design": design.model_dump(), "script": script.model_dump(), "series_context": series_context,
                    "listener": reader.model_dump()}, ensure_ascii=False), "teaching_review.v4-audit",
        lambda answer: validate_teaching_review(answer, design, script, reader))
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
              "human_learning_validated": False, "issues": [i.model_dump() for i in issues],
              "dismissed_gaps": dismissed_objective_gaps(review)}
    write_json(work / "result.json", report)
    return issues, report, list(work.glob("*.json"))
