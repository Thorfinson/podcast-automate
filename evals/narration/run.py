"""Real-model checks of the narration stage (D-180), run by hand.

Each part is offline by default, where it only checks its cases and inputs; ``--live`` spends the chosen subscription.

recall     Story material (narration.story_material) on cases/recall.json: does it keep the scene, the act or the
           surprising result that a read section holds and the verified finding leaves out? One call per case.
calibrate  The narration review on published Orlagau scripts, against the user's listening note and one model
           reading (cases/orlagau_reference.json). Two calls per episode: story material and review.
compare    Variants of chosen Orlagau episodes, each an editorial revision (pla script --revise) in its own copy of
           the project, so the project and its recordings stay untouched:
             B  the pipeline before the narration stage, with a storytelling instruction as revision feedback;
             C  the new pipeline (story material, core story, story rules) with the narration review left out;
             D  the new pipeline;
             N  the new pipeline with a detail the sources do not hold slipped into the narrated draft: the
                evidence review must catch it (negative control).
           A is the published script. The scripts of A to D go into blind/<episode>/ under shuffled letters with a
           key, for a reading or listening the user does without knowing which is which; --score adds the narration
           review's scores of each. A revision makes about 15 to 25 calls.

    python evals/narration/run.py recall --output <dir> [--live] [--backend claude_code --model claude-sonnet-5-5 --effort high]
    python evals/narration/run.py calibrate <project> --output <dir> [--live] [--episodes ep_001,ep_002]
    python evals/narration/run.py compare <project> --output <dir> [--live] [--episodes ep_014] [--variants B,C,D,N] [--score]

A model's score is not a listener's, a recall case found does not show the episode tells it well, and a caught
negative control does not show every invented detail is caught.
"""
import argparse
import contextlib
import json
import os
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "src"))
from podcast_automate import script_pipeline  # noqa: E402
from podcast_automate.editorial import episode_series_context, terminology  # noqa: E402
from podcast_automate.errors import AppError  # noqa: E402
from podcast_automate.models import EpisodeScript, host_labels  # noqa: E402
from podcast_automate.narration import (NARRATION_REVIEW_VERSION, NarrationReview, NarrationScores, Score,  # noqa: E402
                                        allowed_sections, compared_episodes, narrate_episode, review_payload,
                                        review_task, sanitized, story_material)
from podcast_automate.polishing import HOST_ROLES  # noqa: E402
from podcast_automate.prompts import text as prompt_text  # noqa: E402
from podcast_automate.provider_pool import text_generation_settings  # noqa: E402
from podcast_automate.script_artifacts import render_script  # noqa: E402
from podcast_automate.script_checks import episode_sources  # noqa: E402
from podcast_automate.script_models import EpisodePlan, SeriesPlan  # noqa: E402
from podcast_automate.scripting import build_adapter, load_research, run_script  # noqa: E402
from podcast_automate.storage import digest, load_project, read_yaml, write_json  # noqa: E402
from podcast_automate.teaching import TeachingPlan  # noqa: E402

PROJECTS = REPO / "projects"
STORY_CATEGORIES = {"compressed_story", "unused_material", "missing_story"}
PROMPTS = ("story_material", "narration_review", "narration_review_followup", "narration_repair", "write_episode_story",
           "teaching_design_story", "teaching_design_review_story", "series_plan_density")
EARLIER_STAGES = ("planning", "teaching", "writing", "polishing", "review", "publish")
FEEDBACK_B = ("Erzähle die stärkste Geschichte dieser Folge zuerst als Geschichte: wer, wo, was geschah und warum, wie "
              "die Quelle es erzählt, der Moment der Wendung und was folgte; erst danach prüfen die Hosts, wie weit die "
              "Quelle trägt. Sage, wie sicher eine Aussage ist, über die Art ihres Belegs, nie darüber, was gelesen wurde.")
# The detail of the negative control: a number and a place no source of the series holds.
INVENTED = {"de": " Nach der Quelle sahen dabei 412 Zeugen auf der Wartburg zu.",
            "en": " According to the source, 412 witnesses watched it at the Wartburg."}
INVENTED_MARKERS = ("412", "Wartburg")
SKIPPED = Score(score=1, reason="Variant C leaves the narration review out.")


def now_id():
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")


