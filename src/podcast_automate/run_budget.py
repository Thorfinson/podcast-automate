"""Explicit, run-bound call allowances without changing approved content inputs."""
from __future__ import annotations

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
    approved_at: datetime


def effective_limits(work, limits, input_hash):
    path = work / "budget_approval.json"
    if not path.exists():
        return limits
    try:
        approval = BudgetApproval.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("Die gespeicherte Erhöhung des Aufruflimits ist ungültig.",
                       code="invalid_budget_approval", status="blocked") from exc
    if (approval.run_id != work.name or approval.input_hash != input_hash or
        approval.model_calls < limits.model_calls):
        raise AppError("Die Erhöhung des Aufruflimits gehört nicht zu diesem Auftrag.",
                       code="invalid_budget_approval", status="blocked")
    return limits.model_copy(update={"model_calls": approval.model_calls})


def approve_model_call_limit(root, run_id, model_calls):
    """Call only after the user explicitly approves this limit for this script run.

    The separate atomic receipt can be written while the worker is busy. Its counters,
    checkpoints, project configuration and outline/audio approvals remain untouched.
    """
    work = manifest_path(root.resolve(), run_id).parent
    manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
    if manifest.kind != "script":
        raise AppError("Diese Erhöhung gilt nur für einen Skriptauftrag.", code="invalid_budget_approval")
    limits = effective_limits(work, load_project(root).research_limits, manifest.input_hash)
    if type(model_calls) is not int or model_calls < limits.model_calls:
        raise AppError("Das neue Aufruflimit muss eine ganze Zahl mindestens in Höhe des bisherigen Limits sein.",
                       code="invalid_budget_approval")
    approval = BudgetApproval(run_id=manifest.run_id, input_hash=manifest.input_hash,
                              model_calls=model_calls, approved_at=now())
    write_json(work / "budget_approval.json", approval.model_dump(mode="json"))
    return approval
