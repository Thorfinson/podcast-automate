"""Dossier synthesis from verified answers, the final audit and objection routing.

Mixed into ``QuestionResearch``. Composition edits only findings owned by dirty tasks; the audit
binds every objection to a fixed criterion or quality rule before a task may be reopened.
Objections that only concern explicitly accepted gaps are recorded, never researched again.
"""
from __future__ import annotations

import json
from collections import Counter

from .editorial import TERMINOLOGY, TEACHING_SCOPE
from .errors import AppError
from .evidence_models import EVIDENCE_VERSION
from .prompts import instructions
from .question_answering import read_context
from .question_dependencies import invalidate_dependents
from .question_ownership import editable_findings, finding_owners, preserve_unrelated
from .research_gap_probe import coverage_terms, gap_id, probe, settle
from .research_evidence import (EVIDENCE_INSTRUCTIONS, SYNTHESIS_EVIDENCE_RULE, SYNTHESIS_INSTRUCTIONS,
                                evidence_summary, support_errors, validate_objection, validate_synthesis)
from .research_ledger import VERSION, save_value
from .research_models import ResearchDiscovery, ResearchDossier
from .research_patches import edit_dossier, patch_prompt, repair_references
from .research_quality import ResearchAssessment, check_assessment, quality_brief, quality_report, render_quality
from .research_retrieval import merge_context, references, select_context
from .research_review import ROUTING_INSTRUCTIONS, SourceReview, needs_research
from .research_tasks import QuestionPlan, ReopenPlan
from .storage import atomic_text, digest, write_json

# One Claude window (claude_code.PROMPT_LIMIT_CHARS, 300 000 characters) bounds every synthesis call.
# The instructions, the JSON framing and the answer share it, so the material a call carries stays
# under this budget; a run with more verified material composes and audits in bounded parts.
PROMPT_BUDGET_CHARS = 240_000
# The generation of the composing prompts a run was started with. A rule added to those prompts
# applies from the next generation on; earlier runs keep their prompt text and their receipts.
PROMPT_GENERATION = 2
# The dossier a call writes is about as long as the verified answers it integrates, and the CLI cuts
# an answer at its output cap (claude_code.MAX_OUTPUT_TOKENS; 32 000 tokens without that setting,
# roughly 150 000 characters of German JSON). So the answers one composing call integrates stay
# under this budget as well, whatever the prompt would still hold.
ANSWER_BUDGET_CHARS = 80_000
# Closed questions a resumed run integrates per patch at most; fewer when their patch would not fit.
SEED_BATCH_SIZE = 4
PART_NOTE = (" This is one part of the review of this dossier: finding_support only for the supplied findings, "
             "source_assessments only for the supplied sources, issues only about the supplied findings, and "
             "objection_checks for exactly the supplied open_objections. other_findings lists the rest of the "
             "dossier as context; do not assess it.")


def chars(value):
    return len(json.dumps(value, ensure_ascii=False))


def answer_chars(items):
    """The material a composing call turns into dossier findings: the answers, not their passages."""
    return sum(chars(item["answer"]) for item in items)


def compact_baseline(answers):
    """The verified answers as the audit needs them: the qualifications to preserve, not the evidence receipts."""
    return [{"task": {key: item["task"].get(key) for key in ("id", "question", "kind", "acceptance", "question_ids")},
             "summary": item["answer"]["summary"], "limits": item["answer"].get("limits", []),
             "review_limitations": item.get("review_limitations", [])} for item in answers]


def source_outline(context):
    """Sources without their passages: identity and pages, for a call that judges coverage, not wording."""
    return [{**{key: value for key, value in source.items() if key != "sections"},
             "sections": [{"reference": s["reference"], "page": s.get("page")} for s in source["sections"]]}
            for source in context]


# What the assessment reads of a support receipt and a source assessment when the dossier is large:
# the verdicts and identities it judges independence and evidence by, not the review's prose.
SUPPORT_VERDICT_FIELDS = ("finding_id", "verdict", "unsupported_clauses", "suitability", "empirical_status",
                          "independent_evidence_refs")
SOURCE_IDENTITY_FIELDS = ("source_id", "roles", "work_id", "evidence_family", "independence", "method",
                          "research_group", "population", "geography", "period")


def source_identity(context):
    """Sources as identity only, for a call that names them after the review has judged them."""
    return [{key: source.get(key) for key in ("source_id", "title", "url")} for source in context]


# What the routing matches an objection against when the dossier is large: the task's question and
# criteria, the answer's summary, the finding's statement, the objection's anchor. Never receipts.
ROUTING_TASK_FIELDS = ("id", "question", "kind", "acceptance", "question_ids", "requirement_ids", "gap_ids",
                       "finding_ids", "depends_on")
ROUTING_OBJECTION_FIELDS = ("id", "rule", "task_id", "criterion_index", "finding_ids", "reason", "closure_condition",
                            "resolution")


