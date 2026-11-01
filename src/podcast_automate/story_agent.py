"""The story agent: a Claude Code session that plans and writes one narrated episode (D-181).

The narration lab (evals/narration_lab, 2026-10-27) measured it against every single-call writer: it reads the
material and the cited passages, finds the turn, plans its own chapters after the story material, writes, checks
its facts and the script checks itself, asks an editor up to four times and changes only what a point names, and
searches the project's own research corpus for the scenes the findings only name. Its drafts scored 8.0 to 8.5 on the
lab's judge where the pipeline's writer scored about 5.5.

A session works in ``runs/<run_id>/story/<episode_id>/``: ``material.json`` and ``TASK.md`` in, ``plan.md``,
``draft.json`` and optionally ``outline.json`` and ``sources_used.json`` out. Its only commands are the four of this
module (``python -m podcast_automate.story_agent <command>``), reached through small scripts in the folder:
``check`` (the run's script checks on the draft), ``critique`` (the editor's rubric, a model call of the run),
``search`` and ``read`` (the project's research corpus). Code checks the draft again when the session ends.
"""
from __future__ import annotations

import argparse
import json
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
from .models import Contract, EpisodeScript, NonEmpty
from .script_checks import quotation_errors, validate_script
from .script_models import EpisodePlan, ScenePlan
from .storage import digest, file_hash, write_json

AGENT_MODEL = "claude-opus-5-5"
AGENT_EFFORT = "xhigh"
CRITIQUES = 4
# The plan's own length. The writing prompt's 1.3 for Sonnet (script_checks.WRITER_TARGET_FACTORS) makes up for a
# writer that stops at 71 % of its target; the agent counts its words with check.py and reaches the target, so 1.3
# gave the lean research test of 2026-11-01 a draft of 55 minutes for a planned 44, which the script review blocked
# as scope. The lab's best draft (Milgram s1, 8.5) had 1,306 words for ten minutes, the plan's length.
TARGET_FACTOR = 1.0
SESSION_VERSION = "story_agent.v3-framing"
ORIENTATION_VERSION = "orientation_sheet.v1"
ORIENTATION_CHECK_VERSION = "orientation_check.v1"
CRITIQUE_VERSION = "story_critique.v1"
# How long a critique request waits for its answer, and how often the run looks for requests.
CRITIQUE_WAIT = 30 * 60
POLL_SECONDS = 2
# A session read, planned, wrote and revised a ten-minute episode in 31 to 60 minutes in the lab, so its ceiling grows
# with the episode: eight minutes per planned minute, at least two hours (session_timeout).
SESSION_TIMEOUT = 120 * 60
SESSION_SECONDS_PER_MINUTE = 8 * 60
COMMANDS = ("check", "critique", "search", "read")
# Bash may run exactly these scripts; the folder is the session's working directory, and no other tool (web search,
# web fetch, MCP servers, any other shell command) is offered.
TOOLS = "Read,Write,Edit,Glob,Grep,Bash"
ALLOWED_TOOLS = ",".join(["Read", "Write", "Edit", "Glob", "Grep",
                          *(f"Bash(python {name}.py:*)" for name in COMMANDS)])
LENGTH_SLACK = 1.1
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


def task_text(language: str, minutes: float, critiques: int = CRITIQUES) -> str:
    """TASK.md: the agent's instructions (data/story_agent_task.md, a document rather than one prompt paragraph)."""
    raw = files("podcast_automate").joinpath("data/story_agent_task.md").read_text(encoding="utf-8")
    return raw.format(language=language, minutes=f"{minutes:g}", critiques=critiques)


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


