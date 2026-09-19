"""Explicit, run-bound allowances and gap approvals without changing approved content inputs.

Three receipts live next to a run and are written only by an explicit user action:

- ``budget_approval.json`` raises the model-call limit and, optionally, the search-round limit.
- ``gap_approvals.json`` lists blocked research tasks the user accepts as documented gaps, so the
  dossier can be finished and published without them.

Both bind to the run id and its input hash; a copy cannot serve another run or changed inputs.
"""
from __future__ import annotations

import json
from datetime import datetime

from pydantic import Field

from .errors import AppError
from .models import Contract, Identifier, RunManifest, now
from .runner import manifest_path
from .storage import load_project, read_yaml, write_json


class BudgetApproval(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_calls: int = Field(strict=True, gt=0)
    search_rounds: int | None = Field(default=None, strict=True, gt=0)
    approved_at: datetime


class GapApproval(Contract):
    task_id: Identifier
    reason: str = ""
    approved_at: datetime


class GapApprovals(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    gaps: list[GapApproval]


def read_budget_approval(work) -> BudgetApproval | None:
    path = work / "budget_approval.json"
    if not path.exists():
        return None
    try:
        return BudgetApproval.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("Die gespeicherte Erhöhung des Aufruflimits ist ungültig.",
                       code="invalid_budget_approval", status="blocked") from exc


def effective_limits(work, limits, input_hash):
    approval = read_budget_approval(work)
    if approval is None:
        return limits
    if (approval.run_id != work.name or approval.input_hash != input_hash or
        approval.model_calls < limits.model_calls or
            (approval.search_rounds is not None and approval.search_rounds < limits.search_rounds)):
        raise AppError("Die Erhöhung des Aufruflimits gehört nicht zu diesem Auftrag.",
                       code="invalid_budget_approval", status="blocked")
    update = {"model_calls": approval.model_calls}
    if approval.search_rounds is not None:
        update["search_rounds"] = approval.search_rounds
    return limits.model_copy(update=update)


def _text_run(root, run_id):
    work = manifest_path(root.resolve(), run_id).parent
    manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
    if manifest.kind not in {"script", "research"}:
        raise AppError("Diese Freigabe gilt nur für einen Recherche- oder Skriptauftrag.", code="invalid_budget_approval")
    return work, manifest


def approve_model_call_limit(root, run_id, model_calls=None, *, search_rounds=None):
    """Call only after the user explicitly approves these limits for this text run.

    The separate atomic receipt can be written while the worker is busy. Its counters,
    checkpoints, project configuration and outline/audio approvals remain untouched. A previously
    raised search-round limit is kept when only the call limit is raised again; ``model_calls``
    may be omitted to raise only the search rounds.
    """
    work, manifest = _text_run(root, run_id)
    limits = effective_limits(work, load_project(root).research_limits, manifest.input_hash)
    if model_calls is None and search_rounds is not None:
        model_calls = limits.model_calls
    if type(model_calls) is not int or model_calls < limits.model_calls:
        raise AppError("Das neue Aufruflimit muss eine ganze Zahl mindestens in Höhe des bisherigen Limits sein.",
                       code="invalid_budget_approval")
    if search_rounds is not None and (type(search_rounds) is not int or search_rounds < limits.search_rounds):
        raise AppError("Das neue Suchrundenlimit muss eine ganze Zahl mindestens in Höhe des bisherigen Limits sein.",
                       code="invalid_budget_approval")
    previous = read_budget_approval(work)
    if search_rounds is None and previous is not None:
        search_rounds = previous.search_rounds
    approval = BudgetApproval(run_id=manifest.run_id, input_hash=manifest.input_hash,
                              model_calls=model_calls, search_rounds=search_rounds, approved_at=now())
    write_json(work / "budget_approval.json", approval.model_dump(mode="json"))
    return approval


def accepted_gaps(work, input_hash) -> dict[str, dict]:
    """Accepted gaps of this run keyed by task id; empty when no approval was ever written."""
    path = work / "gap_approvals.json"
    if not path.exists():
        return {}
    try:
        approvals = GapApprovals.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("Die gespeicherten Lückenfreigaben sind ungültig.", code="invalid_gap_approval", status="blocked") from exc
    if approvals.run_id != work.name or approvals.input_hash != input_hash:
        raise AppError("Die Lückenfreigaben gehören nicht zu diesem Auftrag.", code="invalid_gap_approval", status="blocked")
    return {gap.task_id: gap.model_dump(mode="json") for gap in approvals.gaps}


def approve_research_gap(root, run_id, task_id, reason=""):
    """Accept one blocked research task as a documented gap after the user explicitly asked for it.

    Only a task the workflow has actually blocked can be accepted; pending or verified tasks are
    never skipped this way. The next resume finishes the dossier without that task, lists the gap in
    the quality report and marks the publication as incomplete for the agreed brief.
    """
    from .research_ledger import read_value
    work, manifest = _text_run(root, run_id)
    if manifest.kind != "research":
        raise AppError("Lücken können nur in einem Rechercheauftrag akzeptiert werden.", code="invalid_gap_approval")
    state_path = work / "question_research/state.json"
    if not state_path.exists():
        raise AppError("Für diesen Lauf gibt es noch keine Recherchefragen.", code="invalid_gap_approval")
    row = read_value(state_path).get("tasks", {}).get(task_id)
    if row is None:
        raise AppError("Unbekannte Recherchefrage.", code="invalid_gap_approval")
    if row.get("status") != "blocked":
        raise AppError("Nur eine blockierte Teilfrage kann als Lücke akzeptiert werden.", code="invalid_gap_approval")
    if not isinstance(reason, str) or len(reason) > 2000:
        raise AppError("Die Begründung muss ein kurzer Text sein.", code="invalid_gap_approval")
    existing = accepted_gaps(work, manifest.input_hash)
    if task_id in existing:
        return GapApproval.model_validate(existing[task_id])
    approval = GapApproval(task_id=task_id, reason=reason.strip(), approved_at=now())
    approvals = GapApprovals(run_id=manifest.run_id, input_hash=manifest.input_hash,
                             gaps=[*(GapApproval.model_validate(g) for g in existing.values()), approval])
    write_json(work / "gap_approvals.json", approvals.model_dump(mode="json"))
    return approval
