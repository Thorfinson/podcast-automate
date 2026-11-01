"""Normal against lean research (2026-10-30): does a research run with far fewer sub-questions still give the story
agent (D-181) what it needs?

The same brief (brief.json, one episode of about 15 minutes) is researched twice: ``normal`` with at most NORMAL_TASKS
sub-questions and ``lean`` with at most LEAN_TASKS (the plan approval's own cap, ``pla approve --research-plan
--max-tasks``). Each research is written as one episode by a script run by the story agent. The two published
scripts are then judged blind by the narration lab's v3 judge (Sonnet and Opus, judge_quality.txt) and its pair judge
in both orders, and the report puts beside them what each side cost: sub-questions, sources, findings, calls, model
hours, the reported cost and the wall clock, and what the script review still found.

The lab measured that research decides what can be told and where the traps lie (evals/narration_lab, 0b ladder);
this is the check of that against a smaller research, on a topic whose popular version and archive differ.

    python evals/research_lean/run.py setup
    pla research .studio/evals/research_lean/normal --backend claude_code --model claude-sonnet-5-5 --reasoning-effort high
    pla approve .studio/evals/research_lean/normal --research-plan --max-tasks 12   (lean: 4), then resume and approve
    pla script .studio/evals/research_lean/normal --backend claude_code --model claude-sonnet-5-5 ...
    python evals/research_lean/run.py judge --live
    python evals/research_lean/run.py report

Outputs live in .studio/evals/research_lean/ (git-ignored): the two projects and judge/.
"""
import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUTPUT = REPO / ".studio" / "evals" / "research_lean"
SIDES = {"normal": 12, "lean": 4}
JUDGES = {"quality": "claude-sonnet-5-5", "quality_claude_opus_5_5": "claude-opus-5-5"}

sys.path.insert(0, str(REPO / "src"))
from podcast_automate.models import EpisodeScript, RunManifest, TopicBrief  # noqa: E402
from podcast_automate.storage import init_project, load_project, read_yaml, write_json  # noqa: E402