def draft_errors(folder: Path, draft: EpisodeScript, corpus=None) -> list[str]:
    """Every defect of the folder's draft: the outline's, the run's script checks against the planned episode
    (structure, citations and the quotation limit over the given passages), echo lines, the quotation limit over the
    corpus sections the agent used, and the length cap."""
    context = json.loads((folder / "context.json").read_text(encoding="utf-8"))
    entry, errors = planned_entry(EpisodePlan.model_validate(context["entry"]), folder)
    errors += validate_script(draft, entry, roles=tuple(context["roles"]))
    if context.get("assembled"):
        errors += quotation_errors(draft, context["sources"])
    errors += echo_errors(draft)
    documents, unknown = used_documents(folder, Path(context["root"]), corpus)
    errors += unknown
    errors += quotation_errors(draft, documents)
    # A segment cites only a section it had: one of material.json's or one it recorded in sources_used.json (D-182).
    material_data = json.loads((folder / "material.json").read_text(encoding="utf-8"))
    had = {section["reference"] for section in material_data.get("sections", [])} | {
        section["reference"] for document in documents for section in document["sections"]}
    errors += [f"{segment.segment_id}: source_refs names {ref}, which is neither one of material.json's sections nor "
               "a section listed in sources_used.json." for segment in draft.segments for ref in segment.source_refs
               if ref not in had]
    words = sum(len(segment.text.split()) for segment in draft.segments)
    if words > context["length"]["target_words"] * LENGTH_SLACK:
        errors.append(f"{words} words: stay within a tenth above length.target_words "
                      f"({context['length']['target_words']}).")
    return errors


# --- the research corpus --------------------------------------------------------------------------------------------

def corpus_sections(root: Path) -> list[tuple[str, str, str]]:
    """Every section of the project's research corpus as (reference, source title, text)."""
    from .scripting import load_research
    from .storage import load_project
    _, _, _, sources, _ = load_research(root, load_project(root))
    return [(f"{source.id}#{section.id}", source.title, section.text)
            for source in sources.sources for section in source.sections]


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
# {command}: one of the story agent's commands, run by the package's own interpreter (story_agent.prepare).
arguments = sys.argv[1:] or (["draft.json"] if {command!r} in ("check", "critique") else [])
result = subprocess.run([{python!r}, "-m", "podcast_automate.story_agent", {command!r}, "--folder",
                         os.path.dirname(os.path.abspath(__file__)), *arguments],
                        capture_output=True, text=True, encoding="utf-8", env={{**os.environ, "PYTHONIOENCODING": "utf-8"}})
print(result.stdout)
if result.returncode:
    print(result.stderr[-4000:])
