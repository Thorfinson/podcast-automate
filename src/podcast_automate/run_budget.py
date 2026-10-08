"""Explicit, run-bound allowances and gap approvals without changing approved content inputs.

Four receipts live next to a run and are written only by an explicit user action:

- ``budget_approval.json`` raises the model-call limit and, optionally, the search-round and source limits and the
  money limit of a billed run (D-146).
- ``gap_approvals.json`` lists blocked research tasks the user accepts as documented gaps, so the
  dossier can be finished and published without them.
- ``retry_requests.json`` lists blocked research tasks the user wants attempted again; the next
  resume gives each a fresh recovery ladder and the allowance of a new question.
- ``plan_approval.json`` approves the projected research plan (``question_research/plan_projection.json``)
  before the first task call, optionally with a cap on the number of tasks. It binds to the plan
  hash as well, so a re-planned run needs a new approval.

``text_switch.json`` lets a script run bound to one subscription continue with the automatic pair (Claude, else
Astra through Codex); the run's inputs and hash keep the selection it started with.

All bind to the run id and its input hash; a copy cannot serve another run or changed inputs.
"""
from __future__ import annotations

import json
from datetime import datetime

from typing import Literal

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
    # Money in USD a billed run may spend (D-146); receipts written before it validate without it.
    cost_usd: float | None = Field(default=None, gt=0, le=100_000)
    approved_at: datetime


class GapApproval(Contract):
    task_id: Identifier
    reason: str = ""
    approved_at: datetime


