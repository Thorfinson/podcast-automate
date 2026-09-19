"""Per-question research loop: seeded reading, reader decisions, web search and independent review.

Mixed into ``QuestionResearch``; every method works on ``self.state["tasks"][task_id]`` rows whose
keys are defined by ``question_scope.pending_task``.
"""
from __future__ import annotations

import json
from collections import Counter

from .editorial import TERMINOLOGY, TEACHING_SCOPE
from .errors import AppError
from .evidence_models import EVIDENCE_VERSION
from .prompts import instructions
from .question_dependencies import prerequisite_answers
from .question_sources import reserve_source, restore_attempts, source_identity
from .research_evidence import EVIDENCE_INSTRUCTIONS, PROFILES, evidence_summary, support_errors
from .research_gap_probe import settle
from .research_ledger import check_sources, read_value, save_value
from .research_models import ResearchDiscovery, SourceDocument, SourceIndex
from .research_reader import source_catalog
from .research_retrieval import references
from .research_tasks import AnswerReview, QuestionAnswer, QuestionSearch, ReaderWindow, ResearchDecision
from .sources import canonical_url, clean, import_failure, import_source
from .storage import digest


def answer_errors(answer, task, reader, read_refs):
    errors, ids = [], [f.id for f in answer.findings]
    if len(set(ids)) != len(ids):
        errors.append("Finding IDs must be unique within the answer.")
    if Counter(c.index for c in answer.criteria) != Counter(range(len(task.acceptance))):
        errors.append("Answer every fixed acceptance criterion exactly once.")
    if any(not set(c.finding_ids) <= set(ids) for c in answer.criteria):
        errors.append("Criterion answers must refer to the answer's actual findings.")
    if answer.outcome == "supported_uncertainty" and (not answer.limits or not any(
            f.claim_contract and f.claim_contract.relation == "uncertainty" for f in answer.findings)):
        errors.append("Supported uncertainty requires a sourced uncertainty claim and explicit limits.")
    for finding in answer.findings:
        external = False
        for evidence in finding.evidence:
            entry = reader.lookup.get(evidence.reference)
            if evidence.reference not in read_refs or entry is None:
                errors.append(f"{finding.id}: evidence must reference a section actually read for this question.")
            elif clean(evidence.excerpt) not in clean(entry[2].text):
                errors.append(f"{finding.id}: quote is not verbatim in the cited section.")
            if entry and entry[0].url and entry[0].final_url:
                external = True
        if not external:
            errors.append(f"{finding.id}: user notes alone do not independently support a finding.")
    return errors


def review_passes(review, task):
    if Counter(c.index for c in review.criteria) != Counter(range(len(task.acceptance))):
        raise AppError("Antwortprüfung muss jedes Abschlusskriterium genau einmal bewerten.",
                       code="invalid_question_review", status="blocked")
    return review.supported and review.source_adequacy and not review.issues and all(c.passed for c in review.criteria)


def read_context(reader, refs):
    """Exact passages for the given references, in stable order and without neighbours."""
    return reader.read([ReaderWindow(reference=ref, before=0, after=0) for ref in dict.fromkeys(refs)],
                       max_chars=2_000_000)["context"]


