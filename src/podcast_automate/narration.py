"""Story material and the narration review of a script run (D-180).

The listener's verdict on Orlagau (2026-10-21): no episode was really good, and the striking things were dealt with in
one or two sentences. The run files showed why. The research kept the number a criterion asked for and left the scene
beside it in its source section; each episode covered 13 to 26 findings in about 300 spoken words each; and no review
asked whether the episode tells. So a run with the narration stage

- collects each episode's story material from the source sections its findings cite (``story_material``), before the
  teaching design, which then chooses a core story to tell first;
- reviews each draft for tension, interest and storytelling between writing and polishing (``narrate_episode``),
  reworks the segments the review names from that material, and reviews the rework once more within the changed
  segments.

The narration review never stops a run: what it leaves open, and the story the material does not hold, is reported with
the episode's review notes. The evidence review after polishing still checks every word, so a reworked story cannot
bring in a detail its source does not carry. A run started before the stage keeps its stage list and skips all of it.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import Field

from .editorial import LISTENABILITY
from .errors import AppError
from .models import Contract, EpisodeScript, Identifier, NonEmpty
from .prompts import fragment, instructions
from .research_evidence import verbatim
from .script_artifacts import script_metrics
from .script_models import episode_findings
from .storage import digest, write_json
from .teaching import TeachingPlan, project_terminology

NARRATION_VERSION = "narration.v1"
# v2-missing: also the stories the sections only point to, for the story research (2026-10-23).
STORY_MATERIAL_VERSION = "story_material.v2-missing"
NARRATION_REVIEW_VERSION = "narration_review.v1"
NARRATION_REPAIR_VERSION = "narration_repair.v1"
MAX_STORY_ITEMS = 12
MAX_MISSING_STORIES = 2
# The ends of a story research that leave the episode with the material it has (ScriptRun.research_story): the
# supplement's answer refused after its corrections or its review, no supplement left, no source retrievable.
STORY_RESEARCH_SETTLED = frozenset({"teaching_research_required", "invalid_supplement", "invalid_research_gap",
                                    "source_download_failed"})
# A story item's anchor is a quote; the broadcast script quotes at most 25 words of a source (script_checks).
STORY_ANCHOR_WORDS = 25
MAX_NARRATION_POINTS = 8
# How far a narration rework may lengthen an episode beyond its draft or its word budget, whichever is larger. A story
# told takes room; the rework makes it by cutting detours and repeated caveats, not by growing the episode.
NARRATION_GROWTH = 1.15
# The code of a rework its checks refused after every correction: the draft stands and the points are reported. Any
# other error, such as a stop for quota or budget, ends the call as everywhere else and is asked again on resume.
REWORK_REFUSED = "narration_rework_refused"
STORY_KINDS = ("event", "person", "scene", "surprise", "quote", "consequence", "case")
CATEGORIES = ("compressed_story", "unused_material", "missing_story", "workshop_talk", "predictable_shape",
              "unpaid_hook", "flat_ending", "detour", "name_confusion")
# rewrite: the rework can fix it from the material; research: the material does not hold what the point needs, so the
# user decides about further research; note: reported only.
FIXES = ("rewrite", "research", "note")
# How many earlier episodes the review compares the episode's opening, ending and devices with.
COMPARED_EPISODES = 3
# Phrases that report how the research went instead of the kind of evidence (Orlagau 2026-10-21: „gelesen“ 207 times in
# 15 episodes, „Wikipedia“ 55; Ontologies: „we've only examined the abstract“). Counted for the review, not enforced.
WORKSHOP_PATTERNS = {
    "de": [r"nicht (?:im Volltext |im Wortlaut |selbst )?gelesen", r"(?:haben|habe) (?:wir|ich) nicht (?:gelesen|geprüft|ausgewertet)",
           r"lag(?:en)? uns nicht vor", r"gelesenen (?:Passagen|Quellen|Texten|Auszügen|Abschnitten)",
           r"Texterkennung", r"Wikipedia", r"\bOCR\b"],
    "en": [r"(?:did|have|had|has) not (?:read|examined|checked)", r"we(?:'ve| have)? only (?:read|examined|have|had) the abstract",
           r"\bfull text\b", r"passages? we (?:have )?read", r"supplied passages", r"\bOCR\b", r"Wikipedia"],
}
OPEN_PATTERNS = {"de": [r"bleibt (?:\w+ )?offen"], "en": [r"remains? open", r"(?:stays|is) unresolved"]}
SPOKEN_DIALOGUE = fragment("spoken_dialogue")


class StoryItem(Contract):
    item_id: Identifier
    kind: Literal[STORY_KINDS]
    text: NonEmpty = Field(description="What the source tells, in at most 60 words, as the source tells it.")
    source_ref: NonEmpty = Field(description="The reference of the allowed section that tells it.")
    anchor: NonEmpty = Field(description="A verbatim run of at most 25 words from that section.")
    finding_ids: list[Identifier] = Field(min_length=1, description="Episode findings listed with that section.")
    in_finding: bool = Field(description="True when a finding's statement already tells it in this detail.")


class MissingStory(Contract):
    question: NonEmpty = Field(description="What to read and tell, naming the account that tells it.")
    why_needed: NonEmpty = Field(description="Which point of the episode the story would carry.")
    finding_ids: list[Identifier] = Field(min_length=1, description="The episode's core findings it belongs to.")


class StoryMaterial(Contract):
    items: list[StoryItem] = Field(max_length=MAX_STORY_ITEMS)
    # A story the sections only point to (a scholar's summary of a chronicle, a case named in passing), which the story
    # research reads once before the design (ScriptRun.research_story).
    missing: list[MissingStory] = Field(default_factory=list, max_length=MAX_MISSING_STORIES)


class Score(Contract):
    score: int = Field(ge=1, le=10)
    reason: NonEmpty


class NarrationScores(Contract):
    tension: Score
    interest: Score
    storytelling: Score


class NarrationPoint(Contract):
    category: Literal[CATEGORIES]
    segment_ids: list[Identifier]
    problem: NonEmpty
    correction: NonEmpty
    fix: Literal[FIXES]
    item_ids: list[Identifier] = Field(description="story_material items the correction draws on; may be empty.")


class NarrationReview(Contract):
    scores: NarrationScores
    points: list[NarrationPoint] = Field(max_length=MAX_NARRATION_POINTS)
    strengths: list[NonEmpty] = Field(max_length=3)


NARRATION_SCHEMAS = {"story_material": StoryMaterial, "narration_review": NarrationReview}


# --- story material -----------------------------------------------------------------------------------------------

def allowed_sections(entry, dossier, sources):
    """The sections of ``sources`` (script_checks.episode_sources) that a finding of the episode cites, with the ids
    of the findings citing each. Only these: every later selection of the episode's sources keeps them, so the evidence
    review always sees the section a story item comes from."""
    cited = set(episode_findings(entry))
    citing = {}
    for finding in dossier.findings:
        if finding.id in cited:
            for evidence in finding.evidence:
                citing.setdefault(evidence.reference, [])
                if finding.id not in citing[evidence.reference]:
                    citing[evidence.reference].append(finding.id)
    return [{"reference": section["reference"], "text": section["text"], "finding_ids": citing[section["reference"]]}
            for document in sources for section in document["sections"] if section["reference"] in citing]


def story_problems(item, allowed):
    """Why ``item`` cannot be used: its section, its anchor or its findings do not match the allowed sections."""
    section = allowed.get(item.source_ref)
    if section is None:
        return f"{item.item_id}: source_ref {item.source_ref} is not one of allowed_sections."
    if len(item.anchor.split()) > STORY_ANCHOR_WORDS or not verbatim(item.anchor, section["text"]):
        return f"{item.item_id}: anchor is not a verbatim run of at most {STORY_ANCHOR_WORDS} words of {item.source_ref}."
    if not set(item.finding_ids) <= set(section["finding_ids"]):
        return f"{item.item_id}: finding_ids must be findings listed with {item.source_ref}."
    return None


def story_prompt(config, entry, dossier, sections):
    findings = [{"id": f.id, "statement": f.statement, **({"illustration": f.illustration} if f.illustration else {})}
                for f in dossier.findings if f.id in set(episode_findings(entry))]
    return (project_terminology(config) + instructions("story_material", language=config.language) + "\n" + json.dumps({
        "episode": {"episode_id": entry.episode_id, "title": entry.title, "central_question": entry.central_question,
                    "core_finding_ids": list(entry.finding_ids),
                    "scenes": [{"scene_id": s.scene_id, "title": s.title, "question": s.question} for s in entry.scenes]},
        "findings": findings, "allowed_sections": sections}, ensure_ascii=False))


def story_material(config, entry, dossier, sources, invoke, folder):
    """The episode's story material as dicts, collected once per prompt (``folder``/story_material.json). Items whose
    section, anchor or findings do not hold are dropped and listed; when more than half are, the call is asked once
    more with the defects named. Material never stops the run: an empty list leaves the episode without a core story."""
    sections = allowed_sections(entry, dossier, sources)
    path = folder / "story_material.json"
    prompt = story_prompt(config, entry, dossier, sections)
    signature = digest({"version": STORY_MATERIAL_VERSION, "prompt": prompt})
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if saved.get("input_hash") == signature:
        return saved["items"]
    if not sections:
        write_json(path, {"input_hash": signature, "items": [], "missing": [], "dropped": []})
        return []
    allowed = {section["reference"]: section for section in sections}
    attempt = prompt
    for round_number in range(2):
        material = invoke(attempt, StoryMaterial, STORY_MATERIAL_VERSION + ("+retry" if round_number else ""))
        seen, kept, dropped = set(), [], []
        for item in material.items:
            problem = story_problems(item, allowed) or (f"{item.item_id}: duplicate item_id." if item.item_id in seen else None)
            seen.add(item.item_id)
            (dropped if problem else kept).append(problem or item.model_dump())
        if len(dropped) <= len(kept):
            break
        attempt = prompt + "\n" + json.dumps({"rejected_items": dropped}, ensure_ascii=False)
    # A missing story extends core findings only (teaching_research.validate_supplement), so it names at least one.
    core = set(entry.finding_ids)
    missing = [{**story.model_dump(), "finding_ids": [key for key in story.finding_ids if key in core]}
               for story in material.missing if set(story.finding_ids) & core]
    write_json(path, {"input_hash": signature, "items": kept, "missing": missing, "dropped": dropped})
    return kept


def missing_stories(folder):
    """The stories the episode's sections only point to, as story_material saved them; empty for material saved before
    the field existed."""
    try:
        return json.loads((folder / "story_material.json").read_text(encoding="utf-8")).get("missing", [])
    except (OSError, ValueError):
        return []


# --- the narration review -----------------------------------------------------------------------------------------

def words(script):
    return script_metrics(script)["words"]


def narration_metrics(script, language, design=None):
    """What code counts for the review: phrases that report the research instead of the evidence, how often something
    stays open, two questions of one host in a row, each chapter's words, host_b's share and the core story's chapter."""
    key = "de" if str(language).lower().startswith("de") else "en"
    workshop = []
    for segment in script.segments:
        for pattern in WORKSHOP_PATTERNS[key]:
            for match in re.finditer(pattern, segment.text, flags=re.IGNORECASE):
                workshop.append({"segment_id": segment.segment_id, "phrase": match.group(0)})
    text = " ".join(segment.text for segment in script.segments)
    opened = sum(len(re.findall(pattern, text, flags=re.IGNORECASE)) for pattern in OPEN_PATTERNS[key])
    repeated = [[first.segment_id, second.segment_id] for first, second in zip(script.segments, script.segments[1:])
                if first.speaker_id == second.speaker_id and first.text.rstrip().endswith("?")
                and second.text.rstrip().endswith("?")]
    chapters = {}
    for segment in script.segments:
        chapters[segment.chapter_id] = chapters.get(segment.chapter_id, 0) + len(segment.text.split())
    total = sum(chapters.values()) or 1
    partner = sum(len(s.text.split()) for s in script.segments if s.speaker_id == "host_b")
    metrics = {"words": total, "workshop_phrases": workshop, "stays_open": opened, "repeated_questions": repeated,
               "chapter_words": chapters, "host_b_share": round(partner / total, 2)}
    if design is not None and design.story_scene_id:
        metrics["core_story_share"] = round(chapters.get(design.story_scene_id, 0) / total, 2)
    return metrics


