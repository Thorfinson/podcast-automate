"""Narration workshop: fixed ten-minute cases through the real script pipeline, run by hand.

A case (cases/<case>.json) is one episode of about ten minutes, set into the real outline of its project in place of
the episode it is cut from, with an element list (cases/<case>.md) of what its sources tell: the story elements by
weight (M must, S should, K can) and the traps a telling must avoid. Everything before the script stays as the case
fixes it, so a difference between two variants comes from what the variant changes.

ladder  Stage 0b: the same pipeline writes the episode from three inputs.
          I1  the findings and their source passages, without story material;
          I2  as the pipeline runs now: story material from the cited sections, and the story research;
          I3  every section of the element list attached as evidence to the finding it belongs to, so story material
              and writer see it (the best possible input); no story research.
        Each input is one script run in its own copy of the project, stopped after writing: the teaching design is
        made once per input and the writer asked --samples times. A stopped run resumes where it stopped.
judge   Scores every kept draft against the element list (judge_coverage.txt) and on the quality rubric
        (judge_quality.txt), and the case's findings and each input's story material against the element list, so
        the report shows where an element is lost. A saved judgment is kept.
report  The tables of the saved judgments; no calls.
status  Where each case and input stands: the run's stage and activity, its calls and the newest one, the samples
        kept and judged; --watch clears the screen and shows it again every --interval seconds (20), as watch does.
        Every case under --output when none is named.
write   Stage 1, the writer alone: on the case's I3 run (its outline, design, story material and sources frozen),
        the baseline (src prompts) and each named variant (variants/<name>/*.txt in place of the src prompts of the
        same name) write --samples drafts, every variant in its own process and all at once. Then every draft is
        judged on coverage and quality, and each variant's sample n against the baseline's sample n, twice with the
        order swapped. write-report prints the table again; write-variant is the per-variant process.
orient  The case's orientation sheet (who, what, when, where of every person, group, place, period and term), drafted by
        --model from the I3 run's episode material and checked by --check-model; a write variant whose
        process.json says "orientation" hands it to the writer (orientation_use.txt).
design  Stage 2, the teaching design: each named variant runs the case's teaching stage anew on the I3 inputs, with
        the I3 run's story material, under its prompts, then writes --samples drafts on its design. The drafts join
        the write variants; pairs compare them with --against (default baseline).

    python evals/narration_lab/run.py ladder kuchavez --output <dir> [--live] [--inputs I1,I2,I3] [--samples 2]
    python evals/narration_lab/run.py judge kuchavez --output <dir> [--live] [--judge-model claude-sonnet-5-5]
    python evals/narration_lab/run.py report kuchavez --output <dir>
    python evals/narration_lab/run.py status --output <dir> [--watch]
    python evals/narration_lab/run.py write kuchavez --output <dir> [--live] --variants scene,payoff [--samples 3]
    python evals/narration_lab/run.py design kuchavez --output <dir> [--live] --variants storydesign --against storyplain

Without --live, ladder and judge only check the case: its episode against the dossier, its oracle sections and its
element list. A model's score is not the user's verdict, and the element list holds what the sources tell, not what a
ten-minute episode must tell.
"""
import argparse
import contextlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from pydantic import Field

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))
# A write variant runs in its own process with its prompt folder (prepared_prompts), set before any module composes a
# rule block from the prompts at import time (editorial.LISTENABILITY and others).
OVERLAY = os.environ.get("NARRATION_LAB_PROMPTS")
if OVERLAY:
    from podcast_automate import prompts as _prompts  # noqa: E402
    _prompts._folder = lambda: Path(OVERLAY)
from podcast_automate import scripting  # noqa: E402
from podcast_automate.errors import AppError  # noqa: E402
from podcast_automate.models import (Contract, EpisodeScript, NonEmpty, RunManifest, StageRecord,  # noqa: E402
                                     host_labels)
from podcast_automate.narration import narration_metrics  # noqa: E402
from podcast_automate.provider_pool import text_generation_settings  # noqa: E402
from podcast_automate.research_models import Evidence, SourceDocument  # noqa: E402
from podcast_automate.script_artifacts import render_script  # noqa: E402
from podcast_automate.script_models import EpisodePlan, SeriesPlan  # noqa: E402
from podcast_automate.scripting import build_adapter, run_script  # noqa: E402
from podcast_automate.storage import digest, load_project, read_yaml, write_json, write_yaml  # noqa: E402
from podcast_automate.subscriptions import parse_iso, quota_retry_at  # noqa: E402
from podcast_automate.teaching import TeachingPlan  # noqa: E402

PROJECTS = REPO / "projects"
INPUTS = ("I1", "I2", "I3")
# Parts of a project a script run never reads; the Studio's own files except the run settings it does read.
# models/ and reports/ stay: the research run's outputs include models/source_index.yaml and its quality report.
SKIP = {"cache", "episodes", "exports", "logs", "probes", "publish"}
STUDIO_FILES = {"audio.json", "execution.json", "text.json"}
# Read-only and large: hard-linked file by file. A script run only adds files there, never rewrites one.
LINKED = {"inputs", "models", "runs", "sources"}
ROW = re.compile(r"^\| ([A-Z]\d\d) \| ([MSK]) \| (.+?) \| (.+?) \|$")
TRAP = re.compile(r"^\| (F\d\d) \| (.+?) \|$")
COVERAGE_VERSION = "workshop_coverage.v1"
# v2 (2026-10-26, the user): every person introduced the first time, and no name the story does not need.
QUALITY_VERSION = "workshop_quality.v3-judge"
# The critic keeps the v2 rubric and its version, so a writer's critique is asked exactly as before.
CRITIQUE_VERSION = "workshop_quality.v2-names"
PAIR_VERSION = "workshop_pair.v2-names"
# Prompts of the workshop itself that a variant may replace; they are not among src's prompts.
LAB_PROMPTS = {"narrative_writer.txt", "dramaturg.txt"}


# --- the case -----------------------------------------------------------------------------------------------------

def element_list(markdown):
    """The elements ({id, weight, element}) and traps ({id, trap}) of a case's element list."""
    elements, traps = [], []
    for line in markdown.splitlines():
        if match := ROW.match(line.strip()):
            elements.append({"id": match[1], "weight": match[2], "element": match[3]})
        elif match := TRAP.match(line.strip()):
            traps.append({"id": match[1], "trap": match[2]})
    return {"elements": elements, "traps": traps}


def load_case(name):
    case = json.loads((HERE / "cases" / f"{name}.json").read_text(encoding="utf-8"))
    case["entry"] = EpisodePlan.model_validate(case["episode"])
    case["list"] = element_list((HERE / "cases" / case["elements"]).read_text(encoding="utf-8"))
    return case


def research_run(project):
    return json.loads((project / "research" / "latest.json").read_text(encoding="utf-8"))["run_id"]


def lab_outline(case):
    """The real outline of the case's project with the case's episode in place of the one it is cut from, and that
    outline run's knowledge model."""
    work = PROJECTS / case["project"] / "runs" / case["outline_run"]
    plan = SeriesPlan.model_validate_json((work / "series_plan.json").read_text(encoding="utf-8"))
    entry = case["entry"]
    if entry.episode_id not in {e.episode_id for e in plan.episodes}:
        raise SystemExit(f"{entry.episode_id} is not in the outline of {case['outline_run']}.")
    episodes = [entry if e.episode_id == entry.episode_id else e for e in plan.episodes]
    knowledge = json.loads((work / "knowledge_model.json").read_text(encoding="utf-8"))
    return plan.model_copy(update={"episodes": episodes}), knowledge


def extra_documents(case):
    """The sources of the oracle that the project's research never read (a story research's supplement), with their
    context entries."""
    documents, context = [], []
    for item in case["oracle"]["documents"]:
        folder = PROJECTS / case["project"] / "runs" / item["run"] / item["folder"]
        index = json.loads((folder / "source_index.json").read_text(encoding="utf-8"))
        index = index.get("value", index)
        documents += [SourceDocument.model_validate(s) for s in index["sources"] if s["id"] == item["source_id"]]
        context += [c for c in json.loads((folder / "source_context.json").read_text(encoding="utf-8"))
                    if c["source_id"] == item["source_id"]]
    return documents, context


def excerpt(text):
    words, kept = text.split(), []
    for word in words:
        if len(" ".join([*kept, word])) > 150:
            break
        kept.append(word)
    return " ".join(kept) or text[:150]


def oracle_research(loaded, case):
    """I3: the research as load_research returns it, with each oracle section attached as evidence to its finding."""
    research_id, dossier, discovery, sources, context = loaded
    documents, extra_context = extra_documents(case)
    known = {s.id for s in sources.sources}
    sources = sources.model_copy(update={"sources": [*sources.sources, *[d for d in documents if d.id not in known]]})
    context = [*context, *[c for c in extra_context if c["source_id"] not in known]]
    texts = {f"{s.id}#{x.id}": x.text for s in sources.sources for x in s.sections}
    findings = []
    for finding in dossier.findings:
        cited = {e.reference for e in finding.evidence}
        added = [Evidence(reference=reference, excerpt=excerpt(texts[reference]))
                 for reference in case["oracle"]["sections"].get(finding.id, []) if reference not in cited]
        findings.append(finding.model_copy(update={"evidence": [*finding.evidence, *added]}) if added else finding)
    return research_id, dossier.model_copy(update={"findings": findings}), discovery, sources, context


def check_case(case):
    """What can be checked without a call: the episode's findings in the dossier, every oracle section readable, the
    element list parsed."""
    project = PROJECTS / case["project"]
    dossier = yaml.safe_load((project / "research" / "dossier.yaml").read_text(encoding="utf-8"))
    ids = {f["id"] for f in dossier["findings"]}
    entry = case["entry"]
    wanted = [*entry.finding_ids, *entry.supporting_finding_ids]
    texts = {}
    run = research_run(project)
    for path in (project / "sources" / "processed" / run).glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data = data.get("value", data)
        texts.update({f"{data['id']}#{s['id']}": s["text"] for s in data.get("sections", [])})
    documents, _ = extra_documents(case)
    texts.update({f"{d.id}#{s.id}": s.text for d in documents for s in d.sections})
    oracle = [r for refs in case["oracle"]["sections"].values() for r in refs]
    lab_outline(case)
    weights = [e["weight"] for e in case["list"]["elements"]]
    return {"case": case["id"], "minutes": entry.target_minutes, "unknown_findings": [f for f in wanted if f not in ids],
            "oracle_sections": len(oracle), "unreadable_oracle_sections": [r for r in oracle if r not in texts],
            "oracle_findings_outside_episode": [f for f in case["oracle"]["sections"] if f not in wanted],
            "elements": {w: weights.count(w) for w in "MSK"}, "traps": len(case["list"]["traps"])}


# --- running the pipeline -----------------------------------------------------------------------------------------

def copy_project(source, target):
    """A working copy of ``source`` with only its research run."""
    keep = research_run(source)
    for folder, names, files in os.walk(source):
        relative = Path(folder).relative_to(source)
        parts = relative.parts
        if parts and parts[0] in SKIP:
            names[:] = []
            continue
        if parts == ("runs",):
            names[:] = [name for name in names if name == keep]
        if parts and parts[0] == "studio":
            names[:] = []
        destination = target / relative
        destination.mkdir(parents=True, exist_ok=True)
        for name in files:
            # runs/latest.json names the copy's own run once it starts.
            if name.endswith(".lock") or parts == ("runs",) or (parts == ("studio",) and name not in STUDIO_FILES):
                continue
            origin, copy = Path(folder) / name, destination / name
            if copy.exists():
                continue
            if parts and parts[0] in LINKED:
                os.link(origin, copy)
            else:
                shutil.copy2(origin, copy)


def no_probes(self, plan=None):
    """No corpus probe in the workshop: the gaps the research left stay as the dossier states them."""
    path = self.probe_path()
    if not path.exists():
        write_json(path, [])
    return []


def empty_material(config, entry, dossier, sources, invoke, folder):
    """I1: the episode without story material, so without a core story and without story research."""
    write_json(folder / "story_material.json", {"input_hash": "workshop-I1", "items": [], "missing": [], "dropped": []})
    return []


def frozen_material(path):
    """story_material as another run collected it (``path``), so a design variant changes nothing before its design."""
    def collect(config, entry, dossier, sources, invoke, folder):
        saved = json.loads(path.read_text(encoding="utf-8"))
        write_json(folder / "story_material.json", saved)
        return saved["items"]
    return collect


