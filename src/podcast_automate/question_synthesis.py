"""Dossier synthesis from verified answers, the final audit and objection routing.

Mixed into ``QuestionResearch``. Composition edits only findings owned by dirty tasks; the audit
binds every objection to a fixed criterion or quality rule before a task may be reopened.
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
from .research_evidence import (EVIDENCE_INSTRUCTIONS, SYNTHESIS_INSTRUCTIONS, evidence_summary,
                                support_errors, validate_objection, validate_synthesis)
from .research_ledger import VERSION, save_value
from .research_models import ResearchDiscovery, ResearchDossier
from .research_patches import edit_dossier, repair_references
from .research_quality import ResearchAssessment, quality_brief, quality_report, render_quality
from .research_retrieval import merge_context, references
from .research_review import ROUTING_INSTRUCTIONS, SourceReview, needs_research
from .research_tasks import QuestionPlan, ReopenPlan
from .storage import atomic_text, digest, write_json


class SynthesisMixin:
    """Compose, audit and, when the audit fails, reopen only the concretely challenged tasks."""

    def compose(self):
        discovery = ResearchDiscovery.model_validate(self.state["discovery"])
        plan = QuestionPlan.model_validate(self.state["plan"])
        answers = [{"task": task.model_dump(), "answer": self.state["tasks"][task.id]["answer"],
                    "evidence_review": self.state["tasks"][task.id].get("verification", {}).get("review")}
                   for task in plan.tasks]
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
            for start in range(0, len(dirty), 4):
                batch = dirty[start:start+4]
                question_ids = {qid for item in batch for qid in item["task"]["question_ids"]}
                batch_ids = {item["task"]["id"] for item in batch}
                targets = editable_findings(owners, batch_ids, self.state["dirty_tasks"])
                legacy_targets = {fid for item in batch for fid in item["task"]["finding_ids"]}
                legacy_targets.update(fid for c in dossier.coverage if c.question_id in question_ids for fid in c.finding_ids)
                before = dossier
                dossier = edit_dossier(folder, f"batch_{start:03d}", dossier, discovery, context, self.config,
                    lambda p, s: self.generate(folder, f"batch_{start:03d}", p, s, f"{VERSION}.compose_patch"),
                    targets=targets, legacy_targets=legacy_targets,
                    instructions={"verified_answers": batch,
                                  "assigned_gaps": {gid: self.state["gaps"][gid] for item in batch for gid in item["task"]["gap_ids"]},
                                  "all_closed_tasks": [{"task": item["task"], "answer": item["answer"]["summary"]} for item in answers],
                                  "rule": "Integrate these independently verified answers. Resolve old open questions explicitly "
                                  "when their assigned tasks fully answer them; do not carry stale editorial to-dos as evidence gaps. "
                                  "Only close a compound question when ALL its obligations are answered. Preserve all unrelated findings."},
                    extra_context=read_context(self.reader, [e["reference"] for item in batch
                                   for f in item["answer"]["findings"] for e in f["evidence"]]), coverage_ids=question_ids)
                added = {f.id for f in dossier.findings} - {f.id for f in before.findings}
                dossier = repair_references(folder, f"batch_{start:03d}_references", dossier, discovery, context, self.config,
                    lambda p, s: self.generate(folder, f"batch_{start:03d}_references", p, s, f"{VERSION}.references"),
                    allowed_ids=targets | added)
                preserve_unrelated(before, dossier, targets)
                additions = dossier.model_copy(update={"findings": [f for f in dossier.findings if f.id in added]})
                owners.update(finding_owners(additions, [t for t in plan.tasks if t.id in batch_ids], self.state["tasks"]))
        else:
            prompt = (TERMINOLOGY + TEACHING_SCOPE + EVIDENCE_INSTRUCTIONS + SYNTHESIS_INSTRUCTIONS +
                instructions("dossier_compose", language=self.config.language) + "\n" + json.dumps({"brief": quality_brief(self.config),
                    "questions": [q.model_dump() for q in discovery.questions], "verified_answers": answers,
                    "sources": context}, ensure_ascii=False))
            dossier = self.call(folder, "dossier", ResearchDossier, prompt)
            dossier = repair_references(folder, "references", dossier, discovery, context, self.config,
                lambda p, s: self.generate(folder, "references", p, s, f"{VERSION}.references"))
            owners = finding_owners(dossier, plan.tasks, self.state["tasks"])
        self.state["composed_findings"] = {"dossier_hash": digest(dossier.model_dump()), "owners": owners}
        self.state["verified_baseline"] = answers
        self.save()
        return dossier, discovery, context

    def audit(self, dossier, discovery, context):
        folder = self.folder / "synthesis" / f"audit_{self.state['audit_round']:02d}"
        self.state["phase"] = "audit"
        self.save("Gesamtdossier wird auf Quellenbezüge, Widersprüche und alle ursprünglichen Leitfragen geprüft")
        for revision in range(3):
            review = self.call(folder, f"grounding_{revision}", SourceReview,
                EVIDENCE_INSTRUCTIONS + SYNTHESIS_INSTRUCTIONS + ROUTING_INSTRUCTIONS +
                " " + instructions("dossier_audit") + "\n" + json.dumps({
                    "brief": quality_brief(self.config), "dossier": dossier.model_dump(), "sources": context,
                    "open_objections": self.state.get("objections", {}),
                    "tasks": self.state["plan"]["tasks"], "verified_baseline": self.state.get("verified_baseline", [])}, ensure_ascii=False))
            expected = self.state.get("objections", {})
            if Counter(c.objection_id for c in review.objection_checks) != Counter(expected.keys()):
                raise AppError("Every existing objection needs an explicit closure check.", code="invalid_evidence_review", status="blocked")
            for check in review.objection_checks:
                if not set(check.references) <= references(context) or (check.verdict == "closed" and not check.references):
                    raise AppError("Objection closure requires read source evidence.", code="invalid_evidence_review", status="blocked")
                if check.verdict == "review_disagreement" or (check.verdict == "open" and not review.issues):
                    save_value(folder / "review_disagreement.json", check.model_dump())
                    raise AppError("An unresolved review disagreement cannot trigger unanchored research.", code="review_disagreement", status="blocked")
            semantic_errors = support_errors(dossier.findings, review, context)
            validate_synthesis(dossier, context)
            rejected = {s.finding_id for s in review.finding_support if s.verdict != "supported" or
                        s.suitability != "suitable" or not s.contract_preserved}
            if semantic_errors and (not rejected or not rejected <= {i.finding_id for i in review.issues}):
                raise AppError("Failing support receipts require explicit corrective issues.", code="invalid_evidence_review", status="blocked")
            for issue in review.issues:
                if (issue.objection is None or issue.finding_id not in issue.objection.finding_ids or
                        issue.objection.resolution not in {issue.resolution, "review_disagreement"}):
                    raise AppError("A dossier objection requires an affected finding and closure condition.",
                                   code="invalid_evidence_review", status="blocked")
                validate_objection(issue.objection, QuestionPlan.model_validate(self.state["plan"]).tasks,
                                   dossier.findings, context)
                if issue.objection.resolution == "review_disagreement":
                    self.state.setdefault("review_disagreements", []).append(issue.objection.model_dump())
                    self.save("Prüfeinwand ist nicht hinreichend belegt")
                    raise AppError("Unanchored review disagreement; no automatic new research.", code="review_disagreement", status="blocked")
            if not {i.finding_id for i in review.issues} <= {f.id for f in dossier.findings}:
                raise AppError("Gesamtprüfung nennt unbekannte Befunde.", code="invalid_model_output", status="blocked")
            if not review.issues or needs_research(review) or revision == 2:
                break
            before = dossier
            targets = {i.finding_id for i in review.issues}
            dossier = edit_dossier(folder, f"correction_{revision}", dossier, discovery, context, self.config,
                lambda p, s: self.generate(folder, f"correction_{revision}", p, s, f"{VERSION}.correction"),
                targets=targets, instructions=review.model_dump(),
                allow_additions=False, coverage_ids=set(), allow_questions=False)
            dossier = repair_references(folder, f"correction_{revision}_references", dossier, discovery, context, self.config,
                lambda p, s: self.generate(folder, f"correction_{revision}_references", p, s, f"{VERSION}.references"),
                allowed_ids=targets)
            preserve_unrelated(before, dossier, targets)
        assessment = self.call(folder, "assessment", ResearchAssessment,
            instructions("research_assessment", language=self.config.language) + "\n" + json.dumps({"brief": quality_brief(self.config),
                "dossier": dossier.model_dump(), "sources": context,
                "finding_support": [r.model_dump() for r in review.finding_support],
                "source_assessments": [a.model_dump() for a in review.source_assessments]}, ensure_ascii=False))
        report = quality_report(self.config, dossier, discovery, self.index, assessment, (i.reason for i in review.issues))
        dossier = dossier.model_copy(update={"evidence_version": EVIDENCE_VERSION,
                                            "source_assessments": review.source_assessments})
        report["dossier_hash"] = digest(dossier.model_dump())
        report["evidence"] = evidence_summary(dossier.findings, review)
        report["objection_checks"] = [c.model_dump() for c in review.objection_checks]
        report["unresolved_scientific_relations"] = [r.model_dump() for r in dossier.synthesis if r.resolution == "unresolved"]
        report["question_workflow"] = VERSION
        write_json(self.work / "research_quality_gate.json", report)
        atomic_text(self.work / "research_quality.md", render_quality(report))
        self.state["composed_findings"]["dossier_hash"] = digest(dossier.model_dump())
        self.save()
        return dossier, review, report

    def reopen(self, dossier, review, report):
        tasks = QuestionPlan.model_validate(self.state["plan"]).tasks
        composed = self.state.get("composed_findings")
        if composed and composed["dossier_hash"] != digest(dossier.model_dump()):
            raise AppError("Befundzuordnung passt nicht zum geprüften Dossier.",
                           code="invalid_research_checkpoint", status="blocked")
        owners = finding_owners(dossier, tasks, self.state["tasks"], composed["owners"] if composed else None)
        objections = list(dict.fromkeys([*(i.reason for i in review.issues), *report["blocking_gaps"],
            *(row["reason"] + " " + " ".join(row["missing"]) for row in report["requirements"] if not row["passed"])]))
        routes = self.call(self.folder / "synthesis" / f"audit_{self.state['audit_round']:02d}", "routes", ReopenPlan,
            instructions("objection_routes") + "\n" +
            json.dumps({"objections": objections, "tasks": [t.model_dump() for t in tasks],
                        "anchored_issues": [i.objection.model_dump() for i in review.issues if i.objection],
                        "finding_owners": owners,
                        "answers": {t.id: self.state["tasks"][t.id]["answer"] for t in tasks},
                        "dossier": dossier.model_dump()}, ensure_ascii=False))
        if (Counter(r.index for r in routes.routes) != Counter(range(len(objections))) or
            any(not set(r.task_ids) <= {t.id for t in tasks} for r in routes.routes)):
            raise AppError("Prüfeinwände sind nicht vollständig bestehenden Recherchefragen zugeordnet.",
                           code="invalid_question_routing", status="blocked")
        reasons = {}
        context = read_context(self.reader, [e.reference for f in dossier.findings for e in f.evidence])
        registry = self.state.setdefault("objections", {})
        closed = {c["objection_id"] for c in report.get("objection_checks", []) if c["verdict"] == "closed"}
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
                        self.state.setdefault("review_disagreements", []).append(anchor.model_dump())
                        self.save("Prüfeinwand benötigt Klärung statt weiterer Suche")
                        raise AppError("Review disagreement cannot trigger more research.", code="review_disagreement", status="blocked")
                    # Keep an unresolved objection stable even when a later reviewer paraphrases
                    # its missing-evidence description. A new defect is allowed after explicit closure.
                    for old_id, old in registry.items():
                        if old_id not in closed and all(old.get(key) == anchor.model_dump().get(key)
                                for key in ("rule", "task_id", "criterion_index", "finding_ids")):
                            identifier = old_id
                            break
                    previous = registry.get(identifier) if identifier not in closed else None
                    if previous and previous["closure_condition"] != anchor.closure_condition:
                        raise AppError("The closure condition of an existing objection changed.",
                                       code="invalid_question_routing", status="blocked")
                    registry[identifier] = {**anchor.model_dump(), "id": identifier, "status": "open"}
                reasons.setdefault(task_id, []).append(objections[route.index] + " " + route.reason)
        for task_id, objections in reasons.items():
            row = self.state["tasks"][task_id]
            if len(row["reopenings"]) >= self.state["limits"]["reopenings"]:
                row.update(status="blocked", reason="Wiederholte Gesamtprüfung widerspricht dem Abschluss: " + " ".join(objections))
                continue
            row["reopenings"].append({"reason": objections, "previous_answer": row["answer"],
                                     "previous_verification": row.get("verification")})
            row.update(status="researching", answer=None, draft_answer=row["reopenings"][-1]["previous_answer"],
                       feedback=objections, step=0, no_progress=0, fallbacks=0, pending=None,
                       activity="Mit konkretem Einwand aus der Gesamtprüfung wieder geöffnet")
        dirty = invalidate_dependents(self.state, reasons)
        self.state.update(seed_dossier=dossier.model_dump(), dirty_tasks=dirty, finding_owners=owners,
                          audit_round=self.state["audit_round"]+1, phase="questions")
        self.save("Konkrete Einwände werden ihren ursprünglichen Recherchefragen zugeordnet")