def adapter_for(config, args):
    selection = text_generation_settings(config, backend=args.backend, model=args.model, reasoning_effort=args.effort)
    pool = build_adapter(config, selection, None)
    pool.require_key()
    return pool


def invoker(adapter, folder):
    def invoke(prompt, output_type, version):
        number = len(list((folder / "calls").glob("call_*"))) + 1
        return adapter.structured(prompt, output_type, folder / "calls" / f"call_{number:03d}",
                                  prompt_version=version)[0]
    return invoke


def project_terms(config):
    return terminology(config.language, config.topic, getattr(config, "central_question", "") or config.topic)


# --- recall -------------------------------------------------------------------------------------------------------

def section_text(root, run_name, reference):
    source, section = reference.split("#")
    candidates = [root / "sources" / "processed" / run_name / f"{source}.json",
                  *sorted((root / "sources" / "processed").glob(f"*/{source}.json"))]
    for path in candidates:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            document = data.get("value", data)
            for item in document.get("sections", []):
                if item.get("id") == section:
                    return item.get("text", "")
    return None


def recall_inputs(case):
    """The case's finding, its cited sections and a one-finding episode around it, as story_material reads them."""
    root = PROJECTS / case["project"]
    run = root / "runs" / case["research_run"]
    if case.get("dossier_finding"):
        findings = json.loads((run / "dossier.json").read_text(encoding="utf-8"))["findings"]
        finding = next(f for f in findings if f["id"] == case["dossier_finding"])
    else:
        state = json.loads((run / "question_research" / "state.json").read_text(encoding="utf-8"))["value"]
        finding = next(f for f in state["tasks"][case["task"]]["answer"]["findings"] if f["id"] == case["finding"])
    references = list(dict.fromkeys(e["reference"] for e in finding["evidence"]))
    texts = {reference: section_text(root, run.name, reference) for reference in references}
    sources = {}
    for reference, text in texts.items():
        if text is not None:
            sources.setdefault(reference.split("#")[0], []).append({"reference": reference, "text": text, "page": None})
    entry = EpisodePlan.model_validate({
        "episode_id": "ep_001", "title": case["id"], "central_question": finding["statement"][:200],
        "target_minutes": 10, "prerequisite_episodes": [], "finding_ids": ["f_case"], "deferred_questions": [],
        "series_role": "Recall case of the narration evaluation.",
        "scenes": [{"scene_id": "scene_case", "title": case["id"], "question": "What happened?",
                    "purpose": "worked_example", "finding_ids": ["f_case"], "explanation_steps": ["Tell it."]}]})
    dossier = SimpleNamespace(findings=[SimpleNamespace(
        id="f_case", statement=finding["statement"], illustration=finding.get("illustration") or "",
        evidence=[SimpleNamespace(reference=reference) for reference in references])])
    config = SimpleNamespace(language=case["language"], topic=load_project(root).topic, central_question="")
    return config, entry, dossier, [{"source_id": key, "sections": rows} for key, rows in sources.items()], texts


def recall(args, output):
    cases = json.loads((HERE / "cases" / "recall.json").read_text(encoding="utf-8"))["cases"]
    adapters, rows = {}, []
    for case in cases:
        config, entry, dossier, sources, texts = recall_inputs(case)
        joined = " ".join(text or "" for text in texts.values()).lower()
        row = {"case": case["id"], "sections": len(texts), "unreadable_sections": [r for r, t in texts.items() if t is None],
               "section_terms_present": all(term.lower() in joined for term in case["section_terms"])}
        if args.live:
            if case["project"] not in adapters:
                adapters[case["project"]] = adapter_for(load_project(PROJECTS / case["project"]), args)
            folder = output / "recall" / case["id"]
            items = story_material(config, entry, dossier, sources, invoker(adapters[case["project"]], folder), folder)
            matched = [item["item_id"] for item in items
                       if any(word in (item["text"] + " " + item["anchor"]).lower() for word in case["expect_any"])]
            row.update(items=len(items), matched=matched, found=bool(matched),
                       texts=[item["text"] for item in items])
        rows.append(row)
        print(json.dumps({key: value for key, value in row.items() if key != "texts"}, ensure_ascii=False), flush=True)
    summary = {"cases": len(rows), "inputs_ok": all(r["section_terms_present"] and not r["unreadable_sections"] for r in rows)}
    if args.live:
        summary["found"] = sum(1 for r in rows if r.get("found"))
    return {"rows": rows, "summary": summary}


