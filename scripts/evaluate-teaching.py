"""Run the real production teaching checks on fixed positive and negative controls.

Offline validation is the default; --live uses the configured ChatGPT subscription.
No research, project publication, or audio is started. Expected labels are never sent to reviewers.
"""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from podcast_automate.codex import CodexAdapter
from podcast_automate.models import Chapter, EpisodeScript, Segment
from podcast_automate.research import reserve_call
from podcast_automate.storage import digest, load_project, write_json
from podcast_automate.teaching import TeachingPlan, assess_teaching, TEACHING_VERSION

REPO = Path(__file__).resolve().parents[1]


def parse_script(text, episode_id="ep_001"):
    turns = re.findall(r"\*\*(Aiden|Vivian):\*\*\s*(.*?)(?=\n\n\*\*|\Z)", text, re.S)
    return EpisodeScript(episode_id=episode_id, title=text.splitlines()[0].lstrip("# "), purpose="deep_dive",
        chapters=[Chapter(chapter_id="scene_main", title="An explanation")],
        segments=[Segment(segment_id=f"seg_{i:03d}", scene_id="scene_main", chapter_id="scene_main",
                          speaker_id="host_a" if voice == "Aiden" else "host_b", text=words.strip())
                  for i, (voice, words) in enumerate(turns, 1)])


