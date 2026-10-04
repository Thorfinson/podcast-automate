"""Who settles a review point, and the record of each decision (docs/specs/2026-10-04-review-loop-decision-gates-plan.md).

A0 are the deterministic checks, absolute in every stage. G is the materiality gate: today the rule G-cap, under which a
dismissable point keeps its loop going for one repair round and is a note after that (operator decision 1). A1 and A2
are the reviewers (``text_settings.STAGE_AUTHORITY``), A3 the bounded last attempt where a loop with spent repairs
stops the run today (``text_settings.A3_TAG``). Which points are dismissable stays with each stage:
``polishing.DISMISSABLE`` and ``script_pipeline.dismissable``.

Each decided round appends its points to ``issues.jsonl`` in the stage's work folder, for the counts of the plan's §16;
no decision reads it back. The loop state that decides stays in each stage's checkpoint.
"""
from __future__ import annotations

import json

from .storage import atomic_text, digest

# Repair rounds a dismissable point keeps its loop going for. Until 2026-10-04 the same rule held only at the stage's
# whole bound (2 repairs in polishing, 3 in the script review).
GATE_ROUNDS = 1
# ``decided_by`` of a record: the gate turned the point into a note; the follow-up scope rule did (a point on text the
# last round passed unchanged, plan §8); the reviewer's point blocks as raised.
GATE_CAP = {"role": "G", "gate": "cap"}
SCOPE_RULE = {"role": "A0", "rule": "scope"}


def gate_holds(repairs):
    """Whether a dismissable point still blocks after ``repairs`` repairs of its loop."""
    return repairs < GATE_ROUNDS


def a3_unavailable(exc):
    """Whether an A3 call failed because no subscription of its rung is usable at all (under ``auto``: Codex missing or
    logged out). The stage then stops with today's code and a resume tries A3 again (plan §9.2, step 6). An A3 call
    without quota pauses the run like any other call, and the automatic resume at the reset tries it again."""
    return exc.code == "subscription_required"


def issue_id(stage, episode, category, segment_ids):
    """The identity of a point: stage, episode, category and its sorted segments; a whole-episode point has none.
    Wording does not count, because reviewers rephrase each round. Two points of one category on the same segments
    are one point, as the scope rules already treat them alike."""
    return digest({"stage": stage, "episode": episode, "category": category,
                   "segments": sorted(set(segment_ids)) or ["episode"]})


def record_round(path, *, stage, episode, round_, points):
    """Append one record per point of a decided round. ``points`` are dicts with ``category``, ``segment_ids``,
    ``dismissable``, ``status`` (``blocking`` or ``note``) and ``decided_by``. A record already in the file, written
    when a resume decides the same round again, is not added twice, so the file only grows."""
    known = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    lines = list(known)
    for point in points:
        segments = sorted(set(point["segment_ids"]))
        line = json.dumps({"issue_id": issue_id(stage, episode, point["category"], segments), "stage": stage,
                           "episode": episode, "round": round_, "category": point["category"], "segment_ids": segments,
                           "dismissable": point["dismissable"], "status": point["status"],
                           "decided_by": point["decided_by"]}, ensure_ascii=False, sort_keys=True)
        if line not in lines:
            lines.append(line)
    if len(lines) != len(known):
        atomic_text(path, "".join(line + "\n" for line in lines))
