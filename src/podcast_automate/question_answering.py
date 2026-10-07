"""Per-question research loop: seeded reading, reader decisions, web search and independent review.

Mixed into ``QuestionResearch``; every method works on ``self.state["tasks"][task_id]`` rows whose
keys are defined by ``question_scope.pending_task``.
"""
from __future__ import annotations

import json
from contextlib import nullcontext

from .editorial import TERMINOLOGY, TEACHING_SCOPE
from .errors import AppError
from .evidence_models import EVIDENCE_VERSION
from .prompts import instructions
from .question_dependencies import prerequisite_answers, prerequisite_gaps
from .question_sources import attempt_folder, reserve_source, restore_attempts, source_identity
from .research_evidence import (EVIDENCE_INSTRUCTIONS, PROFILES, blocks, evidence_profile, collapse_assessments, collapse_support, verbatim,
                                evidence_summary, scope_assessments, settle_receipts, support_errors)
from .research_gap_probe import settle
from .research_ledger import CALL_VERSION, check_sources, read_value, save_value
from .research_models import ResearchDiscovery, SourceDocument, SourceIndex, admissible, is_idea
from .research_dates import research_day
from .research_reader import source_catalog
from .research_retrieval import references
from .research_tasks import AnswerReview, CriterionVerdict, QuestionAnswer, QuestionSearch, ReaderWindow, ResearchDecision
from .sources import canonical_url, clean, import_failure, import_source
from .storage import digest, read_text

READER_ACTIONS = ["search_local", "read", "search_web", "answer", "blocked"]
# Criteria the user narrowed because the source they need refused retrieval (run_budget.approve_criterion_gap).
# Added to a prompt only for a task that has one, so every other prompt keeps its text and its receipts.
ACCESS_GAP_READER = ("accepted_access_gaps lists acceptance criteria the editor accepted as access gaps: the named source "
                     "refused retrieval, so the part of the criterion that needs it cannot be verified. Answer those criteria "
                     "with what the read sources show, do not state or explain the refused part as verified, and do not search "
                     "for that source again. Name the gap in limits as missing source access, not as scientific uncertainty, "
                     "and keep the outcome supported_answer when the rest is supported.")
ACCESS_GAP_REVIEW = ("accepted_access_gaps lists acceptance criteria the editor accepted as access gaps because the named "
                     "source refused retrieval. Judge those criteria on their remaining parts: pass one when those parts are "
                     "met and the answer names the gap in its limits without claiming the refused part. The refused source "
                     "does not count against source_adequacy.")
# Only after this question searched the web (Ontologies, 2026-09-30: honest answers that only vendor estimates
# exist failed "independent effort figures" and blocked, so the finding "there are only vendor claims" was lost).
EVIDENCE_ABSENCE_REVIEW = ("This question's reader searched the web. A criterion that demands a kind of evidence (independent, "
                           "published within a recency window, a number of studies, measured figures) passes when the answer "
                           "shows with read passages what does exist, states in its limits that the demanded kind was not "
                           "found, and claims nothing beyond the passages. Criteria that ask for an exposition, a mechanism "
                           "or a definition are judged as before.")
PREREQUISITE_GAP_READER = ("prerequisite_gaps lists prerequisites of this synthesis that the editor accepted as gaps: they "
                           "have no verified answer. Build the synthesis from the verified prerequisite answers and your "
                           "sources, do not fill a gap with claims of your own, and name each gap in limits.")
PREREQUISITE_GAP_REVIEW = ("prerequisite_gaps lists prerequisites the editor accepted as gaps. Judge the synthesis on the "
                           "verified prerequisites and sources: pass a criterion that needs a gap when the answer names "
                           "that gap in its limits and claims nothing from it.")
LOCK_FEEDBACK = ("The answer is locked after a failed review: read or search new passages first and answer only "
                 "with new evidence. If a criterion needs a kind of source the corpus lacks, search_web for it; "
                 "if the web search brings nothing, choose blocked and name the criterion.")
# Added only to the prompt of a task with verified prerequisites, which now carry digests instead of whole answers.
PREREQUISITE_DIGEST_READER = ("prerequisite_answers gives each verified prerequisite answer as its question, summary, "
                              "outcome, limits and findings (id, statement, relation, references). Read a finding's "
                              "references before you cite its passages, and do not widen its relation or drop its limits.")
PREREQUISITE_DIGEST_REVIEW = ("prerequisite_answers gives each verified prerequisite answer as its question, summary, "
                              "outcome, limits and findings (id, statement, relation, references). A finding that builds "
                              "on one keeps its relation and limits; judge its support on the supplied passages.")
# Only for the review of a reworked answer after a failed one (the design rule for repeated reviews, 2026-10-02).
REVIEW_MEMORY = ("previous_review is your earlier verdict on this question's previous answer, which failed: its failed "
                 "criteria, its blocking findings and whether the sources were adequate. changed_finding_ids are the "
                 "findings that are new or changed since; may_fail is the scope the code set for this review. Say in "
                 "each criterion and finding receipt whether an earlier point is resolved. Only an earlier point that is "
                 "still unresolved, or a new defect in a changed finding or in a criterion resting on one, fails the "
                 "answer now; anything else you notice belongs in issues or limitations.")
UNREVIEWED_CRITERION = "The review returned no verdict on this criterion."


def normalise_criteria(rows, *, worse=None):
    """One entry per criterion index, deterministically: a repeated index collapses to its most
    conservative entry (``worse(new, kept)`` decides), otherwise the first one stays."""
    merged = {}
    for row in rows:
        kept = merged.get(row.index)
        if kept is None or (worse is not None and worse(row, kept)):
            merged[row.index] = row
    return [merged[index] for index in sorted(merged)]


def normalise_answer(answer):
    """Repeated criterion entries merge their findings; identical repeated findings collapse.

    Differing findings under one id stay for ``answer_errors`` to reject.
    """
    criteria = {}
    for row in answer.criteria:
        kept = criteria.get(row.index)
        criteria[row.index] = row if kept is None else kept.model_copy(
            update={"finding_ids": list(dict.fromkeys([*kept.finding_ids, *row.finding_ids]))})
    findings = []
    for finding in answer.findings:
        if finding not in findings:
            findings.append(finding)
    return answer.model_copy(update={"criteria": [criteria[i] for i in sorted(criteria)], "findings": findings})


def reference_defect(reference, reader, read_refs):
    """Why a cited reference is not read evidence, naming it so the next attempt corrects the citation."""
    if reference in reader.sources:
        return (f"evidence reference '{reference}' names a whole source; cite a read section as "
                "<source_id>#<section_id> instead.")
    if reference not in reader.lookup:
        return f"evidence reference '{reference}' is not a known section; use the exact references of the supplied sections."
    return f"evidence reference '{reference}' was not read for this question; cite a section from read_refs or read it first."


def well_formed_decision(decision, final):
    """A reader decision whose payload contradicts its action is re-asked with the defect named."""
    error = decision.payload_error()
    if error:
        raise AppError(error, code="invalid_model_output", status="blocked")


