"""The story agent: a Claude Code session that plans and writes one narrated episode (D-181).

The narration lab (evals/narration_lab, 2026-10-27) measured it against every single-call writer: it reads the
material and the cited passages, finds the turn, plans its own chapters after the story material, writes, checks
its facts and the script checks itself, asks an editor up to four times and changes only what a point names, and
searches the project's own research corpus for the scenes the findings only name. Its drafts scored 8.0 to 8.5 on the
lab's judge where the pipeline's writer scored about 5.5.

A session works in ``runs/<run_id>/story/<episode_id>/``: ``material.json`` and ``TASK.md`` in, ``plan.md``,
``draft.json`` and optionally ``outline.json`` and ``sources_used.json`` out. Its only commands are the four of this
module (``python -m podcast_automate.story_agent <command>``), reached through small scripts beside the folder
(``story/<episode_id>.commands/``, outside what the session may write): ``check`` (the run's script checks on the
draft), ``critique`` (the editor's rubric, a model call of the run), ``search`` and ``read`` (the project's research
corpus). Code checks the draft again when the session ends, with the run's own context.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from .errors import AppError
from .models import Contract, EpisodeScript, NonEmpty, Segment
from .script_artifacts import SPOKEN_WORDS_PER_MINUTE, script_metrics
from .script_checks import quotation_errors, validate_script, word_budget, word_ceiling
from .script_models import EpisodePlan, ScenePlan
from .storage import digest, file_hash, read_optional_json, write_json

AGENT_MODEL = "claude-opus-5-5"
AGENT_EFFORT = "xhigh"
CRITIQUES = 4
# A revision changes what its feedback asks; the editor reads it at most this often.
REVISION_CRITIQUES = 2
# A session on an Anthropic key (claude_api) stops at this cost: ep_007's session cost 12.39 USD in subscription terms.
SESSION_BUDGET_USD = 40.0
# The plan's own length. The writing prompt's 1.3 for Sonnet (script_checks.WRITER_TARGET_FACTORS) makes up for a
# writer that stops at 71 % of its target; the agent counts its words with check.py and reaches the target, so 1.3
# gave the lean research test of 2026-11-01 a draft of 55 minutes for a planned 44, which the script review blocked
# as scope. The lab's best draft (Milgram s1, 8.5) had 1,306 words for ten minutes, the plan's length.
TARGET_FACTOR = 1.0
# The share of its target the agent's draft must reach. Ep_007 of Asimov v2 (2026-11-01) stopped at 89 % of its target,
# inside the old window of validate_script's 85 % floor, and ran 35:47 for 45 planned minutes (D-184).
TARGET_SHARE = 0.95
# v5: the task is written for the brief's audience, no longer for "curious adults" (2026-11-08).
# v6: the commands lie beside the session's folder, outside what it may write, and the run checks the draft with its
# own context, never with a file the session could change (commands_folder, 2026-11-14 review).
SESSION_VERSION = "story_agent.v6-commands"
# The session version before v6: a draft its session left and the run accepted stands on resume (write_episode), with
# the task as it then read (task_text without commands) and the length budget as it then was (legacy_length).
LEGACY_SESSION_VERSION = "story_agent.v5-audience"
ORIENTATION_VERSION = "orientation_sheet.v1"
ORIENTATION_CHECK_VERSION = "orientation_check.v1"
# v2: the editor hears the script as the brief's audience, with the hosts by name and part (critique_input).
# v3: it also reads the script's words against its length window and the story material, so it never asks for a net
# cut below the floor or for a scene the material lacks (2026-11-14 review: the critique asked for cuts the length
# floor then refilled, and four times for an invented Laplace scene).
CRITIQUE_VERSION = "story_critique.v3-length-material"
# How long a critique request waits for its answer, and how often the run and the session look: the pause between two
# looks starts at FIRST_POLL_SECONDS and doubles up to POLL_SECONDS, so a quick step (a check, the test suite's fake
# agent) is seen at once while a long session is still looked at every two seconds (2026-11-10: a fixed two seconds
# on both sides was most of the Python suite's time).
CRITIQUE_WAIT = 30 * 60
POLL_SECONDS = 2
FIRST_POLL_SECONDS = 0.05


def poll_pauses():
    """The pauses between two looks: FIRST_POLL_SECONDS, doubling each time, then POLL_SECONDS for good."""
    pause = FIRST_POLL_SECONDS
    while True:
        yield pause
        pause = min(POLL_SECONDS, pause * 2)
# A session read, planned, wrote and revised a ten-minute episode in 31 to 60 minutes in the lab, so its ceiling grows
# with the episode: eight minutes per planned minute, at least two hours (session_timeout).
SESSION_TIMEOUT = 120 * 60
SESSION_SECONDS_PER_MINUTE = 8 * 60
COMMANDS = ("check", "critique", "search", "read")
# Bash may run exactly these scripts; the folder is the session's working directory, and no other tool (web search,
# web fetch, MCP servers, any other shell command) is offered. The scripts lie beside that folder (commands_folder),
# so the session names them by a path from its folder (allowed_tools).
TOOLS = "Read,Write,Edit,Glob,Grep,Bash"


def allowed_tools(prefix: str = "") -> str:
    """The tools the session may use without asking: the file tools and Bash for exactly the four command scripts,
    named as ``python <prefix><command>.py`` (command_prefix)."""
    return ",".join(["Read", "Write", "Edit", "Glob", "Grep", *(f"Bash(python {prefix}{name}.py:*)" for name in COMMANDS)])


# The rule of the sessions before v6, whose scripts lay in their own folder.
ALLOWED_TOOLS = allowed_tools()
LENGTH_SLACK = 1.1
# A turn's planned pause when the draft sets none (models.Segment), and the words of one turn the length budget
# reserves such a pause for before a draft exists (agent_length). TASK.md asks for turns rarely over 80 words; 40
# reserves 1.5 minutes of pauses for 9,000 words. It is the budget's first guess only: check.py and the run's final
# check count the draft's own pauses (draft_length).
DEFAULT_PAUSE_MS = Segment.model_fields["pause_after_ms"].default
ESTIMATED_TURN_WORDS = 40
SEARCH_HITS = 8
WORD = re.compile(r"\w+")
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class OrientationItem(Contract):
    name: NonEmpty
    kind: NonEmpty
    orientation: NonEmpty
    certain: bool


class OrientationSheet(Contract):
    items: list[OrientationItem] = Field(max_length=25)


class OrientationCheck(Contract):
    name: NonEmpty
    verdict: Literal["correct", "wrong", "uncertain"]
    note: str = ""


class OrientationReview(Contract):
    checks: list[OrientationCheck]


class Scale(Contract):
    score: int = Field(ge=1, le=10)
    reason: NonEmpty


class CritiqueScores(Contract):
    tension: Scale
    interest: Scale
    storytelling: Scale


class CritiquePoint(Contract):
    segment_ids: list[NonEmpty]
    problem: NonEmpty
    fix: NonEmpty


class StoryCritique(Contract):
    scores: CritiqueScores
    keeps_from_10: list[CritiquePoint] = Field(max_length=8)


def task_text(language: str, minutes: float, critiques: int = CRITIQUES, *, cast: str = "",
              revision: bool = False, commands: str = "") -> str:
    """TASK.md: the agent's instructions (data/story_agent_task.md, a document rather than one prompt paragraph). A
    project with a third host appends its cast (cast.cast_note, D-179); a revision appends its own steps
    (data/story_agent_revision.md). ``commands`` is the path from the session's folder to its command scripts
    (command_prefix); empty, the text is the one the sessions before v6 read, with the scripts in their own folder."""
    data = files("podcast_automate").joinpath("data")
    raw = data.joinpath("story_agent_task.md").read_text(encoding="utf-8")
    if revision:
        raw += data.joinpath("story_agent_revision.md").read_text(encoding="utf-8")
    text = raw.format(language=language, minutes=f"{minutes:g}", critiques=critiques, commands=commands)
    return text + ("\n## Three hosts\n\n" + cast.strip() + "\n" if cast else "")


# --- the episode as planned by the agent ----------------------------------------------------------------------------

def planned_entry(entry: EpisodePlan, folder: Path) -> tuple[EpisodePlan, list[str]]:
    """The episode with the agent's own chapters (``outline.json``), or as planned when there is none; with the
    defects of an outline that does not hold. Every core finding stays core, in exactly one of two to four chapters."""
    outline = folder / "outline.json"
    if not outline.is_file():
        return entry, []
    try:
        chapters = json.loads(outline.read_text(encoding="utf-8-sig"))["chapters"]
        scenes = [ScenePlan.model_validate({"purpose": "worked_example", "explanation_steps": [chapter["question"]],
                                            **chapter}) for chapter in chapters]
    except (ValueError, KeyError, TypeError) as exc:
        return entry, [f"outline.json does not parse as chapters: {exc}"]
    defects = []
    if not 2 <= len(scenes) <= 4:
        defects.append("outline.json holds two to four chapters.")
    if sorted(f for scene in scenes for f in scene.finding_ids) != sorted(entry.finding_ids):
        defects.append(f"outline.json places every core finding in exactly one chapter, and no other: "
                       f"{', '.join(entry.finding_ids)}.")
    if len({scene.scene_id for scene in scenes}) != len(scenes):
        defects.append("outline.json needs a unique scene_id per chapter.")
    return (entry, defects) if defects else (entry.model_copy(update={"scenes": scenes}), [])


# --- checks ---------------------------------------------------------------------------------------------------------

def echo_errors(script: EpisodeScript, longest: int = 4) -> list[str]:
    """Turns that open with a short sentence made only of the other host's last words ("Alle." "Mit Hilfe des
    Cuchaviz."): the judges' most frequent complaint about the second host in the lab."""
    errors = []
    for before, segment in zip(script.segments, script.segments[1:]):
        if segment.speaker_id == before.speaker_id:
            continue
        earlier = {word.lower() for word in WORD.findall(before.text)}
        opening = []
        for sentence in SENTENCE_END.split(segment.text.strip()):
            words = [word.lower() for word in WORD.findall(sentence)]
            if not words or len(words) > longest or not all(word in earlier for word in words):
                break
            opening.append(sentence)
        if opening:
            errors.append(f"{segment.segment_id}: the turn opens by repeating the other host's words "
                          f"(\"{' '.join(opening)}\"); open with a thought, guess, feeling or objection of this "
                          "host's own instead, or give the line to the host who is telling.")
    return errors


