"""Which per-episode results of a script run are already complete on disk.

Shared by the Studio progress view and the budget projection, without any model calls.
"""
from __future__ import annotations

from .storage import file_hash
from .storage import read_optional_json as read


def teaching_ready(folder):
    checkpoint = read(folder / "checkpoint.json", {})
    review = read(folder / "review.json")
    plan = read(folder / "plan.json")
    return bool(plan and review is not None and not review.get("issues") and not review.get("research_gaps")
                and checkpoint.get("design") == plan and checkpoint.get("review") == review)


def finished(work, episode, stage):
    if stage == "teaching":
        return teaching_ready(work / "teaching" / episode)
    if stage == "writing":
        path = work / "drafts" / f"{episode}.json"
        stamp = read(path.with_suffix(".checkpoint.json"), {})
        return bool(path.is_file() and stamp.get("sha256") == file_hash(path))
    if stage == "polishing":
        folder = work / "polishing" / episode
        result = read(folder / "result.json", {})
        checkpoint = read(folder / "checkpoint.json", {})
        return bool(result.get("status") == "passed" and read(folder / "script.json")
                    and read(folder / "script.json") == checkpoint.get("candidate"))
    if stage == "review":
        report = read(work / "reviews" / f"{episode}.json")
        checked = read(work / "reviewed" / f"{episode}.json")
        checkpoint = read(work / "reviews" / f"{episode}_checkpoint.json", {})
        return bool(checked and report is not None and not report.get("issues") and checkpoint.get("draft") == checked)
    return False