def design_for(script, *, pilot=False):
    scenes = [c.chapter_id for c in script.chapters]
    if pilot:
        title = "Explain how a model evaluates possibilities, finds outputs and learns from data."
        concepts = [("model", "A parameterized evaluation rule."), ("candidate", "An outcome being considered."),
                    ("learning", "Changing shared model parameters using evidence.")]
        goals = [("goal_orientation", "Explain what the model observes, produces and learns.",
                  "What is given to the model, what is being inferred, and what changes during learning?",
                  ["Separate observation from candidate output.", "Separate changing a candidate from changing the evaluation rule."]),
                 ("goal_learning", "Derive why relative model preferences matter.",
                  "Why can lowering energies of observed data fail to improve the model, and what comparison fixes this?",
                  ["Uniform changes can leave all probabilities unchanged.", "Compare data expectations with current model expectations."]),
                 ("goal_transfer", "Explain the link between generation and learning.",
                  "How can an unrepresentative set of generated examples distort the next learning step?",
                  ["Generated examples estimate the model's preferences.", "Missing regions bias that comparison and therefore the update."])]
        setup = "The same video continuation problem develops into a finite learning example and then a large output space."
        steps = ["Compare data and model proportions.", "Change energies and recompute proportions; explain the correction."]
        conclusion = "Generation can influence the reliability of learning, not only the presentation of outputs."
    else:
        title = "Explain opposite seasons through geometry and test a causal explanation."
        concepts = [("orientation", "The axis is tilted and its orientation changes relative to sunlight during the orbit."),
                    ("illumination", "Angle and daylight duration affect received solar energy.")]
        goals = [("goal_cause", "Explain why opposite hemispheres have opposite seasons.",
                  "Why does changing Earth-Sun distance not explain opposite seasons, and how does axial tilt explain them?",
                  ["Distance alone affects the planet together.", "Tilt changes hemispheres' illumination in opposite directions."]),
                 ("goal_mechanism", "Explain how illumination affects received energy.",
                  "Why do sunlight angle and length of daylight both matter for seasonal heating?",
                  ["Oblique light spreads the same beam over more surface.", "Longer daylight permits longer energy input."]),
                 ("goal_transfer", "Transfer the mechanism to a changed planet.",
                  "On a circular orbit with an untilted axis, what seasonal mechanism disappears and what differences can remain?",
                  ["No yearly change from orbital distance or axial illumination pattern remains.",
                   "Day/night and differences between latitudes need not disappear."])]
        setup = "A flashlight and tilted paper isolate the geometric effect; then consider a planet without tilt."
        steps = ["Spread a fixed beam over a larger area.", "Infer less energy per equal area and combine with daylight duration."]
        conclusion = "A causal explanation should support a prediction for a changed case as well as fit the original observation."
    return TeachingPlan.model_validate({"episode_id": script.episode_id, "learner_start": "Curious adult, no specialist prior knowledge.",
        "opening_problem": title, "relevance": "Explain an observation rather than merely recall terminology.", "destination": title,
        "objectives": [{"objective_id": i, "ability": a, "question": q, "expected_reasoning": r, "finding_ids": ["benchmark_reference"]}
                       for i, a, q, r in goals],
        "concepts": [{"concept_id": i, "meaning": m, "introduced_in": scenes[0],
                      "requires": [concepts[n-1][0]] if n else [], "finding_ids": ["benchmark_reference"]}
                     for n, (i, m) in enumerate(concepts)],
        "scenes": [{"scene_id": s, "entry_question": "What problem makes this next step necessary?",
                    "builds_on": [scenes[i-1]] if i else [], "reasoning_steps": steps,
                    "listener_can_now": title} for i, s in enumerate(scenes)],
        "worked_example": {"scene_ids": scenes, "setup": setup, "reasoning_steps": steps,
                           "misconception": "Naming a mechanism explains how it works.", "correction": "Develop the intermediate reasoning.",
                           "limits": "A constructed example does not establish all real-world behavior."},
        "synthesis": {"premise_concept_ids": [i for i, _ in concepts], "reasoning_steps": steps,
                      "conclusion": conclusion, "transfer_question": goals[-1][2]}, "research_gaps": []})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path, help="Read runtime settings only")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--include-pilot", action="store_true", help="Compatibility option; the archived pilot is included by default")
    parser.add_argument("--case", choices=["seasons_developed", "seasons_glossary", "pilot_rejected"])
    args = parser.parse_args()
    args.project = args.project.resolve()
    args.output = args.output.resolve()
    config = load_project(args.project)
    good = parse_script((REPO / "evals/teaching_quality/seasons.md").read_text(encoding="utf-8"))
    glossary = parse_script("# Jahreszeiten\n\n**Aiden:** Jahreszeiten sind periodische Veränderungen. Die Erdachse ist geneigt. "
                           "Wichtige Begriffe sind Einstrahlung, Einfallswinkel und Tageslänge.\n\n"
                           "**Vivian:** Die Halbkugeln haben entgegengesetzte Jahreszeiten. Neigung ist entscheidend. "
                           "Diese Aspekte hängen zusammen. Damit haben wir die Jahreszeiten erklärt.")
    cases = [("seasons_developed", good, design_for(good), True),
             ("seasons_glossary", glossary, design_for(glossary), False)]
    archived = json.loads((REPO / "evals/teaching_quality/pilot_rejected.json").read_text(encoding="utf-8"))
    pilot = EpisodeScript.model_validate(archived["script"])
    cases.append(("pilot_rejected", pilot, design_for(pilot, pilot=True), False))
    if args.case:
        cases = [case for case in cases if case[0] == args.case]
        if not cases:
            parser.error("The requested case is not selected.")
    adapter = CodexAdapter(config.runtime)
    report_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    rows = []
    for name, script, design, expected in cases:
        brief = archived["brief"] if name == "pilot_rejected" else {**archived["brief"],
            "depth_request": "A short complete adult introductory lesson: causal explanation, worked example, objection and transfer; not a full university course."}
        case_dir = args.output / name
        input_id = digest({"script": script.model_dump(), "design": design.model_dump(),
                           "brief": brief, "version": TEACHING_VERSION})
        write_json(case_dir / "inputs" / f"{input_id}.json", {"script": script.model_dump(), "design": design.model_dump(),
                   "expected_pass": expected, "brief": brief, "script_digest": digest(script.model_dump()),
                   "label_origin": "User-rejected pilot" if name == "pilot_rejected" else "Authored control; not human learning validation"})
        row = {"case": name, "expected_pass": expected, "script_digest": digest(script.model_dump())}
        if args.live:
            def invoke(prompt, output_type, version):
                number = reserve_call(args.output, config.research_limits)
                return adapter.structured(prompt, output_type, args.output / "calls" / f"call_{number:03d}",
                                          prompt_version=version, search=False)[0]
            issues, report, _ = assess_teaching(script, design, invoke, case_dir / "checks",
                audience=brief["audience_level"], prior_knowledge=brief["prior_knowledge"], depth=brief["depth_request"])
            row.update({"actual_pass": not issues, "matches_expectation": (not issues) == expected,
                        "failed_criteria": sorted({c["criterion"] for c in
                            [*report["review"]["checks"], *report["editorial"]["checks"]] if c["verdict"] == "fail"}),
                        "issues": [i.model_dump() for i in issues]})
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        result = {"live": args.live, "teaching_version": TEACHING_VERSION, "report_id": report_id, "cases": rows,
                   "all_match": all(r.get("matches_expectation", False) for r in rows) if args.live else None,
                   "human_learning_validated": False}
        write_json(args.output / "results.json", result)
        write_json(args.output / "history" / f"{report_id}.json", result)
    return 0 if not args.live or all(r["matches_expectation"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