def expected_pauses(words: int) -> float:
    """The minutes of planned pauses a draft of ``words`` is expected to have before it exists: one turn's default
    pause per ESTIMATED_TURN_WORDS."""
    return words / ESTIMATED_TURN_WORDS * DEFAULT_PAUSE_MS / 60_000


def legacy_length(entry: EpisodePlan, rate: float) -> dict:
    """The agent's word budget as every session before v6 got it: the plan's length at the run's rate, its floor at
    TARGET_SHARE of the target, both regardless of the 60-minute cap."""
    length = word_budget(entry, TARGET_FACTOR, rate)
    # The agent reaches its target, not only validate_script's floor (TARGET_SHARE, D-184).
    length["minimum_words"] = max(length["minimum_words"], math.ceil(length["target_words"] * TARGET_SHARE))
    return length


def agent_length(entry: EpisodePlan, rate: float) -> dict:
    """The word budget material.json gives the agent: legacy_length, its target and floor brought under the 60-minute
    cap (script_models.MAX_EPISODE_MINUTES) that validate_script counts the planned pauses into, with the pauses a
    draft of that length is expected to have (expected_pauses). check.py holds the draft to its own pauses
    (draft_length).

    2026-11-14 review: the target ignored the pauses while the cap counts them, so an episode planned at 60 minutes
    could never reach its target, and with three hosts' many short turns at 150 words a minute not even its floor."""
    length = legacy_length(entry, rate)
    ceiling = word_ceiling(rate, expected_pauses(length["target_words"]))
    if length["target_words"] <= ceiling:
        return length
    share = ceiling / length["target_words"]
    return {"minimum_words": min(length["minimum_words"], math.ceil(ceiling * TARGET_SHARE)), "target_words": ceiling,
            "scene_words": {key: round(value * share) for key, value in length["scene_words"].items()}}


def draft_length(length: dict, draft: EpisodeScript, rate: float) -> dict:
    """The word window ``draft`` is held to, counted as validate_script counts it (script_metrics): the budget's floor
    and target, brought under the most words its own planned pauses leave within the 60-minute cap
    (``ceiling_words``). Only pauses up to a turn's default make room this way; longer ones still count against the
    cap, but never lower the floor."""
    pauses = sum(segment.pause_after_ms for segment in draft.segments) / 60_000
    usual = sum(min(segment.pause_after_ms, DEFAULT_PAUSE_MS) for segment in draft.segments) / 60_000
    target = min(length["target_words"], word_ceiling(rate, usual))
    minimum = length["minimum_words"] if target == length["target_words"] else min(
        length["minimum_words"], math.ceil(target * TARGET_SHARE))
    return {"words": script_metrics(draft, rate)["words"], "minimum_words": minimum, "target_words": target,
            "ceiling_words": word_ceiling(rate, pauses), "pause_minutes": round(pauses, 2)}