# Objections routed per call when there are many: the answer names an anchor per routed task and
# objection, so it grows with the objections, not with the dossier. One call for all 116 objections
# of the 20 September run produced nothing in 30 minutes.
ROUTING_PART_SIZE = 25
ROUTING_PART_NOTE = (" This is one part of the routing: route exactly the supplied objections, whose indices count "
                     "from 0 within this part; the other objections of this audit are routed in other parts.")


def split_objections(objections, size):
    return [objections[start:start + size] for start in range(0, len(objections), size)]


def merge_route_plans(plans, size):
    """The parts' routes as one plan, their indices moved back into the audit's objection list."""
    return ReopenPlan(routes=[route.model_copy(update={"index": route.index + number * size})
                              for number, plan in enumerate(plans) for route in plan.routes])


def compact_routing_material(tasks, answers, review, dossier):
    """Tasks, answers, anchored issues and dossier as the objection routing reads them in a large run."""
    data = dossier.model_dump()
    return {"tasks": [{k: v for k, v in t.model_dump().items() if k in ROUTING_TASK_FIELDS} for t in tasks],
            "anchored_issues": [{k: v for k, v in i.objection.model_dump().items() if k in ROUTING_OBJECTION_FIELDS}
                                for i in review.issues if i.objection],
            "answers": {tid: None if not answer else {"summary": answer.get("summary"), "limits": answer.get("limits", []),
                                                       "finding_ids": [f["id"] for f in answer.get("findings", [])]}
                        for tid, answer in answers.items()},
            "dossier": {"topic": data["topic"],
                        "findings": [{k: f[k] for k in ("id", "kind", "statement")} for f in data["findings"]],
                        "coverage": data["coverage"], "open_questions": data["open_questions"],
                        "synthesis": [{k: r[k] for k in ("id", "finding_ids", "relation", "resolution")}
                                      for r in data["synthesis"]]}}


def compact_review_instructions(review, targets):
    """The review as a correction of ``targets`` needs it: its issues, the closure checks and the
    receipts of the corrected findings; source assessments and limitations stay in the review."""
    return {"issues": [i.model_dump() for i in review.issues],
            "objection_checks": [c.model_dump() for c in review.objection_checks],
            "finding_support": [s.model_dump() for s in review.finding_support if s.finding_id in targets]}


def compact_assessment_material(dossier, review):
    """The dossier and the review receipts for an assessment that would not fit one window otherwise.

    The findings keep everything the assessment judges (statement, illustration, claim contract,
    coverage, synthesis) and only their evidence references; the excerpts were verified by the
    review. The receipts keep their verdicts and identities without reasons and rationales, and a
    source that several review parts assessed gets one row with their roles united; the parts'
    full assessments stay in the review and in the report.
    """
    data = dossier.model_dump()
    for finding in data["findings"]:
        finding["evidence"] = [{"reference": e["reference"]} for e in finding["evidence"]]
    assessments = {}
    for assessment in review.source_assessments:
        row = {k: v for k, v in assessment.model_dump().items() if k in SOURCE_IDENTITY_FIELDS}
        known = assessments.get(assessment.source_id)
        if known is None:
            assessments[assessment.source_id] = row
        else:
            known["roles"] = list(dict.fromkeys(known["roles"] + row["roles"]))
    return {"dossier": data,
            "finding_support": [{k: v for k, v in r.model_dump().items() if k in SUPPORT_VERDICT_FIELDS}
                                for r in review.finding_support],
            "source_assessments": list(assessments.values())}


