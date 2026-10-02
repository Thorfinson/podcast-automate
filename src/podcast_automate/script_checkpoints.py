"""Which per-episode results of a script run are already complete on disk.

Shared by the Studio progress view and the budget projection, without any model calls.
"""
from __future__ import annotations

from .storage import digest, file_hash
from .storage import read_optional_json as read

# Script review points that no longer stop a run once its repairs are spent (script_pipeline.review_episode).
NOTED_CATEGORIES = {"clarity", "depth", "dialogue"}


def series_adoption(work, episode, draft):
    """The series review's adopted correction of ``draft`` (the episode review's final script, as a dict), or None.

    ``reviews/<ep>_series_adopted.json`` names the draft it corrected by digest. A resume of the review stage
    publishes the correction instead of the episode review's own draft, so an adopted correction is never
    silently reverted (2026-09-29: resuming after a series stop rewrote ``reviewed/`` from the checkpoints and
    the series review corrected the same episodes again). A new episode review of other text ignores it."""
    adopted = read(work / "reviews" / f"{episode}_series_adopted.json")
    if adopted and draft is not None and adopted.get("base") == digest(draft):
        return adopted
    return None


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
        result = read(folder / "result.json", {}) or {}
        script = read(folder / "script.json")
        if result.get("status") == "kept_draft":
            # The polish kept the checked draft (polishing.polish_dialogue): finished when its script is that draft.
            # Counted as open, it added two polishing calls per such episode to the budget projection and left the
            # Studio's polishing row unfinished (finding of 2026-10-02).
            return bool(script and script == read(work / "drafts" / f"{episode}.json")
                        and digest(script) == result.get("original_digest") == result.get("polished_digest"))
        checkpoint = read(folder / "checkpoint.json", {}) or {}
        return bool(result.get("status") == "passed" and script and script == checkpoint.get("candidate"))
    if stage == "review":
        report = read(work / "reviews" / f"{episode}.json")
        checked = read(work / "reviewed" / f"{episode}.json")
        checkpoint = read(work / "reviews" / f"{episode}_checkpoint.json", {})
        # Points that are only noted leave the episode finished: its script stands and they are reported. Counting such
        # an episode as open froze the Studio's count and inflated the budget projection (2026-09-29: Asimov showed
        # 5 of 14 with 12 done, Ontologies 2 of 10 with all done and "at least 32 more review calls").
        blocking = [issue for issue in (report or {}).get("issues", []) if issue.get("category") not in NOTED_CATEGORIES]
        adopted = series_adoption(work, episode, checkpoint.get("draft"))
        final = adopted["draft"] if adopted else checkpoint.get("draft")
        return bool(checked and report is not None and not blocking and final == checked)
    return False
