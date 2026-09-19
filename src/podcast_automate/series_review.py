"""Review the final script collection; bind the verdict to every reviewed input."""
from __future__ import annotations

import json
from collections import Counter
from typing import Literal

from pydantic import Field

from .prompts import instructions
from .editorial import TERMINOLOGY, TEACHING_SCOPE
from .errors import AppError
from .models import Contract, EpisodeScript, Identifier, NonEmpty
from .script_models import ScriptIssue
from .storage import digest, write_json

SERIES_REVIEW_VERSION = "series_review.v1"
# One bounded cross-episode repair. A second failure is a decision for the operator.
MAX_SERIES_REPAIRS = 1
CRITERIA = ("coverage", "prerequisites", "progression", "deferred_questions", "synthesis")


class SeriesEvidence(Contract):
    episode_id: Identifier
    segment_id: Identifier
    quote: NonEmpty


class SeriesCheck(Contract):
    criterion: Literal["coverage", "prerequisites", "progression", "deferred_questions", "synthesis"]
    verdict: Literal["pass", "fail"]
    reason: NonEmpty
    evidence: list[SeriesEvidence]


class SeriesReview(Contract):
    checked_episodes: list[Identifier] = Field(min_length=1)
    checks: list[SeriesCheck] = Field(min_length=1)
    warnings: list[NonEmpty]


def reviewed_scripts(work, plan, episode=None):
    return [EpisodeScript.model_validate_json(
        (work / "reviewed" / f"{entry.episode_id}.json").read_text(encoding="utf-8"))
        for entry in plan.episodes if episode is None or entry.episode_id == episode]


def validate_review(review, scripts):
    expected = [script.episode_id for script in scripts]
    if review.checked_episodes != expected or Counter(c.criterion for c in review.checks) != Counter(CRITERIA):
        raise AppError("Die Serienprüfung muss alle Folgen und jedes Kriterium genau einmal prüfen.",
                       code="invalid_series_review", status="blocked")
    passages = {(script.episode_id, segment.segment_id): segment.text
                for script in scripts for segment in script.segments}
    cited = set()
    for check in review.checks:
        if check.verdict == "pass" and not check.evidence:
            raise AppError("Eine bestandene Serienprüfung benötigt konkrete Textbelege.",
                           code="invalid_series_review", status="blocked")
        for evidence in check.evidence:
            text = passages.get((evidence.episode_id, evidence.segment_id))
            if text is None or evidence.quote not in text:
                raise AppError("Die Serienprüfung nennt unbekannte oder nicht wörtliche Textbelege.",
                               code="invalid_series_evidence", status="blocked")
            cited.add(evidence.episode_id)
    if all(c.verdict == "pass" for c in review.checks) and cited != set(expected):
        raise AppError("Eine bestandene Gesamtprüfung muss Textbelege aus jeder Folge enthalten.",
                       code="invalid_series_evidence", status="blocked")


def series_issues(review):
    """Failing series checks as per-episode script issues, from the segments they cite."""
    grouped = {}
    for check in review.checks:
        if check.verdict != "fail":
            continue
        for episode_id in dict.fromkeys(e.episode_id for e in check.evidence):
            segments = [e.segment_id for e in check.evidence if e.episode_id == episode_id]
            grouped.setdefault(episode_id, []).append(ScriptIssue(
                category="structure", segment_ids=list(dict.fromkeys(segments)),
                reason=f"{check.criterion}: {check.reason}"))
    return grouped


def review_binding(plan, scripts, input_hash):
    return digest({"version": SERIES_REVIEW_VERSION, "input_hash": input_hash,
                   "plan": plan.model_dump(), "scripts": [s.model_dump() for s in scripts]})


def load_series_review(work, plan, scripts, input_hash):
    try:
        saved = json.loads((work / "series_review.json").read_text(encoding="utf-8"))
        report = saved["report"]
        if (saved["sha256"] != digest(report) or report["version"] != SERIES_REVIEW_VERSION or
                report["input_hash"] != review_binding(plan, scripts, input_hash)):
            raise ValueError("Changed series review inputs")
        complete = [s.episode_id for s in scripts] == [e.episode_id for e in plan.episodes]
        if report["complete"] != complete or report["episode_ids"] != [s.episode_id for s in scripts]:
            raise ValueError("Changed series scope")
        if complete:
            review = SeriesReview.model_validate(report["review"])
            validate_review(review, scripts)
            expected_status = "passed" if all(c.verdict == "pass" for c in review.checks) else "blocked"
        else:
            expected_status = "partial"
            if report["review"] is not None:
                raise ValueError("A partial run cannot claim a whole-series verdict")
        if report["status"] != expected_status:
            raise ValueError("Inconsistent series verdict")
        return report
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AppError("Die gespeicherte Serienprüfung fehlt oder passt nicht zu den Skripten.",
                       code="invalid_series_review", status="blocked") from exc


