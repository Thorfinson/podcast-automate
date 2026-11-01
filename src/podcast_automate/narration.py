"""The story material of a script run (D-180) and the scope of a repair that changes only named segments.

The listener's verdict on Orlagau (2026-10-21): no episode was really good, and the striking things were dealt with in
one or two sentences. The run files showed why: the research kept the number a criterion asked for and left the scene
beside it in its source section. So each episode's teaching stage collects its story material from the source sections
its findings cite (``story_material``), and the story agent tells from it (D-181).

Until D-184 (2026-11-01) this module also held the narration review, which reviewed and reworked the writing prompt's
drafts; the story agent, now the only writer, reviews its own telling with its critiques.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from .models import Contract, Identifier, NonEmpty
from .prompts import instructions
from .research_evidence import verbatim
from .script_models import episode_findings
from .storage import digest, write_json
from .teaching import project_terminology

# v2-missing: also the stories the sections only point to, for the story research (2026-10-23).
STORY_MATERIAL_VERSION = "story_material.v2-missing"
MAX_STORY_ITEMS = 12
MAX_MISSING_STORIES = 2
# The ends of a story research that leave the episode with the material it has (ScriptRun.research_story): the
# supplement's answer refused after its corrections or its review, no supplement left, no source retrievable.
STORY_RESEARCH_SETTLED = frozenset({"teaching_research_required", "invalid_supplement", "invalid_research_gap",
                                    "source_download_failed"})
# A story item's anchor is a quote; the broadcast script quotes at most 25 words of a source (script_checks).
STORY_ANCHOR_WORDS = 25
STORY_KINDS = ("event", "person", "scene", "surprise", "quote", "consequence", "case")


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

def with_neighbours(script, named):
    """The segments a rework may change: those its points name and the one just before and after each. A story told
    more fully needs its handover to the next turn adjusted (Orlagau ep_014 of the narration evaluation, 2026-10-24:
    every one of three reworks changed the segment after the named one, and each was refused)."""
    order = [segment.segment_id for segment in script.segments]
    allowed = set(named)
    for index, key in enumerate(order):
        if key in named:
            allowed.update(order[max(0, index - 1):index + 2])
    return allowed


def scope_errors(original, repaired, allowed):
    """A rework changes only the segments it may (``allowed``, with_neighbours): every other segment stays as it was,
    in its order. New segments may come in anywhere, an allowed one may go."""
    before = {segment.segment_id: segment for segment in original.segments}
    after = {segment.segment_id: segment for segment in repaired.segments}
    errors = [f"{key}: no review point names this segment; keep it exactly as it was."
              for key, segment in before.items() if key not in allowed and after.get(key) != segment]
    expected = [key for key in before if key not in allowed]
    if not errors and [s.segment_id for s in repaired.segments if s.segment_id in expected] != expected:
        errors.append("Keep the segments no point names in their order.")
    return errors
