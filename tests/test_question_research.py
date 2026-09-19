"""Question workflow regressions: retrieval, independent gates, bounded work and replay."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.question_research import QuestionResearch, answer_errors, validate_plan
from podcast_automate.question_scope import QuestionScopeReview
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


from tests.question_fixtures import task_value, decision, answer_for, question_response, complete_fixture_response


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
        engine.reopen(dossier, SourceReview(issues=[], limitations=[]),
                      {"blocking_gaps": ["An empirical check is missing."], "requirements": []})
        self.assertEqual(engine.state["tasks"]["task_definition"], old)
        empirical = engine.state["tasks"]["task_empirical"]
        self.assertEqual(empirical["status"], "researching")
        self.assertIsNone(empirical["answer"])
        self.assertIn("empirical check", empirical["reopenings"][0]["reason"][0])

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
        self.assertTrue(answer_errors(answer, spec, reader, set()))
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


if __name__ == "__main__":
    unittest.main()
