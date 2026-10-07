"""What a billed run costs per model call, and the money view of its budget (D-146).

A run billed to the user's key (``claude_api``, OpenRouter) or one whose web search is billed needs a money limit.
Before and during such a run the Studio shows what it spent and what the rest is expected to cost: expected calls
(question_budget, script_budget) times the money one call costs. That figure comes from, in this order, the run's own
billed calls once there are enough of them, the project's last completed run of the same kind with the same model
(subscription runs count too: Claude Code reports the same calls' value at API prices), and the table below.
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from .storage import read_text

# Mean Claude Code ``total_cost_usd`` per call (API prices) over the completed runs created 2026-09-26 to 2026-10-04:
# research 3,042 Sonnet 5.5 and 1,439 Opus 5.5 calls, script 1,071 and 735 (medians 0.30, 0.60, 0.85 and 1.18).
COST_TABLE_MEASURED_ON = date(2026, 10, 7)
DEFAULT_USD_PER_CALL = {
    ("research", "claude-sonnet-5-5"): 0.37, ("research", "claude-opus-5-5"): 0.75,
    ("script", "claude-sonnet-5-5"): 0.83, ("script", "claude-opus-5-5"): 1.17,
}
# OpenRouter names the same models in its own namespace.
MODEL_ALIASES = {"anthropic/claude-sonnet-5.5": "claude-sonnet-5-5", "anthropic/claude-opus-5.5": "claude-opus-5-5"}
# A run's own mean decides once this many of its calls were priced.
MIN_PRICED_CALLS = 10


def canonical(model):
    return MODEL_ALIASES.get(model, model)


def table_usd(kind, model):
    """The measured money per call of a run kind and model, or None for a model the table does not know."""
    return DEFAULT_USD_PER_CALL.get((kind, canonical(model)))


def fallback_usd(model):
    """The money an attempt without a reported cost is counted at when its run has no priced call yet: the higher
    measured value of the model, or None."""
    values = [usd for (_, name), usd in DEFAULT_USD_PER_CALL.items() if name == canonical(model)]
    return max(values) if values else None


def call_usd(metadata):
    """The money a call's receipt names: the billed amount, else Claude Code's value of a subscription call."""
    for key in ("separately_billed_cost", "reported_cost_usd"):
        value = metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return float(value)
    return None


@lru_cache(maxsize=64)
def _run_mean(work: str, model: str):
    """Mean money per answered call of one finished run with this model; read once per process, since a completed
    run's calls no longer change."""
    total, count = 0.0, 0
    for path in Path(work).glob("calls/call_*/metadata.json"):
        try:
            metadata = json.loads(read_text(path))
        except (OSError, ValueError):
            continue
        if canonical(metadata.get("requested_model")) != model:
            continue
        usd = call_usd(metadata)
        if usd is not None:
            total, count = total + usd, count + 1
    return (total / count, count) if count >= MIN_PRICED_CALLS else None


def project_mean(root: Path, kind, model, *, exclude=None):
    """The mean of the project's most recent completed run of this kind with this model, or None."""
    from .storage import read_yaml
    model = canonical(model)
    runs = sorted((root / "runs").glob("run_*"), reverse=True)
    for work in runs:
        if exclude is not None and work.name == exclude:
            continue
        try:
            manifest = read_yaml(work / "run_manifest.yaml") or {}
        except (OSError, ValueError):
            continue
        if manifest.get("kind") != kind or manifest.get("status") != "completed":
            continue
        found = _run_mean(str(work), model)
        if found is not None:
            return found[0]
    return None


def per_call_usd(root: Path, work: Path, kind, model, budget=None):
    """Money one more call of this run is expected to cost, and where the figure comes from:
    ``run`` (its own billed calls), ``project`` (the last completed run), ``table`` or ``unknown``."""
    budget = budget or {}
    priced = budget.get("priced_attempts", 0)
    if priced >= MIN_PRICED_CALLS:
        return budget.get("billed_usd", 0.0) / priced, "run"
    found = project_mean(root, kind, model, exclude=work.name)
    if found is not None:
        return found, "project"
    usd = table_usd(kind, model)
    return (usd, "table") if usd is not None else (None, "unknown")


def spent_usd(budget):
    """Money a run has spent: billed calls, the counted value of calls without a reported cost, and money spent
    outside its model calls (the Jev gap probe)."""
    return round(budget.get("billed_usd", 0.0) + budget.get("estimated_usd", 0.0) + budget.get("external_usd", 0.0), 4)


def cost_view(root: Path, work: Path, kind, model, limit, expected_calls, budget=None):
    """The money block of a billed run's projection; deterministic, so a resume rewrites it unchanged."""
    budget = budget or {}
    usd, source = per_call_usd(root, work, kind, model, budget)
    spent = spent_usd(budget)
    expected = round(usd * max(0, expected_calls), 2) if usd is not None else None
    return {"limit_usd": limit, "spent_usd": spent, "per_call_usd": round(usd, 4) if usd is not None else None,
            "per_call_source": source, "expected_remaining_usd": expected,
            "expected_total_usd": round(spent + expected, 2) if expected is not None else None,
            "unpriced_attempts": budget.get("unpriced_attempts", 0),
            "feasible": limit is None or expected is None or spent + expected <= limit,
            "table_measured_on": COST_TABLE_MEASURED_ON.isoformat()}


def money_view(root, work: Path, kind, limits, expected_calls):
    """The money block of a run's projection, or None for a run no key pays for (its projection stays as it was)."""
    from .run_budget import run_text_generation
    from .storage import read_optional_json
    from .text_settings import BILLED_TEXT_PROVIDERS
    try:
        selection = run_text_generation(work) or {}
    except (ValueError, OSError):
        # A run folder without a readable request or manifest has no billed selection to show.
        return None
    if selection.get("provider") not in BILLED_TEXT_PROVIDERS:
        return None
    budget = read_optional_json(work / "budget.json", {}) or {}
    return cost_view(Path(root) if root is not None else work.parent.parent, work, kind, selection.get("model"),
                     getattr(limits, "cost_usd", None), expected_calls, budget)