@contextlib.contextmanager
def workshop(case, variant, stop="writing", material=None):
    """The pipeline as the variant runs it: the case's outline instead of a planning call, no corpus probe, a stop
    after ``stop``, the variant's input, and with ``material`` a story_material.json to use instead of a call."""
    plan, knowledge = lab_outline(case)
    real_execute, real_load = scripting.execute_stages, scripting.load_research

    def execute(root, manifest, path, actions, stop_after=None):
        return real_execute(root, manifest, path, actions, stop_after=stop)
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("podcast_automate.scripting.revision_inputs", lambda *a, **k: (plan, knowledge, None)))
        stack.enter_context(patch("podcast_automate.script_pipeline.ScriptRun.run_probes", no_probes))
        stack.enter_context(patch("podcast_automate.scripting.execute_stages", execute))
        if variant == "I1":
            stack.enter_context(patch("podcast_automate.script_pipeline.story_material", empty_material))
        if variant == "I3":
            stack.enter_context(patch("podcast_automate.scripting.load_research",
                                      lambda root, config: oracle_research(real_load(root, config), case)))
            stack.enter_context(patch("podcast_automate.script_pipeline.ScriptRun.research_story",
                                      lambda self, *args, **kwargs: False))
        if material is not None:
            stack.enter_context(patch("podcast_automate.script_pipeline.story_material", frozen_material(material)))
        yield


def wait_for_quota(error=None, attempt=0):
    """Sleep until the subscription's quota is back, as the Studio waits before it resumes a paused run."""
    retry = parse_iso(quota_retry_at(error or SimpleNamespace(details={"provider": "claude_code"}), attempt=attempt))
    print(json.dumps({"waiting_for_quota_until": retry.isoformat()}), flush=True)
    time.sleep(max(60.0, retry.timestamp() - time.time() + 120))


def advance(copy, episode, args):
    """The variant's run, started or resumed, until it stops after writing or for a reason other than quota."""
    pointer = copy / "runs" / "latest.json"
    if pointer.is_file():
        run = run_script(copy, resume=True, run_id=json.loads(pointer.read_text(encoding="utf-8"))["run_id"])
    else:
        run = run_script(copy, revise=episode, backend=args.backend, model=args.model, reasoning_effort=args.effort)
    attempt = 0
    while run.status == "waiting_for_quota":
        wait_for_quota(attempt=attempt)
        attempt += 1
        run = run_script(copy, resume=True, run_id=run.run_id)
    return run


def keep_sample(copy, run, case, folder):
    """The run's draft as the next sample: its JSON, a readable rendering and code's metrics."""
    work = copy / "runs" / run.run_id
    episode = case["entry"].episode_id
    draft = EpisodeScript.model_validate_json((work / "drafts" / f"{episode}.json").read_text(encoding="utf-8"))
    config = load_project(copy)
    design = TeachingPlan.model_validate_json((work / "teaching" / episode / "plan.json").read_text(encoding="utf-8"))
    target = folder / "samples" / f"s{len(list((folder / 'samples').glob('s*'))) + 1}"
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "draft.json", draft.model_dump())
    (target / "draft.md").write_text(render_script(draft, host_labels(config)), encoding="utf-8")
    write_json(target / "metrics.json", narration_metrics(draft, config.language, design))
    for name in ("plan.md", "story_material.json", "story_research.json"):
        source = work / "teaching" / episode / name
        if source.is_file() and not (folder / name).exists():
            shutil.copy2(source, folder / name)
    return target


def reset_writing(copy, run, episode):
    """Ask the writer again with everything before it unchanged: the stage pending, the draft and its stamp gone."""
    work = copy / "runs" / run.run_id
    path = work / "run_manifest.yaml"
    manifest = RunManifest.model_validate(read_yaml(path))
    manifest.stages["writing"] = StageRecord()
    write_yaml(path, manifest.model_dump(mode="json"))
    for name in (f"{episode}.json", f"{episode}.checkpoint.json"):
        (work / "drafts" / name).unlink(missing_ok=True)


def ladder_variant(case, variant, args, output):
    folder = output / case["id"] / "ladder" / variant
    copy = output / case["id"] / "projects" / variant
    # Adds only what a copy lacks, so a copy made before a change of SKIP is completed.
    copy_project(PROJECTS / case["project"], copy)
    folder.mkdir(parents=True, exist_ok=True)
    episode = case["entry"].episode_id
    record = {"case": case["id"], "input": variant}
    with workshop(case, variant):
        while len(list((folder / "samples").glob("s*"))) < args.samples:
            run = advance(copy, episode, args)
            record["run_id"] = run.run_id
            if run.stages["writing"].status != "completed":
                record["stopped"] = {name: stage.error.model_dump() for name, stage in run.stages.items() if stage.error}
                break
            keep_sample(copy, run, case, folder)
            if len(list((folder / "samples").glob("s*"))) < args.samples:
                reset_writing(copy, run, episode)
    record["samples"] = len(list((folder / "samples").glob("s*")))
    budget = copy / "runs" / record.get("run_id", "-") / "budget.json"
    record["model_calls"] = json.loads(budget.read_text(encoding="utf-8")).get("model_calls") if budget.is_file() else None
    write_json(folder / "record.json", record)
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return record


def ladder(args, output):
    case = load_case(args.case)
    rows = [check_case(case)]
    print(json.dumps(rows[0], ensure_ascii=False), flush=True)
    if args.live:
        rows += [ladder_variant(case, variant, args, output) for variant in args.inputs]
    return rows


# --- write: stage 1, the writer alone -----------------------------------------------------------------------------

def prepared_prompts(case_id, variant, output):
    """The prompt folder of a write variant: every prompt of src, with the variant's files (variants/<variant>/*.txt)
    in place of their namesakes. ``baseline`` is src unchanged. One folder per case, so two cases started together do
    not rebuild a folder the other's writers are reading."""
    target = output / case_id / "variants" / variant / "prompts"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(REPO / "src" / "podcast_automate" / "prompts", target)
    own = HERE / "variants" / variant
    if variant != "baseline":
        # The workshop's own writer prompt is read from the variant folder (narrative_prompt), not from src.
        files = sorted(f for f in own.glob("*.txt") if f.name not in LAB_PROMPTS)
        if not files and not (own / "process.json").is_file() and not any((own / name).is_file() for name in LAB_PROMPTS):
            raise SystemExit(f"variants/{variant}/ holds neither a prompt file nor a process.json.")
        unknown = [f.name for f in files if not (target / f.name).exists()]
        if unknown:
            raise SystemExit(f"variants/{variant}/ replaces prompts that do not exist: {', '.join(unknown)}")
        for file in files:
            shutil.copy2(file, target / file.name)
    return target


def frozen_pipeline(case, copy):
    """The ScriptRun of the case's I3 run as a resume would build it, for writing only: its outline, teaching design,
    story material and sources stay as that run made them."""
    from podcast_automate.script_pipeline import ScriptRun
    root = copy.resolve()
    run_id = json.loads((root / "runs" / "latest.json").read_text(encoding="utf-8"))["run_id"]
    config = load_project(root)
    research_id, dossier, discovery, sources, context = scripting.load_research(root, config)
    state = scripting.resumed_run(root, run_id)
    text_generation = scripting.text_generation_settings(config, saved=state["saved_backend"])
    notes = scripting.style_notes(root)
    snapshot = state["path"].parent / "project_snapshot.yaml"
    hash_config = scripting.bound_brief(config, read_yaml(snapshot) if snapshot.is_file() else None, "script")
    _, _, input_hash = scripting.run_inputs(hash_config, research_id, dossier, discovery, sources, context, state,
                                            text_generation, notes)
    work, manifest = state["path"].parent, state["manifest"]
    text_generation = scripting.text_switch(work, manifest.input_hash, text_generation)
    pipeline = ScriptRun(root=root, work=work, config=config, manifest=manifest,
                         adapter=build_adapter(config, text_generation, None), input_hash=input_hash,
                         research_id=research_id, dossier=dossier, discovery=discovery, sources=sources, context=context,
                         episode=state["episode"], revision=state["revision"], execution=state["execution"],
                         plan_only=False, previous_outline=None, outline_feedback="",
                         series_review_version=state["series_review_version"], text_generation=text_generation,
                         resume=True, style_notes=notes, probe_key=None)
    pipeline.refresh_foundations(pipeline.selected()[1])
    return pipeline


def starting_point(output, case, process, target):
    """The draft and the last critique a start_from variant's sample of the same number left, so this variant goes on
    from it; nothing for a variant that writes afresh. start_from may name one variant per case."""
    source = process.get("start_from")
    if isinstance(source, dict):
        source = source.get(case["id"])
    if not source:
        return {}
    sample = output / case["id"] / "write" / source / target.name
    critiques = sorted(sample.glob("edit_critique_*.json")) or [sample / "critique.json"]
    critique = critiques[-1]
    return {"start": EpisodeScript.model_validate_json((sample / "draft.json").read_text(encoding="utf-8")),
            "previous": json.loads(critique.read_text(encoding="utf-8")) if critique.is_file() else None}


def with_model(pipeline, process, args):
    """A variant whose process.json names a model writes (and critiques) with it instead of the run's own choice, at
    the process's effort or --effort."""
    if process.get("model"):
        selection = text_generation_settings(pipeline.config, backend=args.backend, model=process["model"],
                                             reasoning_effort=process.get("effort") or args.effort)
        pipeline.adapter = build_adapter(pipeline.config, selection, None)
    return pipeline


def dramaturg_pipeline(case, copy, process, args):
    """A second pipeline on the same run whose adapter is the dramaturg's model (process dramaturg: model, effort)."""
    if not process.get("dramaturg"):
        return None
    return with_model(frozen_pipeline(case, copy), process["dramaturg"], args)


def story_plan(dramaturg, pipeline, entry, told, output, case, variant, target):
    """The dramaturg's plan for one sample (story_plan.json): the material the writer gets, read the way the
    hand-written probes were made (2026-10-27): the one turn the material holds, a perspective, host_b's claim and its
    payoff, plants, beats each resting on named material, and what the material does not hold. The run's scenes and
    core findings stay; the plan rewords titles, questions and steps. One retry with its defects."""
    path = target / "story_plan.json"
    if path.is_file():
        return StoryPlan.model_validate({key: value for key, value in json.loads(path.read_text(encoding="utf-8")).items()
                                         if key != "remaining_defects"})
    material = narrative_prompt(pipeline, told, output, case, variant, instructions=(
        HERE / "variants" / variant / "dramaturg.txt").read_text(encoding="utf-8"))
    known = known_material(material)
    # What code can tell the dramaturg: the core findings no story_material item tells (2026-10-27: it never used
    # demoted on its own, and Kuchavez's continuity debate has no story at all).
    told_ids = {f for item in pipeline.story_for(told) for f in item.get("finding_ids", [])}
    untold = [f for f in told.finding_ids if f not in told_ids]
    material += "\n" + json.dumps({"core_findings_without_story": untold}, ensure_ascii=False)
    prompt = material
    for attempt in range(2):
        plan = invoke_waiting(dramaturg, prompt, StoryPlan, "workshop_dramaturg.v1" + ("+retry" if attempt else ""))
        defects = plan_defects(plan, entry, known)
        if not defects:
            break
        prompt = material + "\n" + json.dumps({"defects_of_your_last_answer": defects}, ensure_ascii=False)
    target.mkdir(parents=True, exist_ok=True)
    write_json(path, {**plan.model_dump(), "remaining_defects": defects})
    return plan


def known_material(prompt):
    """The ids a beat may name: story items, findings and section references of the material, and orientation."""
    data = json.loads(prompt[prompt.index('{"brief"'):])
    return ({item["item_id"] for item in data["story_material"]} | {f["id"] for f in data["findings"]} |
            {s["reference"] for s in data["sections"]} | {"orientation"})


def plan_defects(plan, entry, known):
    defects = []
    if [s.scene_id for s in plan.scenes] != [s.scene_id for s in entry.scenes]:
        defects.append(f"scenes must be exactly {[s.scene_id for s in entry.scenes]}, in this order.")
    demoted = [d.finding_id for d in plan.demoted]
    if not set(demoted) <= set(entry.finding_ids) or len(demoted) >= len(entry.finding_ids):
        defects.append("demoted may name only the episode's core finding_ids, and at least one must stay core.")
    for scene in plan.scenes:
        for number, beat in enumerate(scene.beats, 1):
            unknown = [name for name in beat.sources if name not in known and not name.startswith("orientation")]
            if unknown or not beat.sources:
                defects.append(f"{scene.scene_id} beat {number}: name the material it rests on by item id, finding "
                               f"id or section reference (unknown: {unknown or 'none named'}).")
    return defects