class TaskResearchMixin:
    """Answer one fixed research task at a time with bounded reading, search and review steps."""

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
        result = self.reader.read(windows)
        new = references(result["context"]) - set(row["read_refs"])
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

    def recover(self, spec, row):
        """One automatic strategy change, then a concrete block instead of an endless loop."""
        row["fallbacks"] += 1
        if row["fallbacks"] > 1:
            return False
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
            row["feedback"] = ["The previous strategy made no progress. Additional candidate sections have now been read. "
                               "Evaluate these passages against this question's fixed criteria; do not add new research goals."]
            return True
        # Exhausted saved passages are a reason to search externally, not to
        # keep rewriting the same answer or ask the user to click again.
        self.progress(f"Neue Originalquelle für diese Frage wird gesucht: {spec.question}")
        novel = self.web_search(spec, row, spec.queries)
        row["actions"].append({"action": "recovery_search_web", "reason": "Saved passages exhausted",
                               "new_evidence": novel, "read_sections": len(row["read_refs"])})
        if novel:
            row["no_progress"] = 0
        elif not row["reason"]:
            row["reason"] = "Auch die gezielte Ersatzsuche lieferte keine neuen passenden Belege. " + " ".join(row["feedback"])
        return novel

    def verify(self, spec, row):
        answer = QuestionAnswer.model_validate(row["answer"])
        refs = [e.reference for f in answer.findings for e in f.evidence]
        passages = read_context(self.reader, refs)
        prompt = (TERMINOLOGY + EVIDENCE_INSTRUCTIONS +
            instructions("question_verify") + "\n" + json.dumps({
                "task": spec.model_dump(), "answer": answer.model_dump(),
                "evidence_profile": PROFILES[spec.kind],
                "prerequisite_answers": prerequisite_answers(spec, self.state),
                "sources": passages}, ensure_ascii=False))

        def well_formed(verdict, final):
            # Shape defects are corrected by a repeated call; substantive non-passes are feedback.
            review_passes(verdict, spec)
            support_errors(answer.findings, verdict, passages)

        verdict = self.call(self.task_folder(spec, row),
                            f"review_{row['step']:03d}", AnswerReview, prompt, validate=well_formed)
        semantic_errors = support_errors(answer.findings, verdict, passages)
        if review_passes(verdict, spec) and not semantic_errors:
            row.update(status="verified", activity="Antwort und Belege geprüft", reason="", feedback=[], no_progress=0)
            row["outcome"] = answer.outcome
            row["verification"] = {"answer_hash": digest(answer.model_dump()), "review": verdict.model_dump(),
                                   "evidence_version": EVIDENCE_VERSION,
                                   "prerequisite_hashes": {a["task_id"]: a["answer_hash"] for a in prerequisite_answers(spec, self.state)},
                                   "support_summary": evidence_summary(answer.findings, verdict),
                                   "source_hashes": {self.reader.lookup[r][0].id: self.reader.lookup[r][0].text_hash for r in refs}}
            self.save(f"Teilfrage abgeschlossen: {spec.question}")
        else:
            row.update(status="researching", draft_answer=row["answer"], answer=None,
                       feedback=[*semantic_errors, *verdict.issues, *(c.reason for c in verdict.criteria if not c.passed),
                                 *([] if verdict.supported else ["The original passages do not support the full answer."]),
                                 *([] if verdict.source_adequacy else ["The sources are not adequate for this type of claim."])],
                       no_progress=row["no_progress"] + 1, activity="Antwortprüfung verlangt eine gezielte Ergänzung")
            self.save(f"Belege zu dieser Frage werden ergänzt: {spec.question}")

    def task_folder(self, spec, row):
        path = self.folder / "tasks" / spec.id / f"attempt_{len(row['reopenings'])}"
        return path / f"dependency_{row['dependency_revision']}" if row.get("dependency_revision") else path

    def receipt_index(self, result):
        """The index a download receipt describes: a whole copy (older receipts) or the current index
        plus the documents this search added. The receipt must build on the index the ledger holds."""
        if "index" in result:
            return SourceIndex.model_validate(result["index"])
        if result.get("base") != self.state["index_hash"]:
            raise AppError("Der gespeicherte Abrufbeleg passt nicht zum aktuellen Quellenindex.",
                           code="invalid_research_checkpoint", status="blocked")
        restored = SourceIndex.model_validate(self.index.model_dump())
        known = {s.id for s in restored.sources}
        for row in result.get("added", []):
            document = SourceDocument.model_validate(row)
            if document.id not in known:
                restored.sources.append(document)
                known.add(document.id)
        restored.failures.extend(f for f in result.get("failures", []) if f not in restored.failures)
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
        budget = json.loads(budget_path.read_text(encoding="utf-8")) if budget_path.exists() else {}
        if self.attempts is None:
            self.attempts = restore_attempts(self.folder, self.index)
        remaining = self.config.research_limits.sources - len(self.attempts)
        folder = self.task_folder(spec, row) / f"step_{row['step']:03d}"
        receipt = folder / "downloads.json"
        request_path = folder / "search_request.json"
        resuming = (folder / "search.json").exists() or request_path.exists()
        if not resuming and (row["web_attempts"] >= self.state["limits"]["web_attempts"] or remaining <= 0):
            row["reason"] = "Für diese Frage wurden die begrenzten zusätzlichen Quellenversuche ausgeschöpft."
            row["outcome"] = "budget_block"
            return False
        # A completed search may still have downloads to restore even when the search budget is now exhausted.
        if budget.get("search_rounds", 0) >= self.limits().search_rounds and not (folder / "search.json").exists():
            row["reason"] = ("Das Web-Suchbudget ist ausgeschöpft; diese konkrete Frage bleibt unbelegt. Ein höheres "
                             "Suchrundenlimit kann ausdrücklich genehmigt werden.")
            row["outcome"] = "budget_block"
            return False
        if (folder / "search.json").exists() and not request_path.exists():
            # Reconstruct the old prompt for pre-ledger cached searches only.
            # The actual downloads below still obey the corrected global limit.
            attempted = {s.url or s.raw_path for s in self.index.sources} | {f["source"] for f in self.index.failures}
            remaining = self.config.research_limits.sources - len(attempted)
        maximum = min(4, remaining)
        prompt = (instructions("question_search", maximum=maximum) + "\n" +
            json.dumps({"task": spec.model_dump(), "queries": queries, "known_sources": source_catalog(self.index),
                        "read_refs": row["read_refs"], "feedback": row["feedback"], "failures": self.index.failures}, ensure_ascii=False))
        if request_path.exists():
            request = read_value(request_path)
            prompt, maximum = request["prompt"], request["maximum"]
        else:
            save_value(request_path, {"prompt": prompt, "maximum": maximum})

        def well_formed(extra, final):
            if not extra.executed_queries or not extra.counterevidence:
                raise AppError("Search must record executed queries and counterevidence outcome.",
                               code="invalid_search_receipt", status="blocked")
            if len(extra.candidates) > maximum or any(not c.primary_source for c in extra.candidates):
                raise AppError("Die Suche überschreitet ihren Quellenauftrag.", code="invalid_model_output", status="blocked")

        extra = self.call(folder, "search", QuestionSearch, prompt, search=True, validate=well_formed)
        result = read_value(receipt) if receipt.exists() else {
            "processed": [], "attempted": [], "base": self.state["index_hash"], "added": [], "failures": []}
        result.setdefault("attempted", [source_identity(url) for url in result["processed"]])
        restored = self.receipt_index(result)
        check_sources(self.root, restored)
        before = {s.text_hash for s in self.index.sources}
        known = {canonical_url(url) for s in restored.sources for url in (s.url, s.final_url) if url}
        for candidate in extra.candidates:
            if source_identity(candidate.url) in {source_identity(url) for url in result["processed"]}:
                continue
            address = source_identity(candidate.url)
            if address not in known and not reserve_source(self.folder, receipt, result, self.attempts, address,
                                                           self.config.research_limits.sources):
                continue
            try:
                address = canonical_url(candidate.url)
                if address not in known:
                    doc, _ = import_source(candidate, self.root, self.work.name)
                    if doc.text_hash not in {s.text_hash for s in restored.sources} or any(
                            s.text_hash == doc.text_hash and not s.url for s in restored.sources):
                        restored.sources.append(doc)
                    else:
                        restored.failures.append({"source": candidate.url, "reason": "Identischer Quellentext bereits eingelesen.",
                                                  "code": "duplicate_source"})
                    known.add(address)
                    if doc.final_url:
                        known.add(canonical_url(doc.final_url))
            except (AppError, OSError, ValueError) as exc:
                restored.failures.append(import_failure(candidate.url, exc))
            result = self.receipt_value({**result, "processed": [*result["processed"], candidate.url]}, restored)
            save_value(receipt, result)
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
        return bool(novel or ({s.text_hash for s in restored.sources} - before))

    def research_task(self, spec):
        row = self.state["tasks"][spec.id]
        self.state["active_task"] = spec.id
        if row["status"] == "pending":
            self.seed(spec, row)
        while row["status"] in {"researching", "reviewing"}:
            if row["status"] == "reviewing":
                self.verify(spec, row)
                continue
            if row["step"] >= self.state["limits"]["steps_per_question"]:
                row.update(status="blocked", activity="Recherche ohne ausreichenden Abschluss beendet",
                           reason="Die begrenzten Lese- und Prüfversuche reichen für diese Frage nicht aus. " + " ".join(row["feedback"]))
                break
            if row["no_progress"] >= 2 and not row["pending"]:
                if not self.recover(spec, row):
                    row.update(status="blocked", activity="Keine neuen passenden Belege",
                               reason=row["reason"] or "Wiederholte Schritte lieferten keine neuen Belege oder geprüfte Antwort.")
                    break
                self.save("Suchstrategie geändert – weitere gespeicherte Abschnitte werden geprüft")
            folder = self.task_folder(spec, row) / f"step_{row['step']:03d}"
            if row["pending"]:
                decision = ResearchDecision.model_validate(row["pending"])
            else:
                prompt = (TERMINOLOGY + TEACHING_SCOPE + EVIDENCE_INSTRUCTIONS +
                    instructions("question_reader", language=self.config.language) + "\n" + json.dumps({
                        "task": spec.model_dump(), "task_groups": self.state.get("task_groups", {}),
                        "evidence_profile": PROFILES[spec.kind],
                        "prerequisite_answers": prerequisite_answers(spec, self.state),
                        "source_catalog": source_catalog(self.index), "candidates": row["catalog"],
                        "sources": read_context(self.reader, row["current_refs"]), "read_refs": row["read_refs"],
                        "deferred": row.get("deferred", []), "feedback": row["feedback"],
                        "previous_answer": row["draft_answer"], "previous_actions": row["actions"][-4:],
                        "reopening": row["reopenings"][-1:]}, ensure_ascii=False))
                decision = self.call(folder, "reader", ResearchDecision, prompt)
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
            elif action == "answer":
                errors = answer_errors(decision.answer, spec, self.reader, set(row["read_refs"]))
                if any(f.claim_contract is None for f in decision.answer.findings):
                    errors.append("Supply a structured claim_contract for every finding.")
                if row["draft_answer"] and digest(decision.answer.model_dump()) == digest(row["draft_answer"]):
                    errors.append("This identical answer already failed independent review. Address the specific feedback before resubmitting.")
                if errors:
                    row["feedback"] = list(dict.fromkeys([*row["feedback"], *errors]))
                else:
                    row.update(answer=decision.answer.model_dump(), status="reviewing", activity="Antwort wird unabhängig geprüft")
            elif action == "blocked":
                if self.recover(spec, row):
                    novel = True
                else:
                    row.update(status="blocked", reason=decision.reason, activity="Konkrete Beleglücke bleibt offen",
                               outcome=(decision.block_kind or "evidence") + "_block")
            row["actions"].append({"action": action, "reason": decision.reason,
                                   "new_evidence": novel, "read_sections": len(row["read_refs"])})
            row["step"] += 1
            row["pending"] = None
            # A differently worded draft is not evidence of progress. Only new
            # passages/candidates or a passing independent review reset the count.
            if row["status"] != "reviewing":
                row["no_progress"] = 0 if novel else row["no_progress"] + 1
            self.save(f"Recherchefrage: {spec.question}")
        if row["status"] == "blocked" and not row.get("outcome"):
            row["outcome"] = "search_block"
        self.settle_probes(spec, row)
        self.save()

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
