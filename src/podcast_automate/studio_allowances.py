"""Allowances the user sets ahead of time, so a run that stops on a routine decision continues by itself.

Two decisions qualify, both the user's own and both bounded per run: fresh correction attempts after a stage
spent its automatic ones, and a raise of the call limit by up to a number of extra calls. Editorial decisions
(accepting a gap, a disputed objection, a residual finding, a new teaching design) never follow from an
allowance; they keep waiting for the user. The Studio's scheduler applies an allowance with the same approval
the stop card's button writes, records the use in ``studio/allowance_log.json`` and resumes the run
(2026-09-29: the two script runs stopped 34 times in all, 9 of them for fresh attempts the user then granted).
"""
from __future__ import annotations

import math

from .errors import AppError
from .models import now
from .storage import load_project, write_json
from .storage import read_optional_json as read

FRESH_ATTEMPT_CHOICES = (0, 1, 2, 3)
EXTRA_CALL_CHOICES = (0, 100, 250, 500, 1000)
# Stops whose card offers "Mit neuen Anläufen fortsetzen" (web/app.js STOP_RULES).
SCRIPT_FRESH_CODES = {"script_review_failed", "teaching_research_required", "series_review_failed"}
RESEARCH_FRESH_CODES = {
    "rejected_output", "invalid_evidence_review", "invalid_question_routing", "invalid_research_patch",
    "invalid_research_assessment", "invalid_search_receipt", "invalid_question_review", "invalid_evidence",
    "invalid_question_plan", "invalid_question_scope", "question_scope_unresolved", "invalid_supplement"}
BUDGET_CODES = {"script_budget_insufficient", "research_budget_insufficient", "research_budget_exhausted"}


def allowances(root):
    """The project's allowances; nothing is allowed until the user sets it."""
    data = read(root / "studio/allowances.json", {}) or {}
    fresh, extra = data.get("fresh_attempts"), data.get("extra_calls")
    return {"fresh_attempts": fresh if fresh in FRESH_ATTEMPT_CHOICES else 0,
            "extra_calls": extra if extra in EXTRA_CALL_CHOICES else 0}


def set_allowances(root, data):
    fresh, extra = data.get("fresh_attempts"), data.get("extra_calls")
    if type(fresh) is not int or fresh not in FRESH_ATTEMPT_CHOICES:
        raise AppError("Neue Anläufe ohne Rückfrage: 0 bis 3 je Lauf.", code="invalid_allowance")
    if type(extra) is not int or extra not in EXTRA_CALL_CHOICES:
        raise AppError("Aufruflimit ohne Rückfrage: " + ", ".join(map(str, EXTRA_CALL_CHOICES)) + " zusätzliche Aufrufe.",
                       code="invalid_allowance")
    write_json(root / "studio/allowances.json", {"fresh_attempts": fresh, "extra_calls": extra, "changed_at": now()})
    return allowances(root)


def allowance_log(root):
    rows = read(root / "studio/allowance_log.json", []) or []
    return rows if isinstance(rows, list) else []


def used(root, run_id):
    """What the allowances already gave this run."""
    rows = [row for row in allowance_log(root) if row.get("run_id") == run_id and not row.get("skipped")]
    return {"fresh_attempts": sum(1 for row in rows if row.get("kind") == "fresh_attempts"),
            "extra_calls": sum(row.get("extra_calls", 0) for row in rows if row.get("kind") == "model_calls")}


def suggested_calls(job):
    """The call limit a stop card suggests (web/app.js suggestedCalls): the projection's need, with a quarter more
    for a script run's corrections, else fifty more."""
    progress = (job or {}).get("progress") or {}
    ledger = (progress.get("research_questions") or {}).get("budget_projection")
    script = progress.get("budget_projection")
    limit, spent = int(progress.get("model_call_limit") or 0), int(progress.get("model_calls") or 0)
    if isinstance(ledger, dict) and isinstance(ledger.get("used"), (int, float)):
        need = max(int(ledger.get("expected_remaining_calls") or 0), int(ledger.get("minimum_remaining_calls") or 0))
        return max(limit + 1, int(ledger["used"]) + need)
    if isinstance(script, dict) and isinstance(script.get("minimum_remaining_calls"), (int, float)):
        return max(limit + 1, int(script.get("used", spent)) + math.ceil(int(script["minimum_remaining_calls"]) * 5 / 4))
    return max(limit, spent) + 50


def current_limit(root, run_id):
    from .run_budget import _text_run, effective_limits
    work, manifest = _text_run(root, run_id)
    return effective_limits(work, load_project(root).research_limits, manifest.input_hash).model_calls


def pending(root, job, code):
    """The allowance the scheduler will apply to this stopped job, as ``{"kind", ...}``, or None."""
    run = (job or {}).get("run") or {}
    run_id, kind = run.get("run_id"), run.get("kind")
    if (job or {}).get("status") != "blocked" or not run_id or kind not in {"script", "research"}:
        return None
    if any(row.get("job_id") == job.get("id") for row in allowance_log(root)):
        return None  # applied once already, or found not to apply
    allowed, spent = allowances(root), used(root, run_id)
    if code in BUDGET_CODES:
        progress = job.get("progress") or {}
        if code == "research_budget_exhausted" and int(progress.get("model_calls") or 0) < int(progress.get("model_call_limit") or 0):
            return None  # the search rounds ran out, not the calls; that stays the user's decision
        left = allowed["extra_calls"] - spent["extra_calls"]
        if left <= 0:
            return None
        try:
            limit = current_limit(root, run_id)
        except (AppError, OSError, ValueError):
            return None
        target = min(max(suggested_calls(job), limit + 1), limit + left)
        return {"kind": "model_calls", "model_calls": target, "extra_calls": target - limit} if target > limit else None
    fresh_codes = SCRIPT_FRESH_CODES if kind == "script" else RESEARCH_FRESH_CODES
    if code in fresh_codes and spent["fresh_attempts"] < allowed["fresh_attempts"]:
        return {"kind": "fresh_attempts", "number": spent["fresh_attempts"] + 1, "of": allowed["fresh_attempts"]}
    return None


def apply(root, job, code):
    """Write the approval an allowance gives this stopped job and record it. True when the run may resume."""
    from .run_budget import approve_fresh_attempts, approve_model_call_limit
    action = pending(root, job, code)
    if action is None:
        return False
    run_id = job["run"]["run_id"]
    row = {"run_id": run_id, "job_id": job.get("id"), "code": code, "kind": action["kind"], "applied_at": now()}
    try:
        if action["kind"] == "model_calls":
            approve_model_call_limit(root, run_id, action["model_calls"])
            row.update(model_calls=action["model_calls"], extra_calls=action["extra_calls"])
        else:
            row["record"] = approve_fresh_attempts(root, run_id)
    except AppError as exc:
        # Nothing to grant here (for instance no step spent its attempts): the stop stays the user's.
        row.update(skipped=True, reason=str(exc)[:300])
    write_json(root / "studio/allowance_log.json", [*allowance_log(root), row])
    return not row.get("skipped")


def summary(root, run_id):
    """The allowances and what they gave this run, for the job page."""
    return {**allowances(root), "used": used(root, run_id) if run_id else {"fresh_attempts": 0, "extra_calls": 0}}