def planned_entry(entry, plan):
    """The episode as the writer reads it: the run's scenes, purposes and findings with the plan's words, each step a
    beat with the material it rests on. A demoted core finding becomes supporting, so the checks no longer ask the
    script to cite it (the story-led planning the lab stands in for, 2026-10-27)."""
    scenes = {scene.scene_id: scene for scene in plan.scenes}
    demoted = [d.finding_id for d in plan.demoted]
    return entry.model_copy(update={
        "title": plan.title, "central_question": plan.central_question,
        "finding_ids": [f for f in entry.finding_ids if f not in demoted],
        "supporting_finding_ids": list(dict.fromkeys([*entry.supporting_finding_ids, *demoted])),
        "scenes": [scene.model_copy(update={
            "title": scenes[scene.scene_id].title, "question": scenes[scene.scene_id].question,
            "finding_ids": [f for f in scene.finding_ids if f not in demoted],
            "explanation_steps": [f"{beat.beat} [{', '.join(beat.sources)}]" for beat in scenes[scene.scene_id].beats]})
            for scene in entry.scenes]})


def invoke_waiting(pipeline, prompt, output_type, version):
    attempt = 0
    while True:
        try:
            return pipeline.invoke(prompt, output_type, version)
        except AppError as exc:
            if exc.status != "waiting_for_quota":
                raise
            wait_for_quota(exc, attempt)
            attempt += 1


def variant_process(variant):
    """How a variant writes beyond its prompts (variants/<name>/process.json): ``orientation`` hands the writer the
    case's checked orientation sheet, ``rework`` has an editor critique the draft and the writer rewrite it."""
    path = HERE / "variants" / variant / "process.json"
    # utf-8-sig: a file written by Windows PowerShell starts with a byte order mark.
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}


def orientation_block(output, case):
    sheet = output / case["id"] / "orientation" / "sheet.json"
    if not sheet.is_file():
        raise SystemExit(f"No orientation sheet for {case['id']}: run orient --live first.")
    items = json.loads(sheet.read_text(encoding="utf-8"))["items"]
    return ("\n" + (HERE / "orientation_use.txt").read_text(encoding="utf-8") + "\n" +
            json.dumps({"orientation": [{"name": i["name"], "orientation": i["orientation"]} for i in items]},
                       ensure_ascii=False))


def narrative_prompt(pipeline, entry, output, case, variant=None, plan=None, instructions=None):
    """The narrative writer's prompt (narrative_writer.txt, or the variant's own): the outline, story material,
    findings, the sections the findings cite, the orientation sheet and the word budget, without the pipeline's rule
    blocks or teaching design. ``plan`` adds the dramaturg's story plan; ``instructions`` replaces the writer's text
    (the dramaturg reads the same material)."""
    from podcast_automate.narration import allowed_sections
    from podcast_automate.script_checks import episode_sources, word_budget, writer_target_factor
    config = pipeline.config
    wanted = {*entry.finding_ids, *entry.supporting_finding_ids}
    findings = [{"id": f.id, "core": f.id in entry.finding_ids, "statement": f.statement,
                 **({"illustration": f.illustration} if f.illustration else {}),
                 **({"limits": f.claim_contract.qualifications} if f.claim_contract and f.claim_contract.qualifications
                    else {})}
                for f in pipeline.dossier.findings if f.id in wanted]
    sources = episode_sources(entry, pipeline.dossier, pipeline.context, pipeline.sources)
    sheet = output / case["id"] / "orientation" / "sheet.json"
    orientation = [{"name": i["name"], "orientation": i["orientation"]}
                   for i in json.loads(sheet.read_text(encoding="utf-8"))["items"]] if sheet.is_file() else []
    factor = writer_target_factor(getattr(pipeline.adapter, "text_generation", None))
    own = HERE / "variants" / (variant or "-") / "narrative_writer.txt"
    if instructions is None:
        instructions = (own if own.is_file() else HERE / "narrative_writer.txt").read_text(encoding="utf-8")
    story = {"story_plan": {key: value for key, value in plan.model_dump().items() if key not in ("title",
                                                                                                    "central_question",
                                                                                                    "scenes")}} \
        if plan is not None else {}
    return (instructions.replace("{language}", config.language) + "\n" +
            json.dumps({"brief": {"language": config.language, "audience": config.audience_level,
                                  "host_names": config.host_names},
                        "episode": entry.model_dump(), "length": word_budget(entry, factor),
                        "story_material": pipeline.story_for(entry), "findings": findings,
                        "sections": [{"reference": s["reference"], "text": s["text"]}
                                     for s in allowed_sections(entry, pipeline.dossier, sources)],
                        "orientation": orientation, **story}, ensure_ascii=False))


WORD = re.compile(r"\w+")
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def echo_errors(script, longest=4):
    """Turns that open with a short sentence made only of the other host's last words ("Alle." "Mit Hilfe des
    Cuchaviz." "Erfordert. Notwendig."): the judges' most frequent complaint about host_b after the narrative writer
    forbade it in words (2026-10-27, 23 such openings in 15 of the best drafts)."""
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
                          f"(\"{' '.join(opening)}\"); open with a thought, guess, feeling or objection of this host's "
                          "own instead, or give the line to the host who is telling.")
    return errors


def lab_errors(pipeline, draft, entry):
    """The pipeline's script checks, plus the lab's echo check for a variant that asks for it (process echo_check)."""
    return pipeline.script_errors(draft, entry) + (echo_errors(draft) if getattr(pipeline, "lab_echo_check", False)
                                                   else [])


def corrected_draft(pipeline, entry, prompt, version):
    """A draft and its corrections against the script checks, as write_episode asks them."""
    from podcast_automate.prompts import instructions
    from podcast_automate.script_pipeline import MAX_REJECTIONS, WRITE_REPAIR_VERSION
    draft = invoke_waiting(pipeline, prompt, EpisodeScript, version)
    errors, repairs = lab_errors(pipeline, draft, entry), 0
    while errors and repairs <= MAX_REJECTIONS:
        draft = invoke_waiting(pipeline, prompt + "\n" + instructions("write_episode_repair") + "\n" + json.dumps(
            {"errors": errors, "draft": draft.model_dump()}, ensure_ascii=False), EpisodeScript, WRITE_REPAIR_VERSION)
        errors, repairs = lab_errors(pipeline, draft, entry), repairs + 1
    return draft, errors, repairs


def line_edit_rounds(pipeline, entry, prompt, draft, rounds, target, previous, points=None):
    """``rounds`` line edits after the rewrites: a critique names segments, and the writer changes only those and their
    neighbours (narration.with_neighbours); code holds every other segment to its words (narration.scope_errors),
    re-asking once with the defects. A full rewrite fixes the named points and opens new ones elsewhere; an edit keeps
    what works (2026-10-27, Milgram's plateau at 8). ``points`` hands the writer only the critique's most important
    points: with all eight and their neighbours nearly every segment is editable, a rewrite again."""
    from podcast_automate.narration import scope_errors, with_neighbours
    from podcast_automate.prompts import instructions
    from podcast_automate.script_pipeline import WRITE_REPAIR_VERSION
    errors, repairs = lab_errors(pipeline, draft, entry), 0
    for number in range(1, rounds + 1):
        payload = {"script": script_text(draft), **({"previous_notes": previous} if previous is not None else {})}
        critic = (HERE / "critique_quality.txt").read_text(encoding="utf-8") + (
            "\n" + (HERE / "critique_followup.txt").read_text(encoding="utf-8") if previous is not None else "")
        critique = invoke_waiting(pipeline, critic + "\n" + json.dumps(payload, ensure_ascii=False), Quality,
                                  "workshop_critique.v1+edit")
        write_json(target / f"edit_critique_{number}.json", critique.model_dump())
        chosen = critique.keeps_from_10[:points] if points else critique.keeps_from_10
        named = {key for point in chosen for key in point.segment_ids}
        allowed = with_neighbours(draft, named)
        notes = critique.model_dump() if not points else {"keeps_from_10": [point.model_dump() for point in chosen]}
        ask = prompt + "\n" + (HERE / "line_edit.txt").read_text(encoding="utf-8") + "\n" + json.dumps(
            {"editor_notes": notes, "editable_segments": sorted(allowed), "draft": draft.model_dump()},
            ensure_ascii=False)
        edited = invoke_waiting(pipeline, ask, EpisodeScript, "workshop_line_edit.v1")
        problems = scope_errors(draft, edited, allowed) + lab_errors(pipeline, edited, entry)
        if problems:
            edited = invoke_waiting(pipeline, ask + "\n" + json.dumps({"defects_of_your_last_answer": problems},
                                                                     ensure_ascii=False), EpisodeScript,
                                    "workshop_line_edit.v1+retry")
            repairs += 1
            problems = scope_errors(draft, edited, allowed) + lab_errors(pipeline, edited, entry)
        write_json(target / f"edit_{number}.json", {"named": sorted(named), "allowed": sorted(allowed),
                                                    "kept": not problems, "problems": problems})
        if not problems:
            draft, errors = edited, []
        previous = critique.model_dump()
    return draft, errors, repairs


def write_sample(pipeline, case, target, extra="", rework=False, start=None, previous=None, rounds=1, writer=None,
                 edits=0, edit_points=None, dramaturg=None):
    """One draft as write_episode makes it, with its corrections, kept in ``target`` instead of the run. ``extra`` is
    appended to the writing prompt (the orientation sheet); with ``rework`` an editor critiques the draft on the
    quality rubric and the writer rewrites the whole episode by the critique, ``rounds`` times, each critique and
    each intermediate draft kept beside the final draft.
    ``start`` is a draft to rework instead of writing one, ``previous`` the critique it already answers: the editor
    sees it, so a second round does not ask to undo the first (design rule for repeated reviews)."""
    from podcast_automate.script_pipeline import WRITE_EPISODE_VERSION
    plan, entries = pipeline.selected()
    entry = entries[0]
    # writer: (output, case, variant) for the narrative writer, which brings its own orientation; else the pipeline's
    # prompt. The narrative writer reads the case's entry, which a variant outline may reword and narrow (supporting
    # findings), as long as its scenes and core findings are the run's: the checks hold the draft to the run's entry.
    if writer:
        told = writer[1]["entry"]
        if [s.scene_id for s in told.scenes] != [s.scene_id for s in entry.scenes] or told.finding_ids != entry.finding_ids:
            raise SystemExit("The variant outline must keep the run's scenes and core findings.")
        # dramaturg: a pipeline on the dramaturg's model; its plan rewords the outline and goes to the writer.
        plan = story_plan(dramaturg, pipeline, entry, told, *writer, target) if dramaturg is not None else None
        prompt = narrative_prompt(pipeline, planned_entry(told, plan) if plan else told, *writer, plan=plan)
        # The checks hold the draft to the episode as planned: a demoted core finding need not be cited.
        entry = planned_entry(entry, plan) if plan else entry
    else:
        prompt = pipeline.writing_prompt(plan, entry) + extra
    if start is None:
        draft, errors, repairs = corrected_draft(pipeline, entry, prompt, WRITE_EPISODE_VERSION)
    else:
        draft, errors, repairs = start, lab_errors(pipeline, start, entry), 0
    config = pipeline.config
    target.mkdir(parents=True, exist_ok=True)
    if rework:
        write_json(target / "first_draft.json", draft.model_dump())
        (target / "first_draft.md").write_text(render_script(draft, host_labels(config)), encoding="utf-8")
        for number in range(1, rounds + 1):
            payload = {"script": script_text(draft)}
            if previous is not None:
                payload["previous_notes"] = previous
            critic = (HERE / "critique_quality.txt").read_text(encoding="utf-8") + (
                "\n" + (HERE / "critique_followup.txt").read_text(encoding="utf-8") if previous is not None else "")
            critique = invoke_waiting(pipeline, critic + "\n" + json.dumps(payload, ensure_ascii=False), Quality,
                                      "workshop_critique.v1" + ("+followup" if previous is not None else ""))
            # critique.json is the last round's, the one a start_from variant continues from.
            write_json(target / "critique.json", critique.model_dump())
            if rounds > 1:
                write_json(target / f"critique_{number}.json", critique.model_dump())
            draft, errors, more = corrected_draft(pipeline, entry, prompt + "\n" + (HERE / "rework.txt").read_text(
                encoding="utf-8") + "\n" + json.dumps({"editor_notes": critique.model_dump(),
                                                       "draft": draft.model_dump()}, ensure_ascii=False),
                "workshop_rework.v1")
            repairs += more
            previous = critique.model_dump()
            if rounds > 1 and number < rounds:
                write_json(target / f"round_{number}_draft.json", draft.model_dump())
    if edits:
        draft, errors, more = line_edit_rounds(pipeline, entry, prompt, draft, edits, target, previous, edit_points)
        repairs += more
    design = pipeline.teaching_for(entry)
    write_json(target / "draft.json", draft.model_dump())
    (target / "draft.md").write_text(render_script(draft, host_labels(config)), encoding="utf-8")
    write_json(target / "metrics.json", {**narration_metrics(draft, config.language, design), "repairs": repairs,
                                         "remaining_errors": errors})
    return target