class SynthesisMixin:
    """Compose, audit and, when the audit fails, reopen only the concretely challenged tasks."""

    @property
    def prompt_generation(self):
        return int((self.state or {}).get("prompt_generation", 1))

    @property
    def prompt_tag(self):
        """The suffix of every call tag from the second prompt generation on."""
        return f".g{self.prompt_generation}" if self.prompt_generation >= 2 else ""

    def synthesis_rule(self):
        return SYNTHESIS_EVIDENCE_RULE if self.prompt_generation >= 2 else ""

    def compose(self):
        discovery = ResearchDiscovery.model_validate(self.state["discovery"])
        plan = QuestionPlan.model_validate(self.state["plan"])
        accepted = self.accepted_summary()
        answers = [{"task": task.model_dump(), "answer": self.state["tasks"][task.id]["answer"],
                    "evidence_review": self.state["tasks"][task.id].get("verification", {}).get("review"),
                    "review_limitations": (self.state["tasks"][task.id].get("verification") or {}).get("limitations", [])}
                   for task in plan.tasks if task.id not in accepted]
        gaps = [{"task_id": tid, **gap} for tid, gap in accepted.items()]
        refs = [e["reference"] for item in answers for f in item["answer"]["findings"] for e in f["evidence"]]
        context = read_context(self.reader, refs)
        seed = self.state["seed_dossier"]
        folder = self.folder / "synthesis" / f"audit_{self.state['audit_round']:02d}"
        self.state["phase"] = "synthesis"
        self.save("Geprüfte Antworten werden zu einem zusammenhängenden Dossier verbunden")
        if seed:
            dossier = ResearchDossier.model_validate(seed)
            owners = finding_owners(dossier, plan.tasks, self.state["tasks"], self.state.get("finding_owners"))
            seed_refs = [e.reference for f in dossier.findings for e in f.evidence]
            context = merge_context(context, read_context(self.reader, seed_refs))
            # Small batches of closed questions, each editing only its related original findings.
            dirty = [item for item in answers if item["task"]["id"] in self.state["dirty_tasks"]]
            def rules_for(batch):
                rules = {"verified_answers": batch,
                         "assigned_gaps": {gid: self.state["gaps"][gid] for item in batch for gid in item["task"]["gap_ids"]},
                         "all_closed_tasks": [{"task": item["task"], "answer": item["answer"]["summary"]} for item in answers],
                         "rule": "Integrate these independently verified answers. Resolve old open questions explicitly "
                         "when their assigned tasks fully answer them; do not carry stale editorial to-dos as evidence gaps. "
                         "Only close a compound question when ALL its obligations are answered. Preserve all unrelated findings."
                         + self.synthesis_rule()}
                if gaps:
                    rules["accepted_gaps"] = {"rule": instructions("accepted_gaps"), "tasks": gaps}
                return rules

            def passages(batch):
                return read_context(self.reader, [e["reference"] for item in batch
                                                  for f in item["answer"]["findings"] for e in f["evidence"]])

            def fits(batch):
                if answer_chars(batch) > ANSWER_BUDGET_CHARS:
                    return False
                batch_ids = {item["task"]["id"] for item in batch}
                prompt = patch_prompt(dossier, discovery, context, self.config,
                                      targets=editable_findings(owners, batch_ids, self.state["dirty_tasks"]),
                                      instructions=rules_for(batch), extra_context=passages(batch),
                                      coverage_ids={qid for item in batch for qid in item["task"]["question_ids"]})
                return len(prompt) <= PROMPT_BUDGET_CHARS

            start = 0
            while start < len(dirty):
                # Up to SEED_BATCH_SIZE closed questions per patch, fewer when their patch would not fit one call.
                size = 1
                while start + size < min(len(dirty), start + SEED_BATCH_SIZE) and fits(dirty[start:start + size + 1]):
                    size += 1
                batch = dirty[start:start + size]
                question_ids = {qid for item in batch for qid in item["task"]["question_ids"]}
                batch_ids = {item["task"]["id"] for item in batch}
                targets = editable_findings(owners, batch_ids, self.state["dirty_tasks"])
                legacy_targets = {fid for item in batch for fid in item["task"]["finding_ids"]}
                legacy_targets.update(fid for c in dossier.coverage if c.question_id in question_ids for fid in c.finding_ids)
                before = dossier
                dossier = edit_dossier(folder, f"batch_{start:03d}", dossier, discovery, context, self.config,
                    lambda p, s, start=start: self.generate(folder, f"batch_{start:03d}", p, s, f"{VERSION}{self.prompt_tag}.compose_patch"),
                    targets=targets, legacy_targets=legacy_targets, instructions=rules_for(batch),
                    extra_context=passages(batch), coverage_ids=question_ids)
                added = {f.id for f in dossier.findings} - {f.id for f in before.findings}
                dossier = repair_references(folder, f"batch_{start:03d}_references", dossier, discovery, context, self.config,
                    lambda p, s, start=start: self.generate(folder, f"batch_{start:03d}_references", p, s, f"{VERSION}.references"),
                    allowed_ids=targets | added)
                preserve_unrelated(before, dossier, targets)
                additions = dossier.model_copy(update={"findings": [f for f in dossier.findings if f.id in added]})
                owners.update(finding_owners(additions, [t for t in plan.tasks if t.id in batch_ids], self.state["tasks"]))
                start += size
        else:
            text = (TERMINOLOGY + TEACHING_SCOPE + EVIDENCE_INSTRUCTIONS + SYNTHESIS_INSTRUCTIONS + self.synthesis_rule() +
                    instructions("dossier_compose", language=self.config.language))
            payload = {"brief": quality_brief(self.config), "questions": [q.model_dump() for q in discovery.questions],
                       "verified_answers": answers, "sources": context}
            if gaps:
                text += " " + instructions("accepted_gaps")
                payload["accepted_gaps"] = gaps
            if len(text) + chars(payload) > PROMPT_BUDGET_CHARS or answer_chars(answers) > ANSWER_BUDGET_CHARS:
                dossier, owners = self.compose_in_batches(folder, discovery, plan, answers, gaps, context, text)
            else:
                dossier = self.call(folder, "dossier", ResearchDossier, text + "\n" + json.dumps(payload, ensure_ascii=False),
                                    validate=lambda candidate, final: validate_synthesis(candidate, context), tag=self.prompt_tag)
                dossier = repair_references(folder, "references", dossier, discovery, context, self.config,
                    lambda p, s: self.generate(folder, "references", p, s, f"{VERSION}.references"))
                owners = finding_owners(dossier, plan.tasks, self.state["tasks"])
        self.state["composed_findings"] = {"dossier_hash": digest(dossier.model_dump()), "owners": owners}
        self.state["verified_baseline"] = answers
        self.save()
        return dossier, discovery, context

    def compose_in_batches(self, folder, discovery, plan, answers, gaps, context, text):
        """More verified material than one call holds, in its prompt or in its answer: the dossier
        starts from the first answers that fit with only their passages, then every further answer
        is integrated in bounded batches, the way a resumed run integrates newly closed questions
        into a saved dossier. A batch is bounded twice, its prompt by PROMPT_BUDGET_CHARS and the
        answers it integrates by ANSWER_BUDGET_CHARS, because the dossier it asks for grows with them."""
        def passages(items):
            return read_context(self.reader, [e["reference"] for item in items
                                              for f in item["answer"]["findings"] for e in f["evidence"]])

        def opening_payload(items):
            payload = {"brief": quality_brief(self.config), "questions": [q.model_dump() for q in discovery.questions],
                       "verified_answers": items, "sources": passages(items)}
            if gaps:
                payload["accepted_gaps"] = gaps
            return payload

        first = 1
        while (first < len(answers) and answer_chars(answers[:first + 1]) <= ANSWER_BUDGET_CHARS
               and len(text) + chars(opening_payload(answers[:first + 1])) <= PROMPT_BUDGET_CHARS):
            first += 1
        opening = answers[:first]
        opening_context = passages(opening)
        dossier = self.call(folder, "dossier_batch_000", ResearchDossier,
                            text + "\n" + json.dumps(opening_payload(opening), ensure_ascii=False),
                            validate=lambda candidate, final: validate_synthesis(candidate, opening_context), tag=self.prompt_tag)
        dossier = repair_references(folder, "dossier_batch_000_references", dossier, discovery, opening_context, self.config,
            lambda p, s: self.generate(folder, "dossier_batch_000_references", p, s, f"{VERSION}.references"))
        opening_ids = {item["task"]["id"] for item in opening}
        owners = finding_owners(dossier, [t for t in plan.tasks if t.id in opening_ids], self.state["tasks"])
        remaining = answers[first:]
        dirty = [item["task"]["id"] for item in remaining]

        def rules_for(batch):
            rules = {"verified_answers": batch,
                     "assigned_gaps": {gid: self.state["gaps"][gid] for item in batch for gid in item["task"]["gap_ids"]},
                     "all_closed_tasks": [{"task": item["task"], "answer": item["answer"]["summary"]} for item in answers],
                     "rule": "Integrate these independently verified answers. Resolve old open questions explicitly "
                     "when their assigned tasks fully answer them; do not carry stale editorial to-dos as evidence gaps. "
                     "Only close a compound question when ALL its obligations are answered. Preserve all unrelated findings."
                     + self.synthesis_rule()}
            if gaps:
                rules["accepted_gaps"] = {"rule": instructions("accepted_gaps"), "tasks": gaps}
            return rules

        def fits(batch):
            if answer_chars(batch) > ANSWER_BUDGET_CHARS:
                return False
            batch_ids = {item["task"]["id"] for item in batch}
            prompt = patch_prompt(dossier, discovery, context, self.config, targets=editable_findings(owners, batch_ids, dirty),
                                  instructions=rules_for(batch), extra_context=passages(batch),
                                  coverage_ids={qid for item in batch for qid in item["task"]["question_ids"]})
            return len(prompt) <= PROMPT_BUDGET_CHARS

        start, index = 0, 1
        while start < len(remaining):
            size = 1
            while start + size < len(remaining) and fits(remaining[start:start + size + 1]):
                size += 1
            batch = remaining[start:start + size]
            question_ids = {qid for item in batch for qid in item["task"]["question_ids"]}
            batch_ids = {item["task"]["id"] for item in batch}
            targets = editable_findings(owners, batch_ids, dirty)
            before = dossier
            name = f"dossier_batch_{index:03d}"
            dossier = edit_dossier(folder, name, dossier, discovery, context, self.config,
                lambda p, s, name=name: self.generate(folder, name, p, s, f"{VERSION}{self.prompt_tag}.compose_patch"),
                targets=targets, instructions=rules_for(batch), extra_context=passages(batch), coverage_ids=question_ids)
            added = {f.id for f in dossier.findings} - {f.id for f in before.findings}
            # A batch repairs only its own findings; a source-wide limit that other batches share waits.
            dossier = repair_references(folder, f"{name}_references", dossier, discovery, context, self.config,
                lambda p, s, name=name: self.generate(folder, f"{name}_references", p, s, f"{VERSION}.references"),
                allowed_ids=targets | added, defer_shared=True)
            preserve_unrelated(before, dossier, targets)
            additions = dossier.model_copy(update={"findings": [f for f in dossier.findings if f.id in added]})
            owners.update(finding_owners(additions, [t for t in plan.tasks if t.id in batch_ids], self.state["tasks"]))
            start, index = start + size, index + 1
        # Source-wide quote and paraphrase limits are the one rule only the sum of the batches can
        # break. Every finding is new here, so the closing pass may edit any of them, as the
        # single-call composition does.
        dossier = repair_references(folder, "dossier_batches_references", dossier, discovery, context, self.config,
            lambda p, s: self.generate(folder, "dossier_batches_references", p, s, f"{VERSION}.references"))
        write_json(folder / "dossier_batches.json", {"opening": sorted(opening_ids), "batches": index - 1,
                   "budget_chars": PROMPT_BUDGET_CHARS, "answer_budget_chars": ANSWER_BUDGET_CHARS})
        return dossier, owners

    def write_gate(self, report):
        write_json(self.work / "research_quality_gate.json", report)
        atomic_text(self.work / "research_quality.md", render_quality(report))

    def audit(self, dossier, discovery, context):
        folder = self.folder / "synthesis" / f"audit_{self.state['audit_round']:02d}"
        self.state["phase"] = "audit"
        self.save("Gesamtdossier wird auf Quellenbezüge, Widersprüche und alle ursprünglichen Leitfragen geprüft")
        tasks = QuestionPlan.model_validate(self.state["plan"]).tasks
        accepted = self.accepted_summary()
        for revision in range(3):
            expected = self.state.get("objections", {})

            def well_formed(review, final, dossier=dossier, findings=None, objections=None):
                # A part of a split review is checked against its own findings and objections; the
                # merged review and a whole-dossier review against everything.
                findings = list(dossier.findings) if findings is None else findings
                objections = expected if objections is None else objections
                if Counter(c.objection_id for c in review.objection_checks) != Counter(objections.keys()):
                    raise AppError("Every existing objection needs an explicit closure check.", code="invalid_evidence_review", status="blocked")
                for check in review.objection_checks:
                    if not set(check.references) <= references(context) or (check.verdict == "closed" and not check.references):
                        raise AppError("Objection closure requires read source evidence.", code="invalid_evidence_review", status="blocked")
                semantic_errors = support_errors(findings, review, context)
                rejected = {s.finding_id for s in review.finding_support if s.verdict != "supported" or
                            s.suitability != "suitable" or not s.contract_preserved}
                if semantic_errors and (not rejected or not rejected <= {i.finding_id for i in review.issues}):
                    raise AppError("Failing support receipts require explicit corrective issues.", code="invalid_evidence_review", status="blocked")
                for issue in review.issues:
                    if (issue.objection is None or issue.finding_id not in issue.objection.finding_ids or
                            issue.objection.resolution not in {issue.resolution, "review_disagreement"}):
                        raise AppError("A dossier objection requires an affected finding and closure condition.",
                                       code="invalid_evidence_review", status="blocked")
                    validate_objection(issue.objection, tasks, dossier.findings, context)
                if not {i.finding_id for i in review.issues} <= {f.id for f in findings}:
                    raise AppError("Gesamtprüfung nennt unbekannte Befunde.", code="invalid_model_output", status="blocked")

            review = self.grounding_review(folder, revision, dossier, context, expected, well_formed)
            # A reviewer's disagreement is a legitimate verdict, not a malformed one: it stops the run.
            for check in review.objection_checks:
                if check.verdict == "review_disagreement" or (check.verdict == "open" and not review.issues):
                    save_value(folder / "review_disagreement.json", check.model_dump())
                    raise AppError("An unresolved review disagreement cannot trigger unanchored research.", code="review_disagreement", status="blocked")
            for issue in review.issues:
                if issue.objection.resolution == "review_disagreement":
                    self.state.setdefault("review_disagreements", []).append(issue.objection.model_dump())
                    self.save("Prüfeinwand ist nicht hinreichend belegt")
                    raise AppError("Unanchored review disagreement; no automatic new research.", code="review_disagreement", status="blocked")
            if not review.issues or needs_research(review) or revision == 2:
                break
            before = dossier
            targets = {i.finding_id for i in review.issues}
            guidance = review.model_dump()
            if len(patch_prompt(dossier, discovery, context, self.config, targets=targets, instructions=guidance,
                                allow_additions=False, coverage_ids=set(), allow_questions=False)) > PROMPT_BUDGET_CHARS:
                # A large review: the correction reads the issues and the receipts of its own findings.
                guidance = compact_review_instructions(review, targets)
            dossier = edit_dossier(folder, f"correction_{revision}", dossier, discovery, context, self.config,
                lambda p, s: self.generate(folder, f"correction_{revision}", p, s, f"{VERSION}.correction"),
                targets=targets, instructions=guidance,
                allow_additions=False, coverage_ids=set(), allow_questions=False)
            dossier = repair_references(folder, f"correction_{revision}_references", dossier, discovery, context, self.config,
                lambda p, s: self.generate(folder, f"correction_{revision}_references", p, s, f"{VERSION}.references"),
                allowed_ids=targets)
            preserve_unrelated(before, dossier, targets)
        text = instructions("research_assessment", language=self.config.language)
        payload = {"brief": quality_brief(self.config), "dossier": dossier.model_dump(), "sources": context,
                   "finding_support": [r.model_dump() for r in review.finding_support],
                   "source_assessments": [a.model_dump() for a in review.source_assessments]}
        if accepted:
            text += " " + instructions("accepted_gaps")
            payload["accepted_gaps"] = [{"task_id": tid, **gap} for tid, gap in accepted.items()]
        if len(text) + chars(payload) > PROMPT_BUDGET_CHARS:
            payload["sources"] = select_context(context, {e.reference for f in dossier.findings for e in f.evidence})
            if len(text) + chars(payload) > PROMPT_BUDGET_CHARS:
                # The assessment judges coverage, explanation and independence against the reviewed
                # dossier and the support receipts; source identity and pages suffice for that.
                payload["sources"] = source_outline(payload["sources"])
            if len(text) + chars(payload) > PROMPT_BUDGET_CHARS:
                # A dossier of many answers: verdicts and identities without the review's prose,
                # findings without their verified excerpts, then sources without their section lists.
                payload.update(compact_assessment_material(dossier, review))
            if len(text) + chars(payload) > PROMPT_BUDGET_CHARS:
                payload["sources"] = source_identity(payload["sources"])
        assessment = self.call(folder, "assessment", ResearchAssessment, text + "\n" + json.dumps(payload, ensure_ascii=False),
                               validate=lambda candidate, final: check_assessment(self.config, dossier, candidate))
        report = quality_report(self.config, dossier, discovery, self.index, assessment, (i.reason for i in review.issues),
                                accepted=accepted, gap_probes=self.probe_declared_gaps(dossier),
                                review_limitations=self.review_limitations())
        dossier = dossier.model_copy(update={"evidence_version": EVIDENCE_VERSION,
                                            "source_assessments": review.source_assessments})
        report["dossier_hash"] = digest(dossier.model_dump())
        report["evidence"] = evidence_summary(dossier.findings, review)
        report["advisories"] = {"single_group_findings": report["evidence"]["single_group_findings"]}
        report["objection_checks"] = [c.model_dump() for c in review.objection_checks]
        report["unresolved_scientific_relations"] = [r.model_dump() for r in dossier.synthesis if r.resolution == "unresolved"]
        report["question_workflow"] = VERSION
        self.write_gate(report)
        self.state["composed_findings"]["dossier_hash"] = digest(dossier.model_dump())
        self.save()
        return dossier, review, report

    def grounding_review(self, folder, revision, dossier, context, expected, well_formed):
        """One review of the whole dossier when it fits the window; otherwise the same review in
        parts. Each part sees the dossier outline, its own findings with their passages and the
        objections that concern them, and the parts merge into one receipt checked as a whole."""
        text = (EVIDENCE_INSTRUCTIONS + SYNTHESIS_INSTRUCTIONS + ROUTING_INSTRUCTIONS + " " +
                instructions("dossier_audit"))
        brief, tasks = quality_brief(self.config), self.state["plan"]["tasks"]
        whole = {"brief": brief, "dossier": dossier.model_dump(), "sources": context, "open_objections": expected,
                 "tasks": tasks, "verified_baseline": self.state.get("verified_baseline", [])}
        if len(text) + chars(whole) <= PROMPT_BUDGET_CHARS:
            return self.call(folder, f"grounding_{revision}", SourceReview, text + "\n" + json.dumps(whole, ensure_ascii=False),
                             validate=well_formed)
        baseline = compact_baseline(self.state.get("verified_baseline", []))
        outline = dossier.model_dump(exclude={"findings"})
        findings = list(dossier.findings)
        # Every objection travels with the first finding it names, so each is checked exactly once.
        objections_of = {}
        for identifier, objection in expected.items():
            named = objection.get("finding_ids") or []
            objections_of.setdefault(named[0] if named else findings[0].id, []).append(identifier)

        def part_payload(part):
            part_ids = {f.id for f in part}
            objections = {oid: expected[oid] for f in part for oid in objections_of.get(f.id, [])}
            refs = ({e.reference for f in part for e in f.evidence}
                    | {ref for objection in objections.values() for ref in objection.get("evidence_refs", [])})
            return {"brief": brief, "dossier": {**outline, "findings": [f.model_dump() for f in part]},
                    "other_findings": [{"id": f.id, "kind": f.kind, "statement": f.statement}
                                       for f in findings if f.id not in part_ids],
                    "sources": select_context(context, refs), "open_objections": objections,
                    "tasks": tasks, "verified_baseline": baseline}

        parts, start = [], 0
        while start < len(findings):
            size = 1
            while (start + size < len(findings)
                   and len(text) + len(PART_NOTE) + chars(part_payload(findings[start:start + size + 1])) <= PROMPT_BUDGET_CHARS):
                size += 1
            parts.append(findings[start:start + size])
            start += size
        reviews = []
        for index, part in enumerate(parts):
            payload = part_payload(part)
            reviews.append(self.call(folder, f"grounding_{revision}_part_{index:03d}", SourceReview,
                text + PART_NOTE + "\n" + json.dumps(payload, ensure_ascii=False),
                validate=lambda review, final, part=part, objections=payload["open_objections"]:
                    well_formed(review, final, findings=part, objections=objections)))
        merged = {}
        for review in reviews:
            for key, value in review.model_dump().items():
                if isinstance(value, list):
                    merged.setdefault(key, []).extend(value)
                else:
                    merged.setdefault(key, value)
        review = SourceReview.model_validate(merged)
        well_formed(review, True)
        write_json(folder / f"grounding_{revision}_merged.json", {"parts": len(parts), "findings_per_part": [len(p) for p in parts],
                   "budget_chars": PROMPT_BUDGET_CHARS, "review": review.model_dump(mode="json")})
        return review

    def probe_declared_gaps(self, dossier):
        """Probe the gaps this composition declares, and settle them against everything read.

        A gap the run itself invented is exactly the case the audit found: nothing had checked
        it against the sections already retrieved. Reading counts across all tasks, so a gap
        whose candidate sections a reader saw is confirmed rather than blocked.
        """
        texts = dict.fromkeys([*dossier.open_questions, *(row.gap for row in dossier.coverage if row.gap)])
        wanted = {gap_id(text): text for text in texts}
        rows = {row["gap_id"]: row for row in self.state.get("gap_probes", [])}
        rows.update({row["gap_id"]: row for row in probe(self.index, {gid: text for gid, text in wanted.items()
                                                                     if gid not in rows},
                                                         gap_terms=coverage_terms(dossier))})
        read = {ref for task in self.state["tasks"].values() for ref in task.get("read_refs", [])}
        self.state["gap_probes"] = [settle(row, read_refs=read) if row["gap_id"] in wanted else row
                                    for row in rows.values()]
        return [row for row in self.state["gap_probes"] if row["gap_id"] in wanted]

    def objections(self, review, report):
        return list(dict.fromkeys([*(i.reason for i in review.issues), *report["blocking_gaps"],
            *(row["reason"] + " " + " ".join(row["missing"]) for row in report["requirements"] if not row["passed"])]))

    def tolerate(self, dossier, review, report):
        """Every remaining objection targets an accepted gap: finish, with those objections on record."""
        report = {**report, "passed": True, "passed_with_accepted_gaps": True,
                  "residual_objections": self.objections(review, report)}
        self.write_gate(report)
        self.save("Verbliebene Einwände betreffen nur akzeptierte Lücken; die Recherche wird abgeschlossen")
        return report

    @staticmethod
    def existing_objection(registry, closed, anchor, identifier):
        # Keep an unresolved objection stable even when a later reviewer paraphrases
        # its missing-evidence description. A new defect is allowed after explicit closure.
        for old_id, old in registry.items():
            if old_id not in closed and all(old.get(key) == anchor.model_dump().get(key)
                    for key in ("rule", "task_id", "criterion_index", "finding_ids")):
                return old_id
        return identifier

    def reopen(self, dossier, review, report):
        """Route objections to tasks; return the ids reopened and the ids blocked for exhausted reopenings."""
        tasks = QuestionPlan.model_validate(self.state["plan"]).tasks
        composed = self.state.get("composed_findings")
        if composed and composed["dossier_hash"] != digest(dossier.model_dump()):
            raise AppError("Befundzuordnung passt nicht zum geprüften Dossier.",
                           code="invalid_research_checkpoint", status="blocked")
        owners = finding_owners(dossier, tasks, self.state["tasks"], composed["owners"] if composed else None)
        objections = self.objections(review, report)
        accepted = self.accepted_summary()
        context = read_context(self.reader, [e.reference for f in dossier.findings for e in f.evidence])
        registry = self.state.setdefault("objections", {})
        closed = {c["objection_id"] for c in report.get("objection_checks", []) if c["verdict"] == "closed"}
        text = instructions("objection_routes")
        payload = {"objections": objections, "tasks": [t.model_dump() for t in tasks],
                   "anchored_issues": [i.objection.model_dump() for i in review.issues if i.objection],
                   "finding_owners": owners,
                   "answers": {t.id: self.state["tasks"][t.id]["answer"] for t in tasks},
                   "dossier": dossier.model_dump()}
        if accepted:
            text += " " + instructions("accepted_gaps")
            payload["accepted_gaps"] = [{"task_id": tid, **gap} for tid, gap in accepted.items()]
        if len(text) + chars(payload) > PROMPT_BUDGET_CHARS:
            # Many answers: the routing reads what an objection is matched against, not the receipts.
            payload.update(compact_routing_material(tasks, payload["answers"], review, dossier))

        def well_formed(routes, final, objections=objections):
            if (Counter(r.index for r in routes.routes) != Counter(range(len(objections))) or
                any(not set(r.task_ids) <= {t.id for t in tasks} for r in routes.routes)):
                raise AppError("Prüfeinwände sind nicht vollständig bestehenden Recherchefragen zugeordnet.",
                               code="invalid_question_routing", status="blocked")
            for route in routes.routes:
                if len(set(route.task_ids)) != len(route.task_ids) or not route.anchors:
                    raise AppError("Every routed task needs a specific evidence or criterion anchor.",
                                   code="invalid_question_routing", status="blocked")
                for task_id in route.task_ids:
                    anchors = [a for a in route.anchors if a.task_id == task_id]
                    if not anchors:
                        raise AppError("Missing task-specific objection anchor.", code="invalid_question_routing", status="blocked")
                    for anchor in anchors:
                        identifier = validate_objection(anchor, tasks, dossier.findings, context,
                                                        target_task=task_id, owners=owners)
                        if anchor.resolution == "review_disagreement":
                            continue
                        identifier = self.existing_objection(registry, closed, anchor, identifier)
                        previous = registry.get(identifier) if identifier not in closed else None
                        if previous and previous["closure_condition"] != anchor.closure_condition:
                            raise AppError("The closure condition of an existing objection changed.",
                                           code="invalid_question_routing", status="blocked")

        folder = self.folder / "synthesis" / f"audit_{self.state['audit_round']:02d}"
        if len(objections) <= ROUTING_PART_SIZE:
            routes = self.call(folder, "routes", ReopenPlan, text + "\n" + json.dumps(payload, ensure_ascii=False),
                               validate=well_formed)
        else:
            # Many objections: routed in parts over the same material, merged into one plan that is
            # checked as a whole, the way a split review merges.
            parts = split_objections(objections, ROUTING_PART_SIZE)
            plans = [self.call(folder, f"routes_part_{number:03d}", ReopenPlan,
                               text + ROUTING_PART_NOTE + "\n" + json.dumps({**payload, "objections": part}, ensure_ascii=False),
                               validate=lambda plan, final, part=part: well_formed(plan, final, objections=part))
                     for number, part in enumerate(parts)]
            routes = merge_route_plans(plans, ROUTING_PART_SIZE)
            well_formed(routes, True)
            write_json(folder / "routes_merged.json", {"parts": len(parts), "objections_per_part": [len(p) for p in parts],
                       "routes": routes.model_dump(mode="json")})
        reasons = {}
        for route in routes.routes:
            for task_id in route.task_ids:
                for anchor in [a for a in route.anchors if a.task_id == task_id]:
                    identifier = validate_objection(anchor, tasks, dossier.findings, context,
                                                    target_task=task_id, owners=owners)
                    if anchor.resolution == "review_disagreement":
                        self.state.setdefault("review_disagreements", []).append(anchor.model_dump())
                        self.save("Prüfeinwand benötigt Klärung statt weiterer Suche")
                        raise AppError("Review disagreement cannot trigger more research.", code="review_disagreement", status="blocked")
                    identifier = self.existing_objection(registry, closed, anchor, identifier)
                    if task_id in accepted:
                        self.state.setdefault("accepted_gap_objections", {})[identifier] = {
                            **anchor.model_dump(), "id": identifier, "objection": objections[route.index], "status": "accepted_gap"}
                    else:
                        registry[identifier] = {**anchor.model_dump(), "id": identifier, "status": "open"}
                reasons.setdefault(task_id, []).append(objections[route.index] + " " + route.reason)
        reopened, blocked = [], []
        for task_id, texts in reasons.items():
            if task_id in accepted:
                continue
            row = self.state["tasks"][task_id]
            if len(row["reopenings"]) >= self.state["limits"]["reopenings"]:
                row.update(status="blocked", reason="Wiederholte Gesamtprüfung widerspricht dem Abschluss: " + " ".join(texts))
                blocked.append(task_id)
                continue
            row["reopenings"].append({"reason": texts, "previous_answer": row["answer"],
                                     "previous_verification": row.get("verification")})
            row.update(status="researching", answer=None, draft_answer=row["reopenings"][-1]["previous_answer"],
                       feedback=texts, step=0, no_progress=0, fallbacks=0, pending=None,
                       activity="Mit konkretem Einwand aus der Gesamtprüfung wieder geöffnet")
            reopened.append(task_id)
        dirty = invalidate_dependents(self.state, [t for t in reasons if t not in accepted])
        self.state.update(seed_dossier=dossier.model_dump(), dirty_tasks=dirty, finding_owners=owners,
                          audit_round=self.state["audit_round"]+1, phase="questions")
        self.save("Konkrete Einwände werden ihren ursprünglichen Recherchefragen zugeordnet" if reopened or blocked
                  else "Verbliebene Einwände betreffen nur akzeptierte Lücken")
        return reopened, blocked
