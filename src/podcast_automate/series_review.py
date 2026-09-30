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
from .research_patches import corrected_call
from .script_models import ScriptIssue
from .storage import digest, write_json

SERIES_REVIEW_VERSION = "series_review.v1"
# The call's prompt label. The report keeps SERIES_REVIEW_VERSION, so saved verdicts and the audio approvals bound
# to them stay valid; new reviews also check the arc and, by the series goal, exposition and guidance (2026-09-30).
SERIES_REVIEW_PROMPT = "series_review.v2-arc"
# One bounded cross-episode repair. A second failure is a decision for the operator.
MAX_SERIES_REPAIRS = 1
CRITERIA = ("coverage", "prerequisites", "progression", "deferred_questions", "synthesis", "arc")
# Checked only when the brief's series_goal weights their aim at 2 or 3.
GOAL_CRITERIA = {"exposition": "understand", "guidance": "apply"}


def series_criteria(config=None):
    goal = (config.series_goal if config is not None else None) or {}
    return (*CRITERIA, *(name for name, aim in GOAL_CRITERIA.items() if goal.get(aim, 0) >= 2))


class SeriesEvidence(Contract):
    episode_id: Identifier
    segment_id: Identifier
    quote: NonEmpty


class SeriesCheck(Contract):
    criterion: Literal["coverage", "prerequisites", "progression", "deferred_questions", "synthesis", "arc",
                       "exposition", "guidance"]
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


def validate_review(review, scripts, criteria=CRITERIA):
    expected = [script.episode_id for script in scripts]
    if review.checked_episodes != expected or Counter(c.criterion for c in review.checks) != Counter(criteria):
        raise AppError("Die Serienprüfung muss alle Folgen und jedes Kriterium genau einmal prüfen. "
                       f"checked_episodes genau in dieser Reihenfolge: {', '.join(expected)}; "
                       f"Kriterien: {', '.join(criteria)}.", code="invalid_series_review", status="blocked")
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
                                  "language": config.language,
                                  **({"series_goal": config.series_goal} if config.series_goal else {})},
                        "criteria": list(series_criteria(config)), "plan": plan.model_dump(),
                        "scripts": [s.model_dump() for s in scripts]}, ensure_ascii=False))
        review = corrected_call(invoke, prompt, SeriesReview, SERIES_REVIEW_PROMPT,
                                lambda answer: validate_review(answer, scripts, series_criteria(config)))
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
    repairs = receipt["repairs"] if receipt else 0
    if (receipt and receipt.get("finished") is False and saved_report is not None
            and saved_report.get("input_hash") == receipt.get("scripts_before")):
        # The round started on these very scripts and never reached its re-check: a stop inside it (the user's, a
        # quota pause, a crash) resumes the round instead of counting it as spent (Ontologies, 2026-09-29: stopped
        # during the correction of episode 2, the resume only repeated the verdict).
        repairs -= 1
    # A saved verdict on today's scripts is not bought again: with its round spent it stops as before, and after
    # the user set a failed round aside (run_budget.approve_fresh_attempts) it is what the new round corrects.
    report = saved_report
    while True:
        if report is None:
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
                   "episodes": list(grouped), "failure": None, "finished": False}
        write_repair_receipt(work, receipt)
        try:
            repaired = repair(grouped)
        except AppError as exc:
            write_repair_receipt(work, {**receipt, "failure": {"code": exc.code, "message": str(exc)}})
            raise
        # Only a round that got this far is spent; one stopped inside it resumes (see above).
        write_repair_receipt(work, {**receipt, "finished": True})
        if not repaired:
            break
        scripts, report = repaired, None
    require_passing_series(report)
    return outputs()