def load_context(folder: Path) -> dict:
    """The context a command of the session in ``folder`` works with (session_context): beside the folder, where the
    session cannot write it; a folder prepared before v6 kept it inside."""
    path = commands_folder(folder) / "context.json"
    return json.loads((path if path.is_file() else folder / "context.json").read_text(encoding="utf-8"))


def allowed_references(folder: Path, context: dict) -> set[str]:
    """The sections of material.json a segment may cite (D-182), as the run gave them; a context written before v6
    names none, and its folder's material.json is read instead."""
    if "sections" in context:
        return set(context["sections"])
    material_data = json.loads((folder / "material.json").read_text(encoding="utf-8"))
    return {section["reference"] for section in material_data.get("sections", [])}


def draft_errors(folder: Path, draft: EpisodeScript, corpus=None, context: dict | None = None) -> list[str]:
    """Every defect of the folder's draft: the outline's, the run's script checks against the planned episode
    (structure, citations and the quotation limit over the given passages), echo lines, the quotation limit over the
    corpus sections the agent used, the length window and the plan. ``context`` is the run's own (session_context);
    without it, as in check.py, the copy beside the folder is read (load_context), never one the session can write."""
    context = context if context is not None else load_context(folder)
    entry, errors = planned_entry(EpisodePlan.model_validate(context["entry"]), folder)
    rate = context.get("rate", SPOKEN_WORDS_PER_MINUTE)
    errors += validate_script(draft, entry, roles=tuple(context["roles"]), rate=rate)
    if context.get("assembled"):
        errors += quotation_errors(draft, context["sources"])
    errors += echo_errors(draft)
    documents, unknown = used_documents(folder, Path(context["root"]), corpus)
    errors += unknown
    errors += quotation_errors(draft, documents)
    # A segment cites only a section it had: one of material.json's or one it recorded in sources_used.json (D-182).
    had = allowed_references(folder, context) | {
        section["reference"] for document in documents for section in document["sections"]}
    errors += [f"{segment.segment_id}: source_refs names {ref}, which is neither one of material.json's sections nor "
               "a section listed in sources_used.json." for segment in draft.segments for ref in segment.source_refs
               if ref not in had]
    length = draft_length(context["length"], draft, rate)
    words = length["words"]
    if words < length["minimum_words"]:
        errors.append(f"{words} words: write at least {length['minimum_words']} (length.minimum_words, within the "
                      f"60-minute cap for this draft's pauses), about {length['target_words']}; develop the scenes the "
                      "sources tell in detail rather than adding commentary.")
    if words > length["target_words"] * LENGTH_SLACK:
        errors.append(f"{words} words: stay within a tenth above about {length['target_words']} "
                      "(length.target_words).")
    if words > length["ceiling_words"]:
        errors.append(f"{words} words: with {length['pause_minutes']:g} minutes of planned pauses the episode holds at "
                      f"most {length['ceiling_words']} words within 60 minutes; cut detours, never the scenes, or "
                      "shorten long pauses.")
    plan = folder / "plan.md"
    if not plan.is_file() or not plan.read_text(encoding="utf-8").strip():
        errors.append("plan.md is missing: write the plan (for a revision, what you changed and why) beside the draft; "
                      "it is published with the episode.")
    return errors


# --- the research corpus --------------------------------------------------------------------------------------------

def library_documents(root: Path, research_run: str | None) -> list:
    """The documents of the research run's starting library (its research_request.json's seed_corpus, read as
    sources.load_library reads it), one per document; none for a run begun without a library.

    2026-11-08: the case that carries Geld verstehen S1 ep_003, a 14-year-old's 2,630 euros of Robux the consumer
    centre got back, stood only in the library the season began with; no finding of the season's research cited it, so
    the agent could neither find nor cite it, and the episode told study after study instead."""
    from .runner import manifest_path
    from .sources import load_library
    if not research_run:
        return []
    try:
        request = json.loads((manifest_path(root, research_run).parent / "research_request.json")
                             .read_text(encoding="utf-8"))
    except (OSError, ValueError, AppError):
        return []
    seed = request.get("seed_corpus") if isinstance(request, dict) else None
    if not isinstance(seed, str) or not seed:
        return []
    unique = {}
    for document in load_library(root, seed).values():
        unique.setdefault(document.id, document)
    return list(unique.values())


def corpus_documents(root: Path, research_run: str | None, sources) -> list:
    """The documents of the project's research corpus: the research's sources, then each document of its starting
    library the research did not take in (library_documents). What the agent may search, read and cite, and what show
    notes and companion kits resolve its cited sections against."""
    known = {source.id for source in sources}
    return [*sources, *(document for document in library_documents(root, research_run) if document.id not in known)]


def corpus_sections(root: Path) -> list[tuple[str, str, str]]:
    """Every section of the project's research corpus (corpus_documents) as (reference, source title, text)."""
    from .scripting import load_research
    from .storage import load_project
    research_run, _, _, sources, _ = load_research(root, load_project(root))
    return [(f"{source.id}#{section.id}", source.title, section.text)
            for source in corpus_documents(root, research_run, sources.sources) for section in source.sections]


def used_documents(folder: Path, root: Path, corpus=None) -> tuple[list[dict], list[str]]:
    """The corpus sections the agent recorded in ``sources_used.json``, grouped by source as episode_sources gives a
    review its passages, and the defects of a file that names something else."""
    used = folder / "sources_used.json"
    if not used.is_file():
        return [], []
    try:
        wanted = {row["reference"] for row in json.loads(used.read_text(encoding="utf-8-sig"))}
    except (ValueError, KeyError, TypeError) as exc:
        return [], [f"sources_used.json does not parse as a list of {{\"reference\", \"why\"}}: {exc}"]
    documents, found = {}, set()
    for reference, title, text in (corpus if corpus is not None else corpus_sections(root)):
        if reference in wanted:
            found.add(reference)
            documents.setdefault(reference.split("#", 1)[0], {"source_id": reference.split("#", 1)[0], "title": title,
                                                              "sections": []})["sections"].append(
                {"reference": reference, "text": text})
    return list(documents.values()), [f"sources_used.json names {reference}, which is no section of the corpus."
                                      for reference in sorted(wanted - found)]