def write_variant(args, output):
    """Runs inside the variant's process (NARRATION_LAB_PROMPTS set): --samples drafts at a time."""
    case = load_case(args.case)
    copy = output / case["id"] / "projects" / "I3"
    folder = output / case["id"] / "write" / args.variant
    process = variant_process(args.variant)
    extra = orientation_block(output, case) if process.get("orientation") else ""
    # The folder exists from the start, so status lists the variant while its first draft is still being written.
    folder.mkdir(parents=True, exist_ok=True)
    def starting(target):
        return starting_point(output, case, process, target)
    with workshop(case, "I3"):
        pipeline = with_model(frozen_pipeline(case, copy), process, args)
        pipeline.lab_echo_check = bool(process.get("echo_check"))
        # A narrative variant on the I3 run writes from the planner's own outline (the case's episode), not from a
        # hand-written one; a dramaturg may reword it first.
        writer = (output, case, args.variant) if process.get("writer") == "narrative" else None
        dramaturg = dramaturg_pipeline(case, copy, process, args)
        missing = [folder / f"s{n}" for n in range(1, args.samples + 1) if not (folder / f"s{n}" / "draft.json").exists()]
        with ThreadPoolExecutor(max_workers=max(1, len(missing))) as pool:
            futures = {pool.submit(write_sample, pipeline, case, target, extra, bool(process.get("rework")),
                                   rounds=int(process.get("rework_rounds", 1)), edits=int(process.get("line_edits", 0)),
                                   edit_points=process.get("edit_points"), writer=writer, dramaturg=dramaturg,
                                   **starting(target)): target.name
                       for target in missing}
            for future in as_completed(futures):
                error = future.exception()
                print(json.dumps({"case": case["id"], "variant": args.variant, "sample": futures[future],
                                  **({"error": str(error)} if error else {})}, ensure_ascii=False), flush=True)


def write(args, output):
    """Each named variant writes --samples drafts in its own process, all variants at once; then every draft is judged
    on its own and each variant's drafts against the baseline's, pair by pair."""
    case = load_case(args.case)
    if not (output / case["id"] / "projects" / "I3" / "runs" / "latest.json").is_file():
        raise SystemExit("write needs the case's I3 run: run the ladder with --inputs I3 first.")
    variants = list(dict.fromkeys(["baseline", args.against, *args.variants]))
    if not args.live:
        for variant in variants:
            print(json.dumps({"variant": variant, "prompts": str(prepared_prompts(case["id"], variant, output))}),
                  flush=True)
        return []
    processes = []
    for variant in variants:
        environment = {**os.environ, "NARRATION_LAB_PROMPTS": str(prepared_prompts(case["id"], variant, output)),
                       "PYTHONIOENCODING": "utf-8"}
        command = [sys.executable, str(Path(__file__).resolve()), "write-variant", case["id"], "--output", str(output),
                   "--variant", variant, "--samples", str(args.samples), "--live"]
        processes.append(subprocess.Popen(command, env=environment))
    for process in processes:
        process.wait()
    judge_writes(args, output, case, variants, against=args.against)
    return write_report(args, output)


def design_variant(args, output):
    """Runs inside a design variant's process: a new script run on the I3 inputs with the I3 run's story material,
    stopped after its teaching design, then --samples drafts on that design at once, kept with the write variants."""
    case = load_case(args.case)
    # A variant may replace the case's episode with its own outline (variants/<name>/<case>.outline.json).
    outline = HERE / "variants" / args.variant / f"{case['id']}.outline.json"
    if outline.is_file():
        case["entry"] = EpisodePlan.model_validate(json.loads(outline.read_text(encoding="utf-8-sig")))
    episode = case["entry"].episode_id
    frozen = output / case["id"] / "projects" / "I3"
    frozen_run = frozen / "runs" / json.loads((frozen / "runs" / "latest.json").read_text(encoding="utf-8"))["run_id"]
    folder = output / case["id"] / "write" / args.variant
    process = variant_process(args.variant)
    # design_from: write on another design variant's finished teaching design, so only the writing differs.
    copy = output / case["id"] / "projects" / f"design-{process.get('design_from') or args.variant}"
    extra = orientation_block(output, case) if process.get("orientation") else ""
    if not process.get("design_from"):
        copy_project(PROJECTS / case["project"], copy)
    with workshop(case, "I3", stop="teaching", material=frozen_run / "teaching" / episode / "story_material.json"):
        run = advance(copy, episode, args) if not process.get("design_from") else None
        if run is not None and run.stages["teaching"].status != "completed":
            print(json.dumps({"case": case["id"], "variant": args.variant, "stopped": {
                name: stage.error.model_dump() for name, stage in run.stages.items() if stage.error}},
                ensure_ascii=False), flush=True)
            return
        pipeline = with_model(frozen_pipeline(case, copy), process, args)
        pipeline.lab_echo_check = bool(process.get("echo_check"))
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pipeline.work / "teaching" / episode / "plan.md", folder / "plan.md")
        missing = [folder / f"s{n}" for n in range(1, args.samples + 1) if not (folder / f"s{n}" / "draft.json").exists()]
        writer = (output, case, args.variant) if process.get("writer") == "narrative" else None
        with ThreadPoolExecutor(max_workers=max(1, len(missing))) as pool:
            futures = {pool.submit(write_sample, pipeline, case, target, extra, bool(process.get("rework")),
                                   rounds=int(process.get("rework_rounds", 1)), writer=writer,
                                   edits=int(process.get("line_edits", 0)), edit_points=process.get("edit_points"),
                                   **starting_point(output, case, process, target)): target.name
                       for target in missing}
            for future in as_completed(futures):
                error = future.exception()
                print(json.dumps({"case": case["id"], "variant": args.variant, "sample": futures[future],
                                  **({"error": str(error)} if error else {})}, ensure_ascii=False), flush=True)


def design(args, output):
    """Each named design variant makes its own teaching design and --samples drafts on it, all variants at once; then
    every draft is judged, and each variant's sample n against the --against variant's sample n."""
    case = load_case(args.case)
    if not (output / case["id"] / "projects" / "I3" / "runs" / "latest.json").is_file():
        raise SystemExit("design needs the case's I3 run: run the ladder with --inputs I3 first.")
    if not args.live:
        for variant in args.variants:
            print(json.dumps({"variant": variant, "prompts": str(prepared_prompts(case["id"], variant, output))}),
                  flush=True)
        return []
    processes = []
    for variant in args.variants:
        environment = {**os.environ, "NARRATION_LAB_PROMPTS": str(prepared_prompts(case["id"], variant, output)),
                       "PYTHONIOENCODING": "utf-8"}
        command = [sys.executable, str(Path(__file__).resolve()), "design-variant", case["id"], "--output",
                   str(output), "--variant", variant, "--samples", str(args.samples), "--live"]
        # A variant's own model also makes its teaching design, unless it writes on another variant's design.
        own = variant_process(variant)
        command += ["--model", own["model"]] if own.get("model") and not own.get("design_from") else []
        processes.append(subprocess.Popen(command, env=environment))
    for process in processes:
        process.wait()
    judge_writes(args, output, case, [args.against, *args.variants], against=args.against)
    return write_report(args, output)


class OrientationItem(Contract):
    name: NonEmpty
    kind: NonEmpty
    orientation: NonEmpty
    certain: bool


class OrientationSheet(Contract):
    items: list[OrientationItem] = Field(max_length=25)


class OrientationCheck(Contract):
    name: NonEmpty
    verdict: NonEmpty
    note: str = ""


class OrientationReview(Contract):
    checks: list[OrientationCheck]


def model_adapter(case, args, model):
    config = load_project(PROJECTS / case["project"])
    pool = build_adapter(config, text_generation_settings(config, backend=args.backend, model=model,
                                                          reasoning_effort=args.effort), None)
    pool.require_key()
    return pool


def orient(args, output):
    """The case's orientation sheet: drafted from the I3 run's episode material by --model, every entry checked by
    --check-model; the entries the check finds correct stay. sheet.md is for reading, sheet.json for the writers."""
    case = load_case(args.case)
    folder = output / case["id"] / "orientation"
    if not args.live:
        print(json.dumps({"case": case["id"], "sheet": str(folder / "sheet.json")}), flush=True)
        return []
    with workshop(case, "I3"):
        pipeline = frozen_pipeline(case, output / case["id"] / "projects" / "I3")
    entry = pipeline.selected()[1][0]
    # A run whose teaching design stopped still has its story material; the sheet does without the design then.
    plan_file = pipeline.work / "teaching" / entry.episode_id / "plan.json"
    material = {"language": pipeline.config.language, "episode": entry.model_dump(),
                **({"teaching_design": pipeline.teaching_for(entry).model_dump()} if plan_file.is_file() else {}),
                "story_material": pipeline.story_for(entry),
                "findings": [{"id": f.id, "statement": f.statement} for f in pipeline.dossier.findings
                             if f.id in {*entry.finding_ids, *entry.supporting_finding_ids}]}
    sheet = ask(model_adapter(case, args, args.model), (HERE / "orientation_sheet.txt").read_text(encoding="utf-8") +
                "\n" + json.dumps(material, ensure_ascii=False), OrientationSheet, "workshop_orientation.v1",
                folder / "draft")
    names = [item.name for item in sheet.items]

    def check(answer):
        given = [c.name for c in answer.checks]
        return [] if given == names else [f"Return every entry exactly once, in this order: {', '.join(names)}."]
    review = ask(model_adapter(case, args, args.check_model), (HERE / "orientation_check.txt").read_text(
        encoding="utf-8") + "\n" + json.dumps({"sheet": [i.model_dump() for i in sheet.items]}, ensure_ascii=False),
        OrientationReview, "workshop_orientation_check.v1", folder / "check", check)
    verdicts = {c.name: c for c in review.checks}
    # The independent check decides: the drafter marks scholars uncertain that the checker confirms (2026-10-26).
    kept = [i for i in sheet.items if verdicts.get(i.name) and verdicts[i.name].verdict == "correct"]
    write_json(folder / "sheet.json", {"items": [i.model_dump() for i in kept]})
    lines = [f"# Orientierungsblatt: {case['id']}", "", f"{len(kept)} von {len(sheet.items)} Einträgen gelten als "
             "geprüft und gehen an den Schreiber.", "", "| Name | Art | Einordnung | Prüfung |", "| --- | --- | --- | --- |"]
    for item in sheet.items:
        verdict = verdicts.get(item.name)
        state = "übernommen" if item in kept else (
            f"verworfen: {verdict.verdict if verdict else 'ohne Prüfung'}{'' if item.certain else ', vom Entwurf als unsicher markiert'}"
            + (f" ({verdict.note})" if verdict and verdict.note else ""))
        lines.append(f"| {item.name} | {item.kind} | {item.orientation} | {state} |")
    (folder / "sheet.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return kept


class Preference(Contract):
    tension: str
    interest: str
    storytelling: str
    overall: str
    reason: NonEmpty


def pair_check(answer):
    allowed = {"first", "second", "tie"}
    wrong = [key for key in ("tension", "interest", "storytelling", "overall") if getattr(answer, key) not in allowed]
    return [f"{', '.join(wrong)}: answer first, second or tie."] if wrong else []


def compare(adapter, first, second, folder):
    prompt = (HERE / "judge_pair.txt").read_text(encoding="utf-8") + "\n" + json.dumps(
        {"first": script_text(first), "second": script_text(second)}, ensure_ascii=False)
    return ask(adapter, prompt, Preference, PAIR_VERSION, folder, pair_check)


def pair_folders(target, against):
    """Where the comparison of the sample in ``target`` with the ``against`` variant's is saved, in both orders."""
    if against == "baseline":
        return target / "pair_first", target / "pair_second"
    return target / f"vs_{against}_first", target / f"vs_{against}_second"


def judge_writes(args, output, case, variants, against="baseline"):
    """Coverage and quality of every draft, and each variant's sample n against the ``against`` variant's sample n,
    asked twice with the order swapped; a pair counts as won only when both orders agree."""
    adapter = judge_adapter(case, args)
    base = output / case["id"] / "write"
    drafts = {variant: {sample.name: EpisodeScript.model_validate_json((sample / "draft.json").read_text(encoding="utf-8"))
                        for sample in sorted((base / variant).glob("s*")) if (sample / "draft.json").is_file()}
              for variant in variants}
    tasks = {}
    for variant, samples in drafts.items():
        for name, script in samples.items():
            target = base / variant / name
            tasks[f"{variant}/{name}/coverage"] = lambda script=script, target=target: coverage(
                adapter, case, "podcast script", script_text(script), target / "coverage")
            tasks[f"{variant}/{name}/quality"] = lambda script=script, target=target: quality(
                adapter, script, target / "quality")
            if variant != against and name in drafts.get(against, {}):
                other, (first, second) = drafts[against][name], pair_folders(target, against)
                tasks[f"{variant}/{name}/vs_{against}_ab"] = lambda script=script, other=other, first=first: compare(
                    adapter, script, other, first)
                tasks[f"{variant}/{name}/vs_{against}_ba"] = lambda script=script, other=other, second=second: compare(
                    adapter, other, script, second)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(task): label for label, task in tasks.items()}
        for future in as_completed(futures):
            error = future.exception()
            print(json.dumps({"judged": futures[future], **({"error": str(error)} if error else {})},
                             ensure_ascii=False), flush=True)