# --- calibrate ----------------------------------------------------------------------------------------------------

def published_run(root, episode):
    pointer = root / "episodes" / episode / "latest.json"
    if not pointer.is_file():
        pointer = root / "episodes" / "latest.json"
    return root / "runs" / json.loads(pointer.read_text(encoding="utf-8"))["run_id"]


def review_script_narration(root, config, research, work, episode, script, invoke, folder, story=None):
    """The narration review of ``script`` as the pipeline asks it, with the published run's design and plan."""
    _, dossier, _, index, context = research
    plan = SeriesPlan.model_validate_json((work / "series_plan.json").read_text(encoding="utf-8"))
    entry = next(e for e in plan.episodes if e.episode_id == episode)
    design = TeachingPlan.model_validate_json((work / "teaching" / episode / "plan.json").read_text(encoding="utf-8"))
    sources = episode_sources(entry, dossier, context, index)
    if story is None:
        story = story_material(config, entry, dossier, sources, invoke, folder)
    brief = {"language": config.language, "audience": config.audience_level, "style": config.depth_request,
             "style_notes": ""}
    payload = review_payload(script, brief=brief, host_roles=HOST_ROLES, entry=entry,
                             series_context=episode_series_context(plan, entry), design=design, story=story,
                             dossier=dossier, sections=allowed_sections(entry, dossier, sources),
                             compared=compared_episodes(plan, entry, work), language=config.language)
    answer = invoke(review_task(project_terms(config), config.language) + "\n" + json.dumps(payload, ensure_ascii=False),
                    NarrationReview, NARRATION_REVIEW_VERSION + "+eval")
    review = sanitized(answer, script, [item["item_id"] for item in story])
    write_json(folder / "review.json", review.model_dump())
    return review, payload["metrics"], story


def calibrate(args, output):
    root = args.project.resolve()
    config = load_project(root)
    reference = json.loads((HERE / "cases" / "orlagau_reference.json").read_text(encoding="utf-8"))
    episodes = args.episodes or ["ep_001", "ep_002", "ep_003", "ep_004"]
    research = load_research(root, config) if args.live else None
    adapter = adapter_for(config, args) if args.live else None
    expected = reference["expectations"]
    rows = []
    for episode in episodes:
        work = published_run(root, episode)
        script = EpisodeScript.model_validate_json((work / "reviewed" / f"{episode}.json").read_text(encoding="utf-8"))
        row = {"episode": episode, "run_id": work.name, "segments": len(script.segments),
               "reference": reference["episodes"].get(episode, {})}
        if args.live:
            folder = output / "calibrate" / episode
            review, metrics, story = review_script_narration(root, config, research, work, episode, script,
                                                             invoker(adapter, folder), folder)
            scores = {key: value["score"] for key, value in review.scores.model_dump().items()}
            categories = [point.category for point in review.points]
            row.update(scores=scores, categories=categories, story_items=len(story),
                       workshop_phrases=len(metrics["workshop_phrases"]),
                       checks={"not_really_good": scores["storytelling"] <= expected["storytelling_at_most"],
                               "story_point": sum(c in STORY_CATEGORIES for c in categories) >= expected["story_points_per_episode"],
                               "workshop_named": len(metrics["workshop_phrases"]) < 3 or "workshop_talk" in categories})
        rows.append(row)
        print(json.dumps({key: value for key, value in row.items() if key != "reference"}, ensure_ascii=False), flush=True)
    summary = {"episodes": len(rows), "listener_note": reference["listener_note"]}
    if args.live:
        summary["all_checks"] = all(all(r["checks"].values()) for r in rows)
    return {"rows": rows, "summary": summary}


# --- compare ------------------------------------------------------------------------------------------------------