'''


def prepare(folder: Path, *, material: dict, context: dict, language: str, minutes: float,
            critiques: int = CRITIQUES) -> None:
    """The session's folder: material.json, TASK.md, context.json (what the commands need: the episode, the hosts,
    the given passages, the length, the project root and the run's call settings) and the four command scripts."""
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / "material.json", material)
    write_json(folder / "context.json", {**context, "critiques": critiques})
    (folder / "TASK.md").write_text(task_text(language, minutes, critiques), encoding="utf-8")
    for command in COMMANDS:
        (folder / f"{command}.py").write_text(COMMAND_SCRIPT.format(command=command, python=sys.executable),
                                              encoding="utf-8")


def session_timeout(minutes: float) -> float:
    return max(SESSION_TIMEOUT, minutes * SESSION_SECONDS_PER_MINUTE)


def session_command(cli: list[str], model: str = AGENT_MODEL, effort: str = AGENT_EFFORT) -> list[str]:
    return [*cli, "-p", "Read TASK.md in this folder and do the task it describes, start to finish.",
            "--model", model, "--effort", effort, "--permission-mode", "dontAsk",
            "--tools", TOOLS, "--allowedTools", ALLOWED_TOOLS, "--no-session-persistence", "--strict-mcp-config",
            "--setting-sources", "", "--disable-slash-commands", "--output-format", "json"]


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
    sheet = pipeline.invoke(drafting, OrientationSheet, ORIENTATION_VERSION)
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


def material(pipeline, plan, entry) -> tuple[dict, dict]:
    """material.json for the agent and context.json for its commands: the outline entry, the series context, the
    story material, the findings with their limits, the passages they cite, the checked orientation, the word
    budget and the brief; the commands get the entry, the hosts, the given passages and the root."""
    from .editorial import EPISODE_FRAMING, episode_series_context
    from .models import host_roles_of
    from .narration import allowed_sections
    from .polishing import host_roles
    from .script_checks import episode_sources, word_budget
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
    length = word_budget(entry, TARGET_FACTOR)
    material_data = {
        "brief": {"language": config.language, "audience": config.audience_level, "host_names": config.host_names,
                  "host_roles": host_roles(config), "style_notes": pipeline.style_notes},
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
    context = {"entry": entry.model_dump(), "roles": list(host_roles_of(config)),
               "assembled": bool(dossier.assembled), "sources": sources if dossier.assembled else [],
               "length": length, "root": str(pipeline.root)}
    return material_data, context


def script_text(script: EpisodeScript) -> str:
    return "\n".join(f"[{s.segment_id}] {s.speaker_id}: {s.text}" for s in script.segments)


def serve_critiques(pipeline, folder: Path) -> None:
    """Answer the critique requests the session left (requests/critique_N.json) with a call of the run, so the
    budget, the receipts and the provider choice apply as to every other call."""
    from .prompts import instructions
    requests = folder / "requests"
    if not requests.is_dir():
        return
    allowed = json.loads((folder / "context.json").read_text(encoding="utf-8"))["critiques"]
    for request in sorted(requests.glob("critique_*.json")):
        if request.name.endswith(".answer.json") or request.with_suffix(".answer.json").exists():
            continue
        answered = len(list(requests.glob("critique_*.answer.json")))
        try:
            if answered >= allowed:
                answer = {"refused": f"critique.py runs at most {allowed} times."}
            else:
                draft = EpisodeScript.model_validate_json(json.loads(request.read_text(encoding="utf-8"))["draft"])
                prompt = instructions("story_critique") + "\n" + json.dumps({"script": script_text(draft)},
                                                                           ensure_ascii=False)
                answer = pipeline.invoke(prompt, StoryCritique, CRITIQUE_VERSION).model_dump()
        except (AppError, ValidationError, ValueError, KeyError) as exc:
            answer = {"error": str(exc), "code": getattr(exc, "code", "invalid_request")}
        write_json(request.with_suffix(".answer.json"), answer)


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
    while time.monotonic() < deadline:
        answer = request.with_suffix(".answer.json")
        if answer.is_file():
            return json.loads(answer.read_text(encoding="utf-8"))
        time.sleep(POLL_SECONDS)
    return {"error": "No critique came back in time; go on without it."}


def run_agent(pipeline, folder: Path, timeout: float = SESSION_TIMEOUT) -> dict:
    """One session in ``folder``, counted as one call of the run (calls/call_NNN with its usage and the cost the
    CLI reports), while the run answers its critique requests."""
    from .claude_code import claude_command, claude_environment, login_status
    from .research import refund_call, reserve_call, settle_call
    command = claude_command()
    status = login_status(command)
    if not status["logged_in"] or status["auth_method"] != "claude.ai":
        raise AppError("Der Erzähl-Agent braucht die Claude-Abo-Anmeldung (claude.ai): claude auth login.",
                       code="authentication_required", status="blocked")
    number = reserve_call(pipeline.work, pipeline.limits(), search=False, billed=False)
    call = pipeline.work / "calls" / f"call_{number:03d}"
    call.mkdir(parents=True, exist_ok=True)
    write_json(call / "output_schema.json", {"title": "StorySession", "story_folder": folder.name})
    started = False
    try:
        with open(folder / "session_stdout.json", "w", encoding="utf-8") as stdout, \
                open(folder / "session_stderr.txt", "w", encoding="utf-8") as stderr:
            process = subprocess.Popen(session_command(command), cwd=folder, env=claude_environment(AGENT_MODEL),
                                       stdout=stdout, stderr=stderr, text=True, encoding="utf-8")
            started = True
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                serve_critiques(pipeline, folder)
                if time.monotonic() > deadline:
                    process.kill()
                    raise AppError("Der Erzähl-Agent hat sein Zeitlimit überschritten.", code="story_agent_timeout",
                                   status="blocked")
                time.sleep(POLL_SECONDS)
            serve_critiques(pipeline, folder)
        raw = (folder / "session_stdout.json").read_text(encoding="utf-8")
        try:
            answer = json.loads(raw or "{}")
        except ValueError:
            answer = {"raw": raw[-4000:]}
        write_json(folder / "session.json", {"returncode": process.returncode, **answer})
        usage = answer.get("usage", {})
        write_json(call / "metadata.json", {"provider": "claude_code", "model": AGENT_MODEL,
                                            "reasoning_effort": AGENT_EFFORT, "prompt_version": SESSION_VERSION,
                                            "usage": usage, "reported_cost_usd": answer.get("total_cost_usd"),
                                            "num_turns": answer.get("num_turns"),
                                            "duration_ms": answer.get("duration_ms"),
                                            "returncode": process.returncode})
        write_json(call / "response.json", {"result": answer.get("result"), "is_error": answer.get("is_error")})
        return answer
    except BaseException:
        if not started:
            refund_call(pipeline.work, number, search=False)
        raise
    finally:
        settle_call(pipeline.work, number)


def write_episode(pipeline, plan, entry) -> list[Path]:
    """The story stage of one episode: the agent's folder, its session, and its draft checked as every draft is,
    accepted as drafts/<episode_id>.json with the episode as the agent planned it (story/<episode_id>/entry.json)."""
    folder = pipeline.work / "story" / entry.episode_id
    destination = pipeline.work / "drafts" / f"{entry.episode_id}.json"
    stamp = destination.with_suffix(".checkpoint.json")
    material_data, context = material(pipeline, plan, entry)
    task = task_text(pipeline.config.language, entry.target_minutes)
    signature = digest({"version": SESSION_VERSION, "input": pipeline.input_hash, "material": material_data,
                        "task": task})
    outputs = [destination, stamp, folder / "entry.json", folder / "plan.md"]
    if stamp.is_file() and destination.is_file() and json.loads(stamp.read_text(encoding="utf-8")) == {
            "input_hash": signature, "sha256": file_hash(destination)}:
        return [path for path in outputs if path.is_file()]
    if (folder / "session.json").exists() or (folder / "draft.json").exists():
        # An earlier session's files stay beside the new one (attempts/NN), never under it.
        attempt = folder / "attempts" / f"{len(list((folder / 'attempts').glob('*'))) + 1:02d}"
        attempt.mkdir(parents=True)
        for path in list(folder.iterdir()):
            if path.name != "attempts":
                shutil.move(str(path), attempt / path.name)
    prepare(folder, material=material_data, context=context, language=pipeline.config.language,
            minutes=entry.target_minutes)
    run_agent(pipeline, folder, session_timeout(entry.target_minutes))
    try:
        draft = EpisodeScript.model_validate_json((folder / "draft.json").read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise AppError(f"Der Erzähl-Agent hat keine gültige Fassung abgegeben: {exc}", code="story_agent_failed",
                       status="blocked") from exc
    errors = draft_errors(folder, draft)
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
    context = json.loads((folder / "context.json").read_text(encoding="utf-8"))
    if args.command == "check":
        path = (folder / (args.arguments[0] if args.arguments else "draft.json")).resolve()
        try:
            draft = EpisodeScript.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            answer = {"passed": False, "errors": [f"{path.name} does not parse as an episode script: {exc}"]}
        else:
            errors = draft_errors(folder, draft)
            answer = {"passed": not errors, "errors": errors,
                      "words": sum(len(segment.text.split()) for segment in draft.segments), **context["length"]}
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
