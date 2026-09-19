"""Run the production dialogue comparison on fixed positive and negative controls.

Offline by default: it only checks that the archived cases are well formed and that the
deterministic half of the review contract behaves. ``--live`` spends the configured
subscription on one real ``compare_dialogue`` call per case and records the verdict.

The negative control is the passage the September 2026 audit read as too dense; the positive
control is the seasons dialogue also used by the teaching evals. A model verdict is not a
listener test, and a passing control does not establish that the dialogue works.

    python evals/dialogue_polishing/run.py <project> --output <dir> [--live] [--case <id>]
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from podcast_automate.codex import CodexAdapter
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript, Segment
from podcast_automate.polishing import (DEMANDING_PASSAGES, POLISH_REVIEW_VERSION, compare_dialogue,
                                        unresolved_referents, validate_polish_review)
from podcast_automate.prompts import text as prompt_text
from podcast_automate.script_models import EpisodePlan
from podcast_automate.storage import digest, load_project, write_json

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def seasons_case():
    """A short, clearly structured dialogue that the review is expected to accept."""
    source = (REPO / "evals/teaching_quality/seasons.md").read_text(encoding="utf-8")
    turns = [block for block in source.split("\n\n") if block.startswith("**")]
    segments = []
    for i, block in enumerate(turns, 1):
        voice, _, spoken = block.partition(":**")
        segments.append(Segment(segment_id=f"seg_{i:03d}", scene_id="scene_main", chapter_id="scene_main",
                                speaker_id="host_a" if "Aiden" in voice else "host_b", text=spoken.strip()))
    script = EpisodeScript(episode_id="ep_001", title=source.splitlines()[0].lstrip("# "), purpose="deep_dive",
                           chapters=[Chapter(chapter_id="scene_main", title="Warum es Jahreszeiten gibt")],
                           segments=segments)
    episode = EpisodePlan(episode_id="ep_001", title=script.title, central_question="Warum gibt es Jahreszeiten?",
        target_minutes=8, prerequisite_episodes=[], finding_ids=["f_tilt"], deferred_questions=[],
        scenes=[{"scene_id": "scene_main", "title": "Warum es Jahreszeiten gibt",
                 "question": "Warum gibt es Jahreszeiten?", "purpose": "explanation",
                 "finding_ids": ["f_tilt"], "explanation_steps": ["Neigung erklären.", "Einstrahlung erklären."]}])
    return {"id": "seasons_control", "origin": "Authored positive control, shared with the teaching evals.",
            "expected": {"verdict": "pass"}, "episode": episode.model_dump(),
            "brief": {"language": "de-DE", "audience": "Neugierige Erwachsene", "prior_knowledge": "",
                      "depth": "Kurze, vollständige Erklärung."},
            "original": script.model_dump(), "candidate": script.model_dump(),
            "label_origin": "Authored control; not a measured listener test."}


def load_cases(selected):
    cases = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((HERE / "cases").glob("*.json"))]
    cases.append(seasons_case())
    return [case for case in cases if not selected or case["id"] == selected]


def offline(case):
    """Check the archive itself: real scripts, real plan, and a review contract that can be met."""
    original = EpisodeScript.model_validate(case["original"])
    candidate = EpisodeScript.model_validate(case["candidate"])
    episode = EpisodePlan.model_validate(case["episode"])
    eligible = [s.segment_id for s in candidate.segments[1:-1]]
    return {"segments": len(candidate.segments), "eligible_passages": len(eligible),
            "passages_required": min(DEMANDING_PASSAGES, len(eligible)),
            "expected_passages_are_eligible": all(
                sid in eligible for sid in case["expected"].get("demanding_passages_include", [])),
            "episode_id": episode.episode_id,
            "original_digest": digest(original.model_dump()),
            "candidate_digest": digest(candidate.model_dump())}


def live(case, adapter, output):
    config = case["brief"]
    episode = EpisodePlan.model_validate(case["episode"])
    original = EpisodeScript.model_validate(case["original"])
    candidate = EpisodeScript.model_validate(case["candidate"])

    def invoke(prompt, output_type, version):
        number = len(list((output / "calls").glob("call_*"))) + 1
        return adapter.structured(prompt, output_type, output / "calls" / f"call_{number:03d}",
                                  prompt_version=version, search=False)[0]

    review = compare_dialogue(config, episode, original, candidate, invoke)
    try:
        density = validate_polish_review(review, original, candidate)
        rejected = None
    except AppError as exc:
        density, rejected = [], exc.code
    spoken = next(c for c in review.checks if c.criterion == "spoken_language")
    return {"verdict": "fail" if spoken.verdict == "fail" or density else "pass",
            "spoken_language": spoken.verdict, "reason": spoken.reason,
            "deterministic_issues": density, "rejected_review": rejected,
            "demanding_passages": [p.segment_id for p in review.demanding_passages],
            "unresolved": [f"{p.segment_id}: {r.expression}" for p, r in unresolved_referents(review)],
            "review": review.model_dump()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Read runtime settings only; nothing is written to it")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--case")
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    cases = load_cases(arguments.case)
    if not cases:
        parser.error("The requested case does not exist.")
    adapter = CodexAdapter(load_project(arguments.project.resolve()).runtime) if arguments.live else None
    report_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    rows = []
    for case in cases:
        row = {"case": case["id"], "expected": case["expected"], "label_origin": case["label_origin"],
               "offline": offline(case)}
        if arguments.live:
            row["actual"] = live(case, adapter, output / case["id"])
            row["matches_expectation"] = row["actual"]["verdict"] == case["expected"]["verdict"]
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "actual"}, ensure_ascii=False), flush=True)
    result = {"live": arguments.live, "report_id": report_id,
              "review_version": POLISH_REVIEW_VERSION,
              "prompt_hashes": {name: digest(prompt_text(name)) for name in
                                ("dialogue_polish", "dialogue_polish_review", "episode_framing", "continuity")},
              "cases": rows,
              "all_match": all(r.get("matches_expectation", False) for r in rows) if arguments.live else None,
              "human_listening_validated": False}
    write_json(output / "results.json", result)
    write_json(output / "history" / f"{report_id}.json", result)
    if not arguments.live:
        return 0
    return 0 if result["all_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
