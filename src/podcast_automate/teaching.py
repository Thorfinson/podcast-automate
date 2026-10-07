"""Source-grounded lesson design and evidence-backed checks of the actual dialogue."""
from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from typing import Literal

from pydantic import Field

from .prompts import instructions
from .errors import AppError
from .editorial import TEACHING_SCOPE, CONTINUITY, EPISODE_FRAMING, terminology
from .models import Contract, Identifier, LaterFields, NonEmpty
from .research_patches import corrected_call
from .script_advisories import humanised
from .script_models import ScriptIssue, episode_findings
from .storage import atomic_text, digest, write_json

TEACHING_VERSION = "teaching.v3"
DESIGN_VERSION = "teaching_design.v2"
# v3-goal (2026-10-02): the brief's series_goal, theory first, an optional misconception and limit, the finale as
# the series' synthesis, and the topic's own terminology instead of the machine-learning names. v4-arc (2026-10-06):
# the narrative arc (TeachingPlan.big_idea to callback), destination held back for the payoff, and entry questions
# that each previous scene leaves open. The review checks the arc; both repairs repeat the design prompt.
DESIGN_PROMPT_VERSION = "teaching_design.v4-arc"
DESIGN_REVIEW_VERSION = "teaching_design_review.v7-arc"
DESIGN_REPAIR_VERSION = "teaching_design_repair.v3-arc"
DESIGN_FOCUSED_REPAIR_VERSION = "teaching_design_focused_repair.v3-arc"
# Stored in every script-review checkpoint: bumping it makes a resumed in-flight run re-run the
# editorial review of its saved draft (draft and repair count are kept). That is intended whenever
# a composed fragment such as episode_framing.txt changes meaning.
EDITORIAL_REVIEW_VERSION = "editorial_review.v5-goal"
TEACHING_REVIEW_VERSION = "teaching_review.v5-goal"
LISTENER_VERSION = "listener_readback.v3-terms"
CRITERIA = ("orientation", "progression", "worked_example", "synthesis", "dialogue", "depth", "spoken_clarity")
# The bases a follow-up design review may give for a new issue that the code cannot check against earlier issues.
CRITICAL_BASIS = {"factual_error", "unsupported_claim", "source_contradiction", "objective_unreachable"}


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
    # Empty where the episode needs none (2026-10-02). Required, they pushed every example toward a study with a
    # measured result and a limit, also where a theory is explained through a qualitative case (the Asimov series).
    # A saved plan keeps its texts and its dump; no checkpoint binds this schema.
    misconception: str = Field(default="", description="A misconception listeners plausibly hold; empty when none.")
    correction: str = Field(default="", description="Its correction; empty exactly when misconception is empty.")
    limits: str = Field(default="", description="A limit of the example worth saying; empty when there is none.")


class Synthesis(Contract):
    premise_concept_ids: list[Identifier] = Field(min_length=2)
    reasoning_steps: list[NonEmpty] = Field(min_length=2)
    conclusion: NonEmpty
    transfer_question: NonEmpty


class ResearchGap(Contract):
    question: NonEmpty
    why_needed: NonEmpty
    kind: Literal["evidence", "editorial_context"] = "evidence"


class TeachingPlan(LaterFields):
    episode_id: Identifier
    learner_start: NonEmpty
    opening_problem: NonEmpty
    relevance: NonEmpty
    # Until 2026-10-06 "what the episode will establish", and the scripts announced it in their first minutes. The name
    # stays: an earlier episode's destination reaches later designs and their reviews (prerequisite_context).
    destination: NonEmpty = Field(description=(
        "The answer the episode arrives at. The plan knows it; the dialogue holds it back and earns it at the payoff."))
    # The narrative arc (2026-10-06: listeners of three finished series found the episodes "Fakten, Fakten, Fakten", a
    # teaching order without tension). Optional in the contract and checked by the design review, so a plan saved
    # before validates; at their defaults they are left out of every dump, so its checkpoint, its plan.json and every
    # prompt and review hash built from it stay as they were.
    big_idea: str = Field(default="", description="The one idea the whole episode serves, in one sentence.")
    hook_question: str = Field(default="", description=(
        "The episode's question as the listener first hears it: an open loop whose answer (destination) is held back."))
    first_answer: str = Field(default="", description=(
        "The plausible answer a listener would give before the episode, voiced as a guess, often by host_b; "
        "never presented as a finding."))
    turning_point: str = Field(default="", description=(
        "Where and how the findings in turning_finding_ids overturn, narrow or deepen the first answer."))
    turning_finding_ids: list[Identifier] = Field(default_factory=list, description=(
        "The episode's findings that bring the turning point about; empty exactly when turning_point is empty."))
    payoff: str = Field(default="", description="How the last scene answers hook_question with what the episode built.")
    callback: str = Field(default="", description="The concrete detail from the opening that the payoff picks up again.")
    objectives: list[LearningObjective] = Field(min_length=1)
    concepts: list[Concept] = Field(min_length=2)
    scenes: list[TeachingScene] = Field(min_length=1)
    worked_example: WorkedExample
    synthesis: Synthesis
    research_gaps: list[ResearchGap]

    LATER = {"big_idea": "", "hook_question": "", "first_answer": "", "turning_point": "", "turning_finding_ids": [],
             "payoff": "", "callback": ""}


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
    findings = set(episode_findings(entry))
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
    if bool(design.worked_example.misconception) != bool(design.worked_example.correction):
        errors.append("Worked example: give a misconception together with its correction, or neither.")
    # The arc stays optional here, so a plan saved before it still counts as reviewed (reviewed_design); the design
    # review asks for it. Where a plan names its turn, the turn rests on the episode's own findings (2026-10-06).
    if bool(design.turning_point) != bool(design.turning_finding_ids):
        errors.append("Give the turning_point together with the findings that bring it about (turning_finding_ids), "
                      "or neither.")
    if not set(design.turning_finding_ids) <= findings:
        errors.append("turning_finding_ids must name findings assigned to this episode.")
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


