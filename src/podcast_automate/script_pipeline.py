"""Stage implementations of a script run: outline, teaching design, drafting, polishing, reviews, publishing.

``scripting.run_script`` resolves inputs, hashes and approvals, then hands ``ScriptRun.stages()`` to the
shared stage runner. Every stage first checks that the approved call allowance can still finish the
selected episodes, so a run stops before spending money it cannot complete with.
"""
from __future__ import annotations

import json

from .codex import CodexAdapter
from .editorial import CONTINUITY, EPISODE_FRAMING, TEACHING_SCOPE, TERMINOLOGY, episode_series_context
from .errors import AppError
from .evidence_models import EVIDENCE_VERSION
from .execution import run_episode_stage
from .models import EpisodeScript
from .openrouter import OpenRouterAdapter
from .polishing import HOST_ROLES, polish_dialogue
from .prompts import fragment, instructions
from .research import PLAIN_LANGUAGE, reserve_call
from .run_budget import effective_limits
from .runner import run_observer
from .script_artifacts import publish_scripts, render_script, script_metrics
from .script_budget import ensure_script_budget
from .script_checks import checked_series_plan, episode_sources, script_review_signature, validate_script
from .script_evidence import SCRIPT_EVIDENCE_INSTRUCTIONS, validate_claim_checks
from .script_models import KnowledgeModel, ScriptReview, SeriesPlan
from .series_review import assess_series, load_series_review, require_passing_series, reviewed_scripts
from .storage import atomic_text, digest, file_hash, write_json
from .teaching import (EDITORIAL_REVIEW_VERSION, TeachingPlan, assess_teaching, build_teaching_plan,
                       prerequisite_context)
from .teaching_research import apply_foundations, research_foundations

SPOKEN_DIALOGUE = fragment("spoken_dialogue")
MAX_REVIEW_REPAIRS = 3


