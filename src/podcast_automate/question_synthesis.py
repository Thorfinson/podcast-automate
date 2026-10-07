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
from .evidence_models import EVIDENCE_VERSION, FindingSupport, SourceAssessment
from .prompts import instructions
from .question_answering import read_context
from .question_dependencies import invalidate_dependents, prerequisites_current, revalidate
from .question_ownership import editable_findings, finding_owners, preserve_unrelated
from .research_gap_probe import coverage_terms, gap_id, probe, settle
from .research_evidence import (EVIDENCE_INSTRUCTIONS, SYNTHESIS_EVIDENCE_RULE, SYNTHESIS_INSTRUCTIONS,
                                blocks, collapse_assessments, collapse_support, evidence_summary, scope_assessments,
                                support_errors, validate_objection, validate_synthesis)
from .research_ledger import VERSION, read_value, save_value
from .research_models import AnswerDigest, Finding, QuestionCoverage, ResearchDiscovery, ResearchDossier
from .research_patches import edit_dossier, patch_prompt, repair_references, retire_stale_receipt
from .research_quality import (CRITERIA, FollowUpAssessment, RequirementAssessment, check_follow_up, requirements_for)
from .research_quality import (ResearchAssessment, check_assessment, cite_findings, quality_brief, quality_report,
                               render_quality)
from .research_retrieval import merge_context, references, select_context
from .research_review import ROUTING_INSTRUCTIONS, SourceReview, needs_research
from .research_tasks import QuestionPlan, ReopenPlan
from .storage import atomic_text, digest, write_json

# One Opus 5 window (claude_code.PROMPT_LIMIT_CHARS, 300 000 characters) bounds every synthesis call.
# The instructions, the JSON framing and the answer share it, so the material a call carries stays
# under this budget; a run with more verified material composes and audits in bounded parts. A
# model with a larger window (claude_code.prompt_limit) takes the part reviews that outgrow it.
PROMPT_BUDGET_CHARS = 240_000
# The generation of the composing prompts a run was started with. A rule added to those prompts
# applies from the next generation on; earlier runs keep their prompt text and their receipts.
PROMPT_GENERATION = 3
# From this generation on the dossier is assembled from the verified answers without a model call
# (assemble_dossier). A composed dossier kept at most MAX_FINDINGS findings and, under the source-wide
# word limits, about a third of the verified research; every audit round then asked for the rest again
# (2026-10-01: 120 of 335 findings in Ontologies, 36 of 59 questions without one). The user's choice: the
# dossier holds every verified answer, and the 25-word quote rule applies to the broadcast script.
ASSEMBLED_GENERATION = 3
# The editor's request to assemble an earlier run's dossier from its verified answers (run_budget.approve_dossier_rebuild).
DOSSIER_REBUILD = "dossier_rebuild.json"
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
# Only in a part whose objections cite passages its findings do not: those sources are context for the
# closure checks, and the source check expects assessments of exactly the sources the findings cite.
OBJECTION_SOURCES_NOTE = (" objection_sources holds passages that only the open objections cite: use them for "
                          "objection_checks, and give source_assessments only for sources listed under sources.")


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
ROUTING_OBJECTION_FIELDS = ("id", "rule", "task_id", "criterion_index", "finding_ids", "evidence_refs", "missing_evidence",
                            "reason", "closure_condition", "resolution")


# Objections routed per call when there are many: the answer names an anchor per routed task and
# objection, so it grows with the objections, not with the dossier. One call for all 116 objections
# of the 20 September run produced nothing in 30 minutes.
ROUTING_PART_SIZE = 25
ROUTING_PART_NOTE = (" This is one part of the routing: route exactly the supplied objections, whose indices count "
                     "from 0 within this part; the other objections of this audit are routed in other parts.")
# The feedback of a task reopened only to correct its wording; the reader may then only answer.
REVISE_NOTE = ("Correction only: the audit found that the supplied passages suffice and only the wording, scope or "
               "attribution of the named findings must change. Answer directly with the corrected answer; keep every "
               "other finding, reference and limit as it was.")
# The routing of an assembled dossier: each objection may reopen only the tasks code allows it (objection_scopes),
# and each answer lists its findings with their statements. Before (2026-10-02), only an objection's text reached the
# router, which reopened unchanged questions too and picked "support" anchors among finding ids it could not read;
# each wrong pick spent one of that question's two reworks.
SCOPED_ROUTING_NOTE = (" objection_scopes lists, by objection index, what code fixed about an objection: task_ids, the "
                       "only tasks it may reopen (route it to one or more of them, never to another task); requirement_id "
                       "and finding_ids where it is an unmet requirement; and remedy where the assessment named one. Each "
                       "entry of answers lists its findings with their statements; anchor a quality rule on the findings "
                       "the objection is actually about.")