def copy_project(source, target, runs):
    """A working copy of ``source`` with only ``runs`` among its runs. The large read-only folders (inputs, sources) are
    hard-linked file by file, so the copy costs little space; a script run only adds files there, never rewrites one."""
    skip = {"cache", "exports", "logs", "probes", "publish"}
    for folder, names, files in os.walk(source):
        relative = Path(folder).relative_to(source)
        parts = relative.parts
        if parts and parts[0] in skip:
            names[:] = []
            continue
        if parts == ("runs",):
            names[:] = [name for name in names if name in runs]
        destination = target / relative
        destination.mkdir(parents=True, exist_ok=True)
        for name in files:
            # runs/latest.json names the copy's own revision once it starts, so the original's is left behind.
            if name.endswith(".lock") or (parts == ("runs",) and name == "latest.json"):
                continue
            origin, copy = Path(folder) / name, destination / name
            if copy.exists():
                continue
            if parts and parts[0] in {"inputs", "sources"}:
                os.link(origin, copy)
            else:
                shutil.copy2(origin, copy)


def skip_review(real):
    def narrate(folder, signature, draft, *, review, repair, followup):
        empty = NarrationReview(scores=NarrationScores(tension=SKIPPED, interest=SKIPPED, storytelling=SKIPPED),
                                points=[], strengths=[])
        return real(folder, signature, draft, review=lambda script: empty, repair=repair, followup=followup)
    return narrate


def tampering(real, language, record):
    def narrate(folder, signature, draft, *, review, repair, followup):
        final, result = real(folder, signature, draft, review=review, repair=repair, followup=followup)
        target = next(segment for segment in final.segments if segment.knowledge_refs)
        changed = final.model_copy(update={"segments": [
            segment.model_copy(update={"text": segment.text + INVENTED["de" if language.startswith("de") else "en"]})
            if segment.segment_id == target.segment_id else segment for segment in final.segments]})
        write_json(folder / "script.json", changed.model_dump())
        result = {**result, "narrated_digest": digest(changed.model_dump()), "tampered_segment": target.segment_id}
        write_json(folder / "result.json", result)
        record["tampered_segment"] = target.segment_id
        return changed, result
    return narrate


def recording_reviews(record):
    original = script_pipeline.ScriptRun.review_script

    def review_script(self, plan, entry, draft, *args, **kwargs):
        result = original(self, plan, entry, draft, *args, **kwargs)
        carrying = [s.segment_id for s in draft.segments if any(marker in s.text for marker in INVENTED_MARKERS)]
        named = [issue.model_dump() for issue in result.issues
                 if set(issue.segment_ids) & set(carrying) or any(m in issue.reason for m in INVENTED_MARKERS)]
        record.setdefault("reviews", []).append({"carrying_segments": carrying, "issues_on_invented_detail": named,
                                                 "issues": [issue.model_dump() for issue in result.issues]})
        return result
    return review_script


def run_variant(root, episode, variant, args, record):
    """One revision of ``episode`` in the copy ``root``, or the resume of one stopped before."""
    with contextlib.ExitStack() as stack:
        if variant == "B":
            stack.enter_context(patch("podcast_automate.scripting.STAGES", EARLIER_STAGES))
        if variant == "C":
            stack.enter_context(patch("podcast_automate.script_pipeline.narrate_episode", skip_review(narrate_episode)))
        if variant == "N":
            stack.enter_context(patch("podcast_automate.script_pipeline.narrate_episode",
                                      tampering(narrate_episode, load_project(root).language, record)))
            stack.enter_context(patch("podcast_automate.script_pipeline.ScriptRun.review_script", recording_reviews(record)))
        started = root / "runs" / "latest.json"
        previous = json.loads(started.read_text(encoding="utf-8"))["run_id"] if started.is_file() else None
        if previous:
            # The copy's own revision, stopped before (quota, budget) or already finished.
            return run_script(root, resume=True, run_id=previous)
        return run_script(root, revise=episode, feedback=FEEDBACK_B if variant == "B" else "",
                          backend=args.backend, model=args.model, reasoning_effort=args.effort)


