"""Review the final script collection; bind the verdict to every reviewed input."""
from __future__ import annotations

import json
from collections import Counter
from typing import ClassVar, Literal

from pydantic import Field

from .prompts import instructions
from .editorial import TEACHING_SCOPE, terminology
from .errors import AppError
from .models import Contract, EpisodeScript, Identifier, LaterFields, NonEmpty
from .research_patches import corrected_call
from .script_models import ScriptIssue
from .storage import digest, write_json

SERIES_REVIEW_VERSION = "series_review.v1"
# The call's prompt label. The report keeps SERIES_REVIEW_VERSION, so saved verdicts and the audio approvals bound
# to them stay valid; new reviews also check the arc and, by the series goal, exposition and guidance (2026-09-30).
# v3 (2026-10-02): a failing check names its episodes and whether it is a source limit, and the re-check after a
# correction round is told the previous checks and the episodes the round changed. Coverage asks for each episode's
# core findings only (supporting_finding_ids are optional), and the terminology rule is the project's own.
SERIES_REVIEW_PROMPT = "series_review.v4-core"
# One bounded cross-episode repair. A second failure is a decision for the operator.
MAX_SERIES_REPAIRS = 1
CRITERIA = ("coverage", "prerequisites", "progression", "deferred_questions", "synthesis", "arc")
# Every series review has checked these five; reports written before the arc (until 2026-09-30) checked only them.
LEGACY_CRITERIA = ("coverage", "prerequisites", "progression", "deferred_questions", "synthesis")
# Checked only when the brief's series_goal weights their aim at 2 or 3.
GOAL_CRITERIA = {"exposition": "understand", "guidance": "apply"}


def series_criteria(config=None):
    goal = (config.series_goal if config is not None else None) or {}
    return (*CRITERIA, *(name for name, aim in GOAL_CRITERIA.items() if goal.get(aim, 0) >= 2))


class SeriesEvidence(Contract):
    episode_id: Identifier
    segment_id: Identifier
    quote: NonEmpty


class SeriesCheck(LaterFields):
    criterion: Literal["coverage", "prerequisites", "progression", "deferred_questions", "synthesis", "arc",
                       "exposition", "guidance"]
    verdict: Literal["pass", "fail"]
    reason: NonEmpty
    evidence: list[SeriesEvidence]
    # Since 2026-10-02; saved reviews without them still validate and dump as they were (LaterFields).
    episode_ids: list[Identifier] = Field(default_factory=list, description=(
        "For a failing check, every episode whose script has to change to fix it; may be empty for a pass."))
    source_limit: bool = Field(default=False, description=(
        "True only for a failing check that rewriting the scripts cannot fix because the supplied research lacks "
        "the material; false otherwise."))

    LATER: ClassVar[dict] = {"episode_ids": [], "source_limit": False}


class SeriesReview(Contract):
    checked_episodes: list[Identifier] = Field(min_length=1)
    checks: list[SeriesCheck] = Field(min_length=1)
    warnings: list[NonEmpty]


def reviewed_scripts(work, plan, episode=None):
    return [EpisodeScript.model_validate_json(
        (work / "reviewed" / f"{entry.episode_id}.json").read_text(encoding="utf-8"))
        for entry in plan.episodes if episode is None or entry.episode_id == episode]


def check_episodes(check):
    """The episodes a check concerns: those it names, then those it quotes."""
    return list(dict.fromkeys([*check.episode_ids, *(e.episode_id for e in check.evidence)]))


def classify(review, scope=None):
    """The failing checks that block, and the ones that only become advisories as ``(check, basis)``.

    A source limit never blocks: rewriting the scripts cannot fix it. A first review (no ``scope``) blocks on every
    other failure. The re-check after a correction round (``scope``, saved before its call) blocks only on an earlier
    objection that is still open, the same criterion on an episode it named, or on a failure in an episode the round
    changed; a new point on an untouched episode is reported, not blocking, so the re-check converges instead of
    finding new details in text that passed (the user's rule for repeated reviews, 2026-10-02)."""
    blocking, advisories = [], []
    objections = [row for row in (scope or {}).get("previous_checks", []) if row.get("blocking")]
    changed = set((scope or {}).get("changed_episodes", []))
    for check in review.checks:
        if check.verdict != "fail":
            continue
        episodes = set(check_episodes(check))
        if check.source_limit:
            advisories.append((check, "source_limit"))
        elif scope is None or not episodes or episodes & changed or any(
                row["criterion"] == check.criterion and (not row["episode_ids"] or episodes & set(row["episode_ids"]))
                for row in objections):
            blocking.append(check)
        else:
            advisories.append((check, "unchanged"))
    return blocking, advisories