def pair_outcome(folder, against="baseline"):
    """For the variant's sample in ``folder`` against the ``against`` variant's: per scale win, loss or tie, both
    orders agreeing."""
    first_folder, second_folder = pair_folders(folder, against)
    first, second = saved(first_folder, Preference), saved(second_folder, Preference)
    if not (first and second):
        return {}
    result = {}
    for key in ("tension", "interest", "storytelling", "overall"):
        a, b = getattr(first, key), {"first": "second", "second": "first"}.get(getattr(second, key), "tie")
        result[key] = "win" if a == b == "first" else "loss" if a == b == "second" else "tie"
    return result


def render_judgment(case, sample):
    """judge.md beside a draft: the judges' reasons, what keeps it from 10, the elements it misses or only mentions,
    the traps it commits and the pair verdicts, for reading next to draft.md."""
    rated, cover = judged(sample / "quality"), saved(sample / "coverage", Coverage)
    if not (rated or cover):
        return
    labels = {"tension": "Spannung", "interest": "Interesse", "storytelling": "Erzählen"}
    lines = [f"# Richter: {sample.parent.name} / {sample.name}", ""]
    if rated:
        lines += ["## Noten", ""]
        for key, label in labels.items():
            scale = getattr(rated.scores, key)
            lines += [f"**{label}: {scale.score}/10.** {scale.reason}", ""]
        lines += ["## Was zur 10 fehlt", ""]
        for number, point in enumerate(weak_points(rated), 1):
            lines += [f"{number}. {point.problem} *({', '.join(point.segment_ids)})*", f"   - Vorschlag: {point.fix}"]
        lines.append("")
    if cover:
        elements = {e["id"]: e for e in case["list"]["elements"]}
        weak = [e for e in cover.elements if snapped(e.score) < 1]
        lines += ["## Soll-Liste: fehlt oder nur erwähnt", ""]
        lines += [f"- {e.id} ({elements.get(e.id, {}).get('weight', '?')}, {snapped(e.score)}): "
                  f"{elements.get(e.id, {}).get('element', '')}" for e in weak] or ["- nichts"]
        traps = {t["id"]: t["trap"] for t in case["list"]["traps"]}
        hits = [t for t in cover.traps if t.hit]
        lines += ["", "## Fallen", ""] + ([f"- {t.id}: {traps.get(t.id, '')} Stelle: „{t.quote}“" for t in hits]
                                          or ["- keine"])
        lines.append("")
    pairs = sorted(sample.glob("pair_first")) + sorted(sample.glob("vs_*_first"))
    if pairs:
        lines += ["## Paarvergleiche", ""]
        for first_folder in pairs:
            opponent = "baseline" if first_folder.name == "pair_first" else first_folder.name[3:-6]
            outcome = pair_outcome(sample, opponent).get("overall", "?")
            first, second = pair_folders(sample, opponent)
            lines.append(f"**gegen {opponent}: {'Sieg' if outcome == 'win' else 'Niederlage' if outcome == 'loss' else 'unentschieden'}**")
            for title, folder in (("diese Fassung zuerst gelesen", first), ("diese Fassung als zweite gelesen", second)):
                answer = saved(folder, Preference)
                if answer:
                    lines.append(f"- {title}, Wahl: {answer.overall}. {answer.reason}")
            lines.append("")
    (sample / "judge.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(args, output):
    case = load_case(args.case)
    base = output / case["id"] / "write"
    for sample in base.glob("*/s*") if base.is_dir() else []:
        render_judgment(case, sample)
    lines = [f"# Workshop write rounds: {case['id']}", "",
             "| Variant | Samples | M | S | Traps | Tension | Interest | Storytelling | Pairs (overall) | Words | host_b | Workshop |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    rows = []
    for folder in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
        samples = sorted(s for s in folder.glob("s*") if (s / "draft.json").is_file())
        covers = [shares(case, c) for c in (saved(s / "coverage", Coverage) for s in samples) if c]
        rated = [q.scores.model_dump() for q in (judged(s / "quality") for s in samples) if q]
        metrics = [json.loads((s / "metrics.json").read_text(encoding="utf-8")) for s in samples]
        opponents = sorted({"baseline" for s in samples if (s / "pair_first").exists()} |
                           {p.name[3:-6] for s in samples for p in s.glob("vs_*_first")})
        records = []
        for opponent in opponents:
            overall = [o["overall"] for o in (pair_outcome(s, opponent) for s in samples) if o]
            if overall:
                records.append(f"vs {opponent} {overall.count('win')}–{overall.count('loss')}–{overall.count('tie')}")
        mean = lambda values: round(statistics.mean(values), 2) if values else ""  # noqa: E731
        row = {"variant": folder.name, "samples": len(samples), "M": mean([c["M"] for c in covers]),
               "S": mean([c["S"] for c in covers]), "traps": sorted({t for c in covers for t in c["traps"]}),
               **{key: mean([r[key]["score"] for r in rated]) for key in ("tension", "interest", "storytelling")},
               "pairs": "; ".join(records),
               "words": mean([m["words"] for m in metrics]), "host_b": mean([m["host_b_share"] for m in metrics]),
               "workshop": mean([len(m["workshop_phrases"]) for m in metrics])}
        rows.append(row)
        lines.append(f"| {row['variant']} | {row['samples']} | {row['M']} | {row['S']} | {', '.join(row['traps']) or '–'} | "
                     f"{row['tension']} | {row['interest']} | {row['storytelling']} | {row['pairs']} | {row['words']} | "
                     f"{row['host_b']} | {row['workshop']} |")
    lines += ["", "Pairs: wins–losses–ties of the variant's samples against the named variant's, overall; a win needs "
              "both orders of the comparison to agree."]
    text = "\n".join(lines) + "\n"
    (output / case["id"] / "write_report.md").write_text(text, encoding="utf-8")
    print(text, flush=True)
    return rows


# --- judging ------------------------------------------------------------------------------------------------------

# Element and trap ids are the list's own (K01, F02), not pipeline identifiers.
class ElementScore(Contract):
    id: NonEmpty
    score: float = Field(ge=0, le=1)
    quote: str = ""


class TrapCheck(Contract):
    id: NonEmpty
    hit: bool
    quote: str = ""


class Coverage(Contract):
    elements: list[ElementScore]
    traps: list[TrapCheck]


class Scale(Contract):
    score: int = Field(ge=1, le=10)
    reason: NonEmpty


class QualityScores(Contract):
    tension: Scale
    interest: Scale
    storytelling: Scale


class Shortfall(Contract):
    segment_ids: list[NonEmpty]
    problem: NonEmpty
    fix: NonEmpty


class Quality(Contract):
    """The critic's answer (critique_quality.txt): scores and what keeps the draft from 10, for the writer."""
    scores: QualityScores
    keeps_from_10: list[Shortfall] = Field(max_length=8)


class Judgment(Contract):
    """The judge's answer (judge_quality.txt, v3): scores, then only the weak spots that cost points. The critic's
    list of up to eight points filled up and held every score at 8 or below (2026-10-27)."""
    scores: QualityScores
    weak_spots: list[Shortfall] = Field(default_factory=list, description=(
        "Only weak spots that actually cost points; empty if there are none."))


def judged(folder):
    """A saved judgment, as the v3 judge (Judgment) or an earlier one (Quality) left it."""
    file = folder / "answer.json"
    if not file.is_file():
        return None
    data = json.loads(file.read_text(encoding="utf-8"))
    return (Judgment if "weak_spots" in data or "keeps_from_10" not in data else Quality).model_validate(data)


def weak_points(rated):
    return rated.weak_spots if isinstance(rated, Judgment) else rated.keeps_from_10


class PlannedBeat(Contract):
    beat: NonEmpty = Field(description="What happens or is said at this point, concretely, in one or two sentences.")
    sources: list[str] = Field(description="The story_material item ids, finding ids or section references this beat "
                                           "rests on; 'orientation' for a placing clause from orientation.")


class PlannedScene(Contract):
    scene_id: NonEmpty
    title: NonEmpty
    question: NonEmpty = Field(description="The question this chapter makes the listener want answered.")
    beats: list[PlannedBeat] = Field(min_length=1)


class Demotion(Contract):
    finding_id: NonEmpty
    reason: NonEmpty


class Plant(Contract):
    plant: NonEmpty = Field(description="The detail, line or choice set early, without hinting at what it hides.")
    payoff: NonEmpty = Field(description="Where and how it turns later.")


class StoryPlan(Contract):
    turn: NonEmpty = Field(description="The one turn the material holds that the episode builds to, and its source.")
    one_idea: NonEmpty = Field(description="The one thing the episode explains, shown on the story's person.")
    perspective: NonEmpty = Field(description="Through whose eyes or in what frame the story is told, and how host_b "
                                              "takes part.")
    host_b_bet: NonEmpty = Field(description="host_b's early claim or bet that the story tests, and how the end pays "
                                             "it off.")
    plants: list[Plant]
    closing_image: NonEmpty
    absent: list[str] = Field(description="What a listener would expect that the material does not hold: told as "
                                          "open or left out, never filled in.")
    demoted: list[Demotion] = Field(default_factory=list, description=(
        "Core findings the story material cannot carry, each moved to supporting with the reason; only where the "
        "dramaturg's instructions allow it, otherwise empty."))
    title: NonEmpty
    central_question: NonEmpty
    scenes: list[PlannedScene] = Field(min_length=1)


def judge_adapter(case, args):
    config = load_project(PROJECTS / case["project"])
    selection = text_generation_settings(config, backend=args.backend, model=args.judge_model,
                                         reasoning_effort=args.effort)
    pool = build_adapter(config, selection, None)
    pool.require_key()
    return pool


def ask(adapter, prompt, output_type, version, folder, check=None):
    """One structured answer, saved in ``folder``; a quota stop waits for the reset, an answer ``check`` refuses is
    asked once more with its defects named."""
    saved, stamp = folder / "answer.json", folder / "prompt.sha256"
    signature = digest({"version": version, "prompt": prompt})
    if saved.is_file() and stamp.is_file() and stamp.read_text(encoding="utf-8") == signature:
        return output_type.model_validate_json(saved.read_text(encoding="utf-8"))
    # A judgment under another judge prompt (or saved before the stamp) is asked again; the old one is kept aside.
    if saved.is_file():
        saved.replace(folder / f"answer_{len(list(folder.glob('answer_*.json'))) + 1}.json")
    attempt, retry = 0, prompt
    for round_number in range(2):
        while True:
            number = len(list((folder / "calls").glob("call_*"))) + 1
            try:
                answer = adapter.structured(retry, output_type, folder / "calls" / f"call_{number:03d}",
                                            prompt_version=version)[0]
                break
            except AppError as exc:
                if exc.status != "waiting_for_quota":
                    raise
                wait_for_quota(exc, attempt)
                attempt += 1
        defects = check(answer) if check else []
        if not defects:
            break
        retry = prompt + "\n" + json.dumps({"defects_of_your_last_answer": defects}, ensure_ascii=False)
    write_json(saved, answer.model_dump())
    stamp.write_text(signature, encoding="utf-8")
    return answer


def coverage_check(listing):
    def check(answer):
        defects = []
        for kind, wanted, given in (("elements", [e["id"] for e in listing["elements"]], [e.id for e in answer.elements]),
                                    ("traps", [t["id"] for t in listing["traps"]], [t.id for t in answer.traps])):
            if given != wanted:
                defects.append(f"Return every one of the {kind} exactly once, in this order: {', '.join(wanted)}.")
        return defects
    return check


def script_text(script):
    return "\n".join(f"[{s.segment_id}] {s.speaker_id}: {s.text}" for s in script.segments)


def coverage(adapter, case, kind, text, folder):
    listing = case["list"]
    prompt = (HERE / "judge_coverage.txt").read_text(encoding="utf-8") + "\n" + json.dumps(
        {"text_kind": kind, "elements": [{"id": e["id"], "element": e["element"]} for e in listing["elements"]],
         "traps": listing["traps"], "text": text}, ensure_ascii=False)
    return ask(adapter, prompt, Coverage, COVERAGE_VERSION, folder, coverage_check(listing))


def quality(adapter, script, folder):
    """The judge's scores (judge_quality.txt, Judgment)."""
    prompt = (HERE / "judge_quality.txt").read_text(encoding="utf-8") + "\n" + json.dumps(
        {"script": script_text(script)}, ensure_ascii=False)
    return ask(adapter, prompt, Judgment, QUALITY_VERSION, folder)


def critique(adapter, script, folder):
    """The critic for a writer (critique_quality.txt, Quality): the rubric with what keeps the draft from 10."""
    prompt = (HERE / "critique_quality.txt").read_text(encoding="utf-8") + "\n" + json.dumps(
        {"script": script_text(script)}, ensure_ascii=False)
    return ask(adapter, prompt, Quality, CRITIQUE_VERSION, folder)


def findings_text(case):
    dossier = yaml.safe_load((PROJECTS / case["project"] / "research" / "dossier.yaml").read_text(encoding="utf-8"))
    wanted = [*case["entry"].finding_ids, *case["entry"].supporting_finding_ids]
    rows = {f["id"]: f for f in dossier["findings"]}
    return "\n\n".join(f"{key}: {rows[key]['statement']}" + (f"\nIllustration: {rows[key]['illustration']}"
                                                            if rows[key].get("illustration") else "")
                       for key in wanted if key in rows)


def judge(args, output):
    """Every judgment not saved yet, --workers at a time; the samples a ladder still writes are judged on a later
    call."""
    case = load_case(args.case)
    print(json.dumps(check_case(case), ensure_ascii=False), flush=True)
    if not args.live:
        return []
    adapter = judge_adapter(case, args)
    base = output / case["id"] / "judge"
    tasks = {"findings": lambda: coverage(adapter, case, "research findings", findings_text(case), base / "findings")}
    for variant in INPUTS:
        folder = output / case["id"] / "ladder" / variant
        material = folder / "story_material.json"
        items = json.loads(material.read_text(encoding="utf-8")).get("items", []) if material.is_file() else []
        if items:
            text = "\n\n".join(f"{i['item_id']} ({i['kind']}): {i['text']}" for i in items)
            tasks[f"{variant}/story"] = lambda text=text, variant=variant: coverage(
                adapter, case, "story material", text, base / variant / "story")
        for sample in sorted((folder / "samples").glob("s*")):
            script = EpisodeScript.model_validate_json((sample / "draft.json").read_text(encoding="utf-8"))
            target = base / variant / sample.name
            tasks[f"{variant}/{sample.name}/coverage"] = lambda script=script, target=target: coverage(
                adapter, case, "podcast script", script_text(script), target / "coverage")
            tasks[f"{variant}/{sample.name}/quality"] = lambda script=script, target=target: quality(
                adapter, script, target / "quality")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(task): label for label, task in tasks.items()}
        for future in as_completed(futures):
            error = future.exception()
            print(json.dumps({"judged": futures[future], **({"error": str(error)} if error else {})},
                             ensure_ascii=False), flush=True)
    return report(args, output)


# --- report -------------------------------------------------------------------------------------------------------

def snapped(score):
    return min((0.0, 0.5, 1.0), key=lambda value: abs(value - score))


def shares(case, answer):
    """The share of each weight's elements the text holds (told 1, mentioned 0.5) and the traps it commits."""
    weight = {e["id"]: e["weight"] for e in case["list"]["elements"]}
    result = {}
    for key in "MSK":
        scores = [snapped(e.score) for e in answer.elements if weight.get(e.id) == key]
        result[key] = round(sum(scores) / len(scores), 2) if scores else None
    result["traps"] = [t.id for t in answer.traps if t.hit]
    result["scores"] = {e.id: snapped(e.score) for e in answer.elements}
    return result


def saved(path, output_type):
    file = path / "answer.json"
    return output_type.model_validate_json(file.read_text(encoding="utf-8")) if file.is_file() else None


def report(args, output):
    case = load_case(args.case)
    base = output / case["id"]
    rows, matrix = [], {}
    findings = saved(base / "judge" / "findings", Coverage)
    if findings:
        matrix["findings"] = shares(case, findings)
    for variant in INPUTS:
        folder = base / "ladder" / variant
        story = saved(base / "judge" / variant / "story", Coverage)
        if story:
            matrix[f"{variant} story"] = shares(case, story)
        for sample in sorted((folder / "samples").glob("s*")):
            judged_folder = base / "judge" / variant / sample.name
            cover, rated = saved(judged_folder / "coverage", Coverage), judged(judged_folder / "quality")
            metrics = json.loads((sample / "metrics.json").read_text(encoding="utf-8"))
            row = {"input": variant, "sample": sample.name, "words": metrics["words"],
                   "host_b_share": metrics["host_b_share"], "workshop_phrases": len(metrics["workshop_phrases"])}
            if cover:
                share = shares(case, cover)
                matrix[f"{variant} {sample.name}"] = share
                row.update(M=share["M"], S=share["S"], K=share["K"], traps=share["traps"])
            if rated:
                row.update({key: value["score"] for key, value in rated.scores.model_dump().items()})
            rows.append(row)
    lines = [f"# Workshop ladder: {case['id']}", "",
             "| Input | Sample | M | S | K | Traps | Tension | Interest | Storytelling | Words | host_b | Workshop |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['input']} | {r['sample']} | {r.get('M', '')} | {r.get('S', '')} | {r.get('K', '')} | "
                     f"{', '.join(r.get('traps', [])) or '–'} | {r.get('tension', '')} | {r.get('interest', '')} | "
                     f"{r.get('storytelling', '')} | {r['words']} | {r['host_b_share']} | {r['workshop_phrases']} |")
    for variant in INPUTS:
        own = [r for r in rows if r["input"] == variant and "tension" in r and "M" in r]
        if own:
            means = {key: round(statistics.mean(r[key] for r in own), 1) for key in ("M", "tension", "interest", "storytelling")}
            lines.append(f"| {variant} | mean | {means['M']} |  |  |  | {means['tension']} | {means['interest']} | "
                         f"{means['storytelling']} |  |  |  |")
    columns = list(matrix)
    lines += ["", "## Where each element is lost", "", "| Element | G | " + " | ".join(columns) + " |",
              "| --- | --- | " + " | ".join("---" for _ in columns) + " |"]
    for element in case["list"]["elements"]:
        lines.append(f"| {element['id']} | {element['weight']} | " +
                     " | ".join(str(matrix[c]["scores"].get(element["id"], "")) for c in columns) + " |")
    text = "\n".join(lines) + "\n"
    (base / "report.md").write_text(text, encoding="utf-8")
    print(text, flush=True)
    return rows


# --- status -------------------------------------------------------------------------------------------------------

def variant_status(output, name, variant):
    """One line of the status table, read from the files the run and the judge write. A design variant's copy is
    projects/design-<variant>; its drafts are kept with the write variants."""
    copy, folder = output / name / "projects" / variant, output / name / "ladder" / variant
    if variant.startswith("design-"):
        folder = output / name / "write" / variant[len("design-"):]
    row = {"case": name, "input": variant, "stage": "-", "activity": "", "calls": 0, "newest": "", "samples": 0,
           "judged": 0}
    if variant.startswith("design-"):
        row["samples"] = len(list(folder.glob("s*/draft.json")))
        row["judged"] = len(list(folder.glob("s*/quality/answer.json")))
    else:
        row["samples"] = len(list((folder / "samples").glob("s*")))
        row["judged"] = len(list((output / name / "judge" / variant).glob("s*/quality/answer.json")))
    record = folder / "record.json"
    if record.is_file() and json.loads(record.read_text(encoding="utf-8")).get("stopped"):
        row["stage"] = "stopped"
    pointer = copy / "runs" / "latest.json"
    if not pointer.is_file():
        return row
    work = copy / "runs" / json.loads(pointer.read_text(encoding="utf-8"))["run_id"]
    try:
        manifest = read_yaml(work / "run_manifest.yaml")
    except AppError:
        manifest = {}
    try:
        progress = json.loads((work / "progress.json").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        progress = {}
    stages = manifest.get("stages", {})
    current = next((key for key in ("teaching", "writing") if stages.get(key, {}).get("status") != "completed"), None)
    if row["stage"] != "stopped":
        row["stage"] = current or ("written" if stages else "-")
    row["activity"] = progress.get("activity") or ""
    calls = sorted((work / "calls").glob("call_*"), key=lambda path: path.stat().st_ctime)
    row["calls"] = len(calls)
    if calls:
        newest = calls[-1]
        try:
            title = json.loads((newest / "output_schema.json").read_text(encoding="utf-8")).get("title", "")
            diagnostics = json.loads((newest / "diagnostics.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            title, diagnostics = "", {}
        minutes = int((time.time() - newest.stat().st_ctime) // 60)
        state = "running" if diagnostics.get("status") == "running" else diagnostics.get("status", "")
        row["newest"] = f"{title} {state} {minutes} min"
    return row


def model_processes():
    """The claude CLI calls running now, by their structured-output flag; Windows only, an empty list elsewhere."""
    if os.name != "nt":
        return []
    try:
        # wmic is gone from current Windows 11; Get-CimInstance answers in a second or two.
        listing = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_Process -Filter \"Name='claude.exe'\").CommandLine"],
                                 capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in listing.splitlines() if "--json-schema" in line]


def status(args, output):
    while True:
        names = [args.case] if args.case else sorted(p.name for p in output.iterdir() if (p / "projects").is_dir())
        rows = [variant_status(output, name, copy.name) for name in names
                for copy in sorted((output / name / "projects").iterdir()) if copy.is_dir()]
        lines = [time.strftime("%H:%M:%S") + f"  {output}", "", "Runs (ladder inputs and design variants)", "",
                 f"{'case':10} {'run':20} {'stage':9} {'calls':>5}  {'drafts':6} {'judged':6}  newest call / activity"]
        for r in rows:
            lines.append(f"{r['case']:10} {r['input']:20} {r['stage']:9} {r['calls']:>5}  {r['samples']:<6} "
                         f"{r['judged']:<6}  {r['newest']}" + (f"  ({r['activity']})" if r["activity"] else ""))
        lines += ["", "Write rounds: newest first; a rework sample goes first draft, critique, final draft", ""]
        for name in names:
            folders = sorted((output / name / "write").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True) \
                if (output / name / "write").is_dir() else []
            counts = [(len(list(f.glob("s*/draft.json"))), len(list(f.glob("s*/quality/answer.json")))) for f in folders]
            writing = [f.name for f, (drafts, _) in zip(folders, counts) if drafts < 3]
            judging = [f.name for f, (drafts, judged) in zip(folders, counts) if judged < drafts]
            phase = (f"writing: {', '.join(writing)}; the judges start when every variant of the round is written"
                     if writing else f"judging: {', '.join(judging)}" if judging else "done, report in write_report.md")
            lines.append(f"{name:10} phase: {phase}")
            for folder in folders:
                drafts = len(list(folder.glob("s*/draft.json")))
                judged = len(list(folder.glob("s*/quality/answer.json")))
                pairs = len(list(folder.glob("s*/pair_*/answer.json"))) + len(list(folder.glob("s*/vs_*/answer.json")))
                steps = ""
                if variant_process(folder.name).get("rework"):
                    steps = (f"first drafts {len(list(folder.glob('s*/first_draft.json')))}  "
                             f"critiques {len(list(folder.glob('s*/critique.json')))}  ")
                # judge.md is rewritten by every report, so it says nothing about progress.
                changed = time.strftime("%H:%M", time.localtime(max(
                    (p.stat().st_mtime for p in folder.rglob("*") if p.is_file() and p.name != "judge.md"),
                    default=folder.stat().st_mtime)))
                lines.append(f"{name:10} {folder.name:12} {steps}final drafts {drafts}  judged {judged}  "
                             f"pair judgments {pairs}  (last change {changed})")
        running = sum(1 for _ in model_processes())
        lines += ["", f"Model calls running on this machine: {running}"]
        if not args.watch:
            print("\n".join(lines), flush=True)
            return rows
        # Everything is computed before the screen is cleared, so the table is replaced at once, as watch does. A
        # Windows console often ignores ANSI escapes, so it gets cls.
        os.system("cls" if os.name == "nt" else "clear")
        print("\n".join(lines) + f"\n\nEvery {args.interval} s. Ctrl+C ends the watch.", flush=True)
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return rows


def post_sample(pipeline, case, source, target, variant, output):
    """One final draft through the stages after writing as a script run has them: the dialogue polish
    (polishing.polish_dialogue, without a teaching design) and the evidence-bound script review
    (ScriptRun.review_script) with up to MAX_REVIEW_REPAIRS repairs, which the narrative writer makes. The teaching
    assessment is left out: a narrative episode has no teaching design."""
    from podcast_automate.editorial import episode_series_context
    from podcast_automate.narration import changed_segments
    from podcast_automate.polishing import polish_dialogue
    from podcast_automate.prompts import instructions
    from podcast_automate.research_patches import corrected_call
    from podcast_automate.script_models import ScriptReview
    from podcast_automate.script_pipeline import MAX_REVIEW_REPAIRS, REVIEW_REPAIR_VERSION, review_blocks
    from podcast_automate.teaching import prerequisite_context
    plan_, entries = pipeline.selected()
    entry = entries[0]

    def invoke(prompt, schema, version, **kwargs):
        return invoke_waiting(pipeline, prompt, schema, version)
    draft = EpisodeScript.model_validate_json((source / "draft.json").read_text(encoding="utf-8"))
    saved_plan = source / "story_plan.json"
    plan = StoryPlan.model_validate({k: v for k, v in json.loads(saved_plan.read_text(encoding="utf-8")).items()
                                     if k != "remaining_defects"}) if saved_plan.is_file() else None
    told = planned_entry(case["entry"], plan) if plan else case["entry"]
    prompt = narrative_prompt(pipeline, told, output, case, variant, plan=plan)
    entry = planned_entry(entry, plan) if plan else entry
    target.mkdir(parents=True, exist_ok=True)
    polished, _ = polish_dialogue(pipeline.config, entry, draft, None, invoke, target / "polish", pipeline.script_errors,
                                  series_context=episode_series_context(plan_, entry),
                                  prerequisite_context=prerequisite_context(plan_, entry, pipeline.work),
                                  style_notes=pipeline.style_notes)
    probes = pipeline.run_probes()
    current = polished
    result = pipeline.review_script(plan_, entry, current, polished.model_dump(), probes)
    reviews, repairs = [result.model_dump()], 0
    while result.issues and repairs < MAX_REVIEW_REPAIRS:
        previous = (ScriptReview.model_validate(result.model_dump()), current)
        current = corrected_call(invoke, prompt + "\n" + instructions("script_review_repair") + "\n" + json.dumps(
            {"draft": current.model_dump(), "review": result.model_dump()}, ensure_ascii=False), EpisodeScript,
            REVIEW_REPAIR_VERSION, lambda answer: pipeline.script_defects(
                answer, entry, target / "review_errors.json", "The repaired script breaks its sources or structure."))
        repairs += 1
        result = pipeline.review_script(plan_, entry, current, polished.model_dump(), probes,
                                        follow_up=(previous[0], changed_segments(previous[1], current)))
        reviews.append(result.model_dump())
    write_json(target / "reviews.json", {"reviews": reviews, "repairs": repairs, "blocks": review_blocks(result)})
    write_json(target / "draft.json", current.model_dump())
    (target / "draft.md").write_text(render_script(current, host_labels(pipeline.config)), encoding="utf-8")
    write_json(target / "metrics.json", {**narration_metrics(current, pipeline.config.language, None),
                                         "repairs": repairs, "remaining_errors": pipeline.script_errors(current, entry),
                                         "review_blocks": review_blocks(result)})
    return target


def post_variant(args, output):
    """Runs inside the post process: every final draft of --variant through polish and review, into <variant>_post."""
    case = load_case(args.case)
    copy = output / case["id"] / "projects" / "I3"
    source = output / case["id"] / "write" / args.variant
    folder = output / case["id"] / "write" / f"{args.variant}_post"
    folder.mkdir(parents=True, exist_ok=True)
    with workshop(case, "I3"):
        pipeline = frozen_pipeline(case, copy)
        missing = [s for s in sorted(source.glob("s*")) if (s / "draft.json").is_file()
                   and not (folder / s.name / "draft.json").is_file()]
        with ThreadPoolExecutor(max_workers=max(1, len(missing))) as pool:
            futures = {pool.submit(post_sample, pipeline, case, sample, folder / sample.name, args.variant, output):
                       sample.name for sample in missing}
            for future in as_completed(futures):
                error = future.exception()
                print(json.dumps({"case": case["id"], "variant": f"{args.variant}_post", "sample": futures[future],
                                  **({"error": str(error)} if error else {})}, ensure_ascii=False), flush=True)


def post(args, output):
    """The stages after writing on a variant's drafts (post_variant), then judged against the drafts before them."""
    case = load_case(args.case)
    if args.live:
        post_variant(args, output)
        judge_writes(args, output, case, [args.variant, f"{args.variant}_post"], against=args.variant)
    return write_report(args, output)


# --- agent: the episode planned and written by an agentic Claude Code session ------------------------------------

AGENT_MODEL = "claude-opus-5-5"
AGENT_TIMEOUT = 6000
AGENT_CRITIQUES = 2


def demoted_entry(entry, path):
    """The run's entry with the core findings an agent moved to supporting (demoted.json beside the draft), and with
    its own chapters where it wrote outline.json beside it (agent3: the outline planned after the story material)."""
    defects = []
    if path.is_file():
        demoted = [row["finding_id"] for row in json.loads(path.read_text(encoding="utf-8-sig"))]
        if not set(demoted) <= set(entry.finding_ids) or len(set(demoted)) >= len(entry.finding_ids):
            defects.append("demoted.json may name only the episode's core finding_ids, and at least one must stay "
                           "core.")
        else:
            entry = entry.model_copy(update={
                "finding_ids": [f for f in entry.finding_ids if f not in demoted],
                "supporting_finding_ids": list(dict.fromkeys([*entry.supporting_finding_ids, *demoted])),
                "scenes": [s.model_copy(update={"finding_ids": [f for f in s.finding_ids if f not in demoted]})
                           for s in entry.scenes]})
    outline = path.parent / "outline.json"
    if outline.is_file():
        from podcast_automate.script_models import ScenePlan
        try:
            chapters = json.loads(outline.read_text(encoding="utf-8-sig"))["chapters"]
            scenes = [ScenePlan.model_validate({"purpose": "worked_example", "explanation_steps": [c["question"]], **c})
                      for c in chapters]
        except Exception as exc:  # noqa: BLE001 - the agent reads the defect and fixes the file
            return entry, [*defects, f"outline.json does not parse: {exc}"]
        placed = [f for scene in scenes for f in scene.finding_ids]
        if not 2 <= len(scenes) <= 4:
            defects.append("outline.json holds two to four chapters.")
        if sorted(placed) != sorted(entry.finding_ids):
            defects.append("outline.json must place every kept core finding in exactly one chapter, and no other "
                           f"finding: {entry.finding_ids}.")
        if len({s.scene_id for s in scenes}) != len(scenes):
            defects.append("outline.json needs a unique scene_id per chapter.")
        if not defects:
            entry = entry.model_copy(update={"scenes": scenes})
    return entry, defects


def check_draft(args, output):
    """check.py of an agent's folder: the script checks of a run on the draft at --path, with its demoted.json."""
    from podcast_automate.script_checks import word_budget, writer_target_factor
    case = load_case(args.case)
    path = Path(args.path).resolve()
    with workshop(case, "I3"):
        pipeline = frozen_pipeline(case, output / case["id"] / "projects" / "I3")
        entry, defects = demoted_entry(pipeline.selected()[1][0], path.parent / "demoted.json")
        try:
            draft = EpisodeScript.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - the agent reads the parse error and fixes the file
            print(json.dumps({"passed": False, "errors": [f"draft.json does not parse as an episode script: {exc}"]},
                             ensure_ascii=False, indent=1))
            return []
        errors = [*defects, *pipeline.script_errors(draft, entry), *echo_errors(draft)]
        used = path.parent / "sources_used.json"
        if used.is_file():
            # agent6: corpus sections the agent found count for the quotation limit like the given ones.
            from podcast_automate.script_checks import quotation_errors
            documents = used_sections(case, path.parent)
            listed = {row["reference"] for row in json.loads(used.read_text(encoding="utf-8-sig"))}
            found = {f"{d['source_id']}#{s['id']}" for d in documents for s in d["sections"]}
            errors += [f"sources_used.json names {ref}, which is no corpus section." for ref in sorted(listed - found)]
            errors += quotation_errors(draft, documents)
        budget = word_budget(entry, writer_target_factor(getattr(pipeline.adapter, "text_generation", None)))
        words = sum(len(s.text.split()) for s in draft.segments)
        if words > budget["target_words"] * 1.1:
            errors.append(f"{words} words: stay within a tenth above length.target_words ({budget['target_words']}).")
        print(json.dumps({"passed": not errors, "errors": errors, "words": words,
                          "target_words": budget["target_words"], "minimum_words": budget["minimum_words"],
                          "core_findings": entry.finding_ids}, ensure_ascii=False, indent=1))
    return []


def critique_draft(args, output):
    """critique.py of an agent's folder: the quality rubric on the draft at --path, at most AGENT_CRITIQUES times."""
    case = load_case(args.case)
    path = Path(args.path).resolve()
    done = sorted(path.parent.glob("critique_*"))
    # limits.json: the variant's own number of critiques (process.json critiques), written with the folder.
    limits = path.parent / "limits.json"
    allowed = json.loads(limits.read_text(encoding="utf-8")).get("critiques", AGENT_CRITIQUES) if limits.is_file() \
        else AGENT_CRITIQUES
    if len(done) >= allowed:
        print(json.dumps({"refused": f"critique.py runs at most {allowed} times."}))
        return []
    draft = EpisodeScript.model_validate_json(path.read_text(encoding="utf-8"))
    rated = critique(judge_adapter(case, args), draft, path.parent / f"critique_{len(done) + 1}")
    print(json.dumps(rated.model_dump(), ensure_ascii=False, indent=1))
    return []


def corpus_sections(case):
    """Every section of the case project's research corpus as (reference, source title, text)."""
    root = (PROJECTS / case["project"]).resolve()
    _, _, _, sources, _ = scripting.load_research(root, load_project(root))
    return [(f"{s.id}#{x.id}", s.title, x.text) for s in sources.sources for x in s.sections]


def search_corpus(args, output):
    """search.py of an agent's folder: the corpus sections that hold the most of the query's words (case-blind,
    word starts), each with an excerpt around its first hit; at most --samples hits (default 8)."""
    case = load_case(args.case)
    words = [w.lower() for w in re.findall(r"\w{3,}", args.path or "")]
    if not words:
        print(json.dumps({"error": "search.py needs words to look for."}))
        return []
    scored = []
    for reference, title, text in corpus_sections(case):
        low = text.lower()
        found = [w for w in words if w in low]
        if found:
            scored.append((len(set(found)), reference, title, text, low.find(found[0])))
    scored.sort(key=lambda row: -row[0])
    limit = args.samples if args.samples and args.samples != 2 else 8
    print(json.dumps({"query": words, "matching_sections": len(scored), "hits": [
        {"reference": reference, "source": title[:120], "words_found": count,
         "excerpt": text[max(0, at - 300):at + 500]} for count, reference, title, text, at in scored[:limit]]},
        ensure_ascii=False, indent=1))
    return []


def read_corpus(args, output):
    """read.py of an agent's folder: one corpus section in full, by its reference."""
    case = load_case(args.case)
    wanted = (args.path or "").strip()
    for reference, title, text in corpus_sections(case):
        if reference == wanted:
            print(json.dumps({"reference": reference, "source": title, "text": text}, ensure_ascii=False, indent=1))
            return []
    print(json.dumps({"error": f"No section {wanted}; use a reference exactly as search.py gives it."}))
    return []


def used_sections(case, folder):
    """The corpus sections an agent recorded in sources_used.json, as the quotation check reads sources."""
    path = folder / "sources_used.json"
    if not path.is_file():
        return []
    wanted = {row["reference"] for row in json.loads(path.read_text(encoding="utf-8-sig"))}
    by_source = {}
    for reference, title, text in corpus_sections(case):
        if reference in wanted:
            source_id, section_id = reference.split("#", 1)
            by_source.setdefault(source_id, {"source_id": source_id, "sections": []})["sections"].append(
                {"id": section_id, "text": text})
    return list(by_source.values())


AGENT_SCRIPT = '''import os, subprocess, sys
# check and critique take a draft file; search takes the words, read a section reference.
argument = " ".join(sys.argv[1:]) if {part!r} in ("search-corpus", "read-corpus") else os.path.abspath(
    sys.argv[1] if len(sys.argv) > 1 else "draft.json")
path = argument
result = subprocess.run([{python!r}, {run!r}, {part!r}, {case!r}, "--path", path, "--output", {output!r}],
                        capture_output=True, text=True, encoding="utf-8",
                        env={{**os.environ, "PYTHONIOENCODING": "utf-8"}})
print(result.stdout)
if result.returncode:
    print(result.stderr[-4000:])
'''


def agent_folder(pipeline, case, output, work, variant="agent6"):
    """The agent's working folder: the writer's material as JSON, the variant's task, and its two commands."""
    work.mkdir(parents=True, exist_ok=True)
    told = case["entry"]
    material = json.loads(narrative_prompt(pipeline, told, output, case, "agent", instructions=""))
    told_ids = {f for item in material["story_material"] for f in item.get("finding_ids", [])}
    material["core_findings_without_story"] = [f for f in told.finding_ids if f not in told_ids]
    write_json(work / "material.json", material)
    (work / "TASK.md").write_text((HERE / "variants" / variant / "TASK.md").read_text(encoding="utf-8").replace(
        "{language}", pipeline.config.language), encoding="utf-8")
    write_json(work / "limits.json", {"critiques": int(variant_process(variant).get("critiques", AGENT_CRITIQUES))})
    for name, part in (("check.py", "check-draft"), ("critique.py", "critique"), ("search.py", "search-corpus"),
                       ("read.py", "read-corpus")):
        (work / name).write_text(AGENT_SCRIPT.format(python=sys.executable, run=str(Path(__file__).resolve()),
                                                     part=part, case=case["id"], output=str(output)),
                                 encoding="utf-8")


def agent_sample(pipeline, case, target, output, variant="agent6"):
    """One episode by an agentic Claude Code session (AGENT_MODEL at xhigh) in target/work, kept as a sample."""
    from podcast_automate.claude_code import claude_command, claude_environment
    work = target / "work"
    if not (work / "TASK.md").is_file():
        agent_folder(pipeline, case, output, work, variant)
    if not (work / "draft.json").is_file() or not (work / "agent_result.json").is_file():
        tools = "Read,Write,Edit,Glob,Grep,Bash"
        allowed = ("Read,Write,Edit,Glob,Grep,Bash(python check.py:*),Bash(python critique.py:*),"
                   "Bash(python search.py:*),Bash(python read.py:*)")
        command = claude_command() + [
            "-p", "Read TASK.md in this folder and do the task it describes, start to finish.",
            "--model", AGENT_MODEL, "--effort", "xhigh", "--permission-mode", "dontAsk",
            "--tools", tools, "--allowedTools", allowed, "--no-session-persistence", "--strict-mcp-config",
            "--setting-sources", "", "--disable-slash-commands", "--output-format", "json"]
        result = subprocess.run(command, cwd=work, env=claude_environment(AGENT_MODEL), capture_output=True,
                                text=True, encoding="utf-8", timeout=AGENT_TIMEOUT)
        (work / "agent_result.json").write_text(result.stdout or "{}", encoding="utf-8")
        (work / "agent_stderr.txt").write_text(result.stderr or "", encoding="utf-8")
    draft = EpisodeScript.model_validate_json((work / "draft.json").read_text(encoding="utf-8"))
    entry, defects = demoted_entry(pipeline.selected()[1][0], work / "demoted.json")
    write_json(target / "draft.json", draft.model_dump())
    (target / "draft.md").write_text(render_script(draft, host_labels(pipeline.config)), encoding="utf-8")
    for name in ("plan.md", "demoted.json", "outline.json", "sources_used.json"):
        if (work / name).is_file():
            shutil.copy2(work / name, target / name)
    write_json(target / "metrics.json", {**narration_metrics(draft, pipeline.config.language, None), "repairs": 0,
                                         "remaining_errors": [*defects, *pipeline.script_errors(draft, entry),
                                                              *echo_errors(draft)],
                                         "critiques": len(list(work.glob("critique_*")))})
    return target


def agent(args, output):
    """--samples episodes by the agentic writer at once, then judged and each paired with --against's sample."""
    case = load_case(args.case)
    # --variant names the task (variants/<variant>/TASK.md); without one, the current task (agent6).
    variant = args.variant if args.variant != "baseline" else "agent6"
    folder = output / case["id"] / "write" / variant
    folder.mkdir(parents=True, exist_ok=True)
    if args.live:
        with workshop(case, "I3"):
            pipeline = frozen_pipeline(case, output / case["id"] / "projects" / "I3")
            missing = [folder / f"s{n}" for n in range(1, args.samples + 1)
                       if not (folder / f"s{n}" / "draft.json").exists()]
            with ThreadPoolExecutor(max_workers=max(1, len(missing))) as pool:
                futures = {pool.submit(agent_sample, pipeline, case, target, output, variant): target.name
                           for target in missing}
                for future in as_completed(futures):
                    error = future.exception()
                    print(json.dumps({"case": case["id"], "variant": variant, "sample": futures[future],
                                      **({"error": str(error)} if error else {})}, ensure_ascii=False), flush=True)
        judge_writes(args, output, case, [variant, args.against], against=args.against)
    return write_report(args, output)


def rejudge(args, output):
    """The v3 judge on the named variants' drafts, by --judge-model and by Opus (quality, quality_claude_opus_5_5);
    a judgment saved under an earlier judge prompt is asked again and kept aside (ask). Prints each variant's means
    by both judges and their average."""
    case = load_case(args.case)
    base = output / case["id"] / "write"
    judges = {"quality": judge_adapter(case, args),
              "quality_claude_opus_5_5": judge_adapter(case, SimpleNamespace(**{**vars(args),
                                                                               "judge_model": "claude-opus-5-5"}))}
    samples = [sample for variant in args.variants for sample in sorted((base / variant).glob("s*"))
               if (sample / "draft.json").is_file()]
    if args.live:
        tasks = {(sample, folder): adapter for sample in samples for folder, adapter in judges.items()}
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(quality, adapter, EpisodeScript.model_validate_json(
                (sample / "draft.json").read_text(encoding="utf-8")), sample / folder):
                f"{sample.parent.name}/{sample.name}/{folder}" for (sample, folder), adapter in tasks.items()}
            for future in as_completed(futures):
                error = future.exception()
                if error:
                    print(json.dumps({"judged": futures[future], "error": str(error)}, ensure_ascii=False), flush=True)
    rows = []
    for variant in args.variants:
        means = {}
        for folder in judges:
            rated = [judged(sample / folder) for sample in sorted((base / variant).glob("s*"))]
            rated = [r for r in rated if isinstance(r, Judgment)]
            if rated:
                means[folder] = round(sum(sum(getattr(r.scores, k).score for k in ("tension", "interest",
                                                                                      "storytelling")) / 3
                                          for r in rated) / len(rated), 2)
                means[f"{folder}_samples"] = len(rated)
        if means:
            both = [means[f] for f in judges if f in means]
            rows.append({"case": case["id"], "variant": variant, **means, "both": round(sum(both) / len(both), 2)})
    for row in rows:
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


def crosscheck(args, output):
    """A second judge (--judge-model) rates the named variants' drafts on the same quality rubric, kept in
    quality_<model> beside the first judge's quality. The critic of a rework is the first judge's rubric and model, so
    a gain only the first judge sees may be the draft learning the critic, not a better telling."""
    case = load_case(args.case)
    adapter = judge_adapter(case, args)
    folder_name = "quality_" + re.sub(r"[^a-z0-9]+", "_", args.judge_model.lower()).strip("_")
    base = output / case["id"] / "write"
    samples = [sample for variant in args.variants for sample in sorted((base / variant).glob("s*"))
               if (sample / "draft.json").is_file()]
    if args.live:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(quality, adapter, EpisodeScript.model_validate_json(
                (sample / "draft.json").read_text(encoding="utf-8")), sample / folder_name):
                f"{sample.parent.name}/{sample.name}" for sample in samples}
            for future in as_completed(futures):
                error = future.exception()
                print(json.dumps({"judged": futures[future], **({"error": str(error)} if error else {})},
                                 ensure_ascii=False), flush=True)
    rows = []
    for variant in args.variants:
        for judge_folder, judge in (("quality", "first"), (folder_name, args.judge_model)):
            rated = [judged(sample / judge_folder) for sample in sorted((base / variant).glob("s*"))]
            rated = [r for r in rated if r]
            if rated:
                means = {key: round(sum(getattr(r.scores, key).score for r in rated) / len(rated), 2)
                         for key in ("tension", "interest", "storytelling")}
                rows.append({"variant": variant, "judge": judge, "samples": len(rated), **means,
                             "mean": round(sum(means.values()) / 3, 2)})
    for row in rows:
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("part", choices=("ladder", "judge", "report", "status", "write", "write-variant", "write-report",
                                         "design", "design-variant", "orient", "crosscheck", "post", "agent",
                                         "check-draft", "critique", "search-corpus", "read-corpus", "rejudge"))
    parser.add_argument("case", nargs="?", help="A case under cases/, such as kuchavez or milgram")
    parser.add_argument("--watch", action="store_true", help="status: clear the screen and repeat, as watch does")
    parser.add_argument("--interval", type=int, default=20, help="status --watch: seconds between refreshes")
    parser.add_argument("--variants", type=lambda value: value.split(","), default=[],
                        help="write: the variants under variants/ to write besides the baseline")
    parser.add_argument("--variant", default="baseline", help="write-variant: the one variant this process writes")
    parser.add_argument("--against", default="baseline", help="write, design: the variant the pairs compare with")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--path", help="check-draft, critique: the draft.json an agent writes")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--inputs", type=lambda value: value.split(","), default=list(INPUTS))
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--backend", default="claude_code")
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--judge-model", default="claude-sonnet-5-5")
    parser.add_argument("--check-model", default="claude-opus-5-5", help="orient: the model that checks the sheet")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--workers", type=int, default=4, help="judge: calls at a time")
    args = parser.parse_args()
    if any(variant not in INPUTS for variant in args.inputs):
        parser.error("--inputs takes I1, I2 and I3.")
    if args.part != "status" and not args.case:
        parser.error(f"{args.part} needs a case.")
    output = args.output.resolve()
    {"ladder": ladder, "judge": judge, "report": report, "status": status, "write": write, "write-variant": write_variant,
     "write-report": write_report, "design": design, "design-variant": design_variant, "orient": orient,
     "crosscheck": crosscheck, "post": post, "agent": agent, "check-draft": check_draft,
     "critique": critique_draft, "search-corpus": search_corpus, "read-corpus": read_corpus, "rejudge": rejudge}[args.part](
        args, output)


if __name__ == "__main__":
    main()
