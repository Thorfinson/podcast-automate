"""Synthetic series verdicts test control flow, not editorial quality."""
import json

from podcast_automate.series_review import CRITERIA, SeriesReview


def series_response(prompt):
    scripts = json.loads(prompt.splitlines()[-1])["scripts"]
    evidence = [{"episode_id": script["episode_id"], "segment_id": script["segments"][-1]["segment_id"],
                 "quote": script["segments"][-1]["text"]} for script in scripts]
    return SeriesReview(checked_episodes=[s["episode_id"] for s in scripts],
        checks=[{"criterion": name, "verdict": "pass", "reason": "Synthetic fixture only.", "evidence": evidence}
                for name in CRITERIA], warnings=["No human or listening evaluation."])