def review_task(terms, language):
    """The narration review's instructions: the project's terminology rule, the spoken-dialogue and listenability
    rules the draft was written by, and the review's own task."""
    return terms + SPOKEN_DIALOGUE + LISTENABILITY + instructions("narration_review", language=language)


def review_payload(script, *, brief, host_roles, entry, series_context, design, story, dossier, sections, compared,
                   language):
    """What the narration review reads beside its task: the draft, its design and story material, the episode's
    findings and cited sections, the episodes before and what code counted."""
    cited = set(episode_findings(entry))
    return {"brief": brief, "host_roles": host_roles, "episode": entry.model_dump(), "series_context": series_context,
            "teaching_design": design.model_dump(), "story_material": story,
            "findings": [{"id": f.id, "statement": f.statement, **({"illustration": f.illustration} if f.illustration else {})}
                         for f in dossier.findings if f.id in cited],
            "sources": sections, "previous_episodes": compared,
            "metrics": narration_metrics(script, language, design), "script": script.model_dump()}


def compared_episodes(plan, entry, work):
    """Opening, ending and devices of up to COMPARED_EPISODES episodes before ``entry``, from their drafts and designs,
    so the review can name a pattern the series repeats."""
    earlier = []
    for candidate in plan.episodes:
        if candidate.episode_id == entry.episode_id:
            break
        earlier.append(candidate)
    rows = []
    for candidate in earlier[-COMPARED_EPISODES:]:
        row = {"episode_id": candidate.episode_id}
        try:
            design = TeachingPlan.model_validate_json(
                (work / "teaching" / candidate.episode_id / "plan.json").read_text(encoding="utf-8"))
            row.update(dramaturgy=design.dramaturgy, first_answer_voice=design.first_answer_voice,
                       opening_device=design.opening)
        except (OSError, ValueError):
            pass
        try:
            draft = EpisodeScript.model_validate_json(
                (work / "drafts" / f"{candidate.episode_id}.json").read_text(encoding="utf-8"))
            row.update(opening=[s.text[:400] for s in draft.segments[:2]],
                       ending=[s.text[:400] for s in draft.segments[-2:]])
        except (OSError, ValueError):
            pass
        rows.append(row)
    return rows