def search(root: Path, query: str, hits: int = SEARCH_HITS) -> dict:
    """The corpus sections holding the most of the query's words (case-blind, words of three letters or more), each
    with an excerpt around its first hit."""
    words = [word.lower() for word in re.findall(r"\w{3,}", query)]
    if not words:
        return {"error": "search.py needs words to look for."}
    scored = []
    for reference, title, text in corpus_sections(root):
        low = text.lower()
        found = [word for word in words if word in low]
        if found:
            scored.append((len(set(found)), reference, title, text, low.find(found[0])))
    scored.sort(key=lambda row: -row[0])
    return {"query": words, "matching_sections": len(scored), "hits": [
        {"reference": reference, "source": title[:120], "words_found": count, "excerpt": text[max(0, at - 300):at + 500]}
        for count, reference, title, text, at in scored[:hits]]}


def read(root: Path, reference: str) -> dict:
    for found, title, text in corpus_sections(root):
        if found == reference.strip():
            return {"reference": found, "source": title, "text": text}
    return {"error": f"No section {reference.strip()}; use a reference exactly as search.py gives it."}


# --- the work folder and the session --------------------------------------------------------------------------------

COMMAND_SCRIPT = '''import os, subprocess, sys
# {command}: one of the story agent's commands, run by the package's own interpreter (story_agent.prepare). This script
# and its context lie beside the session's folder, outside what the session may write; -P keeps the session's folder
# off sys.path and the command runs in this folder, so nothing the session writes is imported as code.
arguments = sys.argv[1:] or (["draft.json"] if {command!r} in ("check", "critique") else [])
result = subprocess.run([{python!r}, "-P", "-m", "podcast_automate.story_agent", {command!r}, "--folder", {folder!r},
                         *arguments], cwd=os.path.dirname(os.path.abspath(__file__)),
                        capture_output=True, text=True, encoding="utf-8", env={{**os.environ, "PYTHONIOENCODING": "utf-8"}})
# Passed on as UTF-8: a Windows pipe's own code page garbles the critique's umlauts and cannot hold a name like Doliński.
sys.stdout.reconfigure(encoding="utf-8")
print(result.stdout)
if result.returncode:
    print(result.stderr[-4000:])
'''


def commands_folder(folder: Path) -> Path:
    """Where the command scripts of the session in ``folder`` and their context lie: ``story/<episode_id>.commands/``,
    beside the folder and outside the working directory the session may write. 2026-11-14 review: with the scripts in
    its own folder, the session's Write tool and ``python check.py`` could run any code it wrote, and the run's final
    check read a context.json the session could change."""
    return folder.parent / f"{folder.name}.commands"


def command_prefix(folder: Path) -> str:
    """How the session names its commands from its folder (allowed_tools, task_text): ``../<episode_id>.commands/``."""
    return f"../{commands_folder(folder).name}/"


def session_context(material: dict, context: dict, critiques: int) -> dict:
    """What the commands work with (material's context): the episode, the hosts, the given passages and the sections
    material.json holds, the length, the words a minute, the root and the critiques allowed. The run keeps it in
    memory for its own checks; check.py reads the copy beside the folder."""
    return {**context, "sections": [section["reference"] for section in material.get("sections", [])],
            "critiques": critiques}


def command_hashes(folder: Path) -> dict[str, str | None]:
    """The checksums of the command scripts and the context beside ``folder``, which no session may change."""
    commands = commands_folder(folder)
    return {name: file_hash(commands / name) if (commands / name).is_file() else None
            for name in ("context.json", *(f"{command}.py" for command in COMMANDS))}


def prepare(folder: Path, *, material: dict, context: dict, task: str, critiques: int = CRITIQUES,
            files: dict[str, str] | None = None) -> dict:
    """The session's folder: material.json, TASK.md (task_text) and ``files`` (session_files: the earlier episodes, a
    revision's published episode, what an earlier attempt left); beside it, the four command scripts and context.json
    (session_context). Returns that context."""
    folder.mkdir(parents=True, exist_ok=True)
    full = session_context(material, context, critiques)
    write_json(folder / "material.json", material)
    (folder / "TASK.md").write_text(task, encoding="utf-8")
    for relative, text in (files or {}).items():
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_text(text, encoding="utf-8")
    commands = commands_folder(folder)
    commands.mkdir(parents=True, exist_ok=True)
    write_json(commands / "context.json", full)
    for command in COMMANDS:
        (commands / f"{command}.py").write_text(COMMAND_SCRIPT.format(command=command, python=sys.executable,
                                                                      folder=str(folder.resolve())), encoding="utf-8")
    return full


def session_timeout(minutes: float) -> float:
    return max(SESSION_TIMEOUT, minutes * SESSION_SECONDS_PER_MINUTE)


SESSION_PROMPT = "Read TASK.md in this folder and do the task it describes, start to finish."
# What a session leaves that a repeated one on the same material and task starts from (write_episode).
CONTINUED_FILES = ("plan.md", "draft.json", "outline.json", "sources_used.json")
# Said to a session that repeats one which stopped before its draft was accepted, with what that one left (write_episode).
CONTINUED_PROMPT = (" An earlier session on this same task stopped before it finished; the plan.md, draft.json and "
                    "other files it wrote are in this folder. Read them, find where it stood, and continue from there "
                    "instead of starting over.")


def session_command(cli: list[str], model: str = AGENT_MODEL, effort: str = AGENT_EFFORT,
                    budget_usd: float | None = None, *, commands: str = "", continued: bool = False) -> list[str]:
    """The session's command line; ``budget_usd`` caps a session billed to an Anthropic key (SESSION_BUDGET_USD).
    ``commands`` is the path from the folder to its command scripts (command_prefix), ``continued`` a session that
    picks up an earlier one's files."""
    return [*cli, "-p", SESSION_PROMPT + (CONTINUED_PROMPT if continued else ""),
            "--model", model, "--effort", effort, "--permission-mode", "dontAsk",
            "--tools", TOOLS, "--allowedTools", allowed_tools(commands), "--no-session-persistence",
            "--strict-mcp-config", "--setting-sources", "", "--disable-slash-commands", "--output-format", "json",
            *(["--max-budget-usd", f"{budget_usd:g}"] if budget_usd else [])]


# --- the run's side: orientation, material, the session and its critiques --------------------------------------------

def story_entry(work: Path, entry: EpisodePlan) -> EpisodePlan:
    """The episode as its story agent wrote it (story/<episode_id>/entry.json), or as planned before."""
    path = work / "story" / entry.episode_id / "entry.json"
    return EpisodePlan.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else entry