def validate_review(review, scripts, criteria=CRITERIA, scope=None):
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
        if set(check.episode_ids) - set(expected):
            raise AppError(f"Die Prüfung {check.criterion} nennt in episode_ids unbekannte Folgen; erlaubt sind: "
                           f"{', '.join(expected)}.", code="invalid_series_review", status="blocked")
        for evidence in check.evidence:
            text = passages.get((evidence.episode_id, evidence.segment_id))
            if text is None or evidence.quote not in text:
                raise AppError("Die Serienprüfung nennt unbekannte oder nicht wörtliche Textbelege.",
                               code="invalid_series_evidence", status="blocked")
            cited.add(evidence.episode_id)
    # A series that passes, even with advisories, is vouched for by text from every episode.
    if not classify(review, scope)[0] and cited != set(expected):
        raise AppError("Eine bestandene Gesamtprüfung muss Textbelege aus jeder Folge enthalten.",
                       code="invalid_series_evidence", status="blocked")


def validate_answer(review, scripts, criteria, scope=None):
    """``validate_review`` for a new answer, which must also place every failure: a failing check without episodes gave
    the correction nothing to work on, and the run stopped at once (finding of 2026-10-02). Saved verdicts are not
    held to it, so a review written before keeps loading."""
    validate_review(review, scripts, criteria, scope)
    unplaced = [check.criterion for check in review.checks if check.verdict == "fail" and not check_episodes(check)]
    if unplaced:
        raise AppError("Jede nicht bestandene Serienprüfung muss in episode_ids die Folgen nennen, deren Skript sich "
                       f"ändern muss: {', '.join(unplaced)}.", code="invalid_series_review", status="blocked")


def saved_criteria(report, review):
    """The criteria a saved verdict was asked for. The report records them since 2026-10-02; an earlier one is judged by
    the checks its review made, which must include the five every series review has had. Validating a saved verdict
    against today's default instead refused every verdict whose series goal added exposition or guidance, and every
    one from before the arc (Asimov and Ontologies of 2026-09-29), at publish, on resume and at the audio gate."""
    criteria = report.get("criteria")
    if criteria is None:
        criteria = list(dict.fromkeys(check.criterion for check in review.checks))
    if not set(LEGACY_CRITERIA) <= set(criteria):
        raise ValueError("A series verdict without the base criteria")
    return tuple(criteria)


def verdict_status(review, scope=None):
    return "blocked" if classify(review, scope)[0] else "passed"


def series_issues(review, scope=None):
    """Blocking series checks as per-episode script issues: to every episode a check names or quotes, with the
    segments it cites there (none when it only names the episode)."""
    grouped = {}
    for check in classify(review, scope)[0]:
        for episode_id in check_episodes(check):
            segments = [e.segment_id for e in check.evidence if e.episode_id == episode_id]
            grouped.setdefault(episode_id, []).append(ScriptIssue(
                category="structure", segment_ids=list(dict.fromkeys(segments)),
                reason=f"{check.criterion}: {check.reason}"))
    return grouped


def advisory_rows(review, scope=None):
    return [{"criterion": check.criterion, "reason": check.reason, "episode_ids": check_episodes(check), "basis": basis}
            for check, basis in classify(review, scope)[1]]


