"""Explicit, run-bound allowances and gap approvals without changing approved content inputs.

Four receipts live next to a run and are written only by an explicit user action:

- ``budget_approval.json`` raises the model-call limit and, optionally, the search-round and source limits.
- ``gap_approvals.json`` lists blocked research tasks the user accepts as documented gaps, so the
  dossier can be finished and published without them.
- ``retry_requests.json`` lists blocked research tasks the user wants attempted again; the next
  resume gives each a fresh recovery ladder and the allowance of a new question.
- ``plan_approval.json`` approves the projected research plan (``question_research/plan_projection.json``)
  before the first task call, optionally with a cap on the number of tasks. It binds to the plan
  hash as well, so a re-planned run needs a new approval.

All bind to the run id and its input hash; a copy cannot serve another run or changed inputs.
"""
from __future__ import annotations

import json
from datetime import datetime

from pydantic import Field

from .errors import AppError
from .models import Contract, Identifier, RunManifest, now
from .runner import manifest_path
from .storage import digest, load_project, read_yaml, write_json


class BudgetApproval(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_calls: int = Field(strict=True, gt=0)
    search_rounds: int | None = Field(default=None, strict=True, gt=0)
    # Sources a run may fetch: a web search that could load none ends a blocked question without searching.
    sources: int | None = Field(default=None, strict=True, gt=0)
    approved_at: datetime


class GapApproval(Contract):
    task_id: Identifier
    reason: str = ""
    approved_at: datetime


class GapApprovals(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    gaps: list[GapApproval]


class PlanApproval(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_tasks: int | None = Field(default=None, strict=True, ge=1)
    approved_at: datetime
    source: str = "explicit"


def read_plan_approval(work) -> PlanApproval | None:
    """The saved plan approval of a run folder; a changed or malformed receipt is refused, not ignored."""
    path = work / "plan_approval.json"
    if not path.exists():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict) or saved.get("sha256") != digest(saved.get("value")):
            raise ValueError("checksum")
        return PlanApproval.model_validate(saved["value"])
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise AppError("Die gespeicherte Freigabe des Rechercheplans ist ungültig.",
                       code="invalid_plan_approval", status="blocked") from exc


def plan_approval_for(work, input_hash, plan_hash) -> PlanApproval | None:
    """The valid approval of exactly this plan.

    A receipt of another run or of changed inputs is refused; one for an earlier plan of the same
    run is simply not an approval, so a re-planned run waits for a new decision.
    """
    approval = read_plan_approval(work)
    if approval is None:
        return None
    if approval.run_id != work.name or approval.input_hash != input_hash:
        raise AppError("Die Freigabe des Rechercheplans gehört nicht zu diesem Auftrag.",
                       code="invalid_plan_approval", status="blocked")
    return approval if approval.plan_hash == plan_hash else None


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
            (approval.search_rounds is not None and approval.search_rounds < limits.search_rounds) or
            (approval.sources is not None and approval.sources < limits.sources)):
        raise AppError("Die Erhöhung des Aufruflimits gehört nicht zu diesem Auftrag.",
                       code="invalid_budget_approval", status="blocked")
    update = {"model_calls": approval.model_calls}
    for key in ("search_rounds", "sources"):
        if getattr(approval, key) is not None:
            update[key] = getattr(approval, key)
    return limits.model_copy(update=update)


def _text_run(root, run_id):
    work = manifest_path(root.resolve(), run_id).parent
    manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
    if manifest.kind not in {"script", "research"}:
        raise AppError("Diese Freigabe gilt nur für einen Recherche- oder Skriptauftrag.", code="invalid_budget_approval")
    return work, manifest


def approve_model_call_limit(root, run_id, model_calls=None, *, search_rounds=None, sources=None):
    """Call only after the user explicitly approves these limits for this text run.

    The separate atomic receipt can be written while the worker is busy. Its counters,
    checkpoints, project configuration and outline/audio approvals remain untouched. A previously
    raised search-round or source limit is kept when another limit is raised; ``model_calls``
    may be omitted to raise only the search rounds or the sources.
    """
    work, manifest = _text_run(root, run_id)
    limits = effective_limits(work, load_project(root).research_limits, manifest.input_hash)
    if model_calls is None and (search_rounds is not None or sources is not None):
        model_calls = limits.model_calls
    if type(model_calls) is not int or model_calls < limits.model_calls:
        raise AppError("Das neue Aufruflimit muss eine ganze Zahl mindestens in Höhe des bisherigen Limits sein.",
                       code="invalid_budget_approval")
    if search_rounds is not None and (type(search_rounds) is not int or search_rounds < limits.search_rounds):
        raise AppError("Das neue Suchrundenlimit muss eine ganze Zahl mindestens in Höhe des bisherigen Limits sein.",
                       code="invalid_budget_approval")
    if sources is not None and (type(sources) is not int or sources < limits.sources):
        raise AppError("Das neue Quellenlimit muss eine ganze Zahl mindestens in Höhe des bisherigen Limits sein.",
                       code="invalid_budget_approval")
    previous = read_budget_approval(work)
    if search_rounds is None and previous is not None:
        search_rounds = previous.search_rounds
    if sources is None and previous is not None:
        sources = previous.sources
    approval = BudgetApproval(run_id=manifest.run_id, input_hash=manifest.input_hash, model_calls=model_calls,
                              search_rounds=search_rounds, sources=sources, approved_at=now())
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


class RetryRequest(Contract):
    task_id: Identifier
    hint: str = ""
    requested_at: datetime


class RetryRequests(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    retries: list[RetryRequest]


def retry_requests(work, input_hash) -> dict[str, dict]:
    """Requested new attempts of this run keyed by task id; empty when none was ever written."""
    path = work / "retry_requests.json"
    if not path.exists():
        return {}
    try:
        requests = RetryRequests.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("Die gespeicherten Wiederholungsanfragen sind ungültig.", code="invalid_retry_request", status="blocked") from exc
    if requests.run_id != work.name or requests.input_hash != input_hash:
        raise AppError("Die Wiederholungsanfragen gehören nicht zu diesem Auftrag.", code="invalid_retry_request", status="blocked")
    return {item.task_id: item.model_dump(mode="json") for item in requests.retries}


def approve_research_retry(root, run_id, task_id, hint=""):
    """Ask for a new attempt at one blocked research task after the user explicitly requested it.

    Only a blocked task that is not an accepted gap can be retried. The next resume gives it a fresh
    recovery ladder, the allowance of a new question on top of what it used (steps and web attempts)
    and the hint as feedback for the model. A later request for the same task replaces the earlier
    one, so a task that blocked again is retried again only by a new explicit decision.
    """
    from .research_ledger import read_value
    work, manifest = _text_run(root, run_id)
    if manifest.kind != "research":
        raise AppError("Ein neuer Versuch gilt nur für einen Rechercheauftrag.", code="invalid_retry_request")
    state_path = work / "question_research/state.json"
    if not state_path.exists():
        raise AppError("Für diesen Lauf gibt es noch keine Recherchefragen.", code="invalid_retry_request")
    row = read_value(state_path).get("tasks", {}).get(task_id)
    if row is None:
        raise AppError("Unbekannte Recherchefrage.", code="invalid_retry_request")
    if row.get("status") != "blocked" or row.get("accepted_gap") or task_id in accepted_gaps(work, manifest.input_hash):
        raise AppError("Nur eine blockierte Teilfrage, die keine akzeptierte Lücke ist, kann erneut versucht werden.",
                       code="invalid_retry_request")
    if row.get("outcome") == "prerequisite_block":
        # Its own attempt never failed; a new attempt at the prerequisite takes it up again on its own.
        raise AppError("Diese Teilfrage wartet auf eine vorausgesetzte Teilfrage. Versuche die Voraussetzung erneut "
                       "oder akzeptiere sie als Lücke.", code="invalid_retry_request")
    if not isinstance(hint, str) or len(hint) > 2000:
        raise AppError("Der Hinweis muss ein kurzer Text sein.", code="invalid_retry_request")
    existing = retry_requests(work, manifest.input_hash)
    request = RetryRequest(task_id=task_id, hint=hint.strip(), requested_at=now())
    existing[task_id] = request.model_dump(mode="json")
    requests = RetryRequests(run_id=manifest.run_id, input_hash=manifest.input_hash,
                             retries=[RetryRequest.model_validate(item) for item in existing.values()])
    write_json(work / "retry_requests.json", requests.model_dump(mode="json"))
    return request


def approve_research_plan(root, run_id, *, max_tasks=None, source="explicit"):
    """Approve the research plan the run currently holds, after the user read its projection.

    The receipt binds to the plan hash: it approves exactly the saved plan. ``max_tasks`` below the
    plan's task count asks the next resume to plan again under that cap and present the new plan
    for approval; it is only accepted while the run waits at the gate, because a running plan can no
    longer be cut. Nothing here spends a call or changes counters, checkpoints or the plan itself.
    """
    from .research_ledger import read_value
    work, manifest = _text_run(root, run_id)
    if manifest.kind != "research":
        raise AppError("Ein Rechercheplan gehört nur zu einem Rechercheauftrag.", code="invalid_plan_approval")
    state_path = work / "question_research/state.json"
    if not state_path.exists():
        raise AppError("Für diesen Lauf gibt es noch keinen Rechercheplan.", code="invalid_plan_approval")
    state = read_value(state_path)
    if max_tasks is not None and (type(max_tasks) is not int or max_tasks < 1):
        raise AppError("Die Obergrenze der Teilfragen muss eine ganze Zahl ab 1 sein.", code="invalid_plan_approval")
    if max_tasks is not None and state.get("phase") != "awaiting_plan_approval":
        raise AppError("Eine Obergrenze der Teilfragen gilt nur, solange der Rechercheplan auf Freigabe wartet.",
                       code="invalid_plan_approval")
    if not isinstance(source, str) or not source.strip() or len(source) > 200:
        raise AppError("Ungültige Herkunft der Planfreigabe.", code="invalid_plan_approval")
    approval = PlanApproval(run_id=manifest.run_id, input_hash=manifest.input_hash, plan_hash=digest(state["plan"]),
                            max_tasks=max_tasks, approved_at=now(), source=source.strip())
    value = approval.model_dump(mode="json")
    write_json(work / "plan_approval.json", {"value": value, "sha256": digest(value)})
    return approval