def orientation(pipeline, entry, story, directory: Path) -> list[Path]:
    """The episode's checked orientation sheet (teaching/<episode_id>/orientation.json): drafted, then every entry
    checked in a second call; only the entries found correct go to the agent. In the lab the check caught real
    errors (Burger's data from 2006, not 2009; the teacher's real 45-volt sample shock)."""
    from .prompts import instructions
    from .research_patches import corrected_call
    path = directory / "orientation.json"
    material = {"language": pipeline.config.language, "episode": entry.model_dump(), "story_material": story,
                "findings": [{"id": f.id, "statement": f.statement} for f in pipeline.dossier.findings
                             if f.id in {*entry.finding_ids, *entry.supporting_finding_ids}]}
    drafting = instructions("orientation_sheet") + "\n" + json.dumps(material, ensure_ascii=False)
    signature = digest({"input": pipeline.input_hash, "prompt": drafting, "check": instructions("orientation_check")})
    if path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("signature") == signature:
        return [path]
    # The drafted sheet is kept before its check, so a stop during the check asks only the check again (found by
    # stopping a run at each of its calls, 2026-11-01).
    draft = directory / "orientation_draft.json"
    saved = json.loads(draft.read_text(encoding="utf-8")) if draft.is_file() else {}
    if saved.get("signature") == signature:
        sheet = OrientationSheet.model_validate(saved["sheet"])
    else:
        sheet = pipeline.invoke(drafting, OrientationSheet, ORIENTATION_VERSION)
        write_json(draft, {"signature": signature, "sheet": sheet.model_dump()})
    names = [item.name for item in sheet.items]

    def complete(answer):
        if [check.name for check in answer.checks] != names:
            raise AppError("Return every entry exactly once, in this order: " + ", ".join(names) + ".",
                           code="invalid_model_output", status="blocked")
    review = corrected_call(pipeline.invoke, instructions("orientation_check") + "\n" + json.dumps(
        {"sheet": [item.model_dump() for item in sheet.items]}, ensure_ascii=False), OrientationReview,
        ORIENTATION_CHECK_VERSION, complete)
    verdicts = {check.name: check for check in review.checks}
    kept = [item for item in sheet.items if verdicts[item.name].verdict == "correct"]
    write_json(path, {"signature": signature, "items": [item.model_dump() for item in kept],
                      "dropped": [{**item.model_dump(), "check": verdicts[item.name].model_dump()}
                                  for item in sheet.items if item not in kept]})
    return [path]


def episode_text(script: EpisodeScript, number: int, labels: dict) -> str:
    """An earlier episode as the agent reads it in earlier/: its chapters and turns, each turn with its speaker."""
    from .script_artifacts import render_script
    text = render_script(script, {role: f"{role} ({label})" for role, label in labels.items()})
    return text.replace(f"# {script.title}", f"# Episode {number}: {script.title}", 1)


def published_from(root: Path, episode: str) -> str | None:
    """The hash of the series plan of the run that published ``episode`` (episodes/<episode>/latest.json), or None."""
    from .runner import manifest_path
    try:
        run = json.loads((root / "episodes" / episode / "latest.json").read_text(encoding="utf-8"))["run_id"]
        return file_hash(manifest_path(root, run).parent / "series_plan.json")
    except (OSError, ValueError, KeyError, TypeError, AppError):
        return None


def earlier_episodes(root: Path, plan, entry, config, plan_file: Path | None = None) -> list[tuple[dict, str]]:
    """The published episodes before ``entry`` in the series' order, each as its row of material.json and the text of
    its file in earlier/: what the listeners have heard, which a new episode builds on (D-184). Until 2026-11-01 an
    episode knew the others only by their titles and questions, even in a series run. With ``plan_file`` (the run's
    series_plan.json), only an episode published from that outline counts: an earlier plan's episode of the same id
    is another series."""
    from .models import host_labels
    from .storage import read_yaml
    outline = file_hash(plan_file) if plan_file is not None and plan_file.is_file() else None
    rows = []
    for number, earlier in enumerate(plan.episodes, start=1):
        if earlier.episode_id == entry.episode_id:
            break
        path = root / "episodes" / earlier.episode_id / "script.yaml"
        if path.is_file() and (outline is None or published_from(root, earlier.episode_id) == outline):
            script = EpisodeScript.model_validate(read_yaml(path))
            rows.append(({"episode_id": earlier.episode_id, "number": number, "title": script.title,
                          "file": f"earlier/{earlier.episode_id}.md"}, episode_text(script, number, host_labels(config))))
    return rows


def revision_entry(pipeline, entry: EpisodePlan) -> EpisodePlan:
    """The episode a session writes: for a revision the chapters its story agent published it with (the revision's
    ``entry``, scripting.revision_inputs); for a new episode given its own length (D-192) the outline's entry at
    those minutes, which entry.json then carries to the review, the published plan and the recording; else the
    outline's."""
    saved = (getattr(pipeline, "revision", None) or {}).get("entry")
    if saved and saved.get("episode_id") == entry.episode_id:
        return EpisodePlan.model_validate(saved)
    minutes = getattr(pipeline, "episode_minutes", None)
    if minutes is not None and not getattr(pipeline, "revision", None):
        return EpisodePlan.model_validate({**entry.model_dump(), "target_minutes": minutes})
    return entry


def session_files(pipeline, plan, entry, material_data) -> dict[str, str]:
    """The files a session gets beside material.json: the earlier episodes, and for a revision the published episode
    and the corpus sections it used (its run's sources_used.json), which the draft may keep citing."""
    from .runner import manifest_path
    files = {row["file"]: text for row, text in earlier_episodes(pipeline.root, plan, entry, pipeline.config,
                                                                 pipeline.work / "series_plan.json")}
    if "revision" in material_data:
        files["published.json"] = json.dumps(pipeline.revision["original_script"], ensure_ascii=False, indent=1)
        used = (manifest_path(pipeline.root, pipeline.revision["source_run_id"]).parent / "story" / entry.episode_id /
                "sources_used.json")
        if used.is_file():
            files["sources_used.json"] = used.read_text(encoding="utf-8")
    return files