# What became of an earlier objection that leaves nothing new to research while its questions stay unchanged: noted
# as a limit (after a covered criterion or spent reworks), disputed as an unsupported demand, or an accepted gap.
SETTLED_OUTCOMES = {"noted", "disputed", "accepted_gap", "blocked"}
def complete_context(reader, refs):
    """The passages of ``refs``, all of them. read_context used to stop at 2,000,000 characters and drop the rest
    silently, so a dossier, an audit or a routing judged material it never received (2026-10-02). The cap is gone;
    a cited passage the reader still cannot supply stops the run with its name instead of being skipped."""
    wanted = list(dict.fromkeys(refs))
    context = read_context(reader, wanted)
    read = references(context)
    missing = [ref for ref in wanted if ref not in read]
    if missing:
        raise AppError(f"{len(missing)} von {len(wanted)} zitierten Quellenabschnitten dieser Synthese sind nicht lesbar, "
                       f"etwa {', '.join(missing[:3])}. Nichts wurde ohne diese Abschnitte geprüft.",
                       code="research_context_incomplete", status="blocked")
    return context


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
            # Every anchor must cite read references: the findings keep theirs, without the excerpts.
            "dossier": {"topic": data["topic"],
                        "findings": [{**{k: f[k] for k in ("id", "kind", "statement")},
                                      "evidence": [{"reference": e["reference"]} for e in f["evidence"]]}
                                     for f in data["findings"]],
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


def dossier_finding_id(task_id, finding_id):
    """A finding's id in an assembled dossier. Ids inside an answer are local (Ontologies, 2026-10-01: five ids
    shared by two answers), so the task qualifies every one of them; a reworked answer never renames another's."""
    return f"{task_id}__{finding_id}"


def assemble_dossier(discovery, plan, rows, accepted, language):
    """The dossier as the sum of the verified answers, without a model call: every finding of every answer, unchanged
    but for its qualified id, in plan order; per research question the findings of the tasks that serve it; and the
    answers' summaries and limits. Accepted gaps stay coverage gaps, as in a composed dossier. Each finding was
    checked against its passages by the review of its answer (question_answering); nothing is rewritten here."""
    findings, digests = [], []
    for task in plan.tasks:
        answer = rows[task.id].get("answer")
        if task.id in accepted or not answer:
            continue
        ids = {f["id"]: dossier_finding_id(task.id, f["id"]) for f in answer["findings"]}
        findings.extend(Finding.model_validate({**f, "id": ids[f["id"]]}) for f in answer["findings"])
        digests.append(AnswerDigest(task_id=task.id, question=task.question, question_ids=list(task.question_ids),
                                    summary=answer["summary"], limits=list(answer.get("limits", [])),
                                    finding_ids=list(ids.values())))
    german = language.startswith("de")
    coverage = []
    for question in discovery.questions:
        serving = [t for t in plan.tasks if question.id in t.question_ids]
        answered = {d.task_id for d in digests if question.id in d.question_ids}
        gaps = [f"{t.question}: {accepted[t.id].get('reason') or ('akzeptierte Lücke' if german else 'accepted gap')}"
                if t.id in accepted else
                (f"{t.question}: nicht beantwortet" if german else f"{t.question}: not answered")
                for t in serving if t.id not in answered]
        if not serving:
            gaps = ["Keine Teilfrage des Plans dient dieser Leitfrage." if german else "No task of the plan serves this question."]
        coverage.append(QuestionCoverage(
            question_id=question.id, status="answered" if not gaps else "partial" if answered else "unanswered",
            finding_ids=[fid for d in digests if d.task_id in answered for fid in d.finding_ids], gap=" ".join(gaps)))
    note = (f"Zusammengesetzt aus {len(digests)} einzeln an ihren Quellen geprüften Antworten auf "
            f"{len(plan.tasks)} Teilfragen; die Befunde stehen unverändert so, wie ihre Prüfung sie bestätigt hat."
            if german else
            f"Assembled from {len(digests)} answers to {len(plan.tasks)} tasks, each checked against its sources; "
            "the findings stand unchanged as their review confirmed them.")
    return ResearchDossier(topic=discovery.topic, scope_note=note, findings=findings, coverage=coverage,
                           open_questions=[], assembled=True, answers=digests)


def assembled_review(dossier, rows, context):
    """The source review of an assembled dossier: each finding keeps the support receipt and its sources the
    assessments of the independent review of the answer it comes from, under the finding's dossier id. A source
    several answers cite keeps the assessment claiming the least independence (collapse_assessments)."""
    support, assessments, limitations = [], [], []
    for row in dossier.answers:
        review = (rows[row.task_id].get("verification") or {}).get("review") or {}
        local = {f["id"] for f in rows[row.task_id]["answer"]["findings"]}
        support.extend(FindingSupport.model_validate({**receipt, "finding_id": dossier_finding_id(row.task_id, receipt["finding_id"])})
                       for receipt in review.get("finding_support", []) if receipt["finding_id"] in local)
        assessments.extend(SourceAssessment.model_validate(a) for a in review.get("source_assessments", []))
        limitations.extend(review.get("limitations", []))
    review = SourceReview(issues=[], limitations=list(dict.fromkeys(limitations)), finding_support=collapse_support(support),
                          source_assessments=collapse_assessments(assessments))
    return scope_assessments(review, dossier.findings, context)


def assembled_assessment_material(dossier, review, context):
    """An assembled dossier as the assessment reads it: each verified answer with its summary and limits, every
    finding with its kind, statement, cited sources and support verdict, the coverage, and one identity row per
    source. The excerpts and claim contracts were checked by the answers' reviews and stay in the dossier; whole,
    the material ran to 850 000 to 920 000 characters on the runs of 2026-10-01."""
    verdicts = {r.finding_id: r for r in review.finding_support}
    titles = {source["source_id"]: source["title"] for source in context}
    sources = [{"title": titles.get(a.source_id, ""), **{k: v for k, v in a.model_dump().items() if k in SOURCE_IDENTITY_FIELDS}}
               for a in review.source_assessments]
    return {"answers": [a.model_dump() for a in dossier.answers],
            "findings": [{"id": f.id, "kind": f.kind, "statement": f.statement,
                          "sources": sorted({e.reference.split("#")[0] for e in f.evidence}),
                          **({"verdict": verdicts[f.id].verdict, "empirical_status": verdicts[f.id].empirical_status}
                             if f.id in verdicts else {})} for f in dossier.findings],
            "coverage": [{"question_id": c.question_id, "status": c.status, "gap": c.gap} for c in dossier.coverage],
            "sources": sources}


def assembled_routing_material(tasks, answers, dossier):
    """An assembled dossier as the objection routing reads it: the tasks, each answer's summary, limits and findings
    with their statements, and every finding with its kind and cited passages. An anchor names a task, its criterion
    and the findings it concerns; without the statements (before 2026-10-02) a "support" anchor was picked blind."""
    return {"tasks": [{k: v for k, v in t.model_dump().items() if k in ROUTING_TASK_FIELDS} for t in tasks],
            "anchored_issues": [],
            "answers": {tid: None if not answer else {"summary": answer.get("summary"), "limits": answer.get("limits", []),
                                                       "finding_ids": [dossier_finding_id(tid, f["id"])
                                                                       for f in answer.get("findings", [])],
                                                       "findings": [{"id": dossier_finding_id(tid, f["id"]),
                                                                     "statement": f.get("statement", "")}
                                                                    for f in answer.get("findings", [])]}
                        for tid, answer in answers.items()},
            "dossier": {"topic": dossier.topic,
                        "findings": [{"id": f.id, "kind": f.kind, "evidence": [{"reference": e.reference} for e in f.evidence]}
                                     for f in dossier.findings],
                        "coverage": [c.model_dump() for c in dossier.coverage], "open_questions": dossier.open_questions,
                        "synthesis": []}}


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

    def adopt_dossier_rebuild(self):
        """The editor's request (run_budget.approve_dossier_rebuild) to assemble a composed run's dossier from its
        verified answers. The answers, their reviews and every receipt stay; the composed dossier and the objections
        its audits raised are set aside in ``synthesis/superseded_audit_NN.json``, and the next audit round, in a
        folder of its own, judges the assembled whole. Spent reworks still count against a question's limit."""
        path = self.work / DOSSIER_REBUILD
        if self.prompt_generation >= ASSEMBLED_GENERATION or not path.exists():
            return
        request = json.loads(path.read_text(encoding="utf-8"))
        if request.get("run_id") != self.work.name:
            raise AppError("Der Neuaufbau des Dossiers gehört nicht zu diesem Lauf.", code="invalid_dossier_rebuild",
                           status="blocked")
        audit_round = int(self.state["audit_round"])
        keys = ("seed_dossier", "composed_findings", "finding_owners", "verified_baseline", "objections",
                "closed_objections", "noted_objections", "disputed_objections", "review_disagreements",
                "accepted_gap_objections")
        save_value(self.folder / "synthesis" / f"superseded_audit_{audit_round:02d}.json",
                   {"prompt_generation": self.prompt_generation, "audit_round": audit_round,
                    "approved_at": request.get("approved_at"), **{key: self.state.get(key) for key in keys}})
        for key in keys:
            self.state.pop(key, None)
        self.state.update(prompt_generation=ASSEMBLED_GENERATION, seed_dossier=None, dirty_tasks=[],
                          audit_round=audit_round + 1,
                          phase="synthesis" if self.state["phase"] in {"synthesis", "audit"} else self.state["phase"])
        self.state.setdefault("rebuilds", []).append({"from_audit_round": audit_round, "approved_at": request.get("approved_at")})

    def assemble(self, discovery, plan, accepted):
        """The dossier of prompt generation 3: the sum of the verified answers (assemble_dossier), without a model call."""
        from .research import validate_dossier
        self.state["phase"] = "synthesis"
        if not any(row.get("answer") for tid, row in self.state["tasks"].items() if tid not in accepted):
            raise AppError("Keine Teilfrage hat eine geprüfte Antwort; das Dossier hat keinen Inhalt.",
                           code="research_coverage_incomplete", status="blocked")
        dossier = assemble_dossier(discovery, plan, self.state["tasks"], accepted, self.config.language)
        context = complete_context(self.reader, [e.reference for f in dossier.findings for e in f.evidence])
        errors = validate_dossier(dossier, discovery, context)
        if errors:
            raise AppError("Das zusammengesetzte Dossier besteht die Quellenprüfung nicht: " + " ".join(errors[:5]),
                           code="invalid_evidence", status="blocked")
        self.state["composed_findings"] = {"dossier_hash": digest(dossier.model_dump()),
                                           "owners": {fid: [row.task_id] for row in dossier.answers for fid in row.finding_ids}}
        self.state.pop("verified_baseline", None)
        self.save(f"Dossier aus {len(dossier.answers)} geprüften Antworten mit {len(dossier.findings)} Befunden zusammengesetzt")
        return dossier, discovery, context

    def compose(self):
        discovery = ResearchDiscovery.model_validate(self.state["discovery"])
        plan = QuestionPlan.model_validate(self.state["plan"])
        accepted = self.accepted_summary()
        if self.prompt_generation >= ASSEMBLED_GENERATION:
            return self.assemble(discovery, plan, accepted)
        answers = [{"task": task.model_dump(), "answer": self.state["tasks"][task.id]["answer"],
                    "evidence_review": self.state["tasks"][task.id].get("verification", {}).get("review"),
                    "review_limitations": (self.state["tasks"][task.id].get("verification") or {}).get("limitations", [])}
                   for task in plan.tasks if task.id not in accepted]
        gaps = [{"task_id": tid, **gap} for tid, gap in accepted.items()]
        refs = [e["reference"] for item in answers for f in item["answer"]["findings"] for e in f["evidence"]]
        context = complete_context(self.reader, refs)
        seed = self.state["seed_dossier"]
        folder = self.folder / "synthesis" / f"audit_{self.state['audit_round']:02d}"
        self.state["phase"] = "synthesis"
        self.save("Geprüfte Antworten werden zu einem zusammenhängenden Dossier verbunden")
        if seed:
            dossier = ResearchDossier.model_validate(seed)
            owners = finding_owners(dossier, plan.tasks, self.state["tasks"], self.state.get("finding_owners"))
            seed_refs = [e.reference for f in dossier.findings for e in f.evidence]
            context = merge_context(context, complete_context(self.reader, seed_refs))
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
                return complete_context(self.reader, [e["reference"] for item in batch
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
                self.save(f"Überarbeitete Antworten werden ins Dossier eingearbeitet: {start + size} von {len(dirty)}")
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
                # A batch repairs only its own findings; a source-wide limit other findings share waits (as in
                # compose_in_batches). Strictly, a longer reworked answer stopped the whole run here.
                dossier = repair_references(folder, f"batch_{start:03d}_references", dossier, discovery, context, self.config,
                    lambda p, s, start=start: self.generate(folder, f"batch_{start:03d}_references", p, s, f"{VERSION}.references"),
                    allowed_ids=targets | added, defer_shared=True)
                preserve_unrelated(before, dossier, targets)
                additions = dossier.model_copy(update={"findings": [f for f in dossier.findings if f.id in added]})
                owners.update(finding_owners(additions, [t for t in plan.tasks if t.id in batch_ids], self.state["tasks"]))
                start += size
            # Source-wide quote and paraphrase limits only the sum of the batches can break: the closing pass
            # repairs them in the findings that cite the source, which the next audit reviews like any other.
            self.save("Belegkorrektur über das ganze Dossier läuft")
            dossier = repair_references(folder, "reopened_references", dossier, discovery, context, self.config,
                lambda p, s: self.generate(folder, "reopened_references", p, s, f"{VERSION}.references"))
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
            return complete_context(self.reader, [e["reference"] for item in items
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
        self.save(f"Dossier wird aus den ersten {len(opening)} von {len(answers)} geprüften Antworten aufgebaut")
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
            self.save(f"Einarbeitung: Block {index} mit {len(batch)} Antworten, danach noch {len(remaining) - start - size} offen")
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
        self.save("Belegkorrektur über das ganze Dossier läuft")
        dossier = repair_references(folder, "dossier_batches_references", dossier, discovery, context, self.config,
            lambda p, s: self.generate(folder, "dossier_batches_references", p, s, f"{VERSION}.references"))
        write_json(folder / "dossier_batches.json", {"opening": sorted(opening_ids), "batches": index - 1,
                   "budget_chars": PROMPT_BUDGET_CHARS, "answer_budget_chars": ANSWER_BUDGET_CHARS})
        return dossier, owners

    def write_gate(self, report):
        write_json(self.work / "research_quality_gate.json", report)
        atomic_text(self.work / "research_quality.md", render_quality(report, language=self.config.language))

    def audit(self, dossier, discovery, context):
        folder = self.folder / "synthesis" / f"audit_{self.state['audit_round']:02d}"
        self.state["phase"] = "audit"
        tasks = QuestionPlan.model_validate(self.state["plan"]).tasks
        accepted = self.accepted_summary()
        review = None
        if dossier.assembled:
            # Every finding was checked against its passages by the review of its answer, and is unchanged since: the
            # audit takes those receipts and judges the whole against the brief. An objection the assessment raises
            # goes back to its question, never into the dossier text, so the dossier stays the sum of its answers.
            self.save("Quellenbelege der Einzelprüfungen übernommen; das Gesamtdossier wird gegen alle Leitfragen bewertet")
            review = assembled_review(dossier, self.state["tasks"], context)
        else:
            self.save("Gesamtdossier wird auf Quellenbezüge, Widersprüche und alle ursprünglichen Leitfragen geprüft")
        for revision in range(0 if dossier.assembled else 3):
            expected = self.state.get("objections", {})

            def well_formed(review, final, dossier=dossier, findings=None, objections=None):
                # A part of a split review is checked against its own findings and objections; the
                # merged review and a whole-dossier review against everything.
                findings = list(dossier.findings) if findings is None else findings
                objections = expected if objections is None else objections
                scope_assessments(review, findings, context)
                if Counter(c.objection_id for c in review.objection_checks) != Counter(objections.keys()):
                    raise AppError("Every existing objection needs an explicit closure check.", code="invalid_evidence_review", status="blocked")
                for check in review.objection_checks:
                    if not set(check.references) <= references(context) or (check.verdict == "closed" and not check.references):
                        raise AppError("Objection closure requires read source evidence.", code="invalid_evidence_review", status="blocked")
                semantic_errors = support_errors(findings, review, context)
                # The receipts that fail on their own (research_evidence.blocks), as in a question's review: a
                # partially supported finding whose contract and source hold is a limitation and needs no issue.
                rejected = {s.finding_id for s in review.finding_support if blocks(s)}
                missing = sorted(rejected - {i.finding_id for i in review.issues})
                if semantic_errors and (not rejected or missing):
                    raise AppError("Failing support receipts require explicit corrective issues."
                                   + (f" Add one issue for each of: {', '.join(missing)}." if missing else ""),
                                   code="invalid_evidence_review", status="blocked")
                for issue in review.issues:
                    if (issue.objection is None or issue.finding_id not in issue.objection.finding_ids or
                            issue.objection.resolution not in {issue.resolution, "review_disagreement"}):
                        raise AppError("A dossier objection requires an affected finding and closure condition.",
                                       code="invalid_evidence_review", status="blocked")
                    validate_objection(issue.objection, tasks, dossier.findings, context)
                if not {i.finding_id for i in review.issues} <= {f.id for f in findings}:
                    raise AppError("Gesamtprüfung nennt unbekannte Befunde.", code="invalid_model_output", status="blocked")

            review = self.grounding_review(folder, revision, dossier, context, expected, well_formed)
            # A reviewer's disagreement is a legitimate verdict, not a malformed one: it stops the run until
            # the editor takes a side (run_budget.decide_review_disagreement); the decision is kept on record.
            decided = self.disputes()
            for check in review.objection_checks:
                if check.verdict == "review_disagreement" or (check.verdict == "open" and not review.issues):
                    decision = decided.get(check.objection_id)
                    if decision is None:
                        save_value(folder / "review_disagreement.json",
                                   {**check.model_dump(), "objection": expected.get(check.objection_id)})
                        raise AppError("An unresolved review disagreement cannot trigger unanchored research.", code="review_disagreement", status="blocked")
                    self.state.setdefault("disputed_objections", {})[check.objection_id] = {
                        "objection_id": check.objection_id, "objection": expected.get(check.objection_id),
                        "review": check.model_dump(), "audit_round": self.state["audit_round"], **decision}
            for issue in review.issues:
                if issue.objection.resolution == "review_disagreement":
                    # A resume replays the same review: the disagreement is on record once, not once per resume.
                    recorded = self.state.setdefault("review_disagreements", [])
                    if issue.objection.model_dump() not in recorded:
                        recorded.append(issue.objection.model_dump())
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
        if dossier.assembled:
            # One call over the answers and their findings: about 450 000 characters on the runs of 2026-10-01, which
            # the 1 000 000-token windows of Sonnet and Opus 5.5 take (claude_code.prompt_limit).
            payload = {"brief": quality_brief(self.config), **assembled_assessment_material(dossier, review, context)}
        else:
            payload = {"brief": quality_brief(self.config), "dossier": dossier.model_dump(), "sources": context,
                       "finding_support": [r.model_dump() for r in review.finding_support],
                       "source_assessments": [a.model_dump() for a in review.source_assessments]}
        if accepted:
            text += " " + instructions("accepted_gaps")
            payload["accepted_gaps"] = [{"task_id": tid, **gap} for tid, gap in accepted.items()]
        if not dossier.assembled and len(text) + chars(payload) > PROMPT_BUDGET_CHARS:
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
        # A round whose assessment is complete is rebuilt from its record, whatever the assessment prompt says today.
        record = self.assessed_record(folder) if dossier.assembled else None
        previous = self.previous_assessment(folder) if dossier.assembled and record is None else None
        notes, limits, follow = [], set(), {}
        if record is not None:
            self.save("Die Bewertung dieser Prüfrunde liegt vor; die Runde wird mit ihr fortgesetzt")
            assessment = ResearchAssessment.model_validate({"requirements": record["requirements"], "issues": record["issues"]})
            notes, limits = list(record.get("script_notes", [])), set(record.get("source_limits", []))
            follow = {"recorded": set(record.get("recorded_limits", []))}
        elif previous is None:
            self.save("Bewertung des Gesamtdossiers gegen alle Leitfragen läuft")
            assessment = self.call(folder, "assessment", ResearchAssessment, text + "\n" + json.dumps(payload, ensure_ascii=False),
                                   validate=lambda candidate, final: check_assessment(self.config, dossier,
                                                                                       cite_findings(dossier, candidate)))
        else:
            assessment, notes, limits, follow = self.follow_up_assessment(folder, text, payload, previous, dossier, tasks)
        report = quality_report(self.config, dossier, discovery, self.index, assessment,
                                [*(i.reason for i in review.issues), *self.upheld_objections().values()],
                                accepted=accepted, gap_probes=self.probe_declared_gaps(dossier),
                                review_limitations=self.review_limitations())
        if dossier.assembled:
            # A requirement the follow-up judged unmet for a limit of the sources is recorded, not researched again; so is
            # one whose questions did not change since its earlier objection was noted, disputed or accepted as a gap.
            recorded = follow.get("recorded", set())
            for row in report["requirements"]:
                if row["requirement_id"] in limits and not row["passed"]:
                    row["source_limit"] = True
                elif row["requirement_id"] in recorded and not row["passed"]:
                    row["recorded_limit"] = True
            if notes:
                report["script_notes"] = notes
            # Which questions each objection may reopen, fixed by code before the routing (objection_scopes).
            routing = (record or {}).get("routing_scope") or {
                "issues": follow.get("issues", []),
                "requirements": self.requirement_routes(report["requirements"], tasks, dossier, follow)}
            report["routing_scope"] = routing
            if record is None:
                # This round's verdicts and the answers they judged: the next round's follow-up starts from them, and a
                # resume of this round rebuilds it from them (assessed_record).
                save_value(folder / "assessment_merged.json", {
                    "requirements": [r.model_dump() for r in assessment.requirements], "issues": list(assessment.issues),
                    "script_notes": notes, "source_limits": sorted(limits), "recorded_limits": sorted(recorded),
                    "passed": [row["requirement_id"] for row in report["requirements"] if row["passed"]],
                    "routing_scope": routing})
                self.state.update(assessed_round=self.state["audit_round"],
                                  assessed_answers={tid: digest(row["answer"]) for tid, row in self.state["tasks"].items()
                                                    if row.get("answer")})
            revalidated = [{"task_id": task.id, "question": task.question, "count": self.state["tasks"][task.id]["revalidations"]}
                           for task in tasks if self.state["tasks"][task.id].get("revalidations")]
            if revalidated:
                report["revalidations"] = revalidated
        if report["passed"] and self.noted_limits_remain(report):
            report["passed_with_noted_limits"] = True
        if self.state.get("disputed_objections"):
            report["disputed_objections"] = list(self.state["disputed_objections"].values())
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

    def previous_assessment(self, folder):
        """The last assembled round's verdicts, for a follow-up assessment from the second assembled round on. None for
        the first such round, and for a round that already holds a whole assessment (begun before follow-ups existed),
        so a resume asks the call it saved."""
        if (folder / "assessment.json").exists():
            return None
        first = int(self.state["rebuilds"][0]["from_audit_round"]) + 1 if self.state.get("rebuilds") else 0
        for number in range(int(self.state["audit_round"]) - 1, first - 1, -1):
            prior = self.folder / "synthesis" / f"audit_{number:02d}"
            if (prior / "assessment_merged.json").exists():
                return {**read_value(prior / "assessment_merged.json"), "audit_round": number}
            if (prior / "assessment.json").exists():
                # A round assessed before this record existed (2026-10-02): its whole assessment is the record.
                value = json.loads((prior / "assessment.json").read_text(encoding="utf-8"))["value"]
                return {"requirements": value["requirements"], "issues": value["issues"], "script_notes": [],
                        "source_limits": [], "audit_round": number}
        return None

    def changed_since(self, previous):
        """The questions whose answer changed since the previous assessment: by the answers it judged, or, for a
        round assessed before they were recorded, by the model calls made for a question since that assessment."""
        rows = self.state["tasks"]
        if self.state.get("assessed_round") == previous["audit_round"] and "assessed_answers" in self.state:
            assessed = self.state["assessed_answers"]
            return sorted(tid for tid, row in rows.items() if row.get("answer") and digest(row["answer"]) != assessed.get(tid))
        timings = self.state.get("call_timings", [])
        last = max((i for i, t in enumerate(timings) if str(t.get("name", "")).startswith("assessment")), default=-1)
        return sorted({t["task"] for t in timings[last + 1:] if t.get("task") in rows and rows[t["task"]].get("answer")})

    def assessed_record(self, folder):
        """This round's saved assessment (``assessment_merged.json``) when the answers it judged are still the current
        ones, else None. A resume rebuilds the round from it instead of checking the assessment's prompt again: a run
        stopped during the routing (quota, budget, rejections) and resumed after an update that changed an assessment
        prompt or the assembled material otherwise raised ``invalid_research_checkpoint`` (2026-10-02)."""
        path = folder / "assessment_merged.json"
        if not path.exists() or self.state.get("assessed_round") != self.state["audit_round"]:
            return None
        current = {tid: digest(row["answer"]) for tid, row in self.state["tasks"].items() if row.get("answer")}
        return read_value(path) if self.state.get("assessed_answers") == current else None

    def settled_requirements(self, previous, candidates):
        """The failed requirements among ``candidates`` whose objection of the previous round left nothing to research:
        every question it was routed to noted it, disputed it or holds an accepted gap (SETTLED_OUTCOMES). Rows of
        earlier ledgers carry no requirement id and are matched by the requirement's reason."""
        saved = self.state.get("objection_outcomes") or {}
        if saved.get("audit_round") != previous["audit_round"]:
            return set()
        reasons = {r["requirement_id"]: r.get("reason", "") for r in previous["requirements"]}
        settled = set()
        for rid in candidates:
            rows = [row for row in saved["rows"] if row.get("requirement_id") == rid or (
                "requirement_id" not in row and reasons.get(rid) and str(row.get("objection", "")).startswith(reasons[rid]))]
            outcomes = [outcome for row in rows for outcome in row.get("tasks", {}).values()]
            if outcomes and all(outcome in SETTLED_OUTCOMES for outcome in outcomes):
                settled.add(rid)
        return settled

    def follow_up_scope(self, previous, dossier, tasks):
        """What a follow-up assessment judges again: a requirement a changed question serves, one whose cited findings
        changed, and one that failed for a gap research can close. Every other requirement keeps its verdict.

        A failed requirement whose questions did not change and whose objection the last routing noted, disputed or
        sent to an accepted gap keeps its verdict as a recorded limit (``recorded_limits``). Before (2026-10-02), such
        a requirement was judged and routed again every round: Asimov's rq_005 and rq_011 failed in all seven rounds,
        and in round six 4 of 12 routed objections were such rows, though nothing they depend on had changed."""
        changed = self.changed_since(previous)
        owner = {fid: row.task_id for row in dossier.answers for fid in row.finding_ids}
        rows = {r["requirement_id"]: r for r in previous["requirements"]}
        passed = (set(previous["passed"]) if "passed" in previous
                  else {rid for rid, r in rows.items() if all(r[key] for key in CRITERIA)})
        failed = (set(rows) - passed - set(previous.get("source_limits", []))
                  - set(previous.get("recorded_limits", [])))
        serving = {rid for task in tasks if task.id in changed for rid in task.requirement_ids}
        touched = {rid for rid, r in rows.items() if any(owner.get(fid) in changed or fid not in owner for fid in r["finding_ids"])}
        recorded = self.settled_requirements(previous, failed - serving - touched)
        return {"previous_round": previous["audit_round"], "changed_tasks": changed,
                "requirements": [r["id"] for r in requirements_for(self.config)
                                 if r["id"] not in rows or r["id"] in (failed - recorded) | serving | touched],
                "recorded_limits": sorted(recorded)}

    def previous_outcomes(self, previous, changed):
        """What became of each objection of the previous round, as the follow-up reads it."""
        saved = self.state.get("objection_outcomes") or {}
        if saved.get("audit_round") == previous["audit_round"]:
            # The objection and its outcomes; the requirement and remedy beside them are for the code (settled_requirements).
            return [{"objection": row["objection"], "tasks": row["tasks"]} for row in saved["rows"]]
        return [{"objection": issue, "tasks": {}} for issue in previous.get("issues", [])]

    def requirement_routes(self, rows, tasks, dossier, follow):
        """Per unmet requirement of this round, the questions its objection may reopen: those that serve it and those
        whose findings its verdict cites. A requirement that passed last round and fails now fails for a change, so in a
        follow-up only its changed questions may be reopened. ``remedy`` is the follow-up's: research or limit."""
        owner = {fid: row.task_id for row in dossier.answers for fid in row.finding_ids}
        changed, failed_before = follow.get("changed"), follow.get("failed_before", set())
        routes = {}
        for row in rows:
            if row["passed"] or row.get("source_limit") or row.get("recorded_limit"):
                continue
            rid = row["requirement_id"]
            allowed = {t.id for t in tasks if rid in t.requirement_ids} | {owner[f] for f in row["finding_ids"] if f in owner}
            if changed is not None and rid not in failed_before:
                allowed = (allowed & changed) or allowed
            routes[rid] = {"task_ids": sorted(allowed), "remedy": follow.get("remedies", {}).get(rid)}
        return routes

    def noted_limits_remain(self, report):
        """Whether a passing report rests on recorded limits: an unmet requirement (a source limit, a recorded limit),
        what the assessment noted beside a met one, a script note, or an objection noted instead of researched."""
        return bool(any(not row["passed"] or row.get("noted") for row in report["requirements"])
                    or report.get("script_notes") or self.state.get("noted_objections"))

    def follow_up_assessment(self, folder, text, payload, previous, dossier, tasks):
        """From the second assembled round on, the assessment judges again only what changed or is still open
        (follow_up_scope); every other requirement keeps its verdict. An issue stops the run only when research can
        close it and it concerns a changed answer; a limit of the sources or a point about an unchanged answer goes to
        the script notes. Before (2026-10-02), each round judged the whole again: verdicts flipped without a change
        (Transformer's rq_008 four times in five rounds), every round raised four to six points such as "only the
        abstract was read" again, and each reworked answer gave the next round new details, so the loop never settled.
        The scope is saved before the call (``assessment_scope.json``), so a resume asks the same question.

        Returns the merged assessment, the script notes, the source limits and what the routing needs (``recorded``
        limits, the changed questions, the blocking issues with the changed questions each may reopen, the remedy of
        each judged requirement, and the requirements that had failed before)."""
        scope_path = folder / "assessment_scope.json"
        if scope_path.exists():
            scope = read_value(scope_path)
        else:
            scope = self.follow_up_scope(previous, dossier, tasks)
            save_value(scope_path, scope)
        rows = {r["requirement_id"]: r for r in previous["requirements"]}
        known = {f.id for f in dossier.findings}
        fresh, issues = {}, []
        if scope["requirements"]:
            self.save(f"Folgebewertung: {len(scope['requirements'])} Leitfragen und {len(scope['changed_tasks'])} "
                      "geänderte Antworten werden neu beurteilt, der Rest behält sein Urteil")
            follow_up = {"requirements_in_scope": scope["requirements"], "changed_tasks": scope["changed_tasks"],
                         "previous_requirements": [{k: r[k] for k in ("requirement_id", *CRITERIA, "reason", "missing")}
                                                   for r in previous["requirements"]],
                         "previous_issues": self.previous_outcomes(previous, scope["changed_tasks"]),
                         "script_notes": previous.get("script_notes", [])}
            result = self.call(folder, "assessment_followup", FollowUpAssessment,
                               text + " " + instructions("research_assessment_followup") + "\n"
                               + json.dumps({**payload, "follow_up": follow_up}, ensure_ascii=False),
                               validate=lambda candidate, final: check_follow_up(dossier, candidate, scope, [t.id for t in tasks]))
            fresh = {r.requirement_id: r for r in result.requirements}
            issues = result.issues
        merged = []
        for requirement in requirements_for(self.config):
            rid = requirement["id"]
            if rid in fresh:
                merged.append(RequirementAssessment.model_validate(fresh[rid].model_dump(exclude={"remedy"})))
            else:
                row = rows[rid]
                merged.append(RequirementAssessment.model_validate({**{key: row[key] for key in RequirementAssessment.model_fields},
                                                                    "finding_ids": [f for f in row["finding_ids"] if f in known]}))
        changed = set(scope["changed_tasks"])
        blocking = [i for i in issues if i.remedy == "research" and set(i.task_ids) & changed]
        texts = [i.text for i in blocking]
        notes = list(dict.fromkeys([*previous.get("script_notes", []), *(i.text for i in issues if i.text not in texts)]))
        limits = ({rid for rid in previous.get("source_limits", []) if rid not in fresh}
                  | {rid for rid, row in fresh.items() if row.remedy == "limit"})
        recorded = ({rid for rid in previous.get("recorded_limits", []) if rid not in fresh}
                    | set(scope.get("recorded_limits", []))) - limits
        passed_before = (set(previous["passed"]) if "passed" in previous
                         else {rid for rid, r in rows.items() if all(r[key] for key in CRITERIA)})
        follow = {"recorded": recorded, "changed": changed,
                  # A blocking issue may reopen only the changed questions it names (design rule: only new defects in
                  # changed material and unresolved earlier objections block).
                  "issues": [{"text": i.text, "task_ids": sorted(set(i.task_ids) & changed), "remedy": i.remedy}
                             for i in blocking],
                  "remedies": {rid: row.remedy for rid, row in fresh.items()},
                  "failed_before": set(rows) - passed_before}
        return ResearchAssessment(requirements=merged, issues=texts), notes, limits, follow

    def settled_findings(self, folder, revision, dossier, context, expected):
        """What the last round's final review already settled, for a targeted audit from the second round on.

        A finding unchanged since that review, whose receipt passed, that drew no issue and that no objection
        still to be checked names, keeps its receipt; a closed objection whose findings are all unchanged stays
        closed. Only the rest is reviewed again: without this, every round re-reviewed the whole dossier and
        found new details in unchanged findings as well, so the loop never settled (Asimov, 2026-09-27: 9 and
        then 16 new objections on unchanged findings). None means a whole review: the first round, a
        correction revision, a round begun as a whole review, or no saved receipt. The choice is saved with
        the round (``grounding_N_targeted.json``), so a resume keeps it."""
        marker = folder / f"grounding_{revision}_targeted.json"
        if marker.exists():
            return read_value(marker)
        if (revision != 0 or int(self.state.get("audit_round", 0)) < 1 or not self.state.get("seed_dossier")
                or any(folder.glob(f"grounding_{revision}_part_*.json")) or (folder / f"grounding_{revision}.json").exists()):
            return None
        previous = self.folder / "synthesis" / f"audit_{int(self.state['audit_round']) - 1:02d}"
        review = None
        for number in (2, 1, 0):
            merged, whole = previous / f"grounding_{number}_merged.json", previous / f"grounding_{number}.json"
            if merged.exists():
                review = json.loads(merged.read_text(encoding="utf-8"))["review"]
                break
            if whole.exists():
                review = json.loads(whole.read_text(encoding="utf-8"))["value"]
                break
        if review is None:
            return None
        before = {f["id"]: digest(f) for f in self.state["seed_dossier"]["findings"]}
        unchanged = {f.id for f in dossier.findings if before.get(f.id) == digest(f.model_dump())}
        read = references(context)
        receipts = {r["finding_id"]: r for r in review.get("finding_support", [])}
        issued = {i["finding_id"] for i in review.get("issues", [])}
        closed = {c["objection_id"]: c for c in review.get("objection_checks", [])
                  if c["verdict"] == "closed" and set(c.get("references", [])) <= read}
        checks = {oid: closed[oid] for oid, objection in expected.items()
                  if oid in closed and set(objection.get("finding_ids") or []) <= unchanged}
        named = {fid for oid, objection in expected.items() if oid not in checks for fid in objection.get("finding_ids") or []}
        kept = sorted(fid for fid in unchanged if fid in receipts and fid not in issued and fid not in named
                      and not blocks(FindingSupport.model_validate(receipts[fid]))
                      and set(receipts[fid].get("references", [])) <= read)
        if len(kept) == len(dossier.findings) and len(checks) < len(expected):
            return None  # an objection to check needs a finding under review to travel with
        source_of = {section["reference"]: source["source_id"] for source in context for section in source["sections"]}
        cited = {source_of[e.reference] for f in dossier.findings if f.id in set(kept) for e in f.evidence
                 if e.reference in source_of}
        settled = {"findings": kept, "support": [receipts[fid] for fid in kept], "checks": list(checks.values()),
                   "source_assessments": [a for a in review.get("source_assessments", []) if a["source_id"] in cited]}
        save_value(marker, settled)
        return settled

    def grounding_review(self, folder, revision, dossier, context, expected, well_formed):
        """One review of the whole dossier when it fits the window; otherwise the same review in
        parts. Each part sees the dossier outline, its own findings with their passages and the
        objections that concern them, and the parts merge into one receipt checked as a whole.
        From the second round on only what changed or is still contested is reviewed (settled_findings)."""
        text = (EVIDENCE_INSTRUCTIONS + SYNTHESIS_INSTRUCTIONS + ROUTING_INSTRUCTIONS + " " +
                instructions("dossier_audit"))
        brief, tasks = quality_brief(self.config), self.state["plan"]["tasks"]
        settled = self.settled_findings(folder, revision, dossier, context, expected)
        whole = {"brief": brief, "dossier": dossier.model_dump(), "sources": context, "open_objections": expected,
                 "tasks": tasks, "verified_baseline": self.state.get("verified_baseline", [])}
        if settled is None and len(text) + chars(whole) <= PROMPT_BUDGET_CHARS:
            return self.call(folder, f"grounding_{revision}", SourceReview, text + "\n" + json.dumps(whole, ensure_ascii=False),
                             validate=well_formed)
        baseline = compact_baseline(self.state.get("verified_baseline", []))
        outline = dossier.model_dump(exclude={"findings"})
        everything = list(dossier.findings)
        kept = set(settled["findings"]) if settled else set()
        findings = [f for f in everything if f.id not in kept]
        if settled:
            carried = {check["objection_id"] for check in settled["checks"]}
            expected = {oid: objection for oid, objection in expected.items() if oid not in carried}
            self.save(f"Gezielte Quellenprüfung: {len(findings)} geänderte oder beanstandete Befunde, "
                      f"{len(kept)} unverändert bestandene übernommen")
        # Every objection travels with the first finding it names, so each is checked exactly once.
        objections_of = {}
        for identifier, objection in expected.items():
            named = objection.get("finding_ids") or []
            objections_of.setdefault(named[0] if named else findings[0].id, []).append(identifier)

        def part_payload(part):
            part_ids = {f.id for f in part}
            objections = {oid: expected[oid] for f in part for oid in objections_of.get(f.id, [])}
            cited = {e.reference for f in part for e in f.evidence}
            named = {ref for objection in objections.values() for ref in objection.get("evidence_refs", [])} - cited
            payload = {"brief": brief, "dossier": {**outline, "findings": [f.model_dump() for f in part]},
                       "other_findings": [{"id": f.id, "kind": f.kind, "statement": f.statement}
                                          for f in everything if f.id not in part_ids],
                       "sources": select_context(context, cited), "open_objections": objections,
                       "tasks": tasks, "verified_baseline": baseline}
            if named:
                payload["objection_sources"] = select_context(context, named)
            return payload

        # Every part repeats the outline, the other findings, the tasks and the baseline. Once that
        # shared context leaves less than ANSWER_BUDGET_CHARS of the budget, a part still checks that
        # much of its own material rather than one finding per call, and needs the larger window.
        shared = len(text) + len(PART_NOTE) + chars(part_payload([]))
        room = max(PROMPT_BUDGET_CHARS, shared + ANSWER_BUDGET_CHARS)
        parts, start = [], 0
        while start < len(findings):
            size = 1
            while (start + size < len(findings)
                   and len(text) + len(PART_NOTE) + chars(part_payload(findings[start:start + size + 1])) <= room):
                size += 1
            parts.append(findings[start:start + size])
            start += size
        calls, reviews = [], []
        for index, part in enumerate(parts):
            payload = part_payload(part)
            note = PART_NOTE + (OBJECTION_SOURCES_NOTE if "objection_sources" in payload else "")
            calls.append((f"grounding_{revision}_part_{index:03d}", text + note + "\n" + json.dumps(payload, ensure_ascii=False),
                          lambda review, final, part=part, objections=payload["open_objections"]:
                              well_formed(review, final, findings=part, objections=objections)))
        for index, (name, prompt, check) in enumerate(calls):
            self.save(f"Quellenprüfung in Teilen: Teil {index + 1} von {len(parts)} mit {len(parts[index])} Befunden")
            reviews.append(self.call(folder, name, SourceReview, prompt, validate=check))

        def merge(reviews):
            merged = {"issues": [], "limitations": []}
            for review in reviews:
                for key, value in review.model_dump().items():
                    if isinstance(value, list):
                        merged.setdefault(key, []).extend(value)
                    else:
                        merged.setdefault(key, value)
            if settled:
                # What the last round settled joins the new receipts, so the whole is checked as one review.
                for key, rows in (("finding_support", settled["support"]), ("objection_checks", settled["checks"]),
                                  ("source_assessments", settled["source_assessments"])):
                    merged.setdefault(key, []).extend(rows)
            return SourceReview.model_validate(merged)

        carried = [SourceAssessment.model_validate(a) for a in (settled or {}).get("source_assessments", [])]
        while True:
            review = merge(reviews)
            try:
                well_formed(review, True)
                break
            except AppError as error:
                # The parts passed on their own and the whole does not, typically because the merged review keeps the
                # assessment of a shared source that claims the least independence (collapse_assessments) and a part's
                # "independently tested" receipt rests on that source. Before (2026-10-02) the run stopped, and every
                # resume replayed the same parts into the same failure. Each part is now checked under the other parts'
                # assessments of the sources it cites; one that fails is asked again with the defect named, like any
                # rejection (cached_call), and the whole is checked again. Unattributable defects stop as before.
                changed = False
                for index, (name, prompt, check) in enumerate(calls):
                    others = [a for number, other in enumerate(reviews) if number != index
                              for a in other.source_assessments] + carried

                    def consistent(candidate, final, check=check, others=others):
                        check(candidate, final)
                        joined = candidate.model_copy(update={"source_assessments": [*candidate.source_assessments, *others]})
                        try:
                            check(joined, final)
                        except AppError as defect:
                            cited = {a.source_id for a in candidate.source_assessments}
                            seen = sorted({f"{a.source_id}: {a.independence}" for a in others if a.source_id in cited})
                            raise AppError(f"{defect} The merged review keeps, for each source, the assessment that claims "
                                           "the least independence, and other parts of this review assessed "
                                           + (", ".join(seen) or "the sources these findings cite") + ". The receipts of "
                                           "this part must hold under those assessments, or name an issue for each finding "
                                           "that no longer passes.", code=defect.code, status="blocked") from defect

                    self.save(f"Quellenprüfung in Teilen: Teil {index + 1} von {len(parts)} wird gegen die übrigen Teile geprüft")
                    again = self.call(folder, name, SourceReview, prompt, validate=consistent)
                    if again.model_dump() != reviews[index].model_dump():
                        reviews[index], changed = again, True
                if not changed:
                    raise error
        write_json(folder / f"grounding_{revision}_merged.json", {"parts": len(parts), "findings_per_part": [len(p) for p in parts],
                   "budget_chars": room, "review": review.model_dump(mode="json"),
                   **({"targeted": {"carried_findings": len(kept), "carried_checks": len(settled["checks"])}} if settled else {})})
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

    def upheld_objections(self):
        """This round's disputed objections the editor upheld, as the text that sends each back to its question."""
        return {oid: f"Einwand von der Redaktion aufrechterhalten: {row['objection']['reason']} {row['objection']['correction']}"
                for oid, row in self.state.get("disputed_objections", {}).items()
                if row["audit_round"] == self.state["audit_round"] and row["decision"] == "objection" and row.get("objection")}

    @staticmethod
    def requirement_objection(row):
        """The objection text of an unmet requirement, as the routing reads it."""
        return row["reason"] + " " + " ".join(row["missing"])

    def objections(self, review, report):
        # A requirement unmet only for a limit of the sources (follow_up_assessment), or kept as a recorded limit because
        # nothing it depends on changed since its objection was noted (follow_up_scope), is recorded, not researched.
        return list(dict.fromkeys([*(i.reason for i in review.issues), *report["blocking_gaps"],
            *(self.requirement_objection(row) for row in report["requirements"]
              if not row["passed"] and not row.get("source_limit") and not row.get("recorded_limit"))]))

    def objection_scopes(self, objections, report):
        """Per objection, what code fixed before the routing (report["routing_scope"], an assembled audit): the tasks
        it may reopen, its requirement, the findings its verdict cites and the assessment's remedy. None where nothing
        is fixed: a composed dossier's objections, a first assessment's issues, a coverage gap."""
        scope = report.get("routing_scope") or {}
        known = {}
        for issue in scope.get("issues", []):
            known.setdefault(issue["text"], []).append({"task_ids": issue["task_ids"], "requirement_id": None,
                                                        "finding_ids": [], "remedy": issue.get("remedy")})
        for row in report.get("requirements", []):
            route = scope.get("requirements", {}).get(row["requirement_id"])
            if route is not None:
                known.setdefault(self.requirement_objection(row), []).append({
                    "task_ids": route["task_ids"], "requirement_id": row["requirement_id"],
                    "finding_ids": list(row["finding_ids"]), "remedy": route.get("remedy")})
        scopes = []
        for text in objections:
            rows = known.get(text)
            if not rows:
                scopes.append(None)
                continue
            # The same text from two sources: either may reopen what it allows, and research wins over anything else.
            allowed = None if any(not r["task_ids"] for r in rows) else sorted({t for r in rows for t in r["task_ids"]})
            remedies = {r["remedy"] for r in rows}
            scopes.append({"task_ids": allowed, "requirement_id": next((r["requirement_id"] for r in rows if r["requirement_id"]), None),
                           "finding_ids": list(dict.fromkeys(f for r in rows for f in r["finding_ids"])),
                           "remedy": "research" if "research" in remedies else next(iter(remedies - {None}), None)})
        return scopes

    def tolerate(self, dossier, review, report, finish=None):
        """Every remaining objection targets an accepted gap, is noted as a limit or is a recorded review disagreement:
        finish, with those objections on record. ``finish`` is the editor's request to finish with residual objections
        (run_budget.approve_residual_finish): the audit's remaining objections are recorded the same way.

        ``passed_with_accepted_gaps`` says whether a gap was accepted; ``passed_with_noted_limits`` whether the research
        passed on recorded limits (unmet requirements, script notes, noted objections). Before (2026-10-02), every such
        finish said accepted gaps: both completed runs had none and still published ``accepted_gaps_remaining``.
        A remaining objection is a recorded limit too, a disputed one included: a finish whose objections were all
        review disagreements published ``no_remaining_issues`` beside them (2026-10-02 review)."""
        residual = self.objections(review, report)
        report = {**report, "passed": True, "passed_with_accepted_gaps": bool(self.accepted_summary()),
                  "passed_with_noted_limits": self.noted_limits_remain(report) or bool(residual),
                  "residual_objections": residual}
        if finish:
            report.update(passed_with_residual_objections=True, residual_note=finish.get("note", ""))
        self.write_gate(report)
        self.save("Abschluss mit dokumentierten Resteinwänden auf Wunsch der Redaktion" if finish else
                  "Verbliebene Einwände sind als Grenzen vermerkt oder betreffen akzeptierte Lücken; die Recherche wird abgeschlossen")
        return report

    @staticmethod
    def existing_objection(registry, closed, anchor, identifier):
        # Keep an unresolved objection stable even when a later reviewer paraphrases
        # its missing-evidence description. A new defect is allowed after explicit closure.
        # A different closure condition is a second defect of the same finding, not a reworded first
        # one: it gets an id of its own, and neither replaces the other (Asimov, round two: eleven
        # such defects refused the whole routing as "closure condition changed").
        current = anchor.model_dump()
        for old_id, old in registry.items():
            if old_id not in closed and all(old.get(key) == current.get(key)
                    for key in ("rule", "task_id", "criterion_index", "finding_ids", "closure_condition")):
                return old_id
        if (identifier in registry and identifier not in closed
                and registry[identifier].get("closure_condition") != anchor.closure_condition):
            return "obj_" + digest([identifier, anchor.closure_condition])[:16]
        return identifier

    def criterion_covered(self, anchor, remedy=None):
        """A completeness objection to a criterion the verified answer already treats with findings. It is noted as
        a limit of the dossier instead of reopening the question: such objections kept every audit round busy and the
        loop did not settle (2026-10-01, the user's choice: 178 of Transformer's 220 objections were of this kind; the
        first series added as many new objections per round as it closed). A criterion the answer leaves without any
        finding is still researched, and every other rule (support, sources, claims, synthesis, scope) still reopens.

        ``remedy`` is the assessment's own verdict on the objection where it gave one (a follow-up). A verified answer
        names findings for every criterion, so this test alone held for every criterion anchor, and whether a point was
        researched or noted depended on the rule label the router picked (2026-10-02). An objection the assessment says
        research can close (``research``) is therefore researched whatever its anchor; the rule above decides only
        where no remedy was given."""
        if remedy == "research":
            return False
        if anchor is None or anchor.rule != "criterion" or anchor.criterion_index is None:
            return False
        answer = (self.state["tasks"].get(anchor.task_id) or {}).get("answer") or {}
        return any(c.get("index") == anchor.criterion_index and c.get("finding_ids") for c in answer.get("criteria", []))

    def reopen(self, dossier, review, report):
        """Route objections to tasks; return the ids reopened and the ids blocked for exhausted reopenings."""
        tasks = QuestionPlan.model_validate(self.state["plan"]).tasks
        composed = self.state.get("composed_findings")
        if composed and composed["dossier_hash"] != digest(dossier.model_dump()):
            raise AppError("Befundzuordnung passt nicht zum geprüften Dossier.",
                           code="invalid_research_checkpoint", status="blocked")
        owners = finding_owners(dossier, tasks, self.state["tasks"], composed["owners"] if composed else None)
        # An upheld disputed objection goes back to its question as it stands; the router never rewords it.
        upheld = self.upheld_objections()
        objections = [text for text in self.objections(review, report) if text not in set(upheld.values())]
        scopes = self.objection_scopes(objections, report)
        accepted = self.accepted_summary()
        context = complete_context(self.reader, [e.reference for f in dossier.findings for e in f.evidence])
        registry = self.state.setdefault("objections", {})
        closed = {c["objection_id"] for c in report.get("objection_checks", []) if c["verdict"] == "closed"}
        # Following the reviewer closes the disputed objection; the dispute itself stays in the report.
        closed |= {oid for oid, row in self.state.get("disputed_objections", {}).items()
                   if row["audit_round"] == self.state["audit_round"] and row["decision"] == "reviewer"}
        if dossier.assembled:
            # An assembled audit checks no closure: each round's assessment judges the whole again, so an earlier
            # objection it does not raise again is closed, and one it raises again is registered open anew.
            closed |= set(registry)
        # Which earlier objections this audit closed: the ledger shows each question's still open ones.
        self.state["closed_objections"] = sorted(closed)
        text = instructions("objection_routes")
        payload = {"objections": objections, "tasks": [t.model_dump() for t in tasks],
                   "anchored_issues": [i.objection.model_dump() for i in review.issues if i.objection],
                   "finding_owners": owners,
                   "answers": {t.id: self.state["tasks"][t.id]["answer"] for t in tasks},
                   "dossier": dossier.model_dump()}
        if accepted:
            text += " " + instructions("accepted_gaps")
            payload["accepted_gaps"] = [{"task_id": tid, **gap} for tid, gap in accepted.items()]
        scoped = dossier.assembled and any(scopes)
        if dossier.assembled:
            payload.update(assembled_routing_material(tasks, payload["answers"], dossier))
            if scoped:
                text += SCOPED_ROUTING_NOTE
        elif len(text) + chars(payload) > PROMPT_BUDGET_CHARS:
            # Many answers: the routing reads what an objection is matched against, not the receipts.
            payload.update(compact_routing_material(tasks, payload["answers"], review, dossier))

        def scope_rows(part_scopes):
            return [{"index": index, **scope} for index, scope in enumerate(part_scopes) if scope]

        def well_formed(routes, final, objections=objections, scopes=scopes):
            if (Counter(r.index for r in routes.routes) != Counter(range(len(objections))) or
                any(not set(r.task_ids) <= {t.id for t in tasks} for r in routes.routes)):
                raise AppError("Prüfeinwände sind nicht vollständig bestehenden Recherchefragen zugeordnet.",
                               code="invalid_question_routing", status="blocked")
            for route in routes.routes:
                if len(set(route.task_ids)) != len(route.task_ids) or not route.anchors:
                    raise AppError("Every routed task needs a specific evidence or criterion anchor.",
                                   code="invalid_question_routing", status="blocked")
                allowed = (scopes[route.index] or {}).get("task_ids")
                if allowed and not set(route.task_ids) <= set(allowed):
                    # The code-set scope of the objection (objection_scopes): only these questions may be reopened for it.
                    raise AppError(f"Objection {route.index} may reopen only {', '.join(allowed)} (objection_scopes); it was "
                                   f"routed to {', '.join(sorted(set(route.task_ids) - set(allowed)))} as well. Route it "
                                   "only to tasks from its task_ids, with an anchor for each.",
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
        # ".scoped": the routing with code-set scopes and the findings' statements (2026-10-02).
        tag = {"tag": f"{self.prompt_tag}.scoped"} if scoped else {}

        def ask_routing(name, prompt, validate):
            # Nothing is bound to a routing receipt until the whole routing is done, so one that answered an earlier
            # prompt (an update since the stop) is set aside and asked again instead of stopping the run.
            if retire_stale_receipt(folder, name, ReopenPlan, prompt):
                self.save("Die Zuordnung der Einwände wird nach einer Aktualisierung neu angefragt")
            return self.call(folder, name, ReopenPlan, prompt, validate=validate, **tag)

        if not objections:
            routes = None
        elif len(objections) <= ROUTING_PART_SIZE:
            routes = ask_routing("routes", text + "\n" + json.dumps({**payload, **({"objection_scopes": scope_rows(scopes)}
                                                                          if scoped else {})}, ensure_ascii=False),
                           well_formed)
        else:
            # Many objections: routed in parts over the same material, merged into one plan that is
            # checked as a whole, the way a split review merges.
            parts = split_objections(objections, ROUTING_PART_SIZE)
            part_scopes = split_objections(scopes, ROUTING_PART_SIZE)
            plans = []
            for number, part in enumerate(parts):
                self.save(f"Zuordnung der Einwände: Teil {number + 1} von {len(parts)} mit {len(part)} Einwänden")
                extra = {"objection_scopes": scope_rows(part_scopes[number])} if scoped else {}
                plans.append(ask_routing(f"routes_part_{number:03d}", text + ROUTING_PART_NOTE + "\n"
                                   + json.dumps({**payload, "objections": part, **extra}, ensure_ascii=False),
                                   lambda plan, final, part=part, number=number:
                                       well_formed(plan, final, objections=part, scopes=part_scopes[number])))
            routes = merge_route_plans(plans, ROUTING_PART_SIZE)
            well_formed(routes, True)
            write_json(folder / "routes_merged.json", {"parts": len(parts), "objections_per_part": [len(p) for p in parts],
                       "routes": routes.model_dump(mode="json")})
        reasons, disagreements, opened, spent = {}, [], set(), []
        # Per task, how its objections resolve and which passages they cite: a task whose objections all
        # say "revise" (the read passages suffice) only corrects its answer instead of researching again.
        resolutions, cited = {}, {}
        for oid, text in upheld.items():
            anchor = registry[oid]
            if anchor["task_id"] in accepted:
                continue
            reasons.setdefault(anchor["task_id"], []).append(text)
            resolutions.setdefault(anchor["task_id"], set()).add(anchor["resolution"])
            cited.setdefault(anchor["task_id"], []).extend(anchor.get("evidence_refs", []))
        for route in (routes.routes if routes else []):
            for task_id in route.task_ids:
                routed = False
                for anchor in [a for a in route.anchors if a.task_id == task_id]:
                    identifier = validate_objection(anchor, tasks, dossier.findings, context,
                                                    target_task=task_id, owners=owners)
                    if anchor.resolution == "review_disagreement":
                        # An unsupported demand starts no research (objection_routes.txt): it is recorded and
                        # the other objections are still routed. Stopping here would leave the objections
                        # registered so far in the state, and the resumed audit would no longer match its receipts.
                        disagreements.append(anchor.model_dump())
                        continue
                    remedy = (scopes[route.index] or {}).get("remedy")
                    if task_id not in accepted and self.criterion_covered(anchor, remedy):
                        self.state.setdefault("noted_objections", {})[identifier] = {
                            **anchor.model_dump(), "id": identifier, "objection": objections[route.index],
                            "audit_round": self.state["audit_round"], "status": "noted",
                            **({"remedy": remedy} if remedy else {})}
                        continue
                    routed = True
                    resolutions.setdefault(task_id, set()).add(anchor.resolution)
                    cited.setdefault(task_id, []).extend(anchor.evidence_refs)
                    identifier = self.existing_objection(registry, closed, anchor, identifier)
                    if task_id in accepted:
                        self.state.setdefault("accepted_gap_objections", {})[identifier] = {
                            **anchor.model_dump(), "id": identifier, "objection": objections[route.index], "status": "accepted_gap"}
                    else:
                        registry[identifier] = {**anchor.model_dump(), "id": identifier, "status": "open"}
                        opened.add(identifier)
                if routed:
                    reasons.setdefault(task_id, []).append(objections[route.index] + " " + route.reason)
        if disagreements:
            recorded = self.state.setdefault("review_disagreements", [])
            recorded.extend(row for row in disagreements if row not in recorded)
        reopened, blocked = [], []
        for task_id, texts in reasons.items():
            if task_id in accepted:
                continue
            row = self.state["tasks"][task_id]
            if len(row["reopenings"]) >= self.state["limits"]["reopenings"] and dossier.assembled and row.get("answer"):
                # Its reworks are spent: the verified answer stays, and the objection is a limit (keep_spent_answers).
                noted = [oid for oid in opened if oid in registry and registry[oid].get("task_id") == task_id]
                self.note_after_reworks(task_id, texts, noted)
                opened.difference_update(noted)  # noted, no longer open (Transformer, 2026-10-02: two spent at once)
                spent.append(task_id)
                continue
            if len(row["reopenings"]) >= self.state["limits"]["reopenings"]:
                row.update(status="blocked", outcome="audit_block",
                           reason="Wiederholte Gesamtprüfung widerspricht dem Abschluss: " + " ".join(texts))
                blocked.append(task_id)
                continue
            row["reopenings"].append({"reason": texts, "previous_answer": row["answer"],
                                     "previous_verification": row.get("verification")})
            revise = resolutions.get(task_id) == {"revise"}
            previous_refs = [e["reference"] for f in (row["answer"] or {}).get("findings", []) for e in f.get("evidence", [])]
            row.update(status="researching", answer=None, draft_answer=row["reopenings"][-1]["previous_answer"],
                       feedback=[*texts, *([REVISE_NOTE] if revise else [])], step=0, no_progress=0, fallbacks=0, pending=None,
                       revise_only=revise, activity=("Einwand der Gesamtprüfung betrifft nur den Wortlaut: Antwort wird an den "
                       "gelesenen Stellen korrigiert") if revise else "Mit konkretem Einwand aus der Gesamtprüfung wieder geöffnet")
            if revise:
                # The passages the answer cited and the objections name: everything the correction may rest on.
                row["current_refs"] = [ref for ref in dict.fromkeys([*previous_refs, *cited.get(task_id, [])])
                                       if ref in self.reader.lookup]
            reopened.append(task_id)
        # A question noted after its reworks keeps its answer, so it did not change: its dependents stay, and it is
        # itself revalidated when a prerequisite of it was reopened (Ontologies, 2026-10-02: t53 beside t52).
        # question_dependencies.revalidate counts each revalidation in row["revalidations"].
        dirty = invalidate_dependents(self.state, [t for t in reasons if t not in accepted and t not in spent])
        if dossier.assembled:
            # What became of each objection, for the next round's follow-up assessment; the requirement and remedy let the
            # next round's scope recognise a requirement whose objection left nothing to research (settled_requirements).
            self.state["objection_outcomes"] = {"audit_round": self.state["audit_round"], "rows": [
                {"objection": objections[route.index],
                 "tasks": {tid: "reworked" if tid in reopened else "blocked" if tid in blocked else "accepted_gap"
                           if tid in accepted else "disputed" if all(a.resolution == "review_disagreement"
                                                                     for a in route.anchors if a.task_id == tid)
                           else "noted" for tid in route.task_ids},
                 "requirement_id": (scopes[route.index] or {}).get("requirement_id"),
                 "remedy": (scopes[route.index] or {}).get("remedy")}
                for route in (routes.routes if routes else [])]}
            # Registered open again by this routing: not closed after all.
            self.state["closed_objections"] = sorted(closed - opened)
        # An assembled dossier is made again from the answers; only a composed one is the next round's seed.
        self.state.update(seed_dossier=None if dossier.assembled else dossier.model_dump(), dirty_tasks=dirty,
                          finding_owners=owners, audit_round=self.state["audit_round"]+1, phase="questions")
        self.save("Konkrete Einwände werden ihren ursprünglichen Recherchefragen zugeordnet" if reopened or blocked
                  else "Verbliebene Einwände betreffen nur akzeptierte Lücken oder unbelegte Prüfforderungen" if disagreements
                  else "Verbliebene Einwände sind als Grenzen vermerkt oder betreffen akzeptierte Lücken")
        return reopened, blocked

    def note_after_reworks(self, task_id, texts, identifiers, basis="reworks_spent", **extra):
        """Record the objections to a question whose two reworks are spent as limits of its verified answer
        (``noted_objections`` with basis ``reworks_spent``); they leave the register of open objections. A rework that
        ended blocked (keep_spent_answers) records its objections the same way, with basis ``rework_blocked``."""
        registry = self.state.setdefault("objections", {})
        noted = self.state.setdefault("noted_objections", {})
        text = " ".join(texts)
        prefix = "spent_" if basis == "reworks_spent" else "blocked_"
        for identifier in identifiers or [prefix + digest([task_id, text])[:16]]:
            noted[identifier] = {**registry.pop(identifier, {"id": identifier, "task_id": task_id}), "objection": text,
                                 "audit_round": self.state["audit_round"], "status": "noted", "basis": basis, **extra}

    @staticmethod
    def failed_rework(row):
        """The answer and verification a question had before its last rework, when that rework ended blocked without a
        verified answer (evidence, search or access block, or that block accepted as a gap). None otherwise; a question
        waiting for a prerequisite is decided when the prerequisite is (release_ready)."""
        if (row["status"] != "blocked" or row.get("answer") or row.get("outcome") == "prerequisite_block"
                or not row.get("reopenings") or row["reopenings"][-1].get("settled")):
            # ``settled``: that rework ended verified, and this block came from a later check of its answer
            # (question_dependencies.revalidate); the answer from before the reopening was rejected by an audit.
            return None
        last = row["reopenings"][-1]
        answer, verification = last.get("previous_answer"), last.get("previous_verification") or {}
        if not answer or verification.get("answer_hash") != digest(answer):
            return None
        return answer, verification

    def keep_spent_answers(self):
        """Prompt generation 3 (the user's choice, 2026-10-02): a question whose two reworks are spent keeps its last
        verified answer instead of stopping the run, and the objection that blocked it is noted as a limit. So is a
        question accepted as a gap while it still had a verified answer: before, accepting it dropped that answer
        from the dossier (Transformer, 2026-10-01: both synthesis questions, so requirement 10 lost its answer), and
        every audit round blocked another spent question, so the run stopped again after each decision.

        So is a question whose rework ended blocked (failed_rework): the rework found nothing that closes the objection,
        so the answer verified before it comes back and the objection is noted with basis ``rework_blocked``. Before
        (2026-10-02), the reopening had cleared that answer, the run stopped, and accepting the gap dropped a verified
        answer from the dossier; a residual finish kept only questions blocked for spent reworks."""
        if self.prompt_generation < ASSEMBLED_GENERATION:
            return []
        kept, reworked = [], False
        with self.guarded():
            closed = set(self.state.get("closed_objections", []))
            tasks = {task.id: task for task in QuestionPlan.model_validate(self.state["plan"]).tasks}
            for task_id, row in self.state["tasks"].items():
                answer, verification = row.get("answer"), row.get("verification") or {}
                spent = row.get("outcome") == "audit_block" or row.get("accepted_gap")
                open_ids = [oid for oid, objection in self.state.get("objections", {}).items()
                            if objection.get("task_id") == task_id and oid not in closed]
                restored = self.failed_rework(row)
                if restored is not None:
                    block = row.get("reason") or ""
                    self.note_after_reworks(task_id, row["reopenings"][-1].get("reason") or [block], open_ids,
                                            basis="rework_blocked", rework_block=block)
                    # The next follow-up reads this objection as noted, not as reworked (settled_requirements).
                    for outcome in (self.state.get("objection_outcomes") or {}).get("rows", []):
                        if outcome["tasks"].get(task_id) == "reworked":
                            outcome["tasks"][task_id] = "noted"
                    row.pop("accepted_gap", None)
                    row.update(status="verified", answer=restored[0], verification=restored[1],
                               outcome=restored[0].get("outcome", "supported_answer"), reason="", revise_only=False,
                               answer_locked=False, lock=None, pending=None,
                               activity="Die Nachbesserung fand keine neuen Belege; die zuletzt geprüfte Antwort bleibt, "
                                        "der Einwand steht als Grenze im Bericht")
                    self.recheck_kept(tasks[task_id], row)
                    kept.append(task_id)
                    reworked = True
                    continue
                if row["status"] != "blocked" or not spent or not answer or verification.get("answer_hash") != digest(answer):
                    continue
                reason = row.get("reason") or "Einwand nach zwei Nachbesserungen."
                self.note_after_reworks(task_id, [reason], open_ids)
                row.pop("accepted_gap", None)
                row.update(status="verified", outcome=answer.get("outcome", "supported_answer"), reason="",
                           activity="Nach zwei Nachbesserungen bleibt die zuletzt geprüfte Antwort; der Einwand steht als Grenze im Bericht")
                self.recheck_kept(tasks[task_id], row)
                kept.append(task_id)
            if kept:
                self.save("Teilfragen ohne weitere Nachbesserung behalten ihre zuletzt geprüfte Antwort; ihre Einwände stehen "
                          "als Grenzen im Bericht" if reworked else
                          "Zweimal nachgebesserte Teilfragen behalten ihre geprüfte Antwort; ihre Einwände stehen als Grenzen im Bericht")
        return kept

    def recheck_kept(self, task, row):
        """A kept answer whose verification bound other prerequisite answers than today's is checked against them
        again, as the resume does (prerequisites_current). A prerequisite reworked in the same round as this question
        passed its new answer, and the dependent came back verified against the old one (2026-10-02 review)."""
        if not prerequisites_current(task, self.state, row.get("verification")):
            revalidate(row, activity="Behaltene Antwort wird gegen die geänderte Voraussetzung nachgeprüft")

    def noted_after_reworks(self):
        """The objections noted against questions whose reworks are spent, as the quality report lists them, and those
        a rework could not close because it ended blocked (``basis: rework_blocked``, rendered in a section of its own)."""
        return [{"task_id": row.get("task_id"), "objection": row["objection"],
                 **({"basis": "rework_blocked", "rework_block": row.get("rework_block", "")}
                    if row.get("basis") == "rework_blocked" else {})}
                for row in self.state.get("noted_objections", {}).values()
                if row.get("basis") in {"reworks_spent", "rework_blocked"}]

    def noted_limits(self):
        """The completeness objections noted as limits (criterion_covered), as the quality report lists them."""
        return [row["objection"] for row in self.state.get("noted_objections", {}).values()
                if row.get("basis") not in {"reworks_spent", "rework_blocked"}]