def round_scope(corrected, changed, repairs):
    """What the re-check after a correction round is told and judged by (``classify``): every check of the verdict the
    round corrected, with what became of each failing one, and the episodes the round changed."""
    review = SeriesReview.model_validate(corrected["review"])
    blocking, advisories = classify(review, corrected.get("scope"))
    noted = {check.criterion: basis for check, basis in advisories}
    rows = []
    for check in review.checks:
        row = {"criterion": check.criterion, "verdict": check.verdict, "reason": check.reason,
               "episode_ids": check_episodes(check)}
        if check in blocking:
            row.update(blocking=True, outcome="corrected" if set(row["episode_ids"]) & set(changed) else "uncorrected")
        elif check.verdict == "fail":
            row.update(blocking=False, outcome="source_limit" if noted.get(check.criterion) == "source_limit" else "noted")
        rows.append(row)
    return {"round": repairs, "previous_checks": rows, "changed_episodes": list(changed)}


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
            # Judged by the criteria and the scope it was asked with; its sha256 above guards both.
            validate_review(review, scripts, saved_criteria(report, review), report.get("scope"))
            expected_status = verdict_status(review, report.get("scope"))
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
        review = SeriesReview.model_validate(report["review"])
        reasons = [check.reason for check in classify(review, report.get("scope"))[0]]
        raise AppError("Die Gesamtprüfung der Serie meldet Einwände: " + " ".join(reasons),
                       code="series_review_failed", status="blocked")


def series_report(config, plan, scripts, input_hash, invoke, repairs=0, scope=None):
    """One review of the whole collection; a partial selection gets no whole-series verdict.

    ``scope`` (``round_scope``) makes it the re-check after a correction round: the call is told the previous checks
    and the changed episodes, and ``classify`` decides by them which failures block. The report records the criteria
    and the scope it was asked with, so a saved verdict is judged on load exactly as it was judged here."""
    identifiers = [s.episode_id for s in scripts]
    complete = identifiers == [e.episode_id for e in plan.episodes]
    report = {"version": SERIES_REVIEW_VERSION, "input_hash": review_binding(plan, scripts, input_hash),
              "episode_ids": identifiers, "complete": complete, "status": "partial", "review": None,
              "missing_episodes": [e.episode_id for e in plan.episodes if e.episode_id not in identifiers],
              "repairs": repairs, "human_reviewed": False}
    if complete:
        criteria = series_criteria(config)
        prompt = (terminology(config.language, config.topic, config.central_question) + TEACHING_SCOPE +
            instructions("series_review") + "\n" +
            json.dumps({"brief": {"central_question": config.central_question or config.topic,
                                  "focus_questions": config.focus_questions, "depth": config.depth_request,
                                  "language": config.language,
                                  **({"series_goal": config.series_goal} if config.series_goal else {})},
                        "criteria": list(criteria), "plan": plan.model_dump(),
                        **({"previous_checks": scope["previous_checks"], "changed_episodes": scope["changed_episodes"]}
                           if scope else {}),
                        "scripts": [s.model_dump() for s in scripts]}, ensure_ascii=False))
        review = corrected_call(invoke, prompt, SeriesReview, SERIES_REVIEW_PROMPT,
                                lambda answer: validate_answer(answer, scripts, criteria, scope))
        report.update(review=review.model_dump(), status=verdict_status(review, scope), criteria=list(criteria),
                      advisories=advisory_rows(review, scope), **({"scope": scope} if scope else {}))
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


# The verdicts on a correction itself: its evidence check rejected it, or it broke the source mapping or structure
# after its repeated attempts. Only they are the round's decision.
CORRECTION_VERDICTS = frozenset({"script_review_failed", "invalid_script"})


def resumable(error):
    """A failure inside a correction round after which no verdict on the correction exists, so a resume goes on with
    the round instead of replaying it: a timeout, a stall, the user's stop, a quota pause, a provider failure, and as
    well a spent call limit, a missing key, a busy project or a malformed answer. Until 2026-10-02 only the first kinds
    were listed, and the others were recorded as the round's decision: every resume raised them again without a call,
    after a raised limit too."""
    return error.code not in CORRECTION_VERDICTS


def script_digests(scripts):
    return {script.episode_id: digest(script.model_dump()) for script in scripts}