def answer_errors(answer, task, reader, read_refs):
    errors, ids = [], [f.id for f in answer.findings]
    if len(set(ids)) != len(ids):
        errors.append("Finding IDs must be unique within the answer.")
    if sorted({c.index for c in answer.criteria}) != list(range(len(task.acceptance))):
        errors.append("Answer every fixed acceptance criterion exactly once.")
    if any(not set(c.finding_ids) <= set(ids) for c in answer.criteria):
        errors.append("Criterion answers must refer to the answer's actual findings.")
    if answer.outcome == "supported_uncertainty" and (not answer.limits or not any(
            f.claim_contract and f.claim_contract.relation == "uncertainty" for f in answer.findings)):
        errors.append("Supported uncertainty requires a sourced uncertainty claim and explicit limits.")
    for finding in answer.findings:
        external = resolved = False
        for evidence in finding.evidence:
            entry = reader.lookup.get(evidence.reference)
            if evidence.reference not in read_refs or entry is None:
                errors.append(f"{finding.id}: {reference_defect(evidence.reference, reader, read_refs)}")
            elif not verbatim(evidence.excerpt, entry[2].text):
                # Named, because the bare rule did not stop the reader from shortening (Asimov, 2026-09-30).
                cut = (" It contains an ellipsis: quote one continuous passage, or give each part as its own evidence "
                       "entry." if "..." in evidence.excerpt or "…" in evidence.excerpt else "")
                errors.append(f"{finding.id}: quote is not verbatim in the cited section.{cut}")
            if entry and not is_idea(entry[0]):
                external = True
            resolved = resolved or entry is not None
        # An unresolved reference is reported above; only resolved citations can show notes-only support.
        if resolved and not external:
            errors.append(f"{finding.id}: user notes alone do not independently support a finding.")
    return errors


def normalise_review(review, task, *, final=False):
    """The review with repeated criterion, finding and source entries collapsed conservatively.

    A repeated criterion keeps its failing verdict. A missing or unknown criterion index is a shape
    defect that costs a repeated call, named so the call can correct it; on the ``final`` attempt an
    unknown index is dropped and a missing one fails with that reason instead of stopping the run.
    """
    criteria = normalise_criteria(review.criteria, worse=lambda new, kept: not new.passed and kept.passed)
    expected = list(range(len(task.acceptance)))
    if [c.index for c in criteria] != expected:
        if not final:
            given = {c.index for c in criteria}
            missing, unknown = sorted(set(expected) - given), sorted(given - set(expected))
            raise AppError("Antwortprüfung muss jedes Abschlusskriterium genau einmal bewerten."
                           + (f" Fehlend: {', '.join(map(str, missing))}." if missing else "")
                           + (f" Unbekannt: {', '.join(map(str, unknown))}." if unknown else ""),
                           code="invalid_question_review", status="blocked")
        kept = {c.index: c for c in criteria if c.index in expected}
        criteria = [kept.get(i) or CriterionVerdict(index=i, passed=False, reason=UNREVIEWED_CRITERION) for i in expected]
    return review.model_copy(update={"criteria": criteria, "finding_support": collapse_support(review.finding_support),
                                     "source_assessments": collapse_assessments(review.source_assessments)})


def review_passes(review, task):
    """The blocking tier that needs no passages: every fixed criterion passed and the sources are
    adequate for this kind of claim. ``supported`` and ``issues`` are limitations, not gates."""
    review = normalise_review(review, task)
    return review.source_adequacy and all(c.passed for c in review.criteria)


def review_points(review, task, findings, passages):
    """(blocking points, limitations) of a well-formed review under the tiered rule. A point is
    ``(kind, key, text)``: ``("finding", finding id, ...)``, ``("criterion", index, ...)`` or ``("sources", None, ...)``."""
    review = normalise_review(review, task)
    limitations, per_finding = [], {}
    support_errors(findings, review, passages, limitations=limitations, per_finding=per_finding)
    points = [("finding", finding_id, text) for finding_id, texts in per_finding.items() for text in texts]
    points += [("criterion", c.index, f"Criterion {c.index} not met: {c.reason}") for c in review.criteria if not c.passed]
    if not review.source_adequacy:
        points.append(("sources", None, "The sources are not adequate for this type of claim."))
    limitations += [{"finding_id": "", "kind": "issue", "text": issue} for issue in review.issues]
    limitations += [{"finding_id": "", "kind": "review_limitation", "text": text} for text in review.limitations]
    if not review.supported:
        limitations.append({"finding_id": "", "kind": "not_fully_supported",
                            "text": "Die Prüfung stuft die Antwort insgesamt als nicht vollständig gestützt ein."})
    return points, limitations


def review_outcome(review, task, findings, passages):
    """(blocking feedback, limitations) of a well-formed review under the tiered rule.

    Blocking: a failed criterion, inadequate sources, a contradicted or insufficient_context
    finding, an unsuitable source, a broken claim contract or unestablished independence. Every
    other observation passes the answer and is stored beside it as a limitation.
    """
    points, limitations = review_points(review, task, findings, passages)
    return [text for _, _, text in points], limitations


def review_scope(memory, answer):
    """From the second review of a reworked answer on, what may fail it, set by code (the design rule for repeated
    reviews): the points the earlier review failed, and the findings that are new or changed since together with
    the criteria resting on them. None for a first review, or after a pass."""
    if not memory:
        return None
    hashes = memory.get("finding_hashes", {})
    changed = [f.id for f in answer.findings if hashes.get(f.id) != digest(f.model_dump())]
    removed = set(hashes) - {f.id for f in answer.findings}
    earlier = [row["finding_id"] for row in memory.get("blocking_findings", [])]
    failed = [row["index"] for row in memory.get("failed_criteria", [])]
    grouped = memory.get("criterion_findings")

    def regrouped(criterion):
        # A criterion resting on a changed finding, or on another set of findings than before (one the rework removed
        # or moved), is judged afresh: its earlier pass was about other material. A memory written before the
        # criterion sets were kept (2026-10-02) cannot tell which criterion lost a removed finding, so all may fail.
        if set(criterion.finding_ids) & set(changed):
            return True
        if grouped is None:
            return bool(removed)
        return set(grouped.get(str(criterion.index), ())) != set(criterion.finding_ids)
    resting = [c.index for c in answer.criteria if regrouped(c)]
    return {"step": memory.get("step"), "changed_finding_ids": changed,
            "unchanged_finding_ids": [f.id for f in answer.findings if f.id not in changed],
            "may_fail": {"finding_ids": sorted(set(changed) | set(earlier)), "criteria": sorted(set(failed) | set(resting)),
                         "source_adequacy": bool(changed or removed) or not memory.get("source_adequacy", True)}}