def compare(args, output):
    source = args.project.resolve()
    config = load_project(source)
    episodes = args.episodes or ["ep_014"]
    variants = args.variants or ["B", "C", "D", "N"]
    research_run = json.loads((source / "research" / "latest.json").read_text(encoding="utf-8"))["run_id"]
    rows = []
    for episode in episodes:
        published = published_run(source, episode).name
        for variant in variants:
            target = output / "projects" / f"{variant}_{episode}"
            record = {"episode": episode, "variant": variant, "project": str(target), "published_run": published}
            if not args.live:
                record["planned"] = {"runs_copied": [research_run, published], "feedback": variant == "B"}
                rows.append(record)
                continue
            copy_project(source, target, {research_run, published})
            try:
                run = run_variant(target, episode, variant, args, record)
                record.update(run_id=run.run_id, status=run.status,
                              error={name: stage.error.model_dump() for name, stage in run.stages.items() if stage.error})
            except AppError as exc:
                record.update(status="error", error={"code": exc.code, "message": str(exc)})
            work = target / "runs" / record.get("run_id", "missing")
            budget = work / "budget.json"
            record["model_calls"] = json.loads(budget.read_text(encoding="utf-8")).get("model_calls") if budget.is_file() else None
            narration = work / "narration" / episode / "result.json"
            if narration.is_file():
                record["narration"] = json.loads(narration.read_text(encoding="utf-8"))
            if variant == "N":
                record["caught"] = any(review["issues_on_invented_detail"] for review in record.get("reviews", []))
            rows.append(record)
            print(json.dumps({key: value for key, value in record.items() if key not in {"reviews", "narration"}},
                             ensure_ascii=False), flush=True)
        if args.live:
            blind(source, config, output, episode, [v for v in variants if v != "N"], args)
    return {"rows": rows, "summary": {"variants": variants, "episodes": episodes,
                                      "negative_control_caught": [r.get("caught") for r in rows if r["variant"] == "N"]}}


def blind(source, config, output, episode, variants, args):
    """The scripts of A and the variants under shuffled letters, a key, and with --score the narration review's
    scores of each, asked as the pipeline asks them, with the published run's design and the D run's story material."""
    scripts = {"A": source / "episodes" / episode / "script.yaml"}
    for variant in variants:
        scripts[variant] = output / "projects" / f"{variant}_{episode}" / "episodes" / episode / "script.yaml"
    present = {name: path for name, path in scripts.items() if path.is_file()}
    letters = list("WXYZV"[:len(present)])
    random.Random(f"{episode}:{sorted(present)}").shuffle(letters)
    key, folder = {}, output / "blind" / episode
    labels = host_labels(config)
    for (name, path), letter in zip(sorted(present.items()), letters):
        script = EpisodeScript.model_validate(read_yaml(path))
        (folder).mkdir(parents=True, exist_ok=True)
        (folder / f"Fassung_{letter}.md").write_text(render_script(script, labels), encoding="utf-8")
        key[letter] = name
    write_json(output / "blind" / f"{episode}_key.json", key)
    if not args.score:
        return
    research = load_research(source, config)
    adapter = adapter_for(config, args)
    work = published_run(source, episode)
    story_file = output / "projects" / f"D_{episode}"
    story = None
    found = sorted(story_file.glob(f"runs/*/teaching/{episode}/story_material.json"))
    if found:
        story = json.loads(found[-1].read_text(encoding="utf-8")).get("items", [])
    scores = {}
    for letter, name in key.items():
        script = EpisodeScript.model_validate(read_yaml(present[name]))
        review, _, story = review_script_narration(source, config, research, work, episode, script,
                                                   invoker(adapter, output / "scores" / episode / letter),
                                                   output / "scores" / episode / letter, story=story)
        scores[letter] = {key: value["score"] for key, value in review.scores.model_dump().items()}
    write_json(output / "blind" / f"{episode}_scores.json", scores)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("part", choices=("recall", "calibrate", "compare"))
    parser.add_argument("project", type=Path, nargs="?", help="The Orlagau project (calibrate, compare); never written")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--backend", default="claude_code")
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--effort", default="high")
    parser.add_argument("--episodes", type=lambda value: value.split(","))
    parser.add_argument("--variants", type=lambda value: value.split(","))
    parser.add_argument("--score", action="store_true", help="compare: also score every blind script")
    args = parser.parse_args()
    if args.part != "recall" and args.project is None:
        parser.error("calibrate and compare need the project.")
    output = args.output.resolve()
    report_id = now_id()
    result = {"part": args.part, "live": args.live, "report_id": report_id,
              "text_generation": {"backend": args.backend, "model": args.model, "effort": args.effort},
              "prompt_hashes": {name: digest(prompt_text(name)) for name in PROMPTS},
              **{"recall": recall, "calibrate": calibrate, "compare": compare}[args.part](args, output),
              "human_listening_validated": False}
    write_json(output / f"{args.part}_results.json", result)
    write_json(output / "history" / f"{args.part}_{report_id}.json", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