def assess_series(work, config, plan, scripts, input_hash, invoke, *, repair=None):
    """``repair`` receives the blocking checks grouped by episode and returns the rewritten scripts.

    It is called at most ``MAX_SERIES_REPAIRS`` times per plan and input hash, across resumes:
    ``series_repair.json`` records the round, the digest of every script it started from and, when
    the repair raised a decision on the correction (a rejected correction, a contract that stayed
    broken), that failure. A resume after such a round raises it again without a model call; the
    user's "Mit neuen Anläufen fortsetzen" sets it aside. A failure without such a decision (a timeout,
    a stall, a quota pause, the user's stop, a provider failure) is not recorded: the resume goes on
    with the round, for the episodes whose correction was not adopted yet. The caller reviews a
    repaired script again before returning it, so the re-check judges text that still carries its own
    evidence. The re-check gets the round's scope (``round_scope``), saved in the receipt before its
    call, so a resume asks it the same question.
    """
    path = work / "series_review.json"
    receipt_path = work / "series_repair.json"

    def outputs():
        return [path, receipt_path] if receipt_path.exists() else [path]

    saved, saved_report = None, None
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
    # A saved verdict on today's scripts is not bought again: with its round spent it stops as before, and after
    # the user set a failed round aside (run_budget.approve_fresh_attempts) it is what the new round corrects.
    report, scope, open_round = saved_report, None, None
    if receipt and receipt.get("finished") is False:
        if saved_report is not None and saved_report.get("input_hash") == receipt.get("scripts_before"):
            # The round started on these very scripts and never reached its re-check: a stop inside it (the user's, a
            # quota pause, a crash) resumes the round instead of counting it as spent (Ontologies, 2026-09-29: stopped
            # during the correction of episode 2, the resume only repeated the verdict).
            repairs -= 1
        elif (repair is not None and receipt.get("before") and saved is not None
              and saved["report"].get("input_hash") == receipt.get("scripts_before")):
            # Part of the round was adopted before it stopped (a parallel round adopts what passed, then raises the
            # timeout of another episode): it goes on from the verdict it corrects, for the episodes still uncorrected.
            open_round = saved["report"]
    elif receipt and receipt.get("finished") and report is None and receipt.get("scope") and (
            receipt.get("scope_scripts") == review_binding(plan, scripts, input_hash)):
        # Stopped during the re-check of a finished round: asked again with the scope saved before its call.
        scope = receipt["scope"]
    while True:
        if open_round is None:
            if report is None:
                report = series_report(config, plan, scripts, input_hash, invoke, repairs, scope)
                write_json(path, {"report": report, "sha256": digest(report)})
            if report["status"] != "blocked" or repair is None or repairs >= MAX_SERIES_REPAIRS:
                break
            corrected = report
            grouped = series_issues(SeriesReview.model_validate(corrected["review"]), corrected.get("scope"))
            if not grouped:
                break
            repairs += 1
            # The round is spent when it starts; the receipt outlives a failure inside it.
            receipt = {"version": SERIES_REVIEW_VERSION, "binding": repair_binding(plan, input_hash),
                       "repairs": repairs, "scripts_before": corrected["input_hash"],
                       "episodes": list(grouped), "failure": None, "finished": False,
                       "before": script_digests(scripts)}
            write_repair_receipt(work, receipt)
        else:
            corrected, open_round = open_round, None
            grouped = series_issues(SeriesReview.model_validate(corrected["review"]), corrected.get("scope"))
        before, current = receipt["before"], script_digests(scripts)
        # An episode whose script left the round's starting point has its correction adopted already.
        pending = {episode: issues for episode, issues in grouped.items() if current.get(episode) == before.get(episode)}
        try:
            repaired = repair(pending) if pending else None
        except AppError as exc:
            if not resumable(exc):
                write_repair_receipt(work, {**receipt, "failure": {"code": exc.code, "message": str(exc)}})
            raise
        changed = [episode for episode, value in script_digests(repaired or scripts).items() if value != before.get(episode)]
        if not repaired and not changed:
            # Only a round that got this far is spent; one stopped inside it resumes (see above).
            write_repair_receipt(work, {**receipt, "finished": True})
            report = corrected
            break
        scripts, report = repaired or scripts, None
        scope = round_scope(corrected, changed, repairs)
        write_repair_receipt(work, {**receipt, "finished": True, "scope": scope,
                                    "scope_scripts": review_binding(plan, scripts, input_hash)})
    require_passing_series(report)
    return outputs()