def carry_earlier(verdict, memory, scope, passages):
    """Keep, in place, the earlier passing receipt of every point outside ``scope`` that this review now fails, and
    return the new objections as advisories. Before, every review of a reworked answer was a fresh one, and a point
    it had passed could fail the next round (the design rule: only unresolved earlier objections or new defects in
    changed material may block).

    A kept receipt names only passages supplied to this review. It may have assessed others then (another finding's,
    which the rework replaced), and support_errors refused such a receipt after the review was stored, on every
    resume (2026-10-02)."""
    earlier = AnswerReview.model_validate(memory["review"])
    supplied = {section["reference"] for source in passages for section in source["sections"]}
    receipts = {row.finding_id: row.model_copy(update={"references": [r for r in row.references if r in supplied]})
                for row in earlier.finding_support}
    verdicts = {c.index: c for c in earlier.criteria}
    allowed, advisories = scope["may_fail"], []

    def advisory(text):
        advisories.append({"finding_id": "", "kind": "advisory",
                           "text": "Neuer Hinweis der erneuten Prüfung zu unverändertem, zuvor bestandenem Material: " + text})

    support = []
    for row in verdict.finding_support:
        kept = receipts.get(row.finding_id)
        if blocks(row) and row.finding_id not in allowed["finding_ids"] and kept is not None and not blocks(kept):
            advisory(f"{row.finding_id}: {row.verdict}; {row.reason}")
            support.append(kept)
        else:
            support.append(row)
    verdict.finding_support = support
    criteria = []
    for criterion in verdict.criteria:
        kept = verdicts.get(criterion.index)
        if not criterion.passed and criterion.index not in allowed["criteria"] and kept is not None and kept.passed:
            advisory(f"Kriterium {criterion.index}: {criterion.reason}")
            criteria.append(kept)
        else:
            criteria.append(criterion)
    verdict.criteria = criteria
    if not verdict.source_adequacy and not allowed["source_adequacy"] and earlier.source_adequacy:
        advisory("Die Quellen gelten als nicht angemessen.")
        verdict.source_adequacy = True
    return advisories


def review_memory(step, answer, verdict, points):
    """What a failed review leaves for the review of the reworked answer: its verdict and the findings it saw."""
    support = {row.finding_id: row for row in verdict.finding_support}
    blocking = list(dict.fromkeys(key for kind, key, _ in points if kind == "finding"))
    return {"step": step, "finding_hashes": {f.id: digest(f.model_dump()) for f in answer.findings},
            # Which findings each criterion rested on, so a criterion that loses one is judged afresh (review_scope).
            "criterion_findings": {str(c.index): list(c.finding_ids) for c in answer.criteria},
            "review": verdict.model_dump(), "source_adequacy": verdict.source_adequacy,
            "failed_criteria": [{"index": c.index, "reason": c.reason} for c in verdict.criteria if not c.passed],
            "blocking_findings": [{"finding_id": key, "verdict": support[key].verdict, "reason": support[key].reason,
                                   "unsupported_clauses": support[key].unsupported_clauses}
                                  for key in blocking if key in support]}


def prerequisite_digests(spec, state):
    """The verified prerequisite answers as the reader and reviewer of a dependent task see them: question, summary,
    outcome, limits and each finding's id, statement, relation and cited references. The whole answers, with their
    quotes, contracts and criterion explanations, took 238,614 characters for one synthesis of 18 prerequisites, on
    every step (Ontologies, 2026-10-02); a finding's passages can still be read by its references. The verification
    binds the whole answers as before (``prerequisite_hashes`` from question_dependencies.prerequisite_answers)."""
    questions = {task["id"]: task["question"] for task in state["plan"]["tasks"]}
    rows = []
    for row in prerequisite_answers(spec, state):
        answer = row["answer"]
        rows.append({"task_id": row["task_id"], "question": questions.get(row["task_id"], ""),
                     "summary": answer["summary"], "outcome": answer.get("outcome", "supported_answer"),
                     "limits": answer.get("limits", []),
                     "findings": [{"id": f["id"], "statement": f["statement"],
                                   **({"relation": f["claim_contract"]["relation"]} if f.get("claim_contract") else {}),
                                   "references": list(dict.fromkeys(e["reference"] for e in f["evidence"]))}
                                  for f in answer["findings"]]})
    return rows


def access_gap_rows(spec, row):
    return [{"criterion": gap["criterion"], "criterion_text": spec.acceptance[gap["criterion"]], "source": gap["source"],
             "evidence": gap["evidence"], "editor_note": gap.get("reason", "")} for gap in row.get("access_gaps", [])]


SEARCH_BUDGET_REASON = ("Das Web-Suchbudget ist ausgeschöpft; diese konkrete Frage bleibt unbelegt. Ein höheres "
                        "Suchrundenlimit kann ausdrücklich genehmigt werden.")
# The run limits a sub-question's web search can stop at, as ``block_cause`` beside the German ``reason`` (since
# 2026-10-07): the Studio names the limit and offers its raise from this field, not from the reason's wording.
BLOCK_CAUSES = ("search_budget", "source_limit")


def budget_block(row, reason, cause=None):
    """The web search of ``row`` ended before it started: ``reason`` says why, ``cause`` names the run limit it hit
    (``BLOCK_CAUSES``), None for a limit of this sub-question alone. The reason text feeds the advisor's prompt and
    stays as it was; rows written before have no ``block_cause``."""
    row.update(reason=reason, outcome="budget_block")
    if cause:
        row["block_cause"] = cause
    else:
        row.pop("block_cause", None)


def search_folder(folder, queries):
    """Where a step's web search keeps its receipts. One step can search twice, first in the automatic recovery
    and then by the reader's own choice; a search for other queries than the one saved there gets a folder of its
    own, so it never takes the earlier search's receipts for its own (Asimov, 2026-10-01: morris_evaluate stopped on
    "Der gespeicherte Abrufbeleg passt nicht zum aktuellen Quellenindex")."""
    request = folder / "search_request.json"
    if not request.exists():
        return folder
    try:
        saved = json.loads(read_value(request)["prompt"].rsplit("\n", 1)[1]).get("queries")
    except (OSError, ValueError, KeyError, IndexError, AttributeError):
        return folder
    return folder if saved == list(queries) else folder / f"search_{digest(list(queries))[:8]}"


def settled_search(extra, maximum):
    """A search result within its order, as discovery truncates its own (research.discovery_stage): candidates without
    an admissible type are dropped, the first ``maximum`` of the rest stay, and what was dropped or left unrecorded is
    named in ``limitations``. A result that passed its check comes back unchanged."""
    admitted = [c for c in extra.candidates if admissible(c)]
    untyped = [c for c in extra.candidates if not admissible(c)]
    over = admitted[maximum:]
    limitations = list(extra.limitations)
    if untyped:
        limitations.append("Ohne zulässigen Quellentyp, nicht eingelesen: " + "; ".join(c.title for c in untyped))
    if over:
        limitations.append("Über dem Quellenauftrag dieser Suche, nicht eingelesen: " + "; ".join(c.title for c in over))
    if not extra.executed_queries:
        limitations.append("Die Suche hat ihre ausgeführten Suchanfragen nicht festgehalten.")
    if not (untyped or over or not extra.executed_queries or not extra.counterevidence):
        return extra
    return extra.model_copy(update={"candidates": admitted[:maximum], "limitations": limitations,
                                    "counterevidence": extra.counterevidence or "Nicht festgehalten."})


def read_context(reader, refs):
    """Exact passages for the given references, in stable order and without neighbours. All of them: a cap of
    2,000,000 characters dropped the rest without a word (2026-10-02); a prompt too large for its model is refused
    by the adapter's own prompt limit, and a check over a whole assembled dossier needs every passage."""
    return reader.read([ReaderWindow(reference=ref, before=0, after=0) for ref in dict.fromkeys(refs)],
                       max_chars=None)["context"]