def lab():
    """The narration lab's judge (quality, compare, Judgment), loaded from its file."""
    spec = importlib.util.spec_from_file_location("narration_lab", REPO / "evals" / "narration_lab" / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def runs(root, kind):
    found = []
    for manifest in sorted((root / "runs").glob("run_*/run_manifest.yaml")):
        data = RunManifest.model_validate(read_yaml(manifest))
        if data.kind == kind:
            found.append((manifest.parent, data))
    return found


def setup(args):
    brief = TopicBrief.model_validate({**read(HERE / "brief.json"), "text_backend": "claude_code"})
    for side in SIDES:
        root = OUTPUT / side
        if (root / "project.yaml").exists():
            print(f"{side}: exists, left as it is")
            continue
        init_project(root, brief)
        # Both sides research five sub-questions at a time, as the real projects do.
        write_json(root / "studio" / "execution.json", {"text": "parallel", "audio": "sequential"})
        print(f"{side}: {root} (at most {SIDES[side]} sub-questions)")


def call_stats(run):
    """Calls, model seconds from timing.json, reported cost and the span from the first to the last call."""
    count, seconds, cost, starts, ends = 0, 0.0, 0.0, [], []
    schemas = defaultdict(int)
    for call in sorted((run / "calls").glob("call_*")):
        timing, meta = read(call / "timing.json", {}), read(call / "metadata.json", {})
        count += 1
        seconds += float(timing.get("seconds") or 0)
        cost += float(meta.get("reported_cost_usd") or 0)
        schemas[timing.get("schema") or read(call / "output_schema.json", {}).get("title") or "?"] += 1
        for key, bucket in (("started_at", starts), ("finished_at", ends)):
            if timing.get(key):
                bucket.append(datetime.fromisoformat(timing[key]))
    span = (max(ends) - min(starts)).total_seconds() / 3600 if starts and ends else None
    return {"calls": count, "model_hours": round(seconds / 3600, 2), "reported_usd": round(cost, 1),
            "span_hours": round(span, 2) if span is not None else None, "by_schema": dict(schemas)}


def research_stats(root):
    found = runs(root, "research")
    if not found:
        return None
    run, manifest = found[-1]
    questions = read(run / "research_questions.json", {})
    dossier = read(run / "dossier.json", {})
    return {"run_id": run.name, "status": manifest.status, "sub_questions": questions.get("total"),
            "closed": questions.get("closed"), "blocked": questions.get("blocked"),
            "sources": len(read(run / "source_index.json", {}).get("sources", [])),
            "findings": len(dossier.get("findings", [])), **call_stats(run)}


EPISODE = "ep_001"
BLOCKING = {"grounding", "scope", "structure"}


def published(root):
    """The story agent's draft of EPISODE in the side's latest script run, as the session left it and code accepted
    it (drafts/<episode>.json), with that run. The judges read the agent's work before the script review, whose
    findings the report lists apart (review_findings): the lean run's review stopped on a length issue (2026-11-01)."""
    for run, _ in reversed(runs(root, "script")):
        draft = run / "drafts" / f"{EPISODE}.json"
        if draft.is_file():
            return run, EpisodeScript.model_validate_json(draft.read_text(encoding="utf-8"))
    return None, None


def review_findings(run):
    """The script review's blocking issues on EPISODE: in its first review and, after repairs, in its latest."""
    reviews = []
    for call in sorted((run / "calls").glob("call_*")):
        if read(call / "output_schema.json", {}).get("title") != "ScriptReview" or not (call / "response.json").is_file():
            continue
        answer = read(call / "response.json", {})
        segments = {key for issue in answer.get("issues", []) + answer.get("advisories", [])
                    for key in issue.get("segment_ids", [])} | {check.get("segment_id") for check in
                                                                 answer.get("claim_checks", [])}
        if any(str(key).startswith(EPISODE) for key in segments):
            reviews.append(answer)
    latest = read(run / "reviews" / f"{EPISODE}_checkpoint.json", {})

    def blocking(answer):
        return [{"category": issue["category"], "reason": issue["reason"][:300]}
                for issue in (answer or {}).get("issues", []) if issue["category"] in BLOCKING]
    return {"reviews": len(reviews), "repairs": latest.get("repairs"),
            "first": blocking(reviews[0]) if reviews else None, "latest": blocking(latest.get("review"))}


def script_stats(root):
    found = runs(root, "script")
    if not found:
        return None
    run, manifest = found[-1]
    stats = {"run_id": run.name, "status": manifest.status, "stages": list(manifest.stages), **call_stats(run)}
    for folder in [run / "story" / EPISODE]:
        session = read(folder / "session.json", {})
        stats["session"] = {"num_turns": session.get("num_turns"), "minutes": round((session.get("duration_ms") or 0)
                                                                                    / 60000, 1),
                            "reported_usd": session.get("total_cost_usd"),
                            "corpus_sections_used": len(read(folder / "sources_used.json", []) or []),
                            "own_chapters": (folder / "outline.json").is_file()}
        stats["critiques"] = [{k: v["score"] for k, v in (read(answer, {}).get("scores") or {}).items()}
                              for answer in sorted((folder / "requests").glob("critique_*.answer.json"))]
    stats["review"] = review_findings(run)
    _, script = published(root)
    if script:
        stats["words"] = sum(len(segment.text.split()) for segment in script.segments)
    return stats


def judge(args):
    module = lab()
    scripts = {side: published(OUTPUT / side)[1] for side in SIDES}
    missing = [side for side, script in scripts.items() if script is None]
    if missing:
        sys.exit(f"No agent draft of {EPISODE} yet: {', '.join(missing)}")
    config = load_project(OUTPUT / "normal")
    adapters = {}
    for folder, model in JUDGES.items():
        selection = module.text_generation_settings(config, backend="claude_code", model=model,
                                                    reasoning_effort=args.effort)
        adapters[folder] = module.build_adapter(config, selection, None)
    target = OUTPUT / "judge"
    tasks = {}
    for side, script in scripts.items():
        write_json(target / side / "draft.json", script.model_dump())
        (target / side / "draft.md").write_text(module.script_text(script), encoding="utf-8")
        for folder, adapter in adapters.items():
            tasks[f"{side}/{folder}"] = (module.quality, adapter, script, target / side / folder)
    # Both orders, so the pair judge's preference for the second script cancels out (lab, 2026-10-26).
    for first, second in (("normal", "lean"), ("lean", "normal")):
        for folder, adapter in adapters.items():
            name = f"pair_{first}_first_{folder.replace('quality', 'by')}"
            tasks[name] = (module.compare, adapter, scripts[first], scripts[second], target / name)
    if not args.live:
        print(json.dumps(sorted(tasks), indent=1))
        return
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(task[0], *task[1:]): name for name, task in tasks.items()}
        for future in as_completed(futures):
            error = future.exception()
            print(json.dumps({"done": futures[future], **({"error": str(error)} if error else {})}), flush=True)
    report(args)


def judgments():
    target = OUTPUT / "judge"
    rows = {}
    for side in SIDES:
        scores = {}
        for folder in JUDGES:
            answer = read(target / side / folder / "answer.json")
            if answer:
                scores[folder] = {k: answer["scores"][k]["score"] for k in ("tension", "interest", "storytelling")}
        if scores:
            means = [sum(s.values()) / 3 for s in scores.values()]
            rows[side] = {**scores, "mean": round(sum(means) / len(means), 2)}
    # Per judge and scale the side each order preferred (Preference: first, second or tie); a side wins only when
    # both orders agree.
    pairs = {}
    for folder in JUDGES:
        orders = []
        for first, second in (("normal", "lean"), ("lean", "normal")):
            answer = read(target / f"pair_{first}_first_{folder.replace('quality', 'by')}" / "answer.json")
            if answer:
                orders.append({key: {"first": first, "second": second}.get(answer[key], "tie")
                               for key in ("tension", "interest", "storytelling", "overall")})
        if len(orders) == 2:
            pairs[folder] = {key: orders[0][key] if orders[0][key] == orders[1][key] else "tie" for key in orders[0]}
    return {"scores": rows, "pairs": pairs}


def report(args):
    rows = {side: {"max_sub_questions": cap, "research": research_stats(OUTPUT / side),
                   "script": script_stats(OUTPUT / side)} for side, cap in SIDES.items()}
    rows["judge"] = judgments()
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    write_json(OUTPUT / "report.json", rows)


def main():
    parser = argparse.ArgumentParser(prog="evals/research_lean/run.py")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup")
    commands.add_parser("report")
    judged = commands.add_parser("judge")
    judged.add_argument("--live", action="store_true", help="ask the judges; without it, list what would be asked")
    judged.add_argument("--effort", default="high")
    judged.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    {"setup": setup, "report": report, "judge": judge}[args.command](args)


if __name__ == "__main__":
    main()