def require_passing_series(report):
    if report["status"] == "blocked":
        reasons = [c["reason"] for c in report["review"]["checks"] if c["verdict"] == "fail"]
        raise AppError("Die Gesamtprüfung der Serie meldet Einwände: " + " ".join(reasons),
                       code="series_review_failed", status="blocked")


def series_report(config, plan, scripts, input_hash, invoke, repairs=0):
    """One review of the whole collection; a partial selection gets no whole-series verdict."""
    identifiers = [s.episode_id for s in scripts]
    complete = identifiers == [e.episode_id for e in plan.episodes]
    report = {"version": SERIES_REVIEW_VERSION, "input_hash": review_binding(plan, scripts, input_hash),
              "episode_ids": identifiers, "complete": complete, "status": "partial", "review": None,
              "missing_episodes": [e.episode_id for e in plan.episodes if e.episode_id not in identifiers],
              "repairs": repairs, "human_reviewed": False}
    if complete:
        prompt = (TERMINOLOGY + TEACHING_SCOPE +
            instructions("series_review") + "\n" +
            json.dumps({"brief": {"central_question": config.central_question or config.topic,
                                  "focus_questions": config.focus_questions, "depth": config.depth_request,
                                  "language": config.language}, "plan": plan.model_dump(),
                        "scripts": [s.model_dump() for s in scripts]}, ensure_ascii=False))
        review = invoke(prompt, SeriesReview, SERIES_REVIEW_VERSION)
        validate_review(review, scripts)
        report.update(review=review.model_dump(),
                      status="passed" if all(c.verdict == "pass" for c in review.checks) else "blocked")
    return report


def repair_binding(plan, input_hash):
    """The repair round belongs to the plan and the inputs, not to the scripts it rewrites."""
    return digest({"version": SERIES_REVIEW_VERSION, "input_hash": input_hash, "plan": plan.model_dump()})


def load_repair_receipt(work, plan, input_hash):
    """The saved repair round of this plan, or ``None`` when no round was started for it."""
    path = work / "series_repair.json"
    if not path.exists():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        receipt = saved["receipt"]
        if saved["sha256"] != digest(receipt) or receipt["version"] != SERIES_REVIEW_VERSION:
            raise ValueError("Changed series repair receipt")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise AppError("Die gespeicherte Korrektur der Serienprüfung wurde verändert.",
                       code="invalid_series_review", status="blocked") from exc
    return receipt if receipt.get("binding") == repair_binding(plan, input_hash) else None


def write_repair_receipt(work, receipt):
    write_json(work / "series_repair.json", {"receipt": receipt, "sha256": digest(receipt)})


def assess_series(work, config, plan, scripts, input_hash, invoke, *, repair=None):
    """``repair`` receives the failing checks grouped by episode and returns the rewritten scripts.

    It is called at most ``MAX_SERIES_REPAIRS`` times per plan and input hash, across resumes:
    ``series_repair.json`` records the round and, when the repair raised, its failure. A resume
    after a failed round raises that failure again without a model call, because the scripts
    the round left behind may differ from the ones the saved verdict is bound to. The caller
    reviews a repaired script again before returning it, so the re-check judges text that still
    carries its own evidence.
    """
    path = work / "series_review.json"
    receipt_path = work / "series_repair.json"

    def outputs():
        return [path, receipt_path] if receipt_path.exists() else [path]

    saved_report = None
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("sha256") != digest(saved.get("report")):
            raise AppError("Die gespeicherte Serienprüfung wurde verändert.",
                           code="invalid_series_review", status="blocked")
        if saved["report"].get("input_hash") == review_binding(plan, scripts, input_hash):
            saved_report = load_series_review(work, plan, scripts, input_hash)
            if saved_report["status"] != "blocked":
                return outputs()
    receipt = load_repair_receipt(work, plan, input_hash)
    if receipt and receipt.get("failure"):
        failure = receipt["failure"]
        raise AppError(failure["message"], code=failure["code"], status="blocked")
    if saved_report is not None:
        require_passing_series(saved_report)
    repairs = receipt["repairs"] if receipt else 0
    while True:
        report = series_report(config, plan, scripts, input_hash, invoke, repairs)
        write_json(path, {"report": report, "sha256": digest(report)})
        if report["status"] != "blocked" or repair is None or repairs >= MAX_SERIES_REPAIRS:
            break
        grouped = series_issues(SeriesReview.model_validate(report["review"]))
        if not grouped:
            break
        repairs += 1
        # The round is spent when it starts; the receipt outlives a failure inside it.
        receipt = {"version": SERIES_REVIEW_VERSION, "binding": repair_binding(plan, input_hash),
                   "repairs": repairs, "scripts_before": report["input_hash"],
                   "episodes": list(grouped), "failure": None}
        write_repair_receipt(work, receipt)
        try:
            repaired = repair(grouped)
        except AppError as exc:
            write_repair_receipt(work, {**receipt, "failure": {"code": exc.code, "message": str(exc)}})
            raise
        if not repaired:
            break
        scripts = repaired
    require_passing_series(report)
    return outputs()