class ScriptRun:
    """State of one script run. ``dossier``, ``context`` and ``sources`` grow with foundation research."""

    def __init__(self, *, root, work, config, manifest, adapter, input_hash, research_id, dossier, discovery,
                 sources, context, episode, revision, execution, plan_only, previous_outline, outline_feedback,
                 series_review_version, text_generation, resume):
        self.root, self.work, self.config, self.manifest, self.adapter = root, work, config, manifest, adapter
        self.input_hash, self.research_id, self.discovery = input_hash, research_id, discovery
        self.base_dossier, self.base_context, self.base_sources = dossier, context, sources
        self.dossier, self.context, self.sources = dossier, context, sources
        self.episode, self.revision, self.execution, self.plan_only = episode, revision, execution, plan_only
        self.previous_outline, self.outline_feedback = previous_outline, outline_feedback
        self.series_review_version, self.text_generation, self.resume = series_review_version, text_generation, resume
        self.central_question = config.central_question or config.topic

    # --- shared helpers -------------------------------------------------------------------------

    def limits(self):
        return effective_limits(self.work, self.config.research_limits, self.input_hash)

    def invoke(self, prompt, output_type, version, *, search=False, research=False):
        # With Codex selected, supplementary research uses the same saved model and effort.
        current = (CodexAdapter(self.config.runtime) if (research or search) and isinstance(self.adapter, OpenRouterAdapter)
                   else self.adapter)
        if isinstance(current, OpenRouterAdapter):
            current.require_key()
        number = reserve_call(self.work, self.limits(), search=search)
        return current.structured(prompt, output_type, self.work / "calls" / f"call_{number:03d}",
                                  prompt_version=version, search=search)[0]

    def selected(self):
        plan = SeriesPlan.model_validate_json((self.work / "series_plan.json").read_text(encoding="utf-8"))
        return plan, [e for e in plan.episodes if not self.episode or e.episode_id == self.episode]

    def teaching_for(self, entry):
        return TeachingPlan.model_validate_json(
            (self.work / "teaching" / entry.episode_id / "plan.json").read_text(encoding="utf-8"))

    def refresh_foundations(self, entries):
        """Apply verified supplementary research to the working dossier; returns the receipt files."""
        self.dossier, self.context, self.sources, supplements = apply_foundations(
            self.root, self.work, self.config, entries, self.base_dossier, self.base_context, self.base_sources)
        return supplements

    def check_budget(self, entries):
        return ensure_script_budget(self.work, self.manifest, self.limits(), entries,
                                    series_review=bool(self.series_review_version) and self.episode is None
                                    and not self.plan_only)

    def stages(self):
        return {"planning": self.planning, "teaching": self.teaching, "writing": self.writing,
                "polishing": self.polishing, "review": self.review, "publish": self.publish}

    # --- planning -------------------------------------------------------------------------------

    def planning(self):
        self.check_budget([])
        config, dossier = self.config, self.dossier
        prompt = (instructions("series_plan", language=config.language) + " " + PLAIN_LANGUAGE +
                  instructions("series_plan_tail") + "\n" +
                  json.dumps({"brief": {**config.model_dump(mode="json"), "central_question": self.central_question},
                              "dossier": dossier.model_dump(),
                              "research_questions": [q.model_dump() for q in self.discovery.questions]}, ensure_ascii=False))
        if self.previous_outline is not None:
            prompt += "\n" + instructions("series_plan_revision") + "\n" + json.dumps(
                {"previous_outline": self.previous_outline, "feedback": self.outline_feedback}, ensure_ascii=False)
        signature = digest({"input": self.input_hash, "previous_outline": self.previous_outline,
                            "feedback": self.outline_feedback})
        plan = checked_series_plan(self.work, prompt, self.invoke, dossier, self.central_question, signature,
                                   allow_legacy=self.resume and self.previous_outline is None)
        if self.episode and self.episode not in {e.episode_id for e in plan.episodes}:
            raise AppError("Gewünschte Folge kommt im Serienplan nicht vor.", code="unknown_episode", status="blocked")
        knowledge = KnowledgeModel(research_run_id=self.research_id, topic=dossier.topic,
            claims=dossier.findings, key_terms=[f.id for f in dossier.findings if f.kind == "definition"],
            mechanisms=[f.id for f in dossier.findings if f.kind == "mechanism"],
            examples=[f.id for f in dossier.findings if f.kind == "example" or f.illustration],
            counterpoints=[f.id for f in dossier.findings if f.kind == "limitation"],
            synthesis=dossier.synthesis, source_assessments=dossier.source_assessments,
            dependencies=plan.dependencies, uncertainties=dossier.open_questions +
            [c.gap for c in dossier.coverage if c.gap] +
            [r.explanation for r in dossier.synthesis if r.resolution == "unresolved"], editorial_priorities=plan.explanation_path)
        write_json(self.work / "series_plan.json", plan.model_dump())
        write_json(self.work / "knowledge_model.json", knowledge.model_dump())
        return [self.work / name for name in ("series_plan.json", "knowledge_model.json", "inputs.json",
                                              "script_request.json", "project_snapshot.yaml")]

    # --- teaching design ------------------------------------------------------------------------

    def teaching(self):
        plan, entries = self.selected()
        self.check_budget(entries)
        outputs = []
        for entry in entries:
            directory = self.work / "teaching" / entry.episode_id
            continuity = prerequisite_context(plan, entry, self.work)
            write_json(directory / "continuity.json", continuity)
            while True:
                teaching_sources = episode_sources(entry, self.dossier, self.context, self.sources)
                write_json(directory / "source_context.json", teaching_sources)
                try:
                    _, files = build_teaching_plan(self.config, entry, self.dossier, teaching_sources, self.invoke, directory,
                                                   continuity=continuity, series_context=episode_series_context(plan, entry))
                    break
                except AppError as exc:
                    if exc.code != "teaching_research_required":
                        raise
                self.research_missing_foundations(entry, entries)
            outputs.extend([*files, directory / "source_context.json", directory / "continuity.json"])
        supplements = self.refresh_foundations(entries)
        return [*outputs, *supplements]

    def research_missing_foundations(self, entry, entries):
        """One bounded supplementary research pass; stop when it adds no evidence."""
        previous = digest({"dossier": self.dossier.model_dump(), "context": self.context})
        write_json(self.work / "progress.json", {"phase": "foundation_research", "episode_id": entry.episode_id})
        observer = run_observer.get()
        if observer:
            observer(self.manifest)
        try:
            research_foundations(self.root, self.work, self.config, entry, self.base_dossier, self.invoke,
                                 current_dossier=self.dossier)
        finally:
            write_json(self.work / "progress.json", {})
            if observer:
                observer(self.manifest)
        self.refresh_foundations(entries)
        if previous == digest({"dossier": self.dossier.model_dump(), "context": self.context}):
            raise AppError("Die Lehrprüfung meldet erneut eine bereits recherchierte Frage. "
                           "Der Abgleich zwischen Belegen und Lehrkonzept muss geprüft werden; "
                           "der Auftrag bleibt gespeichert.", code="teaching_research_required", status="blocked")

    # --- writing --------------------------------------------------------------------------------

    def writing_prompt(self, plan, entry):
        config, dossier = self.config, self.dossier
        prompt = (instructions("write_episode_opening", language=config.language) + " "
                  + PLAIN_LANGUAGE + SPOKEN_DIALOGUE + CONTINUITY + EPISODE_FRAMING + SCRIPT_EVIDENCE_INSTRUCTIONS +
                  instructions("write_episode") + "\n" +
                  json.dumps({"brief": {"language": config.language, "voices": config.voice_profile,
                                        "audience": config.audience_level, "prior_knowledge": config.prior_knowledge,
                                        "style": config.depth_request},
                              "host_roles": HOST_ROLES,
                              "series": plan.model_dump(), "episode": entry.model_dump(),
                              "series_context": episode_series_context(plan, entry),
                              "prerequisite_context": prerequisite_context(plan, entry, self.work),
                              "teaching_design": self.teaching_for(entry).model_dump(),
                              "teaching_design_review": json.loads((self.work / "teaching" / entry.episode_id / "review.json").read_text(encoding="utf-8")),
                              "findings": [f.model_dump() for f in dossier.findings if f.id in entry.finding_ids],
                              "synthesis": [r.model_dump() for r in dossier.synthesis if set(r.finding_ids) & set(entry.finding_ids)],
                              "sources": episode_sources(entry, dossier, self.context, self.sources)}, ensure_ascii=False))
        if self.revision:
            prompt += ("\n" + instructions("write_episode_revision") + "\n" +
                       json.dumps(self.revision, ensure_ascii=False))
        return prompt

    def write_episode(self, plan, entry):
        destination = self.work / "drafts" / f"{entry.episode_id}.json"
        stamp = destination.with_suffix(".checkpoint.json")
        prompt = self.writing_prompt(plan, entry)
        signature = digest({"input": self.input_hash, "prompt": prompt})
        if stamp.exists() and destination.exists():
            saved = json.loads(stamp.read_text(encoding="utf-8"))
            if saved == {"input_hash": signature, "sha256": file_hash(destination)}:
                return [destination, stamp]
        draft = self.invoke(prompt, EpisodeScript, "write_episode.v6-framing")
        errors = validate_script(draft, entry)
        if errors:
            draft = self.invoke(prompt + "\n" + instructions("write_episode_repair") + "\n" + json.dumps(
                {"errors": errors, "draft": draft.model_dump()}, ensure_ascii=False),
                EpisodeScript, "write_episode_repair.v1")
            errors = validate_script(draft, entry)
        if errors:
            write_json(self.work / f"{entry.episode_id}_script_errors.json", errors)
            raise AppError("Skript verletzt Struktur- oder Quellenzuordnung.", code="invalid_script", status="blocked")
        write_json(destination, draft.model_dump())
        write_json(stamp, {"input_hash": signature, "sha256": file_hash(destination)})
        return [destination, stamp]

    def writing(self):
        plan, entries = self.selected()
        self.check_budget(entries)
        return run_episode_stage(entries, lambda entry: self.write_episode(plan, entry),
                                 workers=self.execution.text_workers, work=self.work, stage="writing")

    # --- polishing ------------------------------------------------------------------------------

    def polish_episode(self, plan, entry):
        original = EpisodeScript.model_validate_json(
            (self.work / "drafts" / f"{entry.episode_id}.json").read_text(encoding="utf-8"))
        folder = self.work / "polishing" / entry.episode_id
        candidate, files = polish_dialogue(self.config, entry, original, self.teaching_for(entry),
                                           self.invoke, folder, validate_script,
                                           series_context=episode_series_context(plan, entry))
        atomic_text(folder / "before.md", render_script(original, self.config.voice_profile))
        atomic_text(folder / "after.md", render_script(candidate, self.config.voice_profile))
        return [*files, folder / "before.md", folder / "after.md"]

    def polishing(self):
        plan, entries = self.selected()
        self.check_budget(entries)
        return run_episode_stage(entries, lambda entry: self.polish_episode(plan, entry),
                                 workers=self.execution.text_workers, work=self.work, stage="polishing")

    # --- reviews --------------------------------------------------------------------------------

    def review_episode(self, plan, entry):
        config, dossier, work = self.config, self.dossier, self.work
        draft_file = work / "polishing" / entry.episode_id / "script.json"
        original_draft = json.loads((work / "drafts" / f"{entry.episode_id}.json").read_text(encoding="utf-8"))
        draft = EpisodeScript.model_validate_json(draft_file.read_text(encoding="utf-8"))
        checkpoint = work / "reviews" / f"{entry.episode_id}_checkpoint.json"
        signature = script_review_signature(self.input_hash, file_hash(draft_file), plan, entry, work)
        result, repairs = None, 0
        if checkpoint.exists():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved.get("input_hash") == signature:
                draft = EpisodeScript.model_validate(saved["draft"])
                repairs = saved["repairs"]
                result = ScriptReview.model_validate(saved["review"]) if saved["review"] else None
                # Reassess an older verdict after a review-policy fix, keeping the
                # latest corrected script and consumed repair allowance intact.
                if saved.get("editorial_review_version") != EDITORIAL_REVIEW_VERSION:
                    result = None
                if dossier.evidence_version and saved.get("evidence_review_version") != EVIDENCE_VERSION:
                    result = None
                if validate_script(draft, entry):
                    raise AppError("Gespeicherter Review-Entwurf ist ungültig.", code="invalid_script", status="blocked")

        def save():
            write_json(checkpoint, {"input_hash": signature, "draft": draft.model_dump(),
                                   "review": result.model_dump() if result else None, "repairs": repairs,
                                   "editorial_review_version": EDITORIAL_REVIEW_VERSION,
                                   "evidence_review_version": EVIDENCE_VERSION})

        def check():
            reviewed = self.invoke(
                TERMINOLOGY + TEACHING_SCOPE + CONTINUITY + EPISODE_FRAMING + SCRIPT_EVIDENCE_INSTRUCTIONS +
                instructions("script_review") + "\n" + json.dumps(
                    {"brief": {"audience": config.audience_level, "depth": config.depth_request},
                     "host_roles": HOST_ROLES, "original_draft": original_draft,
                     "metrics": script_metrics(draft), "episode": entry.model_dump(), "script": draft.model_dump(),
                     "series_context": episode_series_context(plan, entry),
                     "prerequisite_context": prerequisite_context(plan, entry, work),
                     "findings": [f.model_dump() for f in dossier.findings if f.id in entry.finding_ids],
                     "synthesis": [r.model_dump() for r in dossier.synthesis if set(r.finding_ids) & set(entry.finding_ids)],
                     "sources": episode_sources(entry, dossier, self.context, self.sources)}, ensure_ascii=False),
                ScriptReview, "script_review.v8-evidence")
            reviewed.issues.extend(validate_claim_checks(reviewed, draft, dossier.findings,
                                                       required=bool(dossier.evidence_version)))
            ids = {s.segment_id for s in draft.segments}
            if any(not set(issue.segment_ids) <= ids for issue in reviewed.issues):
                raise AppError("Review verweist auf unbekannte Segmente.", code="invalid_model_output")
            if not reviewed.issues:
                teaching_issues, _, _ = self.assess_episode_teaching(plan, entry, draft)
                reviewed.issues.extend(teaching_issues)
            return reviewed

        if result is None:
            result = check()
            save()
        else:
            for issue in validate_claim_checks(result, draft, dossier.findings, required=bool(dossier.evidence_version)):
                if issue not in result.issues:
                    result.issues.append(issue)
        while result.issues and repairs < MAX_REVIEW_REPAIRS:
            draft = self.invoke(self.writing_prompt(plan, entry) +
                "\n" + instructions("script_review_repair") + "\n" +
                json.dumps({"draft": draft.model_dump(), "review": result.model_dump()}, ensure_ascii=False),
                EpisodeScript, "script_review_repair.v1")
            errors = validate_script(draft, entry)
            if errors:
                write_json(work / f"{entry.episode_id}_review_errors.json", errors)
                raise AppError("Überarbeitetes Skript verletzt die Quellenzuordnung oder Struktur.",
                               code="invalid_script", status="blocked")
            repairs += 1
            result = None
            save()
            result = check()
            save()
        report = work / "reviews" / f"{entry.episode_id}.json"
        write_json(report, result.model_dump())
        if result.issues:
            raise AppError("Skriptreview meldet weiterhin Einwände; Reviewbericht prüfen.",
                           code="script_review_failed", status="blocked")
        reviewed_file = work / "reviewed" / f"{entry.episode_id}.json"
        teaching_issues, teaching_report, teaching_outputs = self.assess_episode_teaching(plan, entry, draft)
        if teaching_issues:
            raise AppError("Lehrprüfung nicht bestanden.", code="teaching_review_failed", status="blocked")
        teaching_report_file = work / "reviews" / f"{entry.episode_id}_teaching.json"
        write_json(teaching_report_file, teaching_report)
        write_json(reviewed_file, draft.model_dump())
        return [reviewed_file, report, teaching_report_file, *teaching_outputs]

    def assess_episode_teaching(self, plan, entry, draft):
        return assess_teaching(draft, self.teaching_for(entry), self.invoke,
                               self.work / "reviews" / "teaching" / entry.episode_id,
                               audience=self.config.audience_level, prior_knowledge=self.config.prior_knowledge,
                               depth=self.config.depth_request, series_context=episode_series_context(plan, entry))

    def review(self):
        plan, entries = self.selected()
        self.check_budget(entries)
        outputs = run_episode_stage(entries, lambda entry: self.review_episode(plan, entry),
                                    workers=self.execution.text_workers, work=self.work, stage="review")
        if self.series_review_version:
            outputs.extend(assess_series(self.work, self.config, plan, reviewed_scripts(self.work, plan, self.episode),
                                         self.input_hash, self.invoke))
        return outputs

    # --- publishing -----------------------------------------------------------------------------

    def publish(self):
        plan, entries = self.selected()
        series_report = None
        if self.series_review_version:
            scripts = reviewed_scripts(self.work, plan, self.episode)
            series_report = load_series_review(self.work, plan, scripts, self.input_hash)
            require_passing_series(series_report)
        return publish_scripts(self.root, self.work, plan=plan, entries=entries, dossier=self.dossier, sources=self.sources,
            config=self.config, teaching_for=self.teaching_for, research_id=self.research_id, manifest=self.manifest,
            input_hash=self.input_hash, text_generation=self.text_generation, series_report=series_report)