def _plain(text):
    return " ".join(text.casefold().split()).rstrip(" .;:!?")


def repeats_earlier(issue, previous):
    """Whether ``issue`` holds one of the ``previous`` issues: word for word, or quoted with what is still missing."""
    plain = _plain(issue)
    return any(earlier and earlier in plain for earlier in map(_plain, previous))


def scoped_review(review, previous, editor_note=None):
    """The issues a follow-up review blocks on, decided in code (2026-10-02; until then every issue blocked whatever
    basis the review gave it). An issue blocks when it holds an earlier issue, when its basis is the editor's note and
    there is one, or when its basis is a critical defect. Every other issue, such as a basis previous that quotes no
    earlier issue, becomes an advisory the writer receives and never enters the next round's scope. A first review,
    without earlier issues, sets the scope: all its issues block. Applying it twice changes nothing."""
    if not previous:
        return review
    # Without one basis per issue validate_design_review admits only repeated earlier issues.
    bases = review.issue_basis if len(review.issue_basis) == len(review.issues) else ["previous"] * len(review.issues)
    kept, kept_bases, demoted = [], [], []
    for issue, basis in zip(review.issues, bases):
        if repeats_earlier(issue, previous):
            basis = "previous"
        elif not ((basis == "editor_note" and editor_note) or basis in CRITICAL_BASIS):
            demoted.append(issue)
            continue
        kept.append(issue)
        kept_bases.append(basis)
    return review.model_copy(update={"issues": kept, "issue_basis": kept_bases,
                                     "advisories": list(dict.fromkeys([*review.advisories, *demoted]))})


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