def sanitized(review, script, story_ids):
    """``review`` without the parts that name what is not there: unknown segments and story items are dropped from a
    point, and a rewrite point left without segments becomes a note. The narration review is never asked again for its
    shape, so it costs one call and never stops the run."""
    ids = {segment.segment_id for segment in script.segments}
    points = []
    for point in review.points:
        segments = [key for key in point.segment_ids if key in ids]
        items = [key for key in point.item_ids if key in set(story_ids)]
        fix = "note" if point.fix == "rewrite" and not segments else point.fix
        points.append(point.model_copy(update={"segment_ids": segments, "item_ids": items, "fix": fix}))
    return review.model_copy(update={"points": points})


def scope_errors(original, repaired, allowed):
    """A rework changes only the segments the points name: every other segment stays as it was, in its order. New
    segments may come in anywhere, a named one may go."""
    before = {segment.segment_id: segment for segment in original.segments}
    after = {segment.segment_id: segment for segment in repaired.segments}
    errors = [f"{key}: no narration point names this segment; keep it exactly as it was."
              for key, segment in before.items() if key not in allowed and after.get(key) != segment]
    expected = [key for key in before if key not in allowed]
    if not errors and [s.segment_id for s in repaired.segments if s.segment_id in expected] != expected:
        errors.append("Keep the segments no point names in their order.")
    return errors