# The read passages a reader prompt carries: the latest read in full (at most the 36 000 characters one read
# returns), then this question's earlier passages. With only the latest read in view, a reader could not quote
# what it had read two steps before and read on until its steps ran out (2026-09-30: Transformer attention_qkv
# read 34 sections in 10 steps and never answered, "not yet available as readable text").
VIEW_CHARS = 80_000


def visible_refs(reader, row, budget=VIEW_CHARS):
    """The passages a reader sees: the latest read, then earlier ones of this question, newest first, within
    ``budget`` characters. What no longer fits stays named in ``read_refs`` and can be read again."""
    refs, used = [], 0
    latest = set(row["current_refs"])
    for ref in dict.fromkeys([*row["current_refs"], *reversed(row["read_refs"])]):
        entry = reader.lookup.get(ref)
        if entry is None:
            continue
        size = len(entry[2].text)
        if ref not in latest and used + size > budget:
            continue
        refs.append(ref)
        used += size
    return refs


# The search results a reader prompt carries. Every local search adds its hits to the task's catalog; after
# several attempts that catalog alone outgrew a model window.
CANDIDATE_CHARS = 40_000


def open_candidates(catalog, read_refs, budget=CANDIDATE_CHARS):
    """Search results the reader has not read yet, newest search first, within ``budget`` characters.

    Read passages are named in ``read_refs`` already; an older search that no longer fits can be run again.
    The newest search with unread hits always stays, so the reader never loses its latest results."""
    read, kept, used = set(read_refs), [], 0
    for result in reversed(catalog):
        unread = [c for c in result.get("candidates", []) if c.get("reference") not in read]
        if not unread:
            continue
        entry = {**result, "candidates": unread}
        size = len(json.dumps(entry, ensure_ascii=False))
        if kept and used + size > budget:
            break
        kept.append(entry)
        used += size
    return kept[::-1]