class GapApprovals(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    gaps: list[GapApproval]


class CriterionGap(Contract):
    task_id: Identifier
    criterion: int = Field(ge=0)
    source: str = Field(min_length=1, max_length=2000)
    evidence: str = ""
    reason: str = ""
    approved_at: datetime


class CriterionGaps(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    gaps: list[CriterionGap]


class DisputeDecision(Contract):
    objection_id: Identifier
    decision: Literal["reviewer", "objection"]
    note: str = ""
    decided_at: datetime


class DisputeDecisions(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    decisions: list[DisputeDecision]


class ResidualFinish(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    note: str = ""
    approved_at: datetime


class DossierRebuild(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_at: datetime


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
    if approval.run_id != work.name or approval.input_hash != input_hash:
        raise AppError("Die Erhöhung des Aufruflimits gehört nicht zu diesem Auftrag.",
                       code="invalid_budget_approval", status="blocked")
    # The project's limits are not part of a resumed run's hash (storage.bound_brief, 2026-10-02), so they may have been
    # raised above an earlier approval of this run since; the higher of the two applies. Before, that stopped the run.
    update = {"model_calls": max(approval.model_calls, limits.model_calls)}
    for key in ("search_rounds", "sources"):
        if getattr(approval, key) is not None:
            update[key] = max(getattr(approval, key), getattr(limits, key))
    if approval.cost_usd is not None:
        update["cost_usd"] = max(approval.cost_usd, limits.cost_usd or 0)
    return limits.model_copy(update=update)


def _text_run(root, run_id):
    work = manifest_path(root.resolve(), run_id).parent
    manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
    if manifest.kind not in {"script", "research"}:
        raise AppError("Diese Freigabe gilt nur für einen Recherche- oder Skriptauftrag.", code="invalid_budget_approval")
    return work, manifest


def approve_model_call_limit(root, run_id, model_calls=None, *, search_rounds=None, sources=None, cost_usd=None):
    """Call only after the user explicitly approves these limits for this text run.

    The separate atomic receipt can be written while the worker is busy. Its counters,
    checkpoints, project configuration and outline/audio approvals remain untouched. A previously
    raised search-round, source or money limit is kept when another limit is raised; ``model_calls``
    may be omitted to raise only the search rounds, the sources or the money (``cost_usd``, in USD).
    """
    work, manifest = _text_run(root, run_id)
    limits = effective_limits(work, load_project(root).research_limits, manifest.input_hash)
    if model_calls is None and (search_rounds is not None or sources is not None or cost_usd is not None):
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
    if cost_usd is not None and (type(cost_usd) not in (int, float) or not 0 < cost_usd <= 100_000 or
                                 cost_usd != cost_usd or (limits.cost_usd is not None and cost_usd < limits.cost_usd)):
        raise AppError("Die neue Kostengrenze muss ein Betrag in USD über 0 und mindestens in Höhe der bisherigen sein.",
                       code="invalid_budget_approval")
    previous = read_budget_approval(work)
    if search_rounds is None and previous is not None:
        search_rounds = previous.search_rounds
    if sources is None and previous is not None:
        sources = previous.sources
    if cost_usd is None and previous is not None:
        cost_usd = previous.cost_usd
    approval = BudgetApproval(run_id=manifest.run_id, input_hash=manifest.input_hash, model_calls=model_calls,
                              search_rounds=search_rounds, sources=sources,
                              cost_usd=float(cost_usd) if cost_usd is not None else None, approved_at=now())
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


def criterion_gaps(work, input_hash) -> list[dict]:
    """Access gaps accepted for single criteria of this run; empty when none was ever written."""
    path = work / "criterion_gaps.json"
    if not path.exists():
        return []
    try:
        approvals = CriterionGaps.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("Die gespeicherten Zugangslücken sind ungültig.", code="invalid_gap_approval", status="blocked") from exc
    if approvals.run_id != work.name or approvals.input_hash != input_hash:
        raise AppError("Die Zugangslücken gehören nicht zu diesem Auftrag.", code="invalid_gap_approval", status="blocked")
    return [gap.model_dump(mode="json") for gap in approvals.gaps]


def approve_criterion_gap(root, run_id, task_id, criterion, source, reason=""):
    """Accept that one criterion of a blocked task cannot be met in full because the source it needs
    refused retrieval, after the user explicitly asked for it.

    Unlike an accepted task gap, the task keeps its verified parts: the next resume reopens it, the
    answer and the review see the criterion narrowed to what the readable sources can show, and the
    verified answer carries the gap as a limitation into the dossier and the quality report. Only an
    address this run demonstrably could not read qualifies (``sources.blocked_sources``), so a model
    cannot turn a hard question into an access problem.
    """
    from .research_ledger import load_index, read_value
    from .sources import blocked_sources
    work, manifest = _text_run(root, run_id)
    if manifest.kind != "research":
        raise AppError("Zugangslücken gibt es nur in einem Rechercheauftrag.", code="invalid_gap_approval")
    state_path = work / "question_research/state.json"
    if not state_path.exists():
        raise AppError("Für diesen Lauf gibt es noch keine Recherchefragen.", code="invalid_gap_approval")
    state = read_value(state_path)
    row = state.get("tasks", {}).get(task_id)
    spec = next((t for t in state["plan"]["tasks"] if t["id"] == task_id), None)
    if row is None or spec is None:
        raise AppError("Unbekannte Recherchefrage.", code="invalid_gap_approval")
    if row.get("status") != "blocked" or row.get("accepted_gap"):
        raise AppError("Nur bei einer blockierten Teilfrage kann ein Kriterium als Zugangslücke akzeptiert werden.",
                       code="invalid_gap_approval")
    if isinstance(criterion, bool) or not isinstance(criterion, int) or not 0 <= criterion < len(spec["acceptance"]):
        raise AppError("Unbekanntes Kriterium dieser Teilfrage.", code="invalid_gap_approval")
    if not isinstance(reason, str) or len(reason) > 2000:
        raise AppError("Die Begründung muss ein kurzer Text sein.", code="invalid_gap_approval")
    index = load_index(root, work / "question_research/indexes" / f"{state['index_hash']}.json")
    refused = {entry["url"]: entry for entry in blocked_sources(index)}
    if source not in refused:
        raise AppError("Diese Quelle wurde in diesem Lauf nicht nachweislich gesperrt; nur eine verweigerte Quelle "
                       "begründet eine Zugangslücke.", code="invalid_gap_approval")
    existing = criterion_gaps(work, manifest.input_hash)
    for gap in existing:
        if (gap["task_id"], gap["criterion"], gap["source"]) == (task_id, criterion, source):
            return CriterionGap.model_validate(gap)
    approval = CriterionGap(task_id=task_id, criterion=criterion, source=source, evidence=refused[source]["evidence"],
                            reason=reason.strip(), approved_at=now())
    approvals = CriterionGaps(run_id=manifest.run_id, input_hash=manifest.input_hash,
                              gaps=[*(CriterionGap.model_validate(g) for g in existing), approval])
    write_json(work / "criterion_gaps.json", approvals.model_dump(mode="json"))
    return approval


def dispute_decisions(work, input_hash) -> dict[str, dict]:
    """The editor's decisions on disputed objections of this run, keyed by objection id."""
    path = work / "dispute_decisions.json"
    if not path.exists():
        return {}
    try:
        saved = DisputeDecisions.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("Die gespeicherten Streitfall-Entscheidungen sind ungültig.", code="invalid_gap_approval",
                       status="blocked") from exc
    if saved.run_id != work.name or saved.input_hash != input_hash:
        raise AppError("Die Streitfall-Entscheidungen gehören nicht zu diesem Auftrag.", code="invalid_gap_approval",
                       status="blocked")
    return {row.objection_id: row.model_dump(mode="json") for row in saved.decisions}


def disputed_checks(work, audit_round):
    """Every objection check the given audit round disputed, from its saved review: a split review's merged
    receipt or a whole one, the latest correction revision first. The stop file names only the first."""
    folder = work / "question_research/synthesis" / f"audit_{int(audit_round):02d}"
    for revision in (2, 1, 0):
        merged, whole = folder / f"grounding_{revision}_merged.json", folder / f"grounding_{revision}.json"
        if merged.exists():
            review = json.loads(merged.read_text(encoding="utf-8")).get("review") or {}
        elif whole.exists():
            review = json.loads(whole.read_text(encoding="utf-8")).get("value") or {}
        else:
            continue
        issues = review.get("issues") or []
        return [check for check in review.get("objection_checks") or []
                if check.get("verdict") == "review_disagreement" or (check.get("verdict") == "open" and not issues)]
    return []


def decide_review_disagreement(root, run_id, objection_id, decision, note=""):
    """Settle the objection the whole-dossier audit disputed, after the user explicitly chose a side.

    ``reviewer`` follows the audit: the objection closes and the dispute stays on record in the quality
    report. ``objection`` upholds it: the next routing sends it back to its question as it stands. Only
    the objections the current round disputed can be decided, all of them before the resume if the editor
    likes (``disputed_checks``; ``review_disagreement.json`` names the first); a later decision replaces an earlier one.
    """
    from .research_ledger import read_value
    work, manifest = _text_run(root, run_id)
    if manifest.kind != "research":
        raise AppError("Streitfälle gibt es nur in einem Rechercheauftrag.", code="invalid_gap_approval")
    state_path = work / "question_research/state.json"
    if not state_path.exists():
        raise AppError("Für diesen Lauf gibt es noch keine Recherchefragen.", code="invalid_gap_approval")
    state = read_value(state_path)
    audit_round = int(state.get("audit_round", 0))
    stopped = work / "question_research/synthesis" / f"audit_{audit_round:02d}" / "review_disagreement.json"
    disputed = {check["objection_id"] for check in disputed_checks(work, audit_round)}
    if stopped.exists():
        disputed.add(read_value(stopped).get("objection_id"))
    if objection_id not in state.get("objections", {}) or objection_id not in disputed:
        raise AppError("Dieser Einwand ist in der laufenden Prüfrunde nicht strittig.", code="invalid_gap_approval")
    if decision not in ("reviewer", "objection"):
        raise AppError("Entscheidung: dem Prüfer folgen oder den Einwand aufrechterhalten.", code="invalid_gap_approval")
    if not isinstance(note, str) or len(note) > 2000:
        raise AppError("Die Notiz muss ein kurzer Text sein.", code="invalid_gap_approval")
    existing = {oid: row for oid, row in dispute_decisions(work, manifest.input_hash).items() if oid != objection_id}
    choice = DisputeDecision(objection_id=objection_id, decision=decision, note=note.strip(), decided_at=now())
    saved = DisputeDecisions(run_id=manifest.run_id, input_hash=manifest.input_hash,
                             decisions=[*(DisputeDecision.model_validate(row) for row in existing.values()), choice])
    write_json(work / "dispute_decisions.json", saved.model_dump(mode="json"))
    return choice


def residual_finish(work, input_hash) -> dict | None:
    """The editor's request to finish this run with its remaining audit objections on record, or None."""
    path = work / "residual_finish.json"
    if not path.exists():
        return None
    try:
        saved = ResidualFinish.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("Die gespeicherte Abschlussfreigabe ist ungültig.", code="invalid_gap_approval", status="blocked") from exc
    if saved.run_id != work.name or saved.input_hash != input_hash:
        raise AppError("Die Abschlussfreigabe gehört nicht zu diesem Auftrag.", code="invalid_gap_approval", status="blocked")
    return saved.model_dump(mode="json")


def approve_residual_finish(root, run_id, note=""):
    """Finish the research after its next whole-dossier audit instead of another rework round.

    The objections that audit leaves open stay on record as residual objections in the quality report
    and the publication (``model_review: residual_objections_remaining``); questions blocked only because
    their reworks are spent keep their last verified answer. Only a run that has been audited at least
    once and is not complete can be finished this way.
    """
    from .research_ledger import read_value
    work, manifest = _text_run(root, run_id)
    if manifest.kind != "research":
        raise AppError("Nur ein Rechercheauftrag lässt sich mit Resteinwänden abschließen.", code="invalid_gap_approval")
    state_path = work / "question_research/state.json"
    if not state_path.exists():
        raise AppError("Für diesen Lauf gibt es noch keine Recherchefragen.", code="invalid_gap_approval")
    state = read_value(state_path)
    if state.get("phase") == "completed":
        raise AppError("Die Recherche ist bereits abgeschlossen.", code="invalid_gap_approval")
    if int(state.get("audit_round", 0)) < 1:
        raise AppError("Erst nach der ersten Gesamtprüfung gibt es Resteinwände, mit denen sich abschließen lässt.",
                       code="invalid_gap_approval")
    if not isinstance(note, str) or len(note) > 2000:
        raise AppError("Die Notiz muss ein kurzer Text sein.", code="invalid_gap_approval")
    existing = residual_finish(work, manifest.input_hash)
    if existing:
        return ResidualFinish.model_validate(existing)
    approval = ResidualFinish(run_id=manifest.run_id, input_hash=manifest.input_hash, note=note.strip(), approved_at=now())
    write_json(work / "residual_finish.json", approval.model_dump(mode="json"))
    return approval


def approve_dossier_rebuild(root, run_id):
    """Assemble this run's dossier from its verified answers at the next resume (question_synthesis.ASSEMBLED_GENERATION).

    For a run whose dossier a model composed: that dossier and the objections its audits raised are set aside in the
    run folder, every verified answer and receipt stays, and the next audit judges the assembled whole. No question is
    researched again for the rebuild itself. Only a research run that is not complete and was composed is rebuilt.
    """
    from .question_synthesis import ASSEMBLED_GENERATION, DOSSIER_REBUILD
    from .research_ledger import read_value
    work, manifest = _text_run(root, run_id)
    if manifest.kind != "research":
        raise AppError("Nur ein Rechercheauftrag hat ein Dossier, das neu zusammengesetzt werden kann.",
                       code="invalid_dossier_rebuild")
    state_path = work / "question_research/state.json"
    if not state_path.exists():
        raise AppError("Für diesen Lauf gibt es noch keine Recherchefragen.", code="invalid_dossier_rebuild")
    state = read_value(state_path)
    if state.get("phase") == "completed":
        raise AppError("Die Recherche ist bereits abgeschlossen.", code="invalid_dossier_rebuild")
    path = work / DOSSIER_REBUILD
    if path.exists():
        saved = DossierRebuild.model_validate_json(path.read_text(encoding="utf-8"))
        if saved.run_id == manifest.run_id and saved.input_hash == manifest.input_hash:
            return saved
    if int(state.get("prompt_generation", 1)) >= ASSEMBLED_GENERATION:
        raise AppError("Das Dossier dieses Laufs wird bereits aus den geprüften Antworten zusammengesetzt.",
                       code="invalid_dossier_rebuild")
    approval = DossierRebuild(run_id=manifest.run_id, input_hash=manifest.input_hash, approved_at=now())
    write_json(path, approval.model_dump(mode="json"))
    return approval


def stuck_calls(work):
    """Calls of this run that spent their correction attempts without a receipt. A resume replays their
    stored rejections and stops at once, so only fresh attempts (``approve_fresh_attempts``) move them."""
    from .research_patches import MAX_REJECTIONS
    last = f"_rejected_{MAX_REJECTIONS:02d}.json"
    rows = []
    for path in sorted((work / "question_research").rglob(f"*{last}")):
        name = path.name[:-len(last)]
        if not (path.parent / f"{name}.json").exists():
            rows.append((path.parent, name))
    return rows


# The words every correction loop without a receipt store ends with once its corrections are spent:
# research_patches.corrected_call and re_asked, and the writer's own repairs (script_pipeline.write_episode). Such a
# loop keeps no rejection, so a resume asks the model anew; research_patches.cached_call, whose rejections are saved and
# replayed, ends with "sind gespeichert" instead. tests/test_run_budget pins the words against those functions.
REASKED_MARKER = "die abgewiesenen Antworten liegen bei den Aufrufen"
# Stop codes of a script run's correction loops that keep no rejection: the draft and its repairs (writing), the script
# review and its repair, the teaching design, its review and focused repair, the listener, editorial and teaching reviews,
# the polishing comparison, the series review, and the supplementary research's own checks. A resume after such a stop
# asks the stage anew; "fresh attempts" then set nothing aside, they are that resume (D-155: 4 invalid_script and one
# invalid_teaching_review stop of the 2026-09-30 series waited for the user although a plain resume would have done).
# The same codes without REASKED_MARKER replay a saved state (a changed checkpoint, a series correction's recorded
# failure, which series_repair covers) and stay out.
REASKED_CODES = frozenset({
    "invalid_script", "rejected_output", "invalid_model_output", "invalid_script_evidence_review",
    "invalid_teaching_review", "invalid_teaching_evidence", "invalid_teaching_repair", "invalid_polish_review",
    "invalid_polish_evidence", "invalid_series_review", "invalid_series_evidence", "invalid_supplement",
    "invalid_evidence_review"})


def reasked_stop(manifest):
    """``{"stage", "code"}`` of a script run stopped where its resume asks the model anew (REASKED_CODES with
    REASKED_MARKER), or None."""
    if manifest.kind != "script":
        return None
    for name, record in manifest.stages.items():
        error = record.error
        if record.status == "blocked" and error and error.code in REASKED_CODES and REASKED_MARKER in error.message:
            return {"stage": name, "code": error.code}
    return None


def fresh_attempts_plan(work, manifest):
    """What ``approve_fresh_attempts`` would set aside for this stopped run, without touching anything; raises the
    refusal the approval would raise. The Studio offers the button only where this finds something (2026-10-02:
    the button was offered on research stops without a stuck call, and again after an allowance had reset the
    repairs, and then failed)."""
    if manifest.status == "running":
        raise AppError("Der Lauf arbeitet gerade; neue Anläufe erst, wenn er angehalten hat.", code="invalid_retry_request")
    if manifest.kind == "script":
        # A supplementary research of the teaching stage that spent its corrections (teaching_research.stuck_supplements).
        # Also a script review whose repairs are spent on points that still stop the run (grounding, scope, structure).
        from .script_pipeline import MAX_REVIEW_REPAIRS, NOTED_CATEGORIES
        from .teaching_research import stuck_supplements
        folders = stuck_supplements(work)
        # A series correction that failed its evidence check, or a spent round the series review still objects to:
        # set aside, so the next resume corrects the series in a new round (the user's explicit choice).
        failed_series = None
        series = work / "series_repair.json"
        stage = manifest.stages.get("review")
        objected = bool(stage and stage.error and stage.error.code == "series_review_failed")
        if series.exists() and ((json.loads(series.read_text(encoding="utf-8")).get("receipt") or {}).get("failure")
                                or objected):
            failed_series = series
        reviews = []
        for path in sorted((work / "reviews").glob("ep_*_checkpoint.json")):
            saved = json.loads(path.read_text(encoding="utf-8"))
            issues = (saved.get("review") or {}).get("issues") or []
            if saved.get("repairs", 0) >= MAX_REVIEW_REPAIRS and any(i["category"] not in NOTED_CATEGORIES for i in issues):
                reviews.append((path, saved))
        # A stage whose correction loop keeps no rejection: nothing to set aside, the resume itself asks anew.
        reasked = reasked_stop(manifest)
        if not folders and not reviews and not failed_series and not reasked:
            raise AppError("Keine Nachrecherche, keine Skriptprüfung und keine Serienkorrektur dieses Laufs hat ihre "
                           "Korrekturversuche verbraucht.", code="invalid_retry_request")
        return {"supplements": folders, "reviews": reviews, "series_repair": failed_series, "reasked": reasked}
    if manifest.kind != "research":
        raise AppError("Neue Anläufe gibt es nur für einen Recherche- oder Skriptauftrag.", code="invalid_retry_request")
    stuck = stuck_calls(work)
    if not stuck:
        raise AppError("Kein Schritt dieses Laufs hat seine Korrekturversuche verbraucht.", code="invalid_retry_request")
    return {"calls": stuck}


def fresh_attempts_available(root, run_id):
    """Whether "Mit neuen Anläufen fortsetzen" would be accepted for this stopped run now."""
    try:
        work, manifest = _text_run(root, run_id)
        fresh_attempts_plan(work, manifest)
    except (AppError, OSError, ValueError, KeyError, TypeError):
        return False
    return True


def approve_fresh_attempts(root, run_id):
    """Give every stuck call of a stopped research run a fresh set of correction attempts, after the user
    explicitly asked for it. The spent rejections move aside unchanged (``<name>_superseded_NN_MM.json``),
    so the resume asks the model anew with nothing of the stop carried over; each approval is recorded in
    ``fresh_attempts.json``. A running run is never touched."""
    from .research_patches import MAX_REJECTIONS, supersede_rejections
    work, manifest = _text_run(root, run_id)
    plan = fresh_attempts_plan(work, manifest)
    if manifest.kind == "script":
        from .teaching_research import supersede_corrections
        folders, reviews, failed_series = plan["supplements"], plan["reviews"], plan["series_repair"]
        if failed_series:
            number = 1 + len(list(work.glob("series_repair_superseded_*.json")))
            failed_series.replace(work / f"series_repair_superseded_{number:02d}.json")
        for folder in folders:
            supersede_corrections(folder)
        for path, saved in reviews:
            # The draft and its review stay; the next resume repairs against that review again, MAX_REVIEW_REPAIRS times.
            write_json(path, {**saved, "repairs": 0})
        record = {"supplements": [folder.relative_to(work).as_posix() for folder in folders],
                  "reviews": [path.name.removesuffix("_checkpoint.json") for path, _ in reviews],
                  "series_repair": bool(failed_series), "approved_at": now()}
        if plan.get("reasked"):
            # A correction loop that keeps no rejections (D-155): nothing is set aside, the record names the stop.
            record["reasked"] = plan["reasked"]
        path = work / "fresh_attempts.json"
        write_json(path, [*(json.loads(path.read_text(encoding="utf-8")) if path.exists() else []), record])
        return record
    stuck = plan["calls"]
    for folder, name in stuck:
        supersede_rejections(folder, name, MAX_REJECTIONS + 1)
    record = {"calls": [(folder / name).relative_to(work).as_posix() for folder, name in stuck], "approved_at": now()}
    path = work / "fresh_attempts.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    write_json(path, [*history, record])
    return record


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


class TeachingRedesign(Contract):
    episode_id: Identifier
    note: str = Field(min_length=1, max_length=2000)
    requested_at: datetime


class TeachingRedesigns(Contract):
    run_id: Identifier
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    requests: list[TeachingRedesign]


def teaching_redesigns(work, input_hash) -> dict[str, dict]:
    """Requested new teaching designs of this script run keyed by episode id; empty when none was ever written."""
    path = work / "teaching_redesigns.json"
    if not path.exists():
        return {}
    try:
        saved = TeachingRedesigns.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("Die gespeicherten Neuentwürfe des Lehrkonzepts sind ungültig.", code="invalid_redesign_request",
                       status="blocked") from exc
    if saved.run_id != work.name or saved.input_hash != input_hash:
        raise AppError("Die Neuentwürfe des Lehrkonzepts gehören nicht zu diesem Auftrag.", code="invalid_redesign_request",
                       status="blocked")
    return {item.episode_id: item.model_dump(mode="json") for item in saved.requests}


def request_teaching_redesign(root, run_id, episode_id, note):
    """Ask for a new teaching design of one episode, with the editor's note, after the user explicitly requested it.

    Only a script run whose teaching stage stopped because the design kept its defects after the focused repair
    (``teaching_design_failed``) takes it, and only for an episode without an accepted design. The next resume
    moves the stopped design aside and designs the episode anew with the note and fresh correction rounds
    (script_pipeline.ScriptRun.adopt_redesign); the approved outline stays. A later request for the same
    episode replaces the earlier one."""
    from .script_models import SeriesPlan
    work, manifest = _text_run(root, run_id)
    stage = manifest.stages.get("teaching")
    if manifest.kind != "script" or stage is None or stage.status != "blocked" or (stage.error or None) is None \
            or stage.error.code != "teaching_design_failed":
        raise AppError("Ein neues Lehrkonzept mit Hinweis gibt es nur, wenn das Lehrkonzept nach den automatischen "
                       "Korrekturen offene Punkte behält.", code="invalid_redesign_request")
    plan = SeriesPlan.model_validate_json((work / "series_plan.json").read_text(encoding="utf-8"))
    if episode_id not in {entry.episode_id for entry in plan.episodes}:
        raise AppError("Diese Folge steht nicht im Inhaltsverzeichnis des Laufs.", code="invalid_redesign_request")
    if (work / "teaching" / episode_id / "plan.json").exists():
        raise AppError("Das Lehrkonzept dieser Folge ist bereits geprüft.", code="invalid_redesign_request")
    if not isinstance(note, str) or not note.strip() or len(note) > 2000:
        raise AppError("Bitte einen kurzen Hinweis für das neue Lehrkonzept angeben.", code="invalid_redesign_request")
    existing = teaching_redesigns(work, manifest.input_hash)
    request = TeachingRedesign(episode_id=episode_id, note=note.strip(), requested_at=now())
    existing[episode_id] = request.model_dump(mode="json")
    saved = TeachingRedesigns(run_id=manifest.run_id, input_hash=manifest.input_hash,
                              requests=[TeachingRedesign.model_validate(item) for item in existing.values()])
    write_json(work / "teaching_redesigns.json", saved.model_dump(mode="json"))
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


def text_switch(work, input_hash, saved):
    """The provider selection a text run works with: the one it started with, or the one the user switched it to
    (approve_text_switch). The run's inputs and hash keep the saved selection, so every checkpoint stays valid;
    drafts, answers and reviews hang on the prompt text, not on the provider that answered it."""
    path = work / "text_switch.json"
    if not path.exists():
        return saved
    switch = json.loads(path.read_text(encoding="utf-8"))
    if switch.get("input_hash") != input_hash or switch.get("from") != saved:
        return saved
    return switch["text_generation"]


def request_file(work):
    return work / ("script_request.json" if (work / "script_request.json").exists() else "research_request.json")


def saved_text_generation(work):
    """The selection a script or research run started with; ``None`` for a research run from before the field."""
    path = request_file(work)
    return json.loads(path.read_text(encoding="utf-8")).get("text_generation") if path.exists() else None


def run_text_generation(work):
    """The selection a script or research run works with now, for everything that follows the run's provider:
    the adapter pool, the key handover, the status report and the expression layer of a later recording."""
    saved = saved_text_generation(work)
    try:
        manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
    except AppError:
        return saved  # no readable run: nothing can have been switched
    return text_switch(work, manifest.input_hash, saved)


# The ways a text run may continue (approve_text_switch), by the id the Studio and the CLI send.
TEXT_SWITCHES = ("claude_first", "astra_first", "claude", "astra", "openrouter", "claude_api")


def switch_choice(selection):
    """The TEXT_SWITCHES id a selection corresponds to; a run without a saved selection works with Codex."""
    provider = (selection or {}).get("provider") or "codex_cli"
    if provider == "auto":
        return "astra_first" if selection.get("prefer") == "codex_cli" else "claude_first"
    return {"claude_code": "claude", "codex_cli": "astra"}.get(provider, provider)


def approve_text_switch(root, run_id, choice="claude_first", *, model=None, cost_usd=None):
    """Let a script or research run continue with another text provider, after the user explicitly chose it
    (2026-09-29: Claude's seven-day window ran low while both projects were in the script review, and the user
    asked for the choice for every text run).

    ``claude_first`` and ``astra_first`` ask one subscription first and the other when its quota is spent;
    ``claude`` and ``astra`` stay on one; ``openrouter`` bills ``model`` per token and needs the key, while web
    searches keep running on the subscriptions, since OpenRouter has no search tools, unless the run searches
    through Perplexity (D-151), which a switch keeps. Astra works at xhigh; Claude
    works with the catalog's Claude default (Haiku 5.5 at xhigh since 2026-10-07, D-166; Sonnet 5.5 at high from
    2026-09-29, when the user replaced the runs' Opus 5.5 at medium with it). ``claude_api`` bills Claude to the user's Anthropic key (D-145). A billed
    choice needs the run's money limit (D-146): ``cost_usd`` sets it with the switch. A later choice replaces the
    earlier one, and choosing what the run started with removes the receipt. It may be written while the worker
    runs; the next start reads it."""
    from .provider_pool import text_generation_settings
    from .text_settings import (BILLED_TEXT_PROVIDERS, CLAUDE_MODELS, DEFAULT_CLAUDE_EFFORT, DEFAULT_CLAUDE_MODEL,
                                DEFAULT_CODEX_MODEL, OPENROUTER_MODELS, TEXT_PRESETS)
    work, manifest = _text_run(root, run_id)
    if choice not in TEXT_SWITCHES:
        raise AppError("Weiter mit Claude, Astra oder OpenRouter wählen.", code="invalid_text_switch")
    saved = saved_text_generation(work)
    level = DEFAULT_CLAUDE_EFFORT
    config = load_project(root)

    def claude_choice(provider):
        """The Claude model a switch names (D-170: Orlagau, 2026-10-08, was to continue on Sonnet 5.5 while the
        workspace default stayed Haiku), at its preset's level; without one the catalog default at xhigh."""
        if model is not None and model not in CLAUDE_MODELS:
            raise AppError("Ein Claude-Modell aus der Liste wählen.", code="invalid_text_switch")
        chosen = model or DEFAULT_CLAUDE_MODEL
        effort = next((p["reasoning_effort"] for p in TEXT_PRESETS if p["provider"] == provider and p["model"] == chosen),
                      level) if model else level
        return chosen, effort

    if choice in {"claude_first", "astra_first"}:
        selection = text_generation_settings(config, backend="auto", reasoning_effort=level)
        selection["prefer"] = "claude_code" if choice == "claude_first" else "codex_cli"
        selection["candidates"]["codex_cli"] = {"model": DEFAULT_CODEX_MODEL, "reasoning_effort": "xhigh"}
    elif choice == "claude":
        chosen, effort = claude_choice("claude_code")
        selection = text_generation_settings(config, backend="claude_code", model=chosen if model else None,
                                             reasoning_effort=effort)
    elif choice == "astra":
        selection = text_generation_settings(config, backend="codex_cli", model=DEFAULT_CODEX_MODEL, reasoning_effort="xhigh")
    elif choice == "claude_api":
        chosen, effort = claude_choice("claude_api")
        selection = text_generation_settings(config, backend="claude_api", model=chosen, reasoning_effort=effort)
    else:
        if model not in OPENROUTER_MODELS:
            raise AppError("Ein OpenRouter-Modell aus der Liste wählen.", code="invalid_text_switch")
        effort = next((p["reasoning_effort"] for p in TEXT_PRESETS if p["provider"] == "openrouter" and p["model"] == model), None)
        selection = text_generation_settings(config, backend="openrouter", model=model, reasoning_effort=effort)
    if saved and saved.get("web_search"):
        # The run's web search stays as it started (D-151); only the text model changes.
        selection = {**selection, "web_search": saved["web_search"], "web_search_version": saved.get("web_search_version")}
    if selection["provider"] in BILLED_TEXT_PROVIDERS:
        if cost_usd is not None:
            approve_model_call_limit(root, run_id, cost_usd=cost_usd)
        if effective_limits(work, config.research_limits, manifest.input_hash).cost_usd is None:
            raise AppError("Ein abgerechneter Anbieter braucht eine Kostengrenze in USD für diesen Lauf.",
                           code="cost_limit_required", status="blocked")
    path = work / "text_switch.json"
    if saved is not None and settled(selection) == settled(saved):
        path.unlink(missing_ok=True)
        return saved
    write_json(path, {"run_id": manifest.run_id, "input_hash": manifest.input_hash, "from": saved,
                      "text_generation": selection, "approved_at": now()})
    return selection


def settled(selection):
    """A selection without its unset fields, so a saved form with explicit nulls compares with a new one."""
    return {key: value for key, value in selection.items() if value is not None}