def reviewed_design(folder, earlier):
    """The reviewed teaching plan an earlier episode saved in ``folder``, or None when it has none that passed."""
    try:
        design = TeachingPlan.model_validate_json((folder / "plan.json").read_text(encoding="utf-8"))
        review = TeachingPlanReview.model_validate_json((folder / "review.json").read_text(encoding="utf-8"))
        saved = json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))
        if (TeachingPlan.model_validate(saved["design"]) == design and
            TeachingPlanReview.model_validate(saved["review"]) == review and
            not validate_teaching_plan(design, earlier) and not review.issues and not review.research_gaps and
            not any(g.required_for_objective for g in review.gap_assessments)):
            return design
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def recorded_in_full(work, entry):
    """Whether the teaching stage recorded this episode's context with every prerequisite in full, as before
    2026-10-02. Such a run keeps that form: its script-review checkpoints bind the context
    (script_checks.script_review_signature), and a changed context would restart their reviews."""
    try:
        rows = json.loads((work / "teaching" / entry.episode_id / "continuity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(rows, list) and len(rows) > 1 and all(isinstance(r, dict) and "outline" in r for r in rows)


def prerequisite_context(plan, entry, work):
    """Use reviewed plans from this run; never substitute a future or unreviewed episode.

    Only the nearest prerequisite, the latest earlier episode this one names as its own, comes in full: its outline
    and reviewed design carry the example and the meanings this episode continues. Every other prerequisite comes
    as a summary: its question, series_role, findings and, once reviewed, its destination and spoken terms
    (2026-10-02: Asimov's finale builds on every earlier episode and got 474,000 characters of context, repeated in
    its design, polish, review and every repair). The rows depend only on the plan and the saved designs."""
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
    direct = [identifier for identifier in previous if identifier in entry.prerequisite_episodes]
    full = set(previous) if recorded_in_full(work, entry) else set(direct[-1:])
    rows = []
    for identifier, earlier in previous.items():
        if identifier not in required:
            continue
        design = reviewed_design(work / "teaching" / identifier, earlier)
        status = "reviewed_teaching_plan" if design is not None else "outline_only"
        if identifier in full:
            row = {"episode_id": identifier, "title": earlier.title, "status": status, "outline": earlier.model_dump()}
            if design is not None:
                row.update(teaching_design={
                    "destination": design.destination, "objectives": [g.model_dump() for g in design.objectives],
                    "concepts": [c.model_dump() for c in design.concepts],
                    "worked_example": design.worked_example.model_dump()},
                    established_terms=spoken_terms(design.concepts))
        else:
            row = {"episode_id": identifier, "title": earlier.title, "status": status, "context": "summary",
                   "central_question": earlier.central_question,
                   **({"series_role": earlier.series_role} if earlier.series_role else {}),
                   "finding_ids": list(earlier.finding_ids)}
            if design is not None:
                row.update(destination=design.destination, established_terms=spoken_terms(design.concepts))
        rows.append(row)
    return rows


def project_terminology(config):
    """The terminology rule for this project's topic and language (editorial.terminology)."""
    return terminology(config.language, config.topic, getattr(config, "central_question", ""))


def series_goal_field(config):
    """The brief's series_goal for a payload, only when the project sets one (2026-10-02: the teaching layer was the
    one stage that never saw it, and kept designing every episode around studies and their results)."""
    value = getattr(config, "series_goal", None)
    return {"series_goal": value} if value else {}


def design_prompt(config, entry, dossier, sources, continuity=None, *, series_context=None, editor_note=None):
    """``editor_note`` is the editor's instruction for a new design after the corrections failed
    (run_budget.request_teaching_redesign); without it the prompt is exactly what it was before."""
    return (
        project_terminology(config) + TEACHING_SCOPE + (CONTINUITY if continuity else "") + EPISODE_FRAMING +
        instructions("teaching_design") + (" " + instructions("teaching_editor_note") if editor_note else "") + "\n" + json.dumps({
            "brief": {"language": config.language, "audience": config.audience_level,
                      "prior_knowledge": config.prior_knowledge, "depth": config.depth_request,
                      **series_goal_field(config)},
            "episode": entry.model_dump(), "series_context": series_context,
            "findings": [f.model_dump() for f in dossier.findings if f.id in episode_findings(entry)],
            "synthesis": [r.model_dump() for r in dossier.synthesis if set(r.finding_ids) & set(episode_findings(entry))],
            "sources": sources, **({"prerequisite_context": continuity} if continuity else {}),
            **({"editor_note": editor_note} if editor_note else {})}, ensure_ascii=False))


def render_teaching_plan(design):
    lines = [f"# Lehrplan: {design.episode_id}", "", "## Ausgangspunkt", "", design.learner_start,
             "", design.opening_problem, "", design.relevance, "", design.destination, ""]
    # The arc since 2026-10-06; a plan without it renders as before.
    arc = [f"{label}: {text}" for label, text in (
        ("Große Idee", design.big_idea), ("Leitfrage", design.hook_question),
        ("Naheliegende erste Antwort", design.first_answer), ("Wendepunkt", design.turning_point),
        ("Auflösung", design.payoff), ("Rückgriff auf den Anfang", design.callback)) if text]
    if arc:
        lines.extend(["## Spannungsbogen", "", *(part for line in arc for part in (line, ""))])
    lines.extend(["## Lernziele", ""])
    for goal in design.objectives:
        lines.extend([f"### {goal.objective_id}: {goal.ability}", "", goal.question, "",
                      *[f"- {step}" for step in goal.expected_reasoning], ""])
    lines.extend(["## Gedankengang", ""])
    for scene in design.scenes:
        lines.extend([f"### {scene.scene_id}: {scene.entry_question}", "",
                      *[f"- {step}" for step in scene.reasoning_steps], "", scene.listener_can_now, ""])
    example = design.worked_example
    # A misconception and a limit are optional since 2026-10-02; a plan that has them reads as before.
    lines.extend(["## Durchgearbeitetes Beispiel", "", example.setup, "",
                  *[f"- {step}" for step in example.reasoning_steps], "",
                  *(["Mögliche Fehlvorstellung: " + example.misconception, "", example.correction, ""]
                    if example.misconception else []),
                  *(["Grenze: " + example.limits, ""] if example.limits else []),
                  "## Synthese und Übertragung", "",
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
                # A review saved before its scope was set in code is held to that scope too.
                review = scoped_review(review, review_previous, editor_note)
            except AppError:
                # Saved before its check ran: reviewed again instead of stopping every resume here.
                review = None
        if review is None and not errors:
            review_previous = list(scope)
            followup = "+followup" if review_previous else ""
            review = corrected_call(invoke,
                project_terminology(config) + TEACHING_SCOPE + CONTINUITY + EPISODE_FRAMING +
                instructions("teaching_design_review") +
                (" " + instructions("teaching_editor_note_review") if editor_note else "") +
                (" " + instructions("teaching_design_review_followup") if review_previous else "") + "\n" +
                json.dumps({"brief": {"audience": config.audience_level,
                    "prior_knowledge": config.prior_knowledge, "depth": config.depth_request,
                    **series_goal_field(config)},
                    "episode": entry.model_dump(), "design": design.model_dump(), "series_context": series_context,
                    "findings": [f.model_dump() for f in dossier.findings if f.id in episode_findings(entry)],
                    "sources": sources, "prerequisite_context": continuity or [],
                    **({"editor_note": editor_note} if editor_note else {}),
                    **({"previous_issues": review_previous} if review_previous else {})}, ensure_ascii=False),
                TeachingPlanReview, DESIGN_REVIEW_VERSION + noted + followup,
                lambda answer, previous=review_previous: validate_design_review(answer, design, previous))
            # Code, not the review, decides which of its issues still block (design rule, 2026-09-30).
            review = scoped_review(review, review_previous, editor_note)
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
                TeachingPlanRepair, DESIGN_FOCUSED_REPAIR_VERSION + noted,
                lambda answer: validate_focused_repair(answer, issues))
            focused_repair = True
            save()
            write_json(work / "focused_repair.json", repaired.model_dump())
            design, review = repaired.design, None
            save()
            continue
        design = invoke(prompt + "\n" + instructions("teaching_design_repair") + "\n" +
                        json.dumps({"design": design.model_dump(), "issues": issues}, ensure_ascii=False),
                        TeachingPlan, DESIGN_REPAIR_VERSION + noted)
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


def assess_teaching(script, design, invoke, directory: Path, *, audience, prior_knowledge, depth, series_context=None,
                    parallel=False, series_goal=None, language=None):
    """Fresh reader has only dialogue and questions. Examiner sees expected reasoning too.

    The listener and the editorial review read the script independently. With ``parallel`` (a run whose text
    work is parallel) both ask at once and the teaching review, which reads the listener's answers, follows:
    one after another they took about fifteen minutes per pass on the runs of 2026-09-29.

    ``series_goal`` (the brief's) reaches the editorial and the teaching review, which judge by it; the listener
    answers from the dialogue alone and never sees it. The terminology rule follows the series' topic and the
    project's ``language``; without a language a machine-learning topic keeps its English names."""
    context = series_context or {}
    terms = terminology(language, context.get("topic", ""), context.get("central_question", ""))
    aim = {"series_goal": series_goal} if series_goal else {}
    signature = digest({"version": TEACHING_VERSION, "script": script.model_dump(), "design": design.model_dump(),
                        "audience": audience, "prior_knowledge": prior_knowledge, "depth": depth,
                        "editorial": terms + TEACHING_SCOPE + EPISODE_FRAMING,
                        "series_context": series_context, **aim})
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
    def listen():
        return cached("listener", ListenerReadback,
            terms + TEACHING_SCOPE +
            instructions("listener_readback") + "\n" +
            json.dumps(reader_payload, ensure_ascii=False), LISTENER_VERSION,
            lambda answer: validate_readback(answer, design, script))

    def edit():
        return cached("editorial", EditorialReview,
            terms + TEACHING_SCOPE + EPISODE_FRAMING +
            instructions("editorial_review") + "\n" + json.dumps({"audience": audience, "prior_knowledge": prior_knowledge,
                "depth": depth, **aim, "series_context": series_context, "script": script.model_dump()}, ensure_ascii=False),
            EDITORIAL_REVIEW_VERSION, lambda answer: validate_editorial(answer, script))

    if parallel:
        # Each thread carries the caller's context, so every call still names its episode (execution.CALL_SUBJECT).
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="assess") as pool:
            listening, editing = pool.submit(copy_context().run, listen), pool.submit(copy_context().run, edit)
            reader, editorial = listening.result(), editing.result()
    else:
        reader, editorial = listen(), edit()
    review = cached("review", TeachingReview,
        terms + TEACHING_SCOPE + EPISODE_FRAMING +
        instructions("teaching_review") + "\n" +
        json.dumps({"audience": audience, "prior_knowledge": prior_knowledge, "depth": depth, **aim,
                    "design": design.model_dump(), "script": script.model_dump(), "series_context": series_context,
                    "listener": reader.model_dump()}, ensure_ascii=False), TEACHING_REVIEW_VERSION,
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