def material(pipeline, plan, entry) -> tuple[dict, dict]:
    """material.json for the agent and context.json for its commands: the outline entry, the series context, the
    story material, the findings with their limits, the passages they cite, the checked orientation, the word
    budget, the brief, the earlier episodes and a revision's feedback; the commands get the entry, the hosts, the given
    passages, the length, the words a minute and the root."""
    from .editorial import EPISODE_FRAMING, episode_series_context
    from .models import host_roles_of
    from .narration import allowed_sections
    from .cast import host_roles
    from .script_checks import episode_limits, episode_sources
    from .script_models import episode_findings
    config, dossier = pipeline.config, pipeline.dossier
    wanted = set(episode_findings(entry))
    findings = [{"id": f.id, "core": f.id in entry.finding_ids, "statement": f.statement,
                 **({"illustration": f.illustration} if f.illustration else {}),
                 **({"limits": f.claim_contract.qualifications}
                    if f.claim_contract and f.claim_contract.qualifications else {})}
                for f in dossier.findings if f.id in wanted]
    sources = episode_sources(entry, dossier, pipeline.context, pipeline.sources)
    story = pipeline.story_for(entry)
    told = {f for item in story for f in item.get("finding_ids", [])}
    sheet = pipeline.work / "teaching" / entry.episode_id / "orientation.json"
    rate = getattr(pipeline, "rate", SPOKEN_WORDS_PER_MINUTE)
    # Within the 60-minute cap with the pauses a draft of that length is expected to have (agent_length).
    length = agent_length(entry, rate)
    third = getattr(config, "third_host", None)
    material_data = {
        "brief": {"language": config.language, "audience": config.audience_level, "host_names": config.host_names,
                  "host_roles": host_roles(config), "style_notes": pipeline.style_notes,
                  **({"series_goal": config.series_goal} if getattr(config, "series_goal", None) else {}),
                  **({"third_host": {"speaker_id": "host_c", "name": third.name, "role": third.role}} if third else {})},
        "episode": entry.model_dump(), "series_context": episode_series_context(plan, entry), "length": length,
        # The rule the script review holds the episode's opening and ending to (editorial.EPISODE_FRAMING): both
        # first reviews of 2026-11-01 raised it as structure issues the agent had never been told.
        "framing": EPISODE_FRAMING.strip(),
        "story_material": story, "findings": findings,
        "sections": [{"reference": s["reference"], "text": s["text"]}
                     for s in allowed_sections(entry, dossier, sources)],
        "orientation": [{"name": i["name"], "orientation": i["orientation"]}
                        for i in json.loads(sheet.read_text(encoding="utf-8"))["items"]] if sheet.is_file() else [],
        "core_findings_without_story": [f for f in entry.finding_ids if f not in told]}
    # What the research could not establish for this episode, which the script review expects stated where the statement
    # it limits is made (script_checks.episode_limits); until 2026-11-01 only the review knew them.
    limits = episode_limits(plan, entry, pipeline.stated_limits()) if hasattr(pipeline, "stated_limits") else []
    if limits:
        material_data["research_limits"] = limits
    # Findings whose evidence all comes from one research group, which the review expects attributed audibly once.
    single = pipeline.single_group(entry) if hasattr(pipeline, "single_group") else []
    if single:
        material_data["single_group_findings"] = single
    earlier = earlier_episodes(pipeline.root, plan, entry, config, pipeline.work / "series_plan.json")
    if earlier:
        material_data["earlier_episodes"] = [row for row, _ in earlier]
    revision = getattr(pipeline, "revision", None)
    if revision and (revision.get("original_script") or {}).get("episode_id") == entry.episode_id:
        material_data["revision"] = {"feedback": revision["feedback"], "published": "published.json"}
    context = {"entry": entry.model_dump(), "roles": list(host_roles_of(config)),
               "assembled": bool(dossier.assembled), "sources": sources if dossier.assembled else [],
               "length": length, "rate": rate, "root": str(pipeline.root)}
    return material_data, context


def script_text(script: EpisodeScript) -> str:
    return "\n".join(f"[{s.segment_id}] {s.speaker_id}: {s.text}" for s in script.segments)


def critique_input(config, draft: EpisodeScript, length: dict | None = None, story: list | None = None) -> dict:
    """What the editor reads beside its rubric: the brief's audience, whom it hears the script as, the hosts by name
    and part, the script's words against its length window (``length``, draft_length) and what the story material
    holds (``story``), and the script. Until 2026-11-08 it read every script as "a curious adult listener" with two
    hosts, and rated Geld verstehen S1 ep_003, a chain of studies for 14- to 16-year-olds, 8/8/7. Until 2026-11-14 it
    knew neither the window nor the material: it asked for cuts the floor then refilled, and for scenes the material
    lacked (Geld S2 ep_010: an invented Laplace scene, four times)."""
    from .cast import host_roles
    from .models import host_labels
    labels = host_labels(config)
    audience = getattr(config, "audience_level", None)
    return {**({"audience": audience} if audience else {}),
            "hosts": {role: {"name": labels.get(role, role), "part": part} for role, part in host_roles(config).items()},
            **({"length": {key: length[key] for key in ("words", "minimum_words", "target_words")}} if length else {}),
            **({"story_material": [{"kind": item.get("kind"), "text": item.get("text")} for item in story]}
               if story is not None else {}),
            "script": script_text(draft)}


def counted_critiques(requests: Path) -> int:
    """The critiques a session has used: the answers that carry scores or a refusal. An answer that reports a failed
    call (a quota, a provider error, an unreadable request) used none; until 2026-11-14 it counted, so four quick
    failures left a session with no critique at all."""
    return sum(1 for path in requests.glob("critique_*.answer.json")
               if {"scores", "refused"} & set(read_optional_json(path, {}) or {}))


def serve_critiques(pipeline, folder: Path, context: dict | None = None, story: list | None = None) -> int:
    """Answer the critique requests the session left (requests/critique_N.json) with a call of the run, so the
    budget, the receipts and the provider choice apply as to every other call. ``context`` is the run's own
    (session_context): the critiques allowed, the length budget and the rate; ``story`` the episode's story material.
    Returns how many it answered."""
    from .prompts import instructions
    requests = folder / "requests"
    if not requests.is_dir():
        return 0
    served = 0
    context = context if context is not None else load_context(folder)
    allowed = context["critiques"]
    for request in sorted(requests.glob("critique_*.json")):
        if request.name.endswith(".answer.json") or request.with_suffix(".answer.json").exists():
            continue
        try:
            if counted_critiques(requests) >= allowed:
                answer = {"refused": f"critique.py runs at most {allowed} times."}
            else:
                draft = EpisodeScript.model_validate_json(json.loads(request.read_text(encoding="utf-8"))["draft"])
                length = draft_length(context["length"], draft, context.get("rate", SPOKEN_WORDS_PER_MINUTE)) \
                    if "length" in context else None
                prompt = instructions("story_critique") + "\n" + json.dumps(
                    critique_input(getattr(pipeline, "config", None), draft, length, story), ensure_ascii=False)
                answer = pipeline.invoke(prompt, StoryCritique, CRITIQUE_VERSION).model_dump()
        except (AppError, ValidationError, ValueError, KeyError) as exc:
            answer = {"error": str(exc), "code": getattr(exc, "code", "invalid_request")}
        write_json(request.with_suffix(".answer.json"), answer)
        served += 1
    return served


