"""Count the advisory patterns of ``script_advisories`` over a project's published episodes.

Offline and model-free. It reads ``episodes/*/script.yaml``, ``episodes/*/episode_plan.yaml`` and
``episodes/*/teaching_plan.yaml`` of the latest published run, so the numbers can be compared
before and after a change without regenerating anything.

    python scripts/audit-counts.py projects/<project-id> [--json] [--terms Token,Softmax]

Established terms come from the earlier episodes' teaching plans. Plans written before
``Concept.terms`` existed carry only opaque concept IDs, so ``--terms`` supplies the spoken
words by hand for such a series; a regenerated series needs no flag.
"""
import argparse
import json
from pathlib import Path

import yaml

from podcast_automate.models import EpisodeScript, TopicBrief
from podcast_automate.script_advisories import (COLD_OPEN_WORDS, advisories, definition_sentences,
                                                hedging_hits, humanised, words)
from podcast_automate.script_artifacts import script_metrics
from podcast_automate.script_models import EpisodePlan


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def episodes(root):
    for folder in sorted((root / "episodes").iterdir()):
        if (folder / "script.yaml").exists() and (folder / "episode_plan.yaml").exists():
            yield folder


def terms_before(folders, upto):
    """Terms every earlier episode's teaching plan establishes, in publication order."""
    found = []
    for folder in folders:
        if folder == upto:
            break
        plan_file = folder / "teaching_plan.yaml"
        if not plan_file.exists():
            continue
        for concept in read_yaml(plan_file).get("concepts", []):
            found.extend(concept.get("terms") or [humanised(concept["concept_id"])])
    return list(dict.fromkeys(term for term in (t.strip() for t in found) if term))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--json", action="store_true", help="print the advisory rows instead of a summary")
    parser.add_argument("--terms", default="", help="comma-separated spoken terms to treat as established, "
                        "for series whose teaching plans predate Concept.terms")
    arguments = parser.parse_args()
    root = arguments.project
    config = TopicBrief.model_validate(read_yaml(root / "project.yaml"))
    folders = list(episodes(root))
    if not folders:
        raise SystemExit(f"No published episodes under {root / 'episodes'}")
    extra = [t.strip() for t in arguments.terms.split(",") if t.strip()]
    rows, summary = [], []
    for folder in folders:
        script = EpisodeScript.model_validate(read_yaml(folder / "script.yaml"))
        entry = EpisodePlan.model_validate(read_yaml(folder / "episode_plan.yaml"))
        metrics = script_metrics(script)
        # The supplied terms stand in for what the earlier episodes established, so the
        # first episode of the series is never charged with defining them.
        known = list(dict.fromkeys(([] if folder == folders[0] else extra) + terms_before(folders, folder)))
        found = advisories(script, entry, metrics, language=config.language, terms=known)
        rows.extend(found)
        defined = definition_sentences(script, known, config.language)
        summary.append({"episode_id": script.episode_id,
                        "terms_defined": len(defined),
                        "terms_defined_twice": sum(1 for hits in defined.values() if len(hits) > 1),
                        # The raw match count, also below the advisory's limit of two.
                        "hedging_matches": len(hedging_hits(script, config.language)),
                        "first_segment_words": words(script.segments[0].text),
                        "estimated_minutes": metrics["estimated_minutes"],
                        "target_minutes": entry.target_minutes,
                        "advisories": [r["code"] for r in found]})
    if arguments.json:
        print(json.dumps({"project": str(root), "episodes": summary, "advisories": rows},
                         ensure_ascii=False, indent=2))
        return
    print(f"{root}  ({len(folders)} veröffentlichte Folgen)\n")
    header = (f"{'Folge':<10}{'def.':>7}{'2x def.':>9}{'Hedging':>9}{'1. Abs.':>9}"
              f"{'Min.':>7}{'Ziel':>7}  Hinweise")
    print(header)
    print("-" * len(header))
    for row in summary:
        print(f"{row['episode_id']:<10}{row['terms_defined']:>7}{row['terms_defined_twice']:>9}"
              f"{row['hedging_matches']:>9}{row['first_segment_words']:>9}"
              f"{row['estimated_minutes']:>7.1f}{row['target_minutes']:>7.0f}  " + ", ".join(row["advisories"]))
    redefining = sum(1 for row in summary[1:] if row["terms_defined"])
    long_opens = sum(1 for row in summary if row["first_segment_words"] > COLD_OPEN_WORDS)
    print(f"\nFolgen nach der ersten, die einen bekannten Begriff erneut definieren: "
          f"{redefining} von {len(summary) - 1}")
    print(f"Folgen mit langem Kaltstart (> {COLD_OPEN_WORDS} Wörter): {long_opens} von {len(summary)}")


if __name__ == "__main__":
    main()
