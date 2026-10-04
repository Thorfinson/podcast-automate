"""Where a podcast's model calls go: step 0 (V-10) of the review-loop plan, docs/specs/2026-10-04-review-loop-decision-gates-plan.md.

For every research and script run under the given workspaces (their runs/run_*) or run folders, prints the calls by
stage and role (podcast_automate.production_report), the repair calls, and for each review stage how many review
rounds raised a point of each category, read from the saved answers (calls/*/response.json). Runs from before
2026-10-04 record no role. Nothing is written.

    python scripts/call-baseline.py <workspace or run folder> [...] [--json]
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from podcast_automate.production_report import production_report
from podcast_automate.storage import read_optional_json as read


def review_points(answer, family):
    """The categories one review answer raised, each once per round."""
    if family == "script_review":
        return {issue["category"] for issue in [*answer.get("issues", []), *answer.get("advisories", [])]}
    if family in {"dialogue_polish_review", "editorial_review", "teaching_review"}:
        return {row.get("criterion") or row.get("objective_id")
                for row in [*answer.get("checks", []), *answer.get("objectives", [])] if row.get("verdict") == "fail"}
    if family == "teaching_design_review":
        return {"design"} if answer.get("issues") else set()
    return set()


def baseline(work):
    rounds = defaultdict(Counter)
    for folder in sorted((work / "calls").glob("call_*")):
        family = ((read(folder / "provider_choice.json", {}) or {}).get("prompt_version") or "").split(".")[0]
        answer = read(folder / "response.json")
        if family.endswith("_review") and isinstance(answer, dict):
            rounds[family]["(rounds)"] += 1
            rounds[family].update(review_points(answer, family))
    report = production_report(work)
    return {"run_id": work.name, "calls": report["calls"],
            "stages": {row["stage"]: {"calls": row["calls"], "roles": row["roles"]} for row in report["stages"]},
            "repair_calls": sum(row["calls"] for row in report["stages"] if "repair" in row["stage"]),
            "review_rounds_by_category": {family: dict(counts) for family, counts in rounds.items()},
            "stops": report["stops"]}


def run_folders(paths):
    for path in map(Path, paths):
        yield from ([path] if (path / "calls").is_dir() else sorted((path / "runs").glob("run_*")))


def main(argv):
    as_json = "--json" in argv
    rows = [baseline(work) for work in run_folders(arg for arg in argv if arg != "--json") if (work / "calls").is_dir()]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        print(f"{row['run_id']}: {row['calls']} calls, {row['repair_calls']} repairs, stops {row['stops']['by_stage']}")
        for stage, counts in sorted(row["stages"].items(), key=lambda item: -item[1]["calls"]):
            print(f"  {stage:34} {counts['calls']:5}  {counts['roles']}")
        for family, counts in row["review_rounds_by_category"].items():
            print(f"  rounds of {family}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
