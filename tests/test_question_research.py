"""Question workflow regressions: retrieval, independent gates, bounded work and replay."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.question_answering import (READER_ACTIONS, normalise_answer, normalise_review, read_context,
                                                 review_outcome, review_passes)
from podcast_automate.question_research import QuestionResearch, answer_errors, validate_plan
from podcast_automate.question_scope import QuestionScopeReview
from podcast_automate.evidence_models import ResearchObjection
from podcast_automate.question_synthesis import (SUPPORT_VERDICT_FIELDS, compact_assessment_material,
                                                 compact_review_instructions, compact_routing_material)
from podcast_automate.research import run_research
from podcast_automate.research_ledger import bootstrap_legacy, public_ledger, read_value, save_value
from podcast_automate.research_models import ResearchDossier, SourceIndex, SourceSection
from podcast_automate.research_patches import DossierPatch
from podcast_automate.research_quality import ResearchAssessment
from podcast_automate.research_reader import SourceReader
from podcast_automate.research_review import SourceReview
from podcast_automate.research_review import SourceReviewIssue
from podcast_automate.research_tasks import AnswerReview, QuestionPlan, QuestionSearch, ReaderWindow, ResearchDecision, ReopenPlan
from podcast_automate.runner import status
from podcast_automate.sources import import_source
from podcast_automate.storage import digest, file_hash, init_project, write_json, write_yaml
from podcast_automate.run_budget import approve_model_call_limit
from podcast_automate.studio_progress import research_progress
from tests import research_fixtures as fixtures


from tests.question_fixtures import (task_value, decision, answer_for, question_response, complete_fixture_response,
                                     support_receipts)


class QuestionResearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "Projekt mit Leerzeichen"
        self.config = TopicBrief(topic="Test topic")
        init_project(self.root, self.config)
        self.work = self.root / "runs/run_test"
        self.discovery = fixtures.discovery()
        fetch = patch("podcast_automate.sources.download", return_value=(fixtures.HTML, "text/html", "https://example.org/paper0"))
        self.download = fetch.start()
        self.addCleanup(fetch.stop)
        doc, _ = import_source(self.discovery.candidates[0], self.root, self.work.name)
        self.index = SourceIndex(sources=[doc], failures=[])
        self.ref = f"{doc.id}#{next(s.id for s in doc.sections if 'Models assign an energy' in s.text)}"
        self.calls = []
        self.hook = lambda *args: None

    def model(self, prompt, schema, version="test", **kwargs):
        payload = json.loads(prompt.splitlines()[-1])
        self.calls.append((schema, version))
        override = self.hook(prompt, schema, payload, kwargs)
        if override is not None:
            return complete_fixture_response(override, payload), {}
        if schema is fixtures.ResearchDiscovery:
            return self.discovery, {"research_performed": True, "web_search_events": 1}
        result = question_response(prompt, schema)
        if result is not None:
            return result, {}
        if schema is ResearchDossier:
            if "retrieved_sources" in payload:
                return fixtures.dossier_from_prompt(prompt), {}
            return fixtures.dossier_from_prompt(json.dumps({"topic": "Test topic", "retrieved_sources": payload["sources"]})), {}
        if schema is DossierPatch:
            return DossierPatch(updates=[], additions=[], coverage_updates=[], resolved_open_questions=[], new_open_questions=[]), {}
        if schema is SourceReview:
            return SourceReview(issues=[], limitations=[]), {}
        if schema is QuestionSearch:
            return complete_fixture_response(QuestionSearch(candidates=[], limitations=["No additional suitable source in this fixture."]), payload), {}
        if schema is ResearchAssessment:
            return fixtures.assessment_from_prompt(prompt), {}
        self.fail(f"Unexpected schema {schema}")

    def engine(self):
        return QuestionResearch(self.root, self.work, self.config, self.model,
            lambda activity: write_json(self.work / "research_activity.json", {"activity": activity}))

    def test_scope_split_is_checked_before_reading_and_preserves_coverage(self):
        def hook(prompt, schema, payload, kwargs):
            if schema is QuestionScopeReview and len(payload["tasks"]) == 1:
                task = payload["tasks"][0]
                return QuestionScopeReview(decisions=[{"task_id": task["id"], "reason": "Two independently answerable obligations", "parts": [
                    {"question": title, "criterion_indices": [0], "acceptance": [title], "queries": ["energy"], "key_terms": ["energy"]}
                    for title in ("Define energy", "Explain configuration")]}])
        self.hook = hook
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual([call[0] for call in self.calls[:3]], [QuestionPlan, QuestionScopeReview, QuestionScopeReview])
        self.assertEqual(len(engine.state["plan"]["tasks"]), 2)
        self.assertTrue(all(row["status"] == "verified" for row in engine.state["tasks"].values()))
        self.assertEqual(engine.state["task_groups"]["task_definition"], ["task_definition_a", "task_definition_b"])

    def test_unresolved_scope_never_starts_reading_or_loops_without_limit(self):
        def hook(prompt, schema, payload, kwargs):
            if schema is QuestionScopeReview:
                return QuestionScopeReview(decisions=[{"task_id": task["id"], "reason": "Still bundled", "parts": [
                    {"question": title, "criterion_indices": list(range(len(task["acceptance"]))),
                     "acceptance": [title], "queries": ["energy"], "key_terms": ["energy"]}
                    for title in ("Focus A", "Focus B")]} for task in payload["tasks"]])
        self.hook = hook
        with self.assertRaises(AppError) as caught:
            self.engine().run(self.discovery, self.index)
        self.assertEqual(caught.exception.code, "question_scope_unresolved")
        self.assertEqual([call[0] for call in self.calls], [QuestionPlan, QuestionScopeReview, QuestionScopeReview])

    def test_complete_workflow_freezes_verified_answers_and_replay_has_no_calls(self):
        engine = self.engine()
        outputs = engine.run(self.discovery, self.index)
        self.assertTrue(all(p.exists() for p in outputs))
        self.assertEqual([c[0] for c in self.calls], [QuestionPlan, QuestionScopeReview, ResearchDecision, AnswerReview,
                                                   ResearchDossier, SourceReview, ResearchAssessment])
        answer_hash = engine.state["tasks"]["task_definition"]["verification"]["answer_hash"]
        count = len(self.calls)
        self.engine().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), count)
        ledger = read_value(self.work / "question_research/state.json")
        self.assertEqual(ledger["tasks"]["task_definition"]["verification"]["answer_hash"], answer_hash)
        public = json.loads((self.work / "research_questions.json").read_text())
        self.assertEqual((public["closed"], public["total"], public["phase"]), (1, 1, "completed"))

    def test_material_beyond_one_window_is_composed_and_audited_in_bounded_parts(self):
        assessed = []

        def two_tasks(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchDecision and payload["task"]["kind"] == "empirical":
                return decision("answer", answer=answer_for(self.ref))
            if schema is ResearchAssessment:
                assessed.append(payload)
        self.hook = two_tasks
        with patch("podcast_automate.question_synthesis.PROMPT_BUDGET_CHARS", 1):
            engine = self.engine()
            outputs = engine.run(self.discovery, self.index)
            self.assertTrue(all(p.exists() for p in outputs))
            self.assertEqual(engine.state["phase"], "completed")
            # The assessment of a dossier beyond one window reads verdicts and identities, not prose.
            [payload] = assessed
            self.assertEqual(set(payload["finding_support"][0]), {"finding_id", "verdict", "unsupported_clauses", "suitability",
                                                                  "empirical_status", "independent_evidence_refs"})
            self.assertTrue(all(set(e) == {"reference"} for f in payload["dossier"]["findings"] for e in f["evidence"]))
            self.assertTrue(all(set(s) == {"source_id", "title", "url"} for s in payload["sources"]))
            self.assertTrue(all("illustration" in f and "claim_contract" in f for f in payload["dossier"]["findings"]))
            schemas = [c[0] for c in self.calls]
            # The dossier opens with the first answer, the second is integrated as a patch, the audit runs in parts.
            self.assertEqual(schemas.count(ResearchDossier), 1)
            self.assertEqual(schemas.count(DossierPatch), 1)
            self.assertEqual(schemas.count(SourceReview), 1)
            self.assertEqual(schemas.count(ResearchAssessment), 1)
            audit = self.work / "question_research/synthesis/audit_00"
            for name in ("dossier_batch_000.json", "dossier_batch_001.json", "dossier_batches.json",
                         "grounding_0_part_000.json", "grounding_0_merged.json", "assessment.json"):
                self.assertTrue((audit / name).exists(), name)
            self.assertFalse((audit / "dossier.json").exists())
            batches = json.loads((audit / "dossier_batches.json").read_text(encoding="utf-8"))
            self.assertEqual((batches["opening"], batches["batches"]), (["task_definition"], 1))
            merged = json.loads((audit / "grounding_0_merged.json").read_text(encoding="utf-8"))
            self.assertEqual((merged["parts"], merged["findings_per_part"]), (1, [1]))
            report = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            count = len(self.calls)
            self.engine().run(self.discovery, self.index)
            self.assertEqual(len(self.calls), count, "a replay makes no calls")

    def test_answers_beyond_one_output_are_composed_in_parts_even_when_the_prompt_fits(self):
        # The prompt budget is untouched: the answers alone bound the opening batch, because the
        # dossier a call writes grows with them and the CLI cuts an answer at its output cap.
        openings = []

        def two_tasks(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchDecision and payload["task"]["kind"] == "empirical":
                return decision("answer", answer=answer_for(self.ref))
            if schema is ResearchDossier:
                openings.append([item["task"]["id"] for item in payload["verified_answers"]])
        self.hook = two_tasks
        with patch("podcast_automate.question_synthesis.ANSWER_BUDGET_CHARS", 1):
            engine = self.engine()
            outputs = engine.run(self.discovery, self.index)
            self.assertTrue(all(p.exists() for p in outputs))
            self.assertEqual(engine.state["phase"], "completed")
            schemas = [c[0] for c in self.calls]
            self.assertEqual((schemas.count(ResearchDossier), schemas.count(DossierPatch)), (1, 1))
            self.assertEqual(openings, [["task_definition"]])
            audit = self.work / "question_research/synthesis/audit_00"
            self.assertFalse((audit / "dossier.json").exists())
            batches = json.loads((audit / "dossier_batches.json").read_text(encoding="utf-8"))
            self.assertEqual((batches["opening"], batches["batches"], batches["answer_budget_chars"]), (["task_definition"], 1, 1))
            count = len(self.calls)
            self.engine().run(self.discovery, self.index)
            self.assertEqual(len(self.calls), count, "a replay makes no calls")

    def test_new_runs_state_the_synthesis_evidence_rule_and_old_runs_keep_their_prompts(self):
        prompts = []

        def capture(prompt, schema, payload, kwargs):
            if schema is ResearchDossier:
                prompts.append(prompt)
        self.hook = capture
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["prompt_generation"], 2)
        self.assertEqual(engine.prompt_tag, ".g2")
        self.assertTrue(all("at least one for each compared finding" in p for p in prompts))
        self.assertEqual(len(prompts), 1)
        self.assertTrue(any(v.endswith(".g2.dossier") for _, v in self.calls))
        engine.state["prompt_generation"] = 1
        self.assertEqual((engine.synthesis_rule(), engine.prompt_tag), ("", ""))

    def test_compact_assessment_material_keeps_verdicts_and_one_row_per_source(self):
        dossier = self.seed_dossier()
        sources = [{"source_id": s.id, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text} for x in s.sections]}
                   for s in self.index.sources]
        receipts = support_receipts([f.model_dump() for f in dossier.findings], sources)
        first = receipts["source_assessments"][0]
        second = {**first, "roles": ["empirical_test"], "rationale": "Second part of a split review."}
        review = SourceReview(issues=[], limitations=[], finding_support=receipts["finding_support"],
                              source_assessments=[first, second])
        material = compact_assessment_material(dossier, review)
        self.assertTrue(all(set(e) == {"reference"} for f in material["dossier"]["findings"] for e in f["evidence"]))
        self.assertEqual([f["id"] for f in material["dossier"]["findings"]], [f.id for f in dossier.findings])
        self.assertEqual(set(material["finding_support"][0]), set(SUPPORT_VERDICT_FIELDS))
        self.assertEqual(len(material["source_assessments"]), 1)
        self.assertEqual(material["source_assessments"][0]["roles"], ["original_definition", "empirical_test"])
        self.assertNotIn("rationale", material["source_assessments"][0])

    def test_compact_routing_and_correction_material_keep_anchors_and_statements(self):
        dossier = self.seed_dossier()
        sources = [{"source_id": s.id, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text} for x in s.sections]}
                   for s in self.index.sources]
        receipts = support_receipts([f.model_dump() for f in dossier.findings], sources)
        objection = ResearchObjection(id="obj_energy", rule="support", task_id="task_definition", criterion_index=0,
                                      finding_ids=["f_energy"], evidence_refs=[self.ref], missing_evidence="",
                                      reason="The clause about configurations is not covered.",
                                      correction="Restrict the statement to the read passage.",
                                      closure_condition="A passage covers the configuration clause.", resolution="revise")
        review = SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason="Not covered.", resolution="revise",
                                                        search_queries=[], objection=objection)],
                              limitations=["fixture"], finding_support=receipts["finding_support"],
                              source_assessments=receipts["source_assessments"])
        plan = QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
        answers = {"task_definition": answer_for(self.ref).model_dump(), "task_empirical": None}
        material = compact_routing_material(plan.tasks, answers, review, dossier)
        self.assertTrue(set(material["tasks"][0]) <= set(plan.tasks[0].model_dump()))
        self.assertIn("acceptance", material["tasks"][0])
        self.assertNotIn("queries", material["tasks"][0])
        self.assertEqual(material["anchored_issues"][0]["closure_condition"], objection.closure_condition)
        self.assertEqual(material["anchored_issues"][0]["evidence_refs"], [self.ref])
        self.assertNotIn("correction", material["anchored_issues"][0])
        # An anchor must cite read references, so every finding keeps its references, never its excerpts.
        self.assertEqual([e for f in material["dossier"]["findings"] for e in f["evidence"]],
                         [{"reference": e.reference} for f in dossier.findings for e in f.evidence])
        self.assertEqual(set(material["answers"]["task_definition"]), {"summary", "limits", "finding_ids"})
        self.assertIsNone(material["answers"]["task_empirical"])
        self.assertEqual([set(f) for f in material["dossier"]["findings"]],
                         [{"id", "kind", "statement", "evidence"}] * len(dossier.findings))
        self.assertEqual(material["dossier"]["coverage"], dossier.model_dump()["coverage"])
        guidance = compact_review_instructions(review, {"f_energy"})
        self.assertEqual(set(guidance), {"issues", "objection_checks", "finding_support"})
        self.assertEqual([s["finding_id"] for s in guidance["finding_support"]], ["f_energy"])
        self.assertEqual(guidance["issues"][0]["objection"]["id"], "obj_energy")

    def seed_dossier(self, *open_questions):
        dossier = fixtures.dossier_from_prompt(json.dumps({"topic": "Test topic", "retrieved_sources": [
            {"source_id": s.id, "url": s.final_url, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text}
                                                                 for x in s.sections]} for s in self.index.sources]}))
        dossier.open_questions = list(open_questions)
        return dossier

    def probed(self, gap):
        """Initialise with one declared gap and research its task; no run-level audit involved."""
        context = [{"source_id": s.id, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text}
                                                    for x in s.sections]} for s in self.index.sources]
        engine = self.engine()
        engine.initialise(self.discovery, self.index, self.seed_dossier(gap), context)
        task = next(t for t in QuestionPlan.model_validate(engine.state["plan"]).tasks)
        self.assertEqual(task.gap_ids, list(engine.state["gaps"]))
        engine.research_task(task)
        return engine, json.loads((self.work / "question_research/gap_probes.json").read_text(encoding="utf-8"))

    def test_a_declared_gap_is_probed_and_its_hits_are_read_before_any_reader_call(self):
        gap = "The rule that assigns an energy to each configuration is missing."
        engine, probes = self.probed(gap)
        self.assertEqual([row["text"] for row in probes], [gap])
        self.assertTrue(probes[0]["hits"])
        self.assertIn(probes[0]["status"], ("resolved", "hits_read_confirmed"))
        row = engine.state["tasks"]["task_definition"]
        self.assertIn(probes[0]["hits"][0]["reference"], row["read_refs"])
        # The probe itself costs nothing; only planning and the task spend calls.
        self.assertNotIn("gap_probe", [version for _, version in self.calls])

    def test_a_gap_without_corpus_hits_never_blocks(self):
        from podcast_automate.research_gap_probe import unread
        engine, probes = self.probed("Which tokenizer curriculum shaped the vocabulary schedule?")
        self.assertEqual(probes[0]["hits"], [])
        self.assertEqual(unread(probes), [])
        self.assertEqual(engine.state["tasks"]["task_definition"]["status"], "verified")

    def test_a_blocked_task_leaves_its_gap_unread_when_the_hits_were_never_read(self):
        from podcast_automate.research_gap_probe import unread
        gap = "The rule that assigns an energy to each configuration is missing."
        context = [{"source_id": s.id, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text}
                                                    for x in s.sections]} for s in self.index.sources]
        engine = self.engine()
        engine.initialise(self.discovery, self.index, self.seed_dossier(gap), context)
        task = QuestionPlan.model_validate(engine.state["plan"]).tasks[0]
        engine.state["tasks"][task.id].update(status="blocked", read_refs=[], outcome="evidence_block")
        engine.settle_probes(task, engine.state["tasks"][task.id])
        self.assertEqual([row["status"] for row in engine.state["gap_probes"]], ["hits_unread"])
        self.assertEqual(len(unread(engine.state["gap_probes"])), 1)

    def test_production_pipeline_publishes_and_resumes_without_more_calls(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            first = run_research(self.root)
            used = len(self.calls)
            resumed = run_research(self.root, resume=True)
        self.assertEqual(first.status, "completed", first.model_dump())
        self.assertEqual(first.run_id, resumed.run_id)
        self.assertEqual(len(self.calls), used)
        self.assertEqual(used, 8)
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])
        report = json.loads((self.root / "reports/research_quality.json").read_text())
        self.assertTrue(report["quality_gate"]["passed"])
        self.assertEqual(report["budget"]["model_calls"], used)
        activity = json.loads((self.root / "runs" / first.run_id / "research_activity.json").read_text())
        self.assertEqual(activity["research_questions"]["closed"], 1)
        self.assertEqual(activity["research_questions"]["phase"], "completed")
        self.assertTrue((self.root / "research/questions.md").exists())

    def test_research_allowance_resumes_without_changing_inputs_or_saved_work(self):
        self.config.research_limits.model_calls = 3
        write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            first = run_research(self.root)
            self.assertEqual(first.status, "blocked")
            work = self.root / "runs" / first.run_id
            protected = [self.root / "project.yaml", work / "project_snapshot.yaml", work / "budget.json",
                         work / "question_research/state.json"]
            before = {p: file_hash(p) for p in protected}
            approve_model_call_limit(self.root, first.run_id, 250)
            self.assertEqual(before, {p: file_hash(p) for p in protected})
            self.assertEqual(research_progress(self.root, first.model_dump(mode="json"))["model_call_limit"], 250)
            resumed = run_research(self.root, resume=True, run_id=first.run_id)
        self.assertEqual(resumed.status, "completed", resumed.model_dump())
        self.assertEqual(resumed.run_id, first.run_id)
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(json.loads((work / "budget.json").read_text())["model_calls"], 8)
        self.assertEqual(json.loads((work / "research_activity.json").read_text())["model_call_limit"], 250)

    def test_research_reads_increased_allowance_before_next_call(self):
        self.config.research_limits.model_calls = 3
        write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
        def increase(prompt, schema, payload, kwargs):
            if schema is QuestionScopeReview:
                latest = status(self.root)["run"]
                approve_model_call_limit(self.root, latest["run_id"], 250)
        self.hook = increase
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed", run.model_dump())
        self.assertEqual(len(self.calls), 8)

    def test_pause_after_answer_resumes_at_independent_review(self):
        def pause(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                raise AppError("Stopped", code="interrupted", status="interrupted")
        self.hook = pause
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        saved = read_value(self.work / "question_research/state.json")
        self.assertEqual(saved["tasks"]["task_definition"]["status"], "reviewing")
        self.hook = lambda *args: None
        self.engine().run(self.discovery, self.index)
        self.assertEqual(sum(c[0] is QuestionPlan for c in self.calls), 1)
        self.assertEqual(sum(c[0] is ResearchDecision for c in self.calls), 1)

    def test_paused_legacy_production_run_adopts_new_workflow_and_preserves_budget(self):
        # Build the historical on-disk boundary directly. Migration must not need
        # a second, obsolete executable research engine to manufacture its input.
        from podcast_automate.models import StageRecord
        from podcast_automate.research import source_context
        for stopped_stage in ("review", "completeness"):
            with self.subTest(stopped_stage=stopped_stage):
                with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model), \
                     patch("podcast_automate.research.run_question_research",
                           side_effect=AppError("Old run paused", code="interrupted", status="pending")):
                    old = run_research(self.root)
                work = self.root / "runs" / old.run_id
                index = SourceIndex.model_validate_json((work / "source_index.json").read_text())
                context = source_context(index, self.discovery)
                dossier = fixtures.dossier_from_prompt(json.dumps({"topic": self.config.topic, "retrieved_sources": context}))
                artifacts = {"dossier.json": dossier.model_dump(), "source_context.json": context,
                    "reference_check.json": {"errors": []}, "reviewed_dossier.json": dossier.model_dump(),
                    "review.json": {"issues": [], "limitations": []},
                    "review_context.json": {"prompt_version": "research_review.v6"}}
                for name, value in artifacts.items():
                    write_json(work / name, value)
                stages = {"dossier": ["dossier.json", "source_context.json", "reference_check.json"]}
                if stopped_stage == "completeness":
                    stages["review"] = ["reviewed_dossier.json", "review.json", "review_context.json"]
                for stage, names in stages.items():
                    old.stages[stage] = StageRecord(status="completed", attempts=1, outputs={
                        (work / name).relative_to(self.root).as_posix(): file_hash(work / name) for name in names})
                write_yaml(work / "run_manifest.yaml", old.model_dump(mode="json"))
                spent = 4 if stopped_stage == "completeness" else 2
                write_json(work / "budget.json", {"model_calls": spent, "search_rounds": 1})
                before = len(self.calls)
                downloads = self.download.call_count
                with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
                    resumed = run_research(self.root, resume=True)
                self.assertEqual(resumed.status, "completed", resumed.model_dump())
                self.assertEqual(old.run_id, resumed.run_id)
                self.assertEqual(self.download.call_count, downloads)
                budget = json.loads((work / "budget.json").read_text())
                self.assertEqual(budget["model_calls"], spent + len(self.calls) - before)
                self.assertEqual(budget["search_rounds"], 1)
                self.assertGreater(budget["model_calls"], spent)
                self.assertEqual(sum(c[0] is fixtures.ResearchDiscovery for c in self.calls[before:]), 0)
                self.assertEqual(sum(c[0] is QuestionPlan for c in self.calls[before:]), 1)
                for stage in stages:
                    self.assertEqual(resumed.stages[stage].outputs, old.stages[stage].outputs)

    def test_web_download_receipts_resume_without_repeating_search_or_first_download(self):
        engine = self.engine()
        engine.initialise(self.discovery, self.index, None, [])
        task = QuestionPlan.model_validate(engine.state["plan"]).tasks[0]
        row = engine.state["tasks"][task.id]
        engine.seed(task, row)
        row["pending"] = decision("search_web", web_queries=["energy independent study"]).model_dump()
        engine.save()
        extra = fixtures.discovery(count=3).candidates[1:]
        self.hook = lambda prompt, schema, payload, kwargs: QuestionSearch(candidates=extra, limitations=[]) \
            if schema is QuestionSearch else None
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"These sentences", (url + ". These sentences").encode()), "text/html", url)
        downloaded = []
        def interrupted(candidate, *args):
            if downloaded:
                raise KeyboardInterrupt("Power interruption between downloads")
            downloaded.append(candidate.url)
            return import_source(candidate, *args)
        with patch("podcast_automate.question_answering.import_source", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                engine.research_task(task)
        used = self.download.call_count
        restored = self.engine()
        restored.run(self.discovery, self.index)
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        self.assertEqual(self.download.call_count, used + 1)
        self.assertEqual(len(restored.index.sources), 3)
        self.assertEqual(restored.state["tasks"][task.id]["web_attempts"], 1)

    def test_local_reading_remains_available_when_web_budget_is_exhausted(self):
        self.config.research_limits.sources = 1
        write_json(self.work / "budget.json", {"model_calls": 5, "search_rounds": self.config.research_limits.search_rounds})
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertFalse(any(c[0] is QuestionSearch for c in self.calls))

    def test_exhausted_local_passages_trigger_one_focused_web_recovery(self):
        decisions = []
        def recover(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                decisions.append(1)
                if len(decisions) == 1:
                    return decision("blocked")
            if schema is QuestionSearch:
                self.assertTrue(kwargs["search"])
                return QuestionSearch(candidates=fixtures.discovery(count=2).candidates[1:], limitations=[])
        self.hook = recover
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"These sentences", b"Additional independent evidence. These sentences"), "text/html", url)
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        self.assertEqual(len(engine.index.sources), 2)

    def test_failed_verification_cannot_publish_or_be_shown_as_completed(self):
        def reject(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                return AnswerReview(criteria=[dict(index=0, passed=False, reason="Mechanism absent")],
                                    supported=False, source_adequacy=False, issues=["Read the missing mechanism"])
        self.hook = reject
        with self.assertRaises(AppError) as raised:
            self.engine().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        public = public_ledger(read_value(self.work / "question_research/state.json"))
        self.assertEqual(public["closed"], 0)
        self.assertEqual(public["questions"][0]["answer"], "")
        self.assertLessEqual(sum(c[0] is ResearchDecision for c in self.calls), 10)
        self.assertFalse((self.work / "complete_research/dossier.json").exists())

    def test_no_progress_reads_are_bounded_and_no_blind_retry_on_resume(self):
        self.hook = lambda prompt, schema, payload, kwargs: decision("read", windows=[
            dict(reference=self.ref, before=0, after=0)]) if schema is ResearchDecision else None
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        calls = len(self.calls)
        self.assertLess(calls, 10)
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), calls)

    def test_rephrasing_rejected_answers_does_not_reset_no_progress_detection(self):
        drafts = []
        def rephrase(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                drafts.append(1)
                answer = answer_for(self.ref)
                answer.summary += f" Wording attempt {len(drafts)}."
                return decision("answer", answer=answer)
            if schema is AnswerReview:
                return AnswerReview(criteria=[dict(index=0, passed=False, reason="Original evidence still absent.")],
                                    supported=False, source_adequacy=False, issues=["Missing evidence"])
        self.hook = rephrase
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        self.assertEqual(len(drafts), 2)
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        # The reworded second draft was locked out of review: only the first one cost a review call.
        self.assertEqual(sum(c[0] is AnswerReview for c in self.calls), 1)

    def test_definition_stays_closed_when_empirical_question_remains_blocked(self):
        def separate(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchDecision and payload["task"]["kind"] == "empirical":
                return decision("blocked")
        self.hook = separate
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        state = read_value(self.work / "question_research/state.json")
        self.assertEqual(state["tasks"]["task_definition"]["status"], "verified")
        self.assertEqual(state["tasks"]["task_empirical"]["status"], "blocked")
        self.assertEqual(public_ledger(state)["closed"], 1)

    def test_only_concretely_routed_answer_reopens(self):
        self.hook = lambda prompt, schema, payload, kwargs: QuestionPlan(tasks=[task_value(),
            task_value("task_empirical", "empirical")]) if schema is QuestionPlan else None
        engine = self.engine()
        engine.run(self.discovery, self.index)
        old = dict(engine.state["tasks"]["task_definition"])
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text())
        def route(prompt, schema, payload, kwargs):
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_empirical"], reason="Only validation is challenged.")
                    for i in range(len(payload["objections"]))])
        self.hook = route
        # Two objections, one per routing part: the parts merge back into the audit's objection list.
        with patch("podcast_automate.question_synthesis.ROUTING_PART_SIZE", 1):
            engine.reopen(dossier, SourceReview(issues=[], limitations=[]),
                          {"blocking_gaps": ["An empirical check is missing.", "A second empirical check is missing."],
                           "requirements": []})
        self.assertEqual(engine.state["tasks"]["task_definition"], old)
        empirical = engine.state["tasks"]["task_empirical"]
        self.assertEqual(empirical["status"], "researching")
        self.assertIsNone(empirical["answer"])
        self.assertEqual(len(empirical["reopenings"][0]["reason"]), 2)
        self.assertIn("empirical check", empirical["reopenings"][0]["reason"][0])
        self.assertIn("second empirical check", empirical["reopenings"][0]["reason"][1])
        audit = self.work / "question_research/synthesis/audit_00"
        merged = json.loads((audit / "routes_merged.json").read_text(encoding="utf-8"))
        self.assertEqual((merged["parts"], merged["objections_per_part"], [r["index"] for r in merged["routes"]["routes"]]),
                         (2, [1, 1], [0, 1]))
        self.assertTrue((audit / "routes_part_001.json").exists())
        self.assertFalse((audit / "routes.json").exists())
        ledger = public_ledger(engine.state)
        self.assertEqual((ledger["audit_round"], ledger["reopened"]), (1, 1))
        self.assertEqual(engine.last_activity, "Konkrete Einwände werden ihren ursprünglichen Recherchefragen zugeordnet")

    def test_a_routed_review_disagreement_starts_no_research_and_the_other_objections_still_route(self):
        # Regression, Psychohistorie run of 2026-09-20: the router called a status-note complaint an
        # unsupported demand. The run stopped after registering the objections routed so far, so the
        # resumed audit rebuilt its prompts with them and refused every saved part as a changed input.
        self.hook = lambda prompt, schema, payload, kwargs: QuestionPlan(tasks=[task_value(),
            task_value("task_empirical", "empirical")]) if schema is QuestionPlan else None
        engine = self.engine()
        engine.run(self.discovery, self.index)
        old = dict(engine.state["tasks"]["task_definition"])
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text())

        def anchor(task_id, resolution):
            return ResearchObjection(id=f"obj_{resolution}", rule="criterion", task_id=task_id, criterion_index=0,
                finding_ids=[], evidence_refs=[], missing_evidence=f"{resolution}: a check is missing.",
                reason="Named by the audit.", correction="Supply the check.",
                closure_condition="The check is met by read evidence.", resolution=resolution)
        plan = ReopenPlan(routes=[
            dict(index=0, task_ids=["task_definition"], reason="Only a status note disagrees with the answer.",
                 anchors=[anchor("task_definition", "review_disagreement")]),
            dict(index=1, task_ids=["task_empirical"], reason="Validation is challenged.",
                 anchors=[anchor("task_empirical", "research")])])

        def routed(folder, name, schema, prompt, *, validate=None, **kwargs):
            validate(plan, True)
            return plan

        with patch.object(engine, "call", side_effect=routed):
            reopened, blocked = engine.reopen(dossier, SourceReview(issues=[], limitations=[]),
                {"blocking_gaps": ["The scope note calls answered blocks unresearched.", "An empirical check is missing."],
                 "requirements": []})
        self.assertEqual((reopened, blocked), (["task_empirical"], []))
        self.assertEqual(engine.state["tasks"]["task_definition"], old)
        saved = read_value(self.work / "question_research/state.json")
        self.assertEqual([row["task_id"] for row in saved["objections"].values()], ["task_empirical"])
        self.assertEqual([row["task_id"] for row in saved["review_disagreements"]], ["task_definition"])
        # The registered objections and the next audit round are saved together, never one without the other.
        self.assertEqual((saved["audit_round"], saved["phase"]), (1, "questions"))

    def test_full_audit_reopens_only_empirical_task_then_rechecks_before_publish(self):
        reviews, reviewed_tasks = [], []
        def revise(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is SourceReview:
                reviews.append(1)
                if len(reviews) == 1:
                    return SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason="An empirical boundary is missing.",
                        resolution="research", search_queries=["independent test"])], limitations=[])
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_empirical"], reason="Empirical boundary only.")
                    for i in range(len(payload["objections"]))])
            if schema is ResearchDecision and payload["reopening"]:
                answer = answer_for(self.ref)
                answer.summary += " The empirical scope is restricted to the synthetic fixture."
                return decision("answer", answer=answer)
            if schema is AnswerReview:
                reviewed_tasks.append(payload["task"]["id"])
        self.hook = revise
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(reviewed_tasks, ["task_definition", "task_empirical", "task_empirical"])
        self.assertEqual(len(reviews), 2)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(engine.state["audit_round"], 1)

    def test_repeated_final_objections_remain_blocking_and_reopenings_are_bounded(self):
        drafts = []
        def reject(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                answer = answer_for(self.ref)
                drafts.append(1)
                answer.summary += f" Revision {len(drafts)}."
                return decision("answer", answer=answer)
            if schema is ResearchAssessment:
                report = fixtures.assessment_from_prompt(prompt)
                report.requirements[0].explanation = False
                report.requirements[0].reason = "A required mechanism is still absent."
                return report
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_definition"], reason="Required mechanism absent.")
                    for i in range(len(payload["objections"]))])
        self.hook = reject
        engine = self.engine()
        with self.assertRaises(AppError) as raised:
            engine.run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        self.assertEqual(len(engine.state["tasks"]["task_definition"]["reopenings"]), 2)
        self.assertEqual(len(drafts), 3)
        self.assertFalse((self.work / "complete_research/dossier.json").exists())

    def test_unread_or_uploaded_only_evidence_is_rejected(self):
        spec = QuestionPlan(tasks=[task_value()]).tasks[0]
        answer = answer_for(self.ref)
        reader = SourceReader(self.index)
        self.assertEqual(answer_errors(answer, spec, reader, set()),
                         [f"f_energy: evidence reference '{self.ref}' was not read for this question; "
                          "cite a section from read_refs or read it first."])
        source_id = self.ref.split("#")[0]
        self.assertEqual(answer_errors(answer_for(source_id), spec, reader, {self.ref}),
                         [f"f_energy: evidence reference '{source_id}' names a whole source; cite a read section "
                          "as <source_id>#<section_id> instead."])
        self.assertEqual([e.split(";")[0] for e in answer_errors(answer_for("src_none#sec_none"), spec, reader, {self.ref})],
                         ["f_energy: evidence reference 'src_none#sec_none' is not a known section"])
        local = self.index.model_copy(deep=True)
        local.sources[0].url = local.sources[0].final_url = ""
        self.assertTrue(any("notes alone" in e for e in answer_errors(answer, spec, SourceReader(local), {self.ref})))
        answer.criteria[0].index = 1
        self.assertTrue(any("criterion" in e for e in answer_errors(answer, spec, reader, {self.ref})))

    def test_plan_must_assign_all_original_requirements_and_known_gaps(self):
        plan = QuestionPlan(tasks=[task_value()])
        validate_plan(plan, self.config, self.discovery, None, {})
        self.config.focus_questions = ["Independent validation?"]
        with self.assertRaises(AppError):
            validate_plan(plan, self.config, self.discovery, None, {})
        self.config.focus_questions = []
        with self.assertRaises(AppError):
            validate_plan(plan, self.config, self.discovery, None, {"gap_one": "Original missing evidence"})

    def test_checkpoint_or_original_tampering_is_not_accepted(self):
        engine = self.engine()
        engine.run(self.discovery, self.index)
        path = self.work / "question_research/state.json"
        saved = json.loads(path.read_text())
        saved["value"]["tasks"]["task_definition"]["answer"]["summary"] = "Tampered"
        write_json(path, saved)
        with self.assertRaises(AppError) as raised:
            self.engine().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "invalid_research_checkpoint")
        # Even a re-hashed ledger must retain the independent verification binding.
        save_value(path, saved["value"])
        with self.assertRaises(AppError) as raised:
            self.engine().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "invalid_research_checkpoint")

    def test_legacy_migration_reuses_downloads_and_formally_valid_latest_draft(self):
        reader = SourceReader(self.index)
        context = reader.read([ReaderWindow(reference=self.ref, before=0, after=0)])["context"]
        dossier = fixtures.dossier_from_prompt(json.dumps({"topic": "Test topic", "retrieved_sources": context}))
        folder = self.work / "completeness/round_002"
        save_value(folder / "retrieval.json", {"index": self.index.model_dump()})
        write_json(folder / "source_context.json", context)
        write_json(folder / "dossier_patch_applied.json", {"dossier": dossier.model_dump(), "dossier_hash": digest(dossier.model_dump())})
        result = bootstrap_legacy(self.root, self.work, self.discovery, self.index, None, [])
        self.assertEqual(result[2], dossier)
        self.assertEqual(result[1], self.index)
        count = self.download.call_count
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(self.download.call_count, count)
        self.assertEqual(engine.state["migration"]["drafts"], [str(Path("completeness/round_002/dossier_patch_applied.json"))])

    def test_reader_finds_english_definition_despite_uploaded_link_lists(self):
        original = self.index.sources[0].model_copy(deep=True)
        original.title = "Human Scale Development Max-Neef"
        original.sections = [SourceSection(id=f"sec_{n}", text=f"Max-Neef Human Scale Development introduction {n}") for n in range(40)]
        original.sections[27] = SourceSection(id="sec_definition", page=24,
            text="Singular satisfiers address one need. Synergic satisfiers also stimulate other needs.")
        notes = original.model_copy(deep=True)
        notes.id = "src_notes"
        notes.url = notes.final_url = ""
        notes.sections = [SourceSection(id="sec_links", text=("Max-Neef Human Scale Development " * 100) +
                                        " https://one.test https://two.test https://three.test")]
        reader = SourceReader(SourceIndex(sources=[notes, original], failures=[]))
        query = "Max-Neef Human Scale Development singular satisfiers synergic satisfiers definitions"
        result = reader.search(query, key_terms=["singular satisfiers", "synergic satisfiers"])
        self.assertEqual(result["candidates"][0]["reference"], original.id + "#sec_definition")
        self.assertFalse(any(c["role"] == "user_material" for c in result["candidates"]))
        last = reader.search(query, source_id=original.id, offset=24)
        self.assertEqual(last["offset"], 24)
        self.assertTrue(last["candidates"])
        read = reader.read([ReaderWindow(reference=original.id + "#sec_definition", before=1, after=1)])
        self.assertEqual(len(read["context"][0]["sections"]), 3)
        self.assertEqual(read["context"][0]["sections"][0]["page"], 24)
        bounded = reader.read([ReaderWindow(reference=original.id + "#sec_definition", before=1, after=1)], max_chars=100)
        self.assertTrue(bounded["deferred"])
        with self.assertRaises(AppError):
            reader.read([ReaderWindow(reference="../../secret", before=0, after=0)])

    def test_review_and_reader_prompts_carry_the_brevity_rule_under_the_loop_call_tag(self):
        prompts = {}
        def capture(prompt, schema, payload, kwargs):
            prompts.setdefault(schema, prompt)
        self.hook = capture
        self.engine().run(self.discovery, self.index)
        self.assertIn("one sentence that names the passage and the defect, at most 300 characters", prompts[AnswerReview])
        self.assertIn("at most 300 characters", prompts[ResearchDecision])
        self.assertIn("allowed_actions", prompts[ResearchDecision])
        self.assertIn((ResearchDecision, "question_research.v3-clauses.reader"), self.calls)
        self.assertTrue(any(s is AnswerReview and v.startswith("question_research.v3-clauses.review_") for s, v in self.calls))

    def failing_review(self, reason):
        return AnswerReview(criteria=[dict(index=0, passed=False, reason=reason)],
                            supported=True, source_adequacy=True, issues=[])

    def test_a_failed_review_locks_answering_and_a_fruitless_web_search_blocks_the_task(self):
        payloads = []
        def flow(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                payloads.append(payload)
                if "answer" in payload["allowed_actions"]:
                    return decision("answer", answer=answer_for(self.ref))
                return decision("search_web", web_queries=["freely accessible introduction"])
            if schema is AnswerReview:
                return self.failing_review("No freely accessible introductory source among the passages.")
        self.hook = flow
        engine = self.engine()
        with self.assertRaises(AppError) as raised:
            engine.run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        self.assertEqual(payloads[0]["allowed_actions"], READER_ACTIONS)
        self.assertIsNone(payloads[0]["answer_lock"])
        self.assertEqual(payloads[1]["allowed_actions"], ["search_local", "read", "search_web", "blocked"])
        self.assertEqual(payloads[1]["answer_lock"]["criteria"], [{"index": 0, "text": "Explain energy in this bounded example."}])
        self.assertIn("Criterion 0 not met", payloads[1]["feedback"][0])
        # One web search without new evidence is the escalation: no second recovery, no MAX_STEPS wait.
        self.assertEqual(len(payloads), 2)
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual((row["status"], row["outcome"], row["web_attempts"]), ("blocked", "evidence_block", 1))
        self.assertIn("Kriterium 0: Explain energy in this bounded example.", row["reason"])
        self.assertEqual(public_ledger(engine.state)["blocked"], 1)

    def test_an_answer_while_locked_is_not_reviewed_and_costs_at_most_one_call(self):
        reviews, payloads = [], []
        def flow(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                payloads.append(payload)
                answer = answer_for(self.ref)
                answer.summary += f" Wording {len(payloads)}."
                return decision("answer", answer=answer)
            if schema is AnswerReview:
                reviews.append(1)
                return self.failing_review("The mechanism is absent from the passage.")
        self.hook = flow
        engine = self.engine()
        with self.assertRaises(AppError):
            engine.run(self.discovery, self.index)
        self.assertEqual((len(reviews), len(payloads)), (1, 2))
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual(row["locked_answers"], 1)
        self.assertEqual([a["action"] for a in row["actions"]], ["answer", "answer", "recovery_search_web"])
        self.assertEqual((row["status"], row["outcome"]), ("blocked", "evidence_block"))
        self.assertIn("Kriterium 0", row["reason"])

    def test_recovery_reads_saved_passages_first_and_searches_the_web_before_blocking(self):
        searches = []

        def flow(prompt, schema, payload, kwargs):
            if schema is QuestionSearch:
                searches.append(prompt)
                return QuestionSearch(candidates=[], limitations=["Nothing new in this fixture."])
        self.hook = flow
        engine = self.engine()
        engine.initialise(self.discovery, self.index, None, ())
        spec = QuestionPlan.model_validate(engine.state["plan"]).tasks[0]
        row = engine.state["tasks"][spec.id]
        with engine.guarded():
            engine.seed(spec, row)
            # A reader that answered or chose blocked at once leaves the catalogued passages unread.
            row["read_refs"], row["no_progress"] = [], 2
            self.assertTrue(engine.recover(spec, row))
            self.assertTrue(row["read_refs"])
            self.assertEqual((row["fallbacks"], row["web_attempts"], searches), (1, 0, []))
            # The second recovery is the web search, never a block while a web attempt is left.
            self.assertFalse(engine.recover(spec, row))
            self.assertEqual((row["fallbacks"], row["web_attempts"], len(searches)), (2, 1, 1))
            self.assertEqual(row["actions"][-1]["action"], "recovery_search_web")
            # The third is the concrete block, without another call.
            self.assertFalse(engine.recover(spec, row))
            self.assertEqual((row["fallbacks"], len(searches)), (3, 1))
        # The block names only what happened.
        row.update(answer_locked=True, lock={"criteria": [{"index": 0, "text": spec.acceptance[0]}], "reasons": []})
        engine.lock_block(spec, row)
        self.assertIn("auch die Websuche brachte keine neuen Belege", row["reason"])
        unsearched = {**row, "web_attempts": 0, "reason": ""}
        engine.lock_block(spec, unsearched)
        self.assertIn("die gespeicherten Quellen brachten keine neuen Belege", unsearched["reason"])
        self.assertNotIn("Websuche", unsearched["reason"])

    def test_a_task_blocked_without_a_web_attempt_is_resumed_with_one(self):
        engine = self.engine()
        engine.run(self.discovery, self.index)
        row = engine.state["tasks"]["task_definition"]
        # The block the earlier recovery rule produced: both fallbacks spent on saved passages, the web never searched.
        row.update(status="blocked", outcome="evidence_block", web_attempts=0, fallbacks=2, no_progress=2, pending=None,
                   answer=None, draft_answer=row["answer"], answer_locked=True,
                   lock={"step": row["step"], "web_attempts": 0, "finding_ids": [], "reasons": [],
                         "criteria": [{"index": 0, "text": "Explain energy in this bounded example."}]},
                   reason="Wiederholte Schritte lieferten keine neuen Belege oder geprüfte Antwort.")
        engine.state["phase"] = "blocked"
        engine.save()
        # The Studio learns from the ledger that a resume has something to do here.
        ledger = public_ledger(engine.state)
        self.assertEqual((ledger["reopenable"], ledger["questions"][0]["reopenable"], ledger["questions"][0]["web_attempts"]), (1, True, 0))
        searches = []

        def flow(prompt, schema, payload, kwargs):
            if schema is QuestionSearch:
                searches.append(prompt)
                return QuestionSearch(candidates=[], limitations=["Nothing new in this fixture."])
        self.hook = flow
        resumed = self.engine()
        with self.assertRaises(AppError) as raised:
            resumed.run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        self.assertEqual(len(searches), 1)
        row = resumed.state["tasks"]["task_definition"]
        self.assertEqual((row["status"], row["outcome"], row["web_attempts"], row["fallbacks"]), ("blocked", "evidence_block", 1, 2))
        self.assertIn("auch die Websuche brachte keine neuen Belege", row["reason"])
        self.assertEqual(public_ledger(resumed.state)["reopenable"], 0)
        # Blocked under the current rule, a further resume repeats nothing.
        calls = len(self.calls)
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), calls)

    def test_new_evidence_after_a_failed_review_unlocks_the_answer(self):
        payloads, reviews = [], []
        def flow(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                payloads.append(payload)
                if "answer" not in payload["allowed_actions"]:
                    return decision("search_web", web_queries=["independent evidence"])
                answer = answer_for(self.ref)
                if payload["previous_answer"]:
                    answer.summary += " Revised with the additional source."
                return decision("answer", answer=answer)
            if schema is AnswerReview:
                reviews.append(1)
                if len(reviews) == 1:
                    return self.failing_review("Independent evidence is absent.")
            if schema is QuestionSearch:
                return QuestionSearch(candidates=fixtures.discovery(count=2).candidates[1:], limitations=[])
        self.hook = flow
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"These sentences", b"Additional independent evidence. These sentences"), "text/html", url)
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        locked = [a for a in READER_ACTIONS if a != "answer"]
        self.assertEqual([p["allowed_actions"] for p in payloads], [READER_ACTIONS, locked, READER_ACTIONS])
        self.assertEqual(len(reviews), 2)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual((row["status"], row["answer_locked"]), ("verified", False))
        self.assertEqual(row["verification"]["limitations"], [])

    def test_a_partially_supported_finding_passes_with_a_recorded_limitation_that_reaches_every_output(self):
        composed = []
        def partial(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                review = complete_fixture_response(AnswerReview(
                    criteria=[dict(index=0, passed=True, reason="The passage defines the assignment.")],
                    supported=False, source_adequacy=True, issues=["The summary sharpens 'assign' to 'always assign'."]), payload)
                review.finding_support[0].verdict = "partially_supported"
                review.finding_support[0].unsupported_clauses = ["in this fixture"]
                return review
            if schema is ResearchDossier:
                composed.append(payload)
        self.hook = partial
        engine = self.engine()
        engine.run(self.discovery, self.index)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual(row["status"], "verified")
        limitations = row["verification"]["limitations"]
        self.assertEqual([item["kind"] for item in limitations], ["partial_support", "issue", "not_fully_supported"])
        self.assertEqual(limitations[0]["finding_id"], "f_energy")
        self.assertIn("f_energy: nicht vollständig gestützt: in this fixture", limitations[0]["text"])
        self.assertEqual(limitations[1]["text"], "The summary sharpens 'assign' to 'always assign'.")
        self.assertEqual(public_ledger(engine.state)["questions"][0]["review_limitations"], limitations)
        self.assertEqual(composed[0]["verified_answers"][0]["review_limitations"], limitations)
        quality = (self.work / "research_quality.md").read_text(encoding="utf-8")
        self.assertIn("## Einschränkungen der Prüfung", quality)
        self.assertIn("### What is energy?", quality)
        self.assertIn("- f_energy: nicht vollständig gestützt: in this fixture", quality)
        self.assertIn("always assign", quality)
        self.assertEqual(engine.state["phase"], "completed")
        # The stored verification passes today's check on resume: no call is repeated.
        count = len(self.calls)
        self.engine().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), count)

    def test_blocking_and_limitation_tiers_of_the_review_rule(self):
        spec = QuestionPlan(tasks=[task_value()]).tasks[0]
        answer = answer_for(self.ref)
        passages = read_context(SourceReader(self.index), [self.ref])
        receipts = support_receipts([f.model_dump() for f in answer.findings], passages)
        def review(**changes):
            return AnswerReview(**{"criteria": [dict(index=0, passed=True, reason="Defined in the passage.")], "supported": True,
                                   "source_adequacy": True, "issues": [], **json.loads(json.dumps(receipts)), **changes})
        self.assertEqual(review_outcome(review(), spec, answer.findings, passages), ([], []))
        partial = review()
        partial.finding_support[0].verdict = "partially_supported"
        partial.finding_support[0].unsupported_clauses = ["always"]
        blocking, limitations = review_outcome(partial, spec, answer.findings, passages)
        self.assertEqual(blocking, [])
        self.assertEqual((limitations[0]["finding_id"], limitations[0]["kind"]), ("f_energy", "partial_support"))
        self.assertIn("always", limitations[0]["text"])
        for field, value in (("verdict", "contradicted"), ("verdict", "insufficient_context"),
                             ("suitability", "unsuitable"), ("contract_preserved", False)):
            bad = review()
            bad.finding_support[0].unsupported_clauses = ["always"]
            setattr(bad.finding_support[0], field, value)
            with self.subTest(field=field, value=value):
                self.assertTrue(review_outcome(bad, spec, answer.findings, passages)[0])
        failed = review()
        failed.criteria[0].passed = False
        self.assertEqual(review_outcome(failed, spec, answer.findings, passages)[0], ["Criterion 0 not met: Defined in the passage."])
        self.assertTrue(review_outcome(review(source_adequacy=False), spec, answer.findings, passages)[0])
        self.assertFalse(review_passes(review(source_adequacy=False), spec))
        self.assertTrue(review_passes(review(supported=False, issues=["A wording issue."]), spec))

    def test_repeated_criterion_entries_collapse_and_only_missing_indices_still_reject(self):
        spec = QuestionPlan(tasks=[{**task_value(), "acceptance": ["A", "B", "C", "D"]}]).tasks[0]
        def review(indices):
            return AnswerReview(criteria=[dict(index=i, passed=True, reason="Checked.") for i in indices],
                                supported=True, source_adequacy=True, issues=[])
        # The trace's rejected shape: index 1 listed twice with identical verdicts.
        self.assertTrue(review_passes(review([0, 1, 1, 2, 3]), spec))
        self.assertEqual([c.index for c in normalise_review(review([0, 1, 1, 2, 3]), spec).criteria], [0, 1, 2, 3])
        for position in (1, 2):
            mixed = review([0, 1, 1, 2, 3])
            mixed.criteria[position].passed = False
            with self.subTest(failing_copy=position):
                self.assertFalse(review_passes(mixed, spec), "a differing repeat keeps the failing verdict")
        for indices in ([0, 1, 2, 4], [0, 1, 2], [0, 1, 2, 3, 4]):
            with self.subTest(indices=indices), self.assertRaises(AppError) as raised:
                review_passes(review(indices), spec)
            self.assertEqual(raised.exception.code, "invalid_question_review")
        one = QuestionPlan(tasks=[task_value()]).tasks[0]
        answer = answer_for(self.ref)
        answer.criteria = [answer.criteria[0], answer.criteria[0].model_copy(update={"explanation": "Repeated wording."})]
        answer.findings = answer.findings * 2
        normalised = normalise_answer(answer)
        self.assertEqual((len(normalised.criteria), len(normalised.findings)), (1, 1))
        self.assertEqual(answer_errors(normalised, one, SourceReader(self.index), {self.ref}), [])
        answer.findings[1] = answer.findings[1].model_copy(update={"statement": "A different finding under the same id."})
        self.assertTrue(answer_errors(normalise_answer(answer), one, SourceReader(self.index), {self.ref}))

    def test_the_sequential_ledger_names_the_task_in_progress_and_none_afterwards(self):
        ledgers = []

        def capture(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                ledgers.append(json.loads((self.work / "research_questions.json").read_text(encoding="utf-8")))
        self.hook = capture
        engine = self.engine()
        self.assertEqual(engine.workers, 1)
        engine.run(self.discovery, self.index)
        self.assertEqual([(row["active_task"], row["active_tasks"]) for row in ledgers], [("task_definition", ["task_definition"])])
        self.assertEqual(engine.state["active_tasks"], [])
        self.assertNotIn("active_task", engine.state)
        public = json.loads((self.work / "research_questions.json").read_text(encoding="utf-8"))
        self.assertEqual((public["active_task"], public["active_tasks"]), (None, []))
        # A ledger saved by an earlier version names one task; a resume carries it over as the list.
        path = self.work / "question_research/state.json"
        saved = read_value(path)
        del saved["active_tasks"]
        saved["active_task"] = None
        save_value(path, saved)
        count = len(self.calls)
        resumed = self.engine()
        resumed.run(self.discovery, self.index)
        self.assertEqual((len(self.calls), resumed.state["active_tasks"]), (count, []))
        self.assertNotIn("active_task", read_value(path))


if __name__ == "__main__":
    unittest.main()