class TaskResearchMixin:
    """Answer one fixed research task with bounded reading, search and review steps.

    Every method here edits only the row of its own task. The host provides ``guarded``,
    ``stopping`` and ``save`` so several tasks can run side by side under one ledger lock.
    """

    call_version = CALL_VERSION

    def catalog(self, spec, row, query=None, *, source_id="", offset=0, include_notes=False):
        results = [self.reader.search(q, key_terms=spec.key_terms, source_id=source_id, offset=offset,
                                      include_notes=include_notes) for q in ([query] if query else spec.queries)]
        row["catalog"] = (row["catalog"] + results)[-8:]
        # The receipt records what was searched and which passages surfaced; previews stay in the
        # bounded catalog above, so a long-running task does not grow the ledger by the hit texts.
        row.setdefault("search_receipts", []).extend({"lane": "local", "task_id": spec.id,
            "query": q, "source_id": source_id, "offset": offset, "total": result["total"],
            "next_offset": result["next_offset"], "candidate_refs": [c["reference"] for c in result["candidates"]]}
            for q, result in zip(([query] if query else spec.queries), results))
        row["candidate_refs"] = list(dict.fromkeys([*row.get("candidate_refs", []),
            *(c["reference"] for result in results for c in result["candidates"])]))
        return results

    def read(self, row, windows):
        # New is what the reader could not see: a passage pushed out of view (VIEW_CHARS) and read again counts,
        # as its prompt promised. Measured against all read_refs, that re-read was no progress, and a locked task
        # blocked on it (2026-10-02).
        seen = set(visible_refs(self.reader, row))
        result = self.reader.read(windows)
        new = references(result["context"]) - seen
        row["current_refs"] = list(dict.fromkeys(s["reference"] for source in result["context"] for s in source["sections"]))
        row["read_refs"] = list(dict.fromkeys([*row["read_refs"], *row["current_refs"]]))
        row["deferred"] = result["deferred"]
        return len(new)

    def seed(self, spec, row):
        results = self.catalog(spec, row)
        # A corpus probe already found sections that look like this gap. They are read
        # first, at no extra call, so a gap is never declared over an unread passage.
        pinned = [ref for probe in self.probes_for(spec.gap_ids) for ref in
                  (hit["reference"] for hit in probe["hits"]) if ref in self.reader.lookup]
        refs = list(dict.fromkeys([*pinned, *(c["reference"] for result in results
                                              for c in result["candidates"][:2])]))[:8]
        if refs:
            self.read(row, [ReaderWindow(reference=r, before=1, after=1) for r in refs])
        row["status"] = "researching"
        row["activity"] = "Passende Originalabschnitte gelesen; Antwort wird erarbeitet"
        self.save(f"Recherchefrage: {spec.question}")

    def unlock(self, row):
        """New passages or candidates arrived: the reader may answer again."""
        row["answer_locked"] = False

    def step_limit(self, row):
        """Steps this task may take: the run's limit plus what explicit new attempts granted on top."""
        return self.state["limits"]["steps_per_question"] + row.get("extra_steps", 0)

    def web_attempt_limit(self, row):
        return self.state["limits"]["web_attempts"] + row.get("extra_web_attempts", 0)

    def recover(self, spec, row):
        """Two automatic strategy changes before a concrete block: unread saved passages, then one web search.

        The web search is the safety net for a reader that chose blocked or kept answering instead of
        searching: a task is not blocked for missing evidence while it still has a web attempt and the
        run a search round. Reading saved passages first keeps the cheap step ahead of the expensive one.
        """
        row["fallbacks"] += 1
        if row["fallbacks"] > 2:
            return False
        if row["fallbacks"] == 1:
            candidates = [c["reference"] for result in row["catalog"] for c in result["candidates"]
                          if c["reference"] not in row["read_refs"]]
            if not candidates:
                for result in list(row["catalog"]):
                    if result["next_offset"] is not None:
                        extra = self.catalog(spec, row, result["query"], source_id=result["source_id"], offset=result["next_offset"])
                        candidates.extend(c["reference"] for item in extra for c in item["candidates"]
                                          if c["reference"] not in row["read_refs"])
            if candidates:
                self.read(row, [ReaderWindow(reference=r, before=1, after=1) for r in list(dict.fromkeys(candidates))[:8]])
                row["no_progress"] = 0
                self.unlock(row)
                row["feedback"] = ["The previous strategy made no progress. Additional candidate sections have now been read. "
                                   "Evaluate these passages against this question's fixed criteria; do not add new research goals."]
                return True
        if row["web_attempts"] >= self.web_attempt_limit(row):
            return False
        # Exhausted saved passages are a reason to search externally, not to
        # keep rewriting the same answer or ask the user to click again.
        self.progress(f"Neue Originalquelle für diese Frage wird gesucht: {spec.question}")
        novel = self.web_search(spec, row, spec.queries)
        row["actions"].append({"action": "recovery_search_web", "reason": "Saved passages exhausted",
                               "new_evidence": novel, "read_sections": len(row["read_refs"])})
        if novel:
            row["no_progress"] = 0
            self.unlock(row)
        elif not row["reason"]:
            row["reason"] = "Auch die gezielte Ersatzsuche lieferte keine neuen passenden Belege. " + " ".join(row["feedback"])
            # A reason started afresh carries no run limit (a reopening clears the reason, not block_cause).
            row.pop("block_cause", None)
        return novel

    def verify(self, spec, row):
        answer = QuestionAnswer.model_validate(row["answer"])
        refs = [e.reference for f in answer.findings for e in f.evidence]
        passages = read_context(self.reader, refs)
        prerequisites = prerequisite_digests(spec, self.state)
        text = TERMINOLOGY + EVIDENCE_INSTRUCTIONS + instructions("question_verify")
        payload = {"task": spec.model_dump(), "answer": answer.model_dump(), "evidence_profile": evidence_profile(spec),
                   "prerequisite_answers": prerequisites, "sources": passages}
        if prerequisites:
            text += " " + PREREQUISITE_DIGEST_REVIEW
        if row.get("access_gaps"):
            text += " " + ACCESS_GAP_REVIEW
            payload["accepted_access_gaps"] = access_gap_rows(spec, row)
        gaps = prerequisite_gaps(spec, self.state)
        if gaps:
            # Only then, so every other review prompt stays byte for byte as before.
            text += " " + PREREQUISITE_GAP_REVIEW
            payload["prerequisite_gaps"] = gaps
        searched = row.get("web_attempts", 0) >= 1
        if searched:
            text += " " + EVIDENCE_ABSENCE_REVIEW
        memory = row.get("review_memory")
        scope = review_scope(memory, answer)
        if scope:
            # The review of a reworked answer remembers the verdict that failed it, and its scope is set by code and
            # saved before the call (the design rule for repeated reviews, 2026-10-02).
            text += " " + REVIEW_MEMORY
            payload["previous_review"] = {"failed_criteria": memory["failed_criteria"],
                                          "blocking_findings": memory["blocking_findings"],
                                          "sources_adequate": memory["source_adequacy"],
                                          **{key: scope[key] for key in ("changed_finding_ids", "unchanged_finding_ids", "may_fail")}}
            if row.get("review_scope") != {"review_step": row["step"], **scope}:
                row["review_scope"] = {"review_step": row["step"], **scope}
                self.save(f"Erneute Antwortprüfung mit festgelegtem Umfang: {spec.question}")
        prompt = text + "\n" + json.dumps(payload, ensure_ascii=False)

        def well_formed(verdict, final):
            # Shape defects are corrected by a repeated call; substantive non-passes are feedback.
            # An assessment of a source no finding cites is dropped, not refused (2026-10-01: Asimov's asimov_evaluate
            # and Ontologies' t18 each assessed one extra source, and the refusals spent both automatic fresh attempts).
            # The final attempt is normalised instead of refused (settle_receipts): a shape defect never stops the run.
            review = scope_assessments(normalise_review(verdict, spec, final=final), answer.findings, passages)
            if final:
                settle_receipts(review, answer.findings, passages)
            support_errors(answer.findings, review, passages)

        # ".absence": a demanded kind of evidence the web search did not find is a result (2026-10-01); ".digest":
        # prerequisites as digests; ".memory": the earlier failed verdict and the code-set scope (both 2026-10-02).
        tag = "".join(part for part, used in ((".absence", searched), (".digest", bool(prerequisites)),
                                              (".memory", bool(scope))) if used)
        raw = self.call(self.task_folder(spec, row), f"review_{row['step']:03d}", AnswerReview, prompt,
                        validate=well_formed, tag=tag)
        # A review that passed its checks comes through the normalisation unchanged; only a final attempt is settled.
        unreviewed_criteria = sorted(set(range(len(spec.acceptance))) - {c.index for c in raw.criteria})
        verdict = scope_assessments(normalise_review(raw, spec, final=True), answer.findings, passages)
        notes, unreviewed = settle_receipts(verdict, answer.findings, passages)
        advisories = carry_earlier(verdict, memory, scope, passages) if scope else []
        points, limitations = review_points(verdict, spec, answer.findings, passages)
        blocking = [message for _, _, message in points]
        limitations += notes + advisories
        # The accepted gap travels with the verified answer whatever the answer's own limits say.
        limitations += [{"finding_id": "", "kind": "accepted_access_gap",
                         "text": f"Kriterium {gap['criterion']} ({gap['criterion_text']}): akzeptierte Zugangslücke, "
                                 f"{gap['source']} war nicht abrufbar ({gap['evidence']})."
                                 + (f" {gap['editor_note']}" if gap["editor_note"] else "")}
                        for gap in access_gap_rows(spec, row)]
        answer_hash = digest(answer.model_dump())
        omitted_only = blocking and all((kind == "finding" and key in unreviewed) or
                                        (kind == "criterion" and key in unreviewed_criteria) for kind, key, _ in points)
        if omitted_only and row.get("review_repeat") != answer_hash and row["step"] + 1 < self.step_limit(row):
            # The review left parts of the answer unjudged even on its last attempt: that says nothing about the
            # answer, so the same answer is reviewed once more at the next step instead of being locked.
            row.update(step=row["step"] + 1, review_repeat=answer_hash,
                       activity="Die Prüfung hat Teile der Antwort nicht bewertet; die Antwort wird erneut geprüft")
            self.save(f"Antwort wird erneut geprüft: {spec.question}")
            return
        if not blocking:
            row.update(status="verified", activity="Antwort und Belege geprüft", reason="", feedback=[], no_progress=0,
                       answer_locked=False, lock=None)
            # A pass ends the rework: a later reopening starts a fresh review, and an unchanged resubmission of
            # this answer is no longer let through (``resubmit``, set by an access gap or a revalidation). The cleared
            # reason takes its run limit (``block_cause``) along.
            for key in ("resubmit", "review_memory", "review_scope", "review_repeat", "block_cause"):
                row.pop(key, None)
            row["outcome"] = answer.outcome
            row["verification"] = {"answer_hash": digest(answer.model_dump()), "review": verdict.model_dump(),
                                   "evidence_version": EVIDENCE_VERSION, "limitations": limitations,
                                   "prerequisite_hashes": {a["task_id"]: a["answer_hash"] for a in prerequisite_answers(spec, self.state)},
                                   "support_summary": evidence_summary(answer.findings, verdict),
                                   "source_hashes": {self.reader.lookup[r][0].id: self.reader.lookup[r][0].text_hash for r in refs}}
            self.save(f"Teilfrage abgeschlossen: {spec.question}")
        else:
            # The answer is locked: the same passages cannot pass a second time, so the reader
            # must bring new evidence, search the web for the missing kind of source, or block.
            failed = [c.index for c in verdict.criteria if not c.passed]
            # A rejected correction becomes an ordinary reopening: new passages, a search, or a block.
            row.update(status="researching", draft_answer=row["answer"], answer=None, answer_locked=True, revise_only=False,
                       resubmit=None,
                       lock={"step": row["step"], "web_attempts": row["web_attempts"],
                             "criteria": [{"index": i, "text": spec.acceptance[i]} for i in failed],
                             "finding_ids": [r.finding_id for r in verdict.finding_support if blocks(r)],
                             "reasons": blocking},
                       feedback=[*blocking, *(item["text"] for item in limitations)],
                       no_progress=row["no_progress"] + 1, activity="Antwortprüfung verlangt neue Belege")
            # What the review of the reworked answer remembers (review_scope, carry_earlier).
            row["review_memory"] = review_memory(row["lock"]["step"], answer, verdict, points)
            self.save(f"Belege zu dieser Frage werden ergänzt: {spec.question}")

    def lock_block(self, spec, row):
        """Locked, the recoveries spent, still nothing new: the concrete gap goes to the operator now."""
        lock = row.get("lock") or {}
        unmet = "; ".join(f"Kriterium {c['index']}: {c['text']}" for c in lock.get("criteria", [])) or \
            "; ".join(lock.get("reasons", []))
        # Name only what actually happened: a web search that never ran is not a finding about the web.
        searched = ("auch die Websuche brachte keine neuen Belege" if row.get("web_attempts", 0) >= 1
                    else "die gespeicherten Quellen brachten keine neuen Belege")
        reason = f"Die unabhängige Prüfung hat die Antwort abgewiesen, und {searched}. Unerfüllt: {unmet}"
        if not row["reason"]:
            row.pop("block_cause", None)
        row.update(status="blocked", activity="Keine neuen Belege für die abgewiesenen Kriterien",
                   reason=reason + (" " + row["reason"] if row["reason"] else ""),
                   outcome="budget_block" if row.get("outcome") == "budget_block" else "evidence_block")

    def web_open(self, row):
        """Whether a web search could still load sources for this question: its own web attempts, the run's search
        rounds and the run's source limit. ``web_search`` refuses each of them; a reader still offered search_web
        after they ran out spent one to three calls learning that (2026-10-02)."""
        if row.get("web_attempts", 0) >= self.web_attempt_limit(row):
            return False
        budget_path = self.work / "budget.json"
        budget = json.loads(read_text(budget_path)) if budget_path.exists() else {}
        if budget.get("search_rounds", 0) >= self.limits().search_rounds:
            return False
        if self.attempts is None:
            self.attempts = restore_attempts(self.folder, self.index)
        return len(self.attempts) < self.limits().sources

    def allowed_actions(self, row):
        if row.get("revise_only"):
            # Reopened to correct wording against passages it already cites: the answer is the only step.
            return ["answer"]
        web = self.web_open(row)
        return [a for a in READER_ACTIONS if (a != "answer" or not row.get("answer_locked")) and (a != "search_web" or web)]

    def review_limitations(self):
        """Per verified task, what the independent review confirmed only with a stated limit."""
        return [{"task_id": spec["id"], "question": spec["question"],
                 "limitations": (self.state["tasks"][spec["id"]].get("verification") or {}).get("limitations", [])}
                for spec in self.state["plan"]["tasks"]
                if self.state["tasks"][spec["id"]]["status"] == "verified"
                and (self.state["tasks"][spec["id"]].get("verification") or {}).get("limitations")]

    def task_folder(self, spec, row):
        # The one derivation of the receipt layout, shared with the budget projection and the attempt restore.
        return attempt_folder(self.folder, spec.id, row)

    def receipt_index(self, result):
        """The index a download receipt describes: a whole copy (older receipts) or the index the ledger holds now
        plus the documents and failures this search added.

        ``base`` names the index the receipt was last written against. It differs from the ledger's when another
        task's search changed the index while this one downloaded outside the ledger lock (2026-10-02), and an
        interruption can fall in between. The receipt carries every document and failure of its own, so they join
        the current index: nothing of the other search is dropped and nothing foreign is taken. Before, a differing
        base stopped the resume."""
        if "index" in result:
            return SourceIndex.model_validate(result["index"])
        return self.merged_index([SourceDocument.model_validate(row) for row in result.get("added", [])],
                                 result.get("failures", []))

    def merged_index(self, added, failures):
        """The ledger's current index plus ``added`` documents and ``failures`` it does not hold yet."""
        restored = SourceIndex.model_validate(self.index.model_dump())
        known = {s.id for s in restored.sources}
        for document in added:
            if document.id not in known:
                restored.sources.append(document)
                known.add(document.id)
        restored.failures.extend(f for f in failures if f not in restored.failures)
        return restored

    def receipt_value(self, result, restored):
        if "index" in result:
            return {**result, "index": restored.model_dump()}
        base_sources, base_failures = len(self.index.sources), len(self.index.failures)
        return {**result, "base": self.state["index_hash"],
                "added": [s.model_dump(mode="json") for s in restored.sources[base_sources:]],
                "failures": restored.failures[base_failures:]}

    def web_search(self, spec, row, queries):
        budget_path = self.work / "budget.json"
        budget = json.loads(read_text(budget_path)) if budget_path.exists() else {}
        if self.attempts is None:
            self.attempts = restore_attempts(self.folder, self.index)
        source_limit = self.limits().sources
        remaining = source_limit - len(self.attempts)
        folder = search_folder(self.task_folder(spec, row) / f"step_{row['step']:03d}", queries)
        receipt = folder / "downloads.json"
        request_path = folder / "search_request.json"
        resuming = (folder / "search.json").exists() or request_path.exists()
        if not resuming and remaining <= 0:
            budget_block(row, f"Das Quellenlimit des Laufs ist erreicht ({source_limit} Quellen); eine Websuche könnte "
                              "keine neuen Quellen laden. Ein höheres Quellenlimit kann ausdrücklich genehmigt werden.",
                         "source_limit")
            return False
        if not resuming and row["web_attempts"] >= self.web_attempt_limit(row):
            budget_block(row, "Für diese Frage wurden die begrenzten zusätzlichen Quellenversuche ausgeschöpft.")
            return False
        # A completed search may still have downloads to restore even when the search budget is now exhausted.
        if budget.get("search_rounds", 0) >= self.limits().search_rounds and not (folder / "search.json").exists():
            budget_block(row, SEARCH_BUDGET_REASON, "search_budget")
            return False
        if (folder / "search.json").exists() and not request_path.exists():
            # Reconstruct the old prompt for pre-ledger cached searches only.
            # The actual downloads below still obey the corrected global limit.
            attempted = {s.url or s.raw_path for s in self.index.sources} | {f["source"] for f in self.index.failures}
            remaining = source_limit - len(attempted)
        maximum = min(4, remaining)
        prompt = (instructions("question_search", maximum=maximum) + " " + instructions("open_archives") + "\n" +
            json.dumps({"task": spec.model_dump(), "queries": queries, "known_sources": source_catalog(self.index),
                        "read_refs": row["read_refs"], "feedback": row["feedback"], "failures": self.index.failures,
                        # Not for an explain task: its primary works may be old (Ontologies, 2026-10-01: the search
                        # for the 2014 W3C standards reported "recency filter cannot be met").
                        **({"recency_months": self.config.recency_months, **research_day(self.config, self.work)}
                           if self.config.recency_months and spec.aim != "explain" else {})}, ensure_ascii=False))
        if request_path.exists():
            request = read_value(request_path)
            prompt, maximum = request["prompt"], request["maximum"]
        else:
            save_value(request_path, {"prompt": prompt, "maximum": maximum})

        def well_formed(extra, final):
            # The final attempt is settled below instead of refused: the frozen search prompt failed the same way on
            # every resume, and the run stood still behind a card that promised a new call (2026-10-02).
            if final:
                return
            if not extra.executed_queries or not extra.counterevidence:
                raise AppError("Search must record executed queries and counterevidence outcome.",
                               code="invalid_search_receipt", status="blocked")
            # Every typed source may join (practice docs, standards and critiques as well as primary works); a pointer
            # such as a social post is an idea source, which names what to find but never counts as found.
            if len(extra.candidates) > maximum or not all(admissible(c) for c in extra.candidates):
                raise AppError("Die Suche überschreitet ihren Quellenauftrag: höchstens so viele Kandidaten wie erlaubt, "
                               "jeder mit Quellentyp, keine Ideenquelle.", code="invalid_model_output", status="blocked")

        # ".archives": the search names open archives to prefer (open_archives, 2026-10-01); a resumed step keeps
        # the prompt it saved in search_request.json.
        try:
            extra = settled_search(self.call(folder, "search", QuestionSearch, prompt, search=True, validate=well_formed,
                                             tag=".archives"), maximum)
        except AppError as exc:
            # A question beside this one took the last search round between the check above and this call's
            # reservation (research.reserve_call), or a repeated attempt of this call needed one more. That is this
            # question's budget block, as the check would have found, not a stop of the whole run (as in advise_task).
            if exc.code != "research_budget_exhausted" or not self.calls_left():
                raise
            budget_block(row, SEARCH_BUDGET_REASON, "search_budget")
            return False
        result = read_value(receipt) if receipt.exists() else {
            "processed": [], "attempted": [], "base": self.state["index_hash"], "added": [], "failures": []}
        result.setdefault("attempted", [source_identity(url) for url in result["processed"]])
        restored = self.receipt_index(result)
        check_sources(self.root, restored)
        before = {s.text_hash for s in self.index.sources}
        # A receipt of the older form holds a whole index copy; its search keeps the ledger lock throughout. Every
        # other search keeps what it added in ``added`` and ``failed`` and downloads outside the lock (2026-10-02:
        # the other workers stood still for up to four downloads and parses of one search).
        whole = "index" in result
        added = [] if whole else [SourceDocument.model_validate(r) for r in result.get("added", [])]
        failed = [] if whole else list(result.get("failures", []))
        merged = self.state["index_hash"]

        def known_addresses(index):
            return {canonical_url(url) for s in index.sources for url in (s.url, s.final_url) if url}
        known = known_addresses(restored)
        for candidate in extra.candidates:
            if source_identity(candidate.url) in {source_identity(url) for url in result["processed"]}:
                continue
            address = source_identity(candidate.url)
            # The reservation stays under the lock: two tasks never fetch one address.
            if address not in known and not reserve_source(self.folder, receipt, result, self.attempts, address,
                                                           source_limit):
                continue
            try:
                address = canonical_url(candidate.url)
                if address not in known:
                    with nullcontext() if whole else self.unguarded():
                        doc, _ = import_source(candidate, self.root, self.work.name,
                                               **({"library": self.library} if self.library else {}))
                    if not whole and self.state["index_hash"] != merged:
                        # Another task's search set a new index meanwhile: build on it, so none of its sources is lost.
                        restored, merged = self.merged_index(added, failed), self.state["index_hash"]
                        known |= known_addresses(restored)
                    if doc.text_hash not in {s.text_hash for s in restored.sources} or any(
                            s.text_hash == doc.text_hash and not s.url for s in restored.sources):
                        restored.sources.append(doc)
                        added.append(doc)
                    else:
                        failure = {"source": candidate.url, "reason": "Identischer Quellentext bereits eingelesen.",
                                   "code": "duplicate_source"}
                        restored.failures.append(failure)
                        failed.append(failure)
                    known.add(address)
                    if doc.final_url:
                        known.add(canonical_url(doc.final_url))
            except (AppError, OSError, ValueError) as exc:
                failure = import_failure(candidate.url, exc)
                restored.failures.append(failure)
                failed.append(failure)
            processed = [*result["processed"], candidate.url]
            result = (self.receipt_value({**result, "processed": processed}, restored) if whole else
                      {**result, "processed": processed, "base": self.state["index_hash"],
                       "added": [d.model_dump(mode="json") for d in added], "failures": failed})
            save_value(receipt, result)
        if not whole and self.state["index_hash"] != merged:
            restored = self.merged_index(added, failed)
        self.set_index(restored)
        row.setdefault("search_receipts", []).append({"lane": "web", "task_id": spec.id,
            "requested_queries": queries, **extra.model_dump(exclude={"executed_queries"}),
            "model_reported_queries": extra.executed_queries,
            "observed_queries": read_value(folder / "search_metadata.json").get("observed_search_queries", [])
                if (folder / "search_metadata.json").exists() else [],
            "query_provenance": "Tool trace when available; model-reported queries are separately identified.",
            "downloads": str(receipt.relative_to(self.work)),
            "retrieval_failures": restored.failures})
        row["web_attempts"] += 1
        discovery = ResearchDiscovery.model_validate(self.state["discovery"])
        urls = {c.url for c in discovery.candidates}
        self.state["discovery"] = discovery.model_copy(update={"candidates": [*discovery.candidates,
            *(c for c in extra.candidates if c.url not in urls)]}).model_dump()
        results = self.catalog(spec, row)
        candidates = list(dict.fromkeys(c["reference"] for item in results for c in item["candidates"]
                         if c["reference"] not in row["read_refs"]))[:8]
        novel = self.read(row, [ReaderWindow(reference=r, before=1, after=1) for r in candidates]) if candidates else 0
        row["feedback"] = extra.limitations
        # Only this search's own documents are its progress, not those another task's search added meanwhile.
        own = restored.sources if whole else added
        return bool(novel or ({s.text_hash for s in own} - before))

    def plan_order(self, task_ids):
        wanted = set(task_ids)
        return [task["id"] for task in self.state["plan"]["tasks"] if task["id"] in wanted]

    def research_task(self, spec):
        """Answer one task under the ledger lock; the lock is released only inside each model call."""
        with self.guarded():
            self.state["active_tasks"] = self.plan_order([*self.state.get("active_tasks", []), spec.id])
            try:
                self.answer_task(spec, self.state["tasks"][spec.id])
            finally:
                self.state["active_tasks"] = [task for task in self.state.get("active_tasks", []) if task != spec.id]
            self.save()

    def answer_defects(self, spec, row, answer):
        """What keeps an answer from going to review: its fixed checks, and a resubmitted failed draft."""
        errors = answer_errors(answer, spec, self.reader, set(row["read_refs"]))
        if any(f.claim_contract is None for f in answer.findings):
            errors.append("Supply a structured claim_contract for every finding.")
        # ``resubmit`` names a draft that may come back unchanged: it failed under criteria an accepted access gap has
        # narrowed since, or it passed and only a prerequisite changed (question_dependencies.revalidate). verify clears
        # it with the next pass or failure, so it never lets a later rejected draft through.
        if (row["draft_answer"] and digest(answer.model_dump()) == digest(row["draft_answer"])
                and row.get("resubmit") != digest(row["draft_answer"])):
            errors.append("This identical answer already failed independent review. Address the specific feedback before resubmitting.")
        return errors

    def answer_task(self, spec, row):
        if row["status"] == "pending":
            self.seed(spec, row)
        while row["status"] in {"researching", "reviewing"}:
            if self.stopping.is_set():
                # Another task failed: this row keeps the step it saved last, and a resume continues there.
                return
            if row["status"] == "reviewing":
                self.verify(spec, row)
                continue
            if row["step"] >= self.step_limit(row):
                row.update(status="blocked", activity="Recherche ohne ausreichenden Abschluss beendet",
                           reason="Die begrenzten Lese- und Prüfversuche reichen für diese Frage nicht aus. " + " ".join(row["feedback"]))
                row.pop("block_cause", None)
                break
            if row["no_progress"] >= 2 and not row["pending"]:
                if not self.recover(spec, row):
                    if row.get("answer_locked"):
                        self.lock_block(spec, row)
                    else:
                        if not row["reason"]:
                            row.pop("block_cause", None)
                        row.update(status="blocked", activity="Keine neuen passenden Belege",
                                   reason=row["reason"] or "Wiederholte Schritte lieferten keine neuen Belege oder geprüfte Antwort.")
                    break
                self.save("Suchstrategie geändert – weitere gespeicherte Abschnitte werden geprüft")
            folder = self.task_folder(spec, row) / f"step_{row['step']:03d}"
            if row["pending"]:
                decision = ResearchDecision.model_validate(row["pending"])
            else:
                text = (TERMINOLOGY + TEACHING_SCOPE + EVIDENCE_INSTRUCTIONS +
                        instructions("question_reader", language=self.config.language))
                prerequisites = prerequisite_digests(spec, self.state)
                if prerequisites:
                    text += " " + PREREQUISITE_DIGEST_READER
                payload = {
                    "task": spec.model_dump(), "task_groups": self.state.get("task_groups", {}),
                    "evidence_profile": evidence_profile(spec),
                    "prerequisite_answers": prerequisites,
                    "allowed_actions": self.allowed_actions(row),
                    "answer_lock": row.get("lock") if row.get("answer_locked") else None,
                    "source_catalog": source_catalog(self.index),
                    "candidates": open_candidates(row["catalog"], row["read_refs"]),
                    "sources": read_context(self.reader, visible_refs(self.reader, row)), "read_refs": row["read_refs"],
                    "deferred": row.get("deferred", []), "feedback": row["feedback"],
                    "previous_answer": row["draft_answer"], "previous_actions": row["actions"][-4:],
                    "reopening": row["reopenings"][-1:]}
                if row.get("access_gaps"):
                    text += " " + ACCESS_GAP_READER
                    payload["accepted_access_gaps"] = access_gap_rows(spec, row)
                gaps = prerequisite_gaps(spec, self.state)
                if gaps:
                    text += " " + PREREQUISITE_GAP_READER
                    payload["prerequisite_gaps"] = gaps
                prompt = text + "\n" + json.dumps(payload, ensure_ascii=False)

                def checked(candidate, final, spec=spec, row=row):
                    """A deterministic answer defect is corrected at once, without spending a step; the last
                    permitted answer takes the ordinary path, where its defects become feedback. Before, the
                    reader often answered only in its last step, and a fixable quote blocked the question
                    (Asimov, 2026-09-30: piketty_explain)."""
                    well_formed_decision(candidate, final)
                    if candidate.action == "answer" and not final and not row.get("answer_locked"):
                        defects = self.answer_defects(spec, row, normalise_answer(candidate.answer))
                        if defects:
                            raise AppError("The answer fails its fixed checks; correct exactly these and answer again: "
                                           + " ".join(defects), code="invalid_model_output", status="blocked")
                # ".view": the reader sees this question's earlier passages too (VIEW_CHARS, 2026-09-30);
                # ".absence": a demanded kind of evidence the web search did not find is answered as a result (2026-10-01);
                # ".digest": verified prerequisites as digests, not whole answers (prerequisite_digests, 2026-10-02).
                decision = self.call(folder, "reader", ResearchDecision, prompt, validate=checked,
                                     tag=".view.absence" + (".digest" if prerequisites else ""))
                row["pending"] = decision.model_dump()
                row["activity"] = decision.reason
                self.save(f"{spec.question} · {decision.reason}")
            action = decision.action
            novel = False
            invalid = (action == "read" and any(w.reference not in self.reader.lookup for w in decision.windows)) or (
                action == "search_local" and any(q.source_id and q.source_id not in self.reader.sources for q in decision.searches))
            if invalid:
                row["feedback"] = ["Unknown source or section. Use exact IDs from the supplied catalog; no file paths."]
            elif action == "read":
                novel = bool(self.read(row, decision.windows))
            elif action == "search_local":
                before = set(row.get("candidate_refs", []))
                for query in decision.searches:
                    self.catalog(spec, row, query.query, source_id=query.source_id, offset=query.offset, include_notes=query.include_notes)
                novel = bool(set(row["candidate_refs"]) - before)
            elif action == "search_web":
                novel = self.web_search(spec, row, decision.web_queries)
            elif action == "answer" and row.get("answer_locked"):
                # The same passages already failed review: this answer is not sent to review and
                # counts as no progress; one such call at most before recovery or the block below.
                row["locked_answers"] = row.get("locked_answers", 0) + 1
                row["feedback"] = list(dict.fromkeys([LOCK_FEEDBACK, *row["feedback"]]))
            elif action == "answer":
                answer = normalise_answer(decision.answer)
                errors = self.answer_defects(spec, row, answer)
                if errors:
                    row["feedback"] = list(dict.fromkeys([*row["feedback"], *errors]))
                else:
                    row.update(answer=answer.model_dump(), status="reviewing", activity="Antwort wird unabhängig geprüft")
            elif action == "blocked":
                if self.recover(spec, row):
                    novel = True
                else:
                    # A web search the run's limits stopped says so next to the reader's own reason.
                    stopped = row["reason"] if row.get("outcome") == "budget_block" else ""
                    if not stopped:
                        row.pop("block_cause", None)
                    row.update(status="blocked", reason=" ".join(filter(None, [decision.reason, stopped])),
                               activity="Konkrete Beleglücke bleibt offen", outcome=(decision.block_kind or "evidence") + "_block")
            row["actions"].append({"action": action, "reason": decision.reason,
                                   "new_evidence": novel, "read_sections": len(row["read_refs"])})
            row["step"] += 1
            row["pending"] = None
            if novel:
                self.unlock(row)
            # A differently worded draft is not evidence of progress. Only new
            # passages/candidates or a passing independent review reset the count.
            if row["status"] != "reviewing":
                row["no_progress"] = 0 if novel else row["no_progress"] + 1
            # Locked, the web already searched in this attempt and still nothing new: the concrete
            # gap goes to the operator now instead of after further fruitless steps.
            if row["status"] == "researching" and row.get("answer_locked") and not novel and row["web_attempts"] >= 1:
                self.lock_block(spec, row)
            self.save(f"Recherchefrage: {spec.question}")
        if row["status"] == "blocked" and not row.get("outcome"):
            row["outcome"] = "search_block"
        self.settle_probes(spec, row)

    def probes_for(self, gap_ids):
        wanted = set(gap_ids)
        return [row for row in self.state.get("gap_probes", []) if row["gap_id"] in wanted]

    def settle_probes(self, spec, row):
        """Record, per gap this task owned, whether its corpus hits were actually read."""
        wanted, read = set(spec.gap_ids), row["read_refs"]
        resolved = row["status"] == "verified"
        self.state["gap_probes"] = [settle(probe, read_refs=read, resolved=resolved)
                                    if probe["gap_id"] in wanted else probe
                                    for probe in self.state.get("gap_probes", [])]