def request_critique(folder: Path, name: str) -> dict:
    """critique.py: the draft goes to the run as a request; the answer comes back when the run has made the call."""
    requests = folder / "requests"
    requests.mkdir(exist_ok=True)
    number = len([p for p in requests.glob("critique_*.json") if not p.name.endswith(".answer.json")]) + 1
    request = requests / f"critique_{number}.json"
    try:
        draft = (folder / name).read_text(encoding="utf-8")
    except OSError as exc:
        return {"error": f"{name} cannot be read: {exc}"}
    write_json(request, {"draft": draft})
    deadline = time.monotonic() + CRITIQUE_WAIT
    pauses = poll_pauses()
    while time.monotonic() < deadline:
        answer = request.with_suffix(".answer.json")
        if answer.is_file():
            return json.loads(answer.read_text(encoding="utf-8"))
        time.sleep(next(pauses))
    return {"error": "No critique came back in time; go on without it."}


def session_access(pipeline, command) -> tuple[bool, dict]:
    """Whether the session bills an Anthropic key, and its environment. A run whose text provider is ``claude_api``
    runs it on the run's key (D-184), every other run on the claude.ai login (D-181)."""
    from .claude_code import FIRST_PARTY, claude_api_environment, claude_environment, login_status
    if (getattr(pipeline, "text_generation", None) or {}).get("provider") == "claude_api":
        key = (getattr(pipeline.adapter, "anthropic_key", None) or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            raise AppError("Der Erzähl-Agent über den API-Key braucht einen Anthropic-API-Key: in den Einstellungen "
                           "eingeben.", code="anthropic_key_required", status="blocked")
        return True, claude_api_environment(AGENT_MODEL, key)
    status = login_status(command)
    # Only Anthropic's own claude.ai login: a login routed to Bedrock, Vertex or Foundry is another provider's account
    # (2026-11-14 review).
    if not status["logged_in"] or status["auth_method"] != "claude.ai" or \
            status.get("api_provider") not in FIRST_PARTY:
        raise AppError("Der Erzähl-Agent braucht die Claude-Abo-Anmeldung (claude.ai: claude auth login) oder Claude "
                       "über den API-Key als Textanbieter.", code="authentication_required", status="blocked")
    return False, claude_environment(AGENT_MODEL)


def session_failure(answer: dict, returncode: int | None, stderr: str, billed: bool) -> AppError | None:
    """The error a session ended with, read from the CLI's result envelope as every Claude call's failure is
    (claude_code.classify_claude_failure, by the session's login or key), or None for a session that finished.
    2026-11-14 review: the run read only draft.json, so a quota, an expired login or the money cap stopped a session
    as story_agent_failed, without the quota wait and its automatic resume, or accepted the half-done draft it left."""
    subtype = str(answer.get("subtype") or "")
    if returncode == 0 and not answer.get("is_error") and not subtype.startswith("error"):
        return None
    from .claude_code import classify_claude_failure
    message = answer.get("result") or answer.get("raw") or stderr
    return classify_claude_failure(message, subtype=answer.get("subtype"), auth="api_key" if billed else "subscription")


def run_agent(pipeline, folder: Path, timeout: float = SESSION_TIMEOUT, context: dict | None = None,
              story: list | None = None, continued: bool = False) -> dict:
    """One session in ``folder``, counted as one call of the run (calls/call_NNN with its usage and the cost the
    CLI reports), while the run answers its critique requests with ``context`` (session_context) and ``story`` (the
    story material). A session on an Anthropic key is billed: reserved under the run's money limit, capped at
    SESSION_BUDGET_USD and settled with the cost the CLI reports (billing.json, as provider_pool bills every claude_api
    call). A session that ends on an error raises it (session_failure); ``continued`` tells the session it picks up an
    earlier one's files."""
    from .claude_code import claude_command
    from .models import now
    from .process import stop_quietly
    from .provider_pool import bill
    from .research import refund_call, reserve_call, settle_call
    command = claude_command()
    billed, environment = session_access(pipeline, command)
    number = reserve_call(pipeline.work, pipeline.limits(), search=False, billed=billed)
    call = pipeline.work / "calls" / f"call_{number:03d}"
    call.mkdir(parents=True, exist_ok=True)
    write_json(call / "output_schema.json", {"title": "StorySession", "story_folder": folder.name})
    row = {"attempt": 1, "provider": "claude_api", "model": AGENT_MODEL, "prompt_version": SESSION_VERSION,
           "started_at": now()}
    started = settled = False
    try:
        with open(folder / "session_stdout.json", "w", encoding="utf-8") as stdout, \
                open(folder / "session_stderr.txt", "w", encoding="utf-8") as stderr:
            if billed:
                bill(call, {**row, "state": "started", "usd": None})
            process = subprocess.Popen(session_command(command, budget_usd=SESSION_BUDGET_USD if billed else None,
                                                       commands=command_prefix(folder), continued=continued),
                                       cwd=folder, env=environment, stdout=stdout, stderr=stderr, text=True,
                                       encoding="utf-8")
            started = True
            deadline = time.monotonic() + timeout
            pauses = poll_pauses()
            while process.poll() is None:
                if serve_critiques(pipeline, folder, context, story):
                    # After an answer the agent goes on at once, so the run looks quickly again.
                    pauses = poll_pauses()
                if time.monotonic() > deadline:
                    # The whole tree: a command the session runs (critique.py waits up to half an hour) dies with it.
                    stop_quietly(process)
                    raise AppError("Der Erzähl-Agent hat sein Zeitlimit überschritten.", code="story_agent_timeout",
                                   status="blocked")
                time.sleep(next(pauses))
            serve_critiques(pipeline, folder, context, story)
        raw = (folder / "session_stdout.json").read_text(encoding="utf-8")
        try:
            answer = json.loads(raw or "{}")
        except ValueError:
            answer = {"raw": raw[-4000:]}
        if not isinstance(answer, dict):
            answer = {"raw": raw[-4000:]}
        write_json(folder / "session.json", {"returncode": process.returncode, **answer})
        cost = answer.get("total_cost_usd")
        if billed:
            priced = isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0
            bill(call, {**row, "state": "priced" if priced else "unpriced", "usd": cost if priced else None,
                        "code": None, "ended_at": now()}, replace=True)
            settled = True
        usage = answer.get("usage", {})
        write_json(call / "metadata.json", {"provider": "claude_api" if billed else "claude_code", "model": AGENT_MODEL,
                                            "reasoning_effort": AGENT_EFFORT, "prompt_version": SESSION_VERSION,
                                            "usage": usage, "reported_cost_usd": answer.get("total_cost_usd"),
                                            "num_turns": answer.get("num_turns"),
                                            "duration_ms": answer.get("duration_ms"),
                                            "returncode": process.returncode})
        write_json(call / "response.json", {"result": answer.get("result"), "is_error": answer.get("is_error")})
        failure = session_failure(answer, process.returncode,
                                  (folder / "session_stderr.txt").read_text(encoding="utf-8", errors="replace")[-4000:],
                                  billed)
        if failure is not None:
            raise failure
        return answer
    except BaseException as exc:
        if not started:
            refund_call(pipeline.work, number, search=False)
        elif process.poll() is None:
            # A failure while serving its critiques ends the session too; left running, it waited half an hour for an
            # answer that never came and held the run folder (found by the suite, 2026-11-01).
            stop_quietly(process)
        if billed and not settled:
            # A started session may have run model work without a reported cost; one that never started is free.
            bill(call, {**row, "state": "unpriced" if started else "free", "usd": None,
                        "code": getattr(exc, "code", "interrupted"), "ended_at": now()}, replace=True)
        raise
    finally:
        settle_call(pipeline.work, number)


def write_episode(pipeline, plan, entry) -> list[Path]:
    """The story stage of one episode: the agent's folder, its session, and its draft checked as every draft is,
    accepted as drafts/<episode_id>.json with the episode as the agent planned it (story/<episode_id>/entry.json)."""
    from .cast import cast_note
    entry = revision_entry(pipeline, entry)
    folder = pipeline.work / "story" / entry.episode_id
    destination = pipeline.work / "drafts" / f"{entry.episode_id}.json"
    stamp = destination.with_suffix(".checkpoint.json")
    material_data, context = material(pipeline, plan, entry)
    revising = "revision" in material_data
    critiques = REVISION_CRITIQUES if revising else CRITIQUES
    cast = cast_note(pipeline.config)
    task = task_text(pipeline.config.language, entry.target_minutes, critiques, cast=cast, revision=revising,
                     commands=command_prefix(folder))
    signature = digest({"version": SESSION_VERSION, "input": pipeline.input_hash, "material": material_data,
                        "task": task})
    outputs = [destination, stamp, folder / "entry.json", folder / "plan.md"]
    saved = read_optional_json(stamp, {}) if stamp.is_file() else {}
    if destination.is_file() and isinstance(saved, dict) and saved.get("sha256") == file_hash(destination):
        # A draft a session before v6 left and the run accepted stands too, with the task and the length it then had.
        legacy = digest({"version": LEGACY_SESSION_VERSION, "input": pipeline.input_hash,
                         "material": {**material_data, "length": legacy_length(entry, context["rate"])},
                         "task": task_text(pipeline.config.language, entry.target_minutes, critiques, cast=cast,
                                           revision=revising)})
        if saved.get("input_hash") in (signature, legacy):
            return [path for path in outputs if path.is_file()]
    # The signature the folder's last session was prepared with, kept beside it (prepare).
    prepared = (read_optional_json(commands_folder(folder) / "context.json", {}) or {}).get("signature")
    earlier = None
    if (folder / "session.json").exists() or (folder / "draft.json").exists():
        # An earlier session's files stay beside the new one (attempts/NN), never under it.
        attempt = folder / "attempts" / f"{len(list((folder / 'attempts').glob('*'))) + 1:02d}"
        attempt.mkdir(parents=True)
        for path in list(folder.iterdir()):
            if path.name != "attempts":
                shutil.move(str(path), attempt / path.name)
        earlier = attempt if prepared == signature else None
    files = session_files(pipeline, plan, entry, material_data)
    # A session that stopped before its draft was accepted (a quota, a timeout, a failed check) on this same material
    # and task: the next one starts from what it left instead of starting over (2026-11-14 review).
    left = {name: (earlier / name).read_text(encoding="utf-8") for name in CONTINUED_FILES
            if earlier is not None and (earlier / name).is_file()}
    files.update(left)
    full = prepare(folder, material=material_data, context={**context, "signature": signature}, task=task,
                   critiques=critiques, files=files)
    hashes = command_hashes(folder)
    run_agent(pipeline, folder, session_timeout(entry.target_minutes), full, material_data.get("story_material"),
              bool({"draft.json", "plan.md"} & set(left)))
    if command_hashes(folder) != hashes:
        raise AppError("Die Befehle oder der Prüfkontext neben dem Ordner des Erzähl-Agenten wurden während der Sitzung "
                       "verändert; die Fassung wird nicht übernommen.", code="story_agent_failed", status="blocked")
    try:
        draft = EpisodeScript.model_validate_json((folder / "draft.json").read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise AppError(f"Der Erzähl-Agent hat keine gültige Fassung abgegeben: {exc}", code="story_agent_failed",
                       status="blocked") from exc
    # Checked with the run's own context, never with a file the session could change.
    errors = draft_errors(folder, draft, context=full)
    if errors:
        write_json(folder / "errors.json", errors)
        raise AppError("Die Fassung des Erzähl-Agenten verletzt die Prüfungen: " + " ".join(errors),
                       code="story_agent_failed", status="blocked")
    planned, _ = planned_entry(entry, folder)
    write_json(folder / "entry.json", planned.model_dump())
    write_json(destination, draft.model_dump())
    write_json(stamp, {"input_hash": signature, "sha256": file_hash(destination)})
    return [path for path in outputs if path.is_file()]


# --- the commands -----------------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m podcast_automate.story_agent")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args(argv)
    folder = args.folder.resolve()
    context = load_context(folder)
    if args.command == "check":
        path = (folder / (args.arguments[0] if args.arguments else "draft.json")).resolve()
        try:
            draft = EpisodeScript.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            answer = {"passed": False, "errors": [f"{path.name} does not parse as an episode script: {exc}"]}
        else:
            errors = draft_errors(folder, draft, context=context)
            # The words as validate_script counts them, the window for this draft's own pauses and the most words
            # its pauses leave within 60 minutes (draft_length).
            answer = {"passed": not errors, "errors": errors, **context["length"],
                      **draft_length(context["length"], draft, context.get("rate", SPOKEN_WORDS_PER_MINUTE))}
    elif args.command == "critique":
        answer = request_critique(folder, args.arguments[0] if args.arguments else "draft.json")
    elif args.command == "search":
        answer = search(Path(context["root"]), " ".join(args.arguments))
    else:
        answer = read(Path(context["root"]), " ".join(args.arguments))
    print(json.dumps(answer, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