def length_errors(original, repaired, target_words):
    limit = int(max(words(original), target_words) * NARRATION_GROWTH)
    if words(repaired) > limit:
        return [f"The rework has {words(repaired)} spoken words; stay within about {limit}: make room for the story by "
                "cutting detours and repeated caveats instead of growing the episode."]
    return []


def changed_segments(original, repaired):
    before = {segment.segment_id: segment for segment in original.segments}
    return [segment.segment_id for segment in repaired.segments if before.get(segment.segment_id) != segment]


def narrate_episode(folder, signature, draft, *, review, repair, followup):
    """Review ``draft``, rework the segments its rewrite points name and review the rework within the changed segments.

    ``review(draft)`` and ``followup(draft, previous, changed)`` return a NarrationReview, ``repair(draft, points)`` an
    EpisodeScript that passed every check, or raises AppError with REWORK_REFUSED. Each step is saved before the next
    paid call
    (``folder``/checkpoint.json, bound to ``signature``). Returns the narrated draft and the result written to
    result.json; a rework that fails its checks keeps the draft and says so. Nothing here stops the run."""
    path = folder / "checkpoint.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    state = saved if saved.get("input_hash") == signature else {"input_hash": signature}

    def save():
        write_json(path, state)

    if "review" not in state:
        state["review"] = review(draft).model_dump()
        save()
    first = NarrationReview.model_validate(state["review"])
    points = [point for point in first.points if point.fix == "rewrite"]
    final, final_review, status = draft, first, "unchanged"
    if points:
        if "repaired" not in state and "repair_failed" not in state:
            try:
                state["repaired"] = repair(draft, points).model_dump()
            except AppError as exc:
                if exc.code != REWORK_REFUSED:
                    raise
                state["repair_failed"] = str(exc)
            save()
        if "repaired" in state:
            final, status = EpisodeScript.model_validate(state["repaired"]), "repaired"
            if "followup" not in state:
                state["followup"] = followup(final, first, changed_segments(draft, final)).model_dump()
                save()
            final_review = NarrationReview.model_validate(state["followup"])
        else:
            status = "kept_draft"
    # What stays open is reported: the follow-up's points, the first review's research and note points, and after a
    # failed rework its rewrite points too.
    notes = [point.model_dump() for point in final_review.points]
    if final_review is not first or status == "kept_draft":
        notes += [point.model_dump() for point in first.points if point.fix != "rewrite" or status == "kept_draft"]
    unique = list({json.dumps(note, sort_keys=True): note for note in notes}.values())
    result = {"version": NARRATION_VERSION, "status": status, "draft_digest": digest(draft.model_dump()),
              "narrated_digest": digest(final.model_dump()), "scores": final_review.scores.model_dump(),
              "first_scores": first.scores.model_dump(), "strengths": final_review.strengths,
              "notes": [note for note in unique if note["fix"] != "research"],
              "research_points": [note for note in unique if note["fix"] == "research"],
              **({"repair_failed": state["repair_failed"]} if "repair_failed" in state else {})}
    write_json(folder / "script.json", final.model_dump())
    write_json(folder / "result.json", result)
    return final, result
