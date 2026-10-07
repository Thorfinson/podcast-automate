import json
import threading
import unittest
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.editorial import (MACHINE_LEARNING_TERMS, TERMINOLOGY, TEACHING_SCOPE, TOPIC_TERMINOLOGY,
                                        episode_series_context, terminology)
from podcast_automate.episode_audio import run_episode_audio
from podcast_automate.models import EpisodeScript
from podcast_automate.storage import digest, read_yaml, write_json
from podcast_automate.scripting import run_script
from podcast_automate.teaching import (TeachingPlan, TeachingPlanReview, TeachingPlanRepair, ListenerReadback, TeachingReview, EditorialReview,
    ResearchGap, assess_teaching, build_teaching_plan, validate_readback, validate_teaching_plan)
from tests import script_fixtures as fixtures
from tests.teaching_fixtures import teaching_response


class TeachingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root

    def model(self, prompt, output_type, directory, **kwargs):
        return self.fixture.model(prompt, output_type, directory, **kwargs)

    def design(self):
        return teaching_response(json.dumps({"episode": fixtures.example_plan().episodes[0].model_dump()}), TeachingPlan)

    def test_english_terms_are_required_through_writing_polishing_and_independent_reviews(self):
        prompts = []
        def model(prompt, output_type, directory, **kwargs):
            prompts.append((output_type, prompt))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            self.assertEqual(run_script(self.root).status, "completed")
        # Since 2026-10-02 the teaching layer composes the project's own rule (editorial.terminology); the stages
        # that still compose the earlier rule keep it until they switch.
        self.assertTrue(all(TERMINOLOGY in prompt or TOPIC_TERMINOLOGY in prompt for _, prompt in prompts))
        teaching = (TeachingPlan, TeachingPlanReview, TeachingPlanRepair, ListenerReadback, TeachingReview, EditorialReview)
        self.assertTrue(all(TOPIC_TERMINOLOGY in prompt for schema, prompt in prompts if schema in teaching))
        self.assertTrue(all(TEACHING_SCOPE in prompt for schema, prompt in prompts
                            if schema in (TeachingPlan, TeachingPlanReview, TeachingReview, EditorialReview)))

    def test_the_terminology_rule_names_machine_learning_terms_only_for_such_a_topic(self):
        """2026-10-02: every teaching prompt named Query, Key and Value, also for the Asimov series (history,
        sociology) and the English Ontologies series. A German machine-learning topic keeps the names; the rule
        research receipts bind stays byte for byte."""
        from podcast_automate.scripting import load_research
        from podcast_automate.teaching import design_prompt
        self.assertEqual(terminology("de-DE", "Die Entwicklung der Transformer Architektur"),
                         TOPIC_TERMINOLOGY + MACHINE_LEARNING_TERMS)
        for language, topic, question in (
                ("de-DE", "Auf den Spuren von Asimovs Psychohistorie", "Wie entstehen gesellschaftliche Muster?"),
                ("en-US", "Ontologies and Knowledge Work", "How do you build a knowledge base for LLMs and agents?")):
            with self.subTest(topic=topic):
                self.assertEqual(terminology(language, topic, question), TOPIC_TERMINOLOGY)
        self.assertNotIn("Query", TOPIC_TERMINOLOGY)
        self.assertIn("For machine learning use Query, Key, Value", TERMINOLOGY)
        _, dossier, _, _, sources = load_research(self.root, self.fixture.config)
        entry = fixtures.example_plan().episodes[0]
        for topic, expected in (("Test topic", False), ("Wie ein Transformer Attention berechnet", True)):
            config = self.fixture.config.model_copy(update={"topic": topic})
            prompt = design_prompt(config, entry, dossier, sources)
            self.assertTrue(prompt.startswith(TOPIC_TERMINOLOGY))
            self.assertEqual(MACHINE_LEARNING_TERMS in prompt, expected)

    def test_design_rejects_forward_prerequisites_and_fake_synthesis(self):
        entry = fixtures.example_plan().episodes[0]
        design = self.design()
        design.concepts[0].requires = ["energy"]
        design.synthesis.premise_concept_ids = ["candidate", "candidate"]
        errors = validate_teaching_plan(design, entry)
        self.assertTrue(any("prerequisite" in e for e in errors))
        self.assertTrue(any("distinct" in e for e in errors))

    def test_changed_guidance_preserves_draft_but_never_reuses_its_old_review(self):
        from podcast_automate.scripting import load_research
        config = self.fixture.config
        _, dossier, _, _, context = load_research(self.root, config)
        entry = fixtures.example_plan().episodes[0]
        calls = []
        def invoke(prompt, schema, version):
            calls.append(schema)
            result = teaching_response(prompt, schema)
            if schema is TeachingPlanReview and len(calls) > 2:
                result.research_gaps = [ResearchGap(question="An unresolved mechanism?", why_needed="New evidence must be checked.")]
            return result
        folder = self.root / "design"
        build_teaching_plan(config, entry, dossier, context, invoke, folder)
        with patch("podcast_automate.teaching.TEACHING_SCOPE", TEACHING_SCOPE + "New guidance. "):
            with self.assertRaises(AppError):
                build_teaching_plan(config, entry, dossier, context, invoke, folder)
        self.assertEqual(calls.count(TeachingPlan), 1)
        self.assertEqual(calls.count(TeachingPlanReview), 2)

    def test_missing_research_blocks_writing_and_exports_actionable_questions(self):
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingPlanReview:
                result.research_gaps = [ResearchGap(question="How is the score learned?", why_needed="The requested learning mechanism has no evidence.")]
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.script_pipeline.research_foundations", side_effect=AppError(
                 "No additional evidence found.", code="teaching_research_required", status="blocked")):
            run = run_script(self.root)
            call_count = len(self.fixture.calls)
            resumed = run_script(self.root, resume=True)
        self.assertEqual(run.stages["teaching"].error.code, "teaching_research_required")
        self.assertEqual(resumed.status, "blocked")
        self.assertEqual(len(self.fixture.calls), call_count)
        self.assertNotIn(fixtures.EpisodeScript, self.fixture.calls)
        gap = next((self.root / "runs" / run.run_id / "teaching").glob("*/research_needed.json"))
        self.assertIn("learned", json.loads(gap.read_text())["questions"][0]["question"])

    def test_editorial_gap_is_repaired_without_web_research_even_if_author_misclassified_it(self):
        reviews = 0
        def model(prompt, output_type, directory, **kwargs):
            nonlocal reviews
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingPlan and "Repair these design issues" not in prompt:
                result.research_gaps = [ResearchGap(question="Which position of our example should we mark?",
                    why_needed="Our internal illustration needs a concrete position.")]
            elif output_type is TeachingPlan:
                self.assertIn("Redaktionellen Anschluss", prompt)
                result.research_gaps = []
                result.worked_example.setup += " Mark the first candidate in this illustration."
            if output_type is TeachingPlanReview:
                reviews += 1
                for assessment in result.gap_assessments:
                    assessment.kind = "editorial_context"
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.script_pipeline.research_foundations", side_effect=AssertionError("No web search for internal context")):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(reviews, 2)
        self.assertFalse((self.root / "runs" / run.run_id / "teaching/ep_001/research_needed.json").exists())

    def test_prerequisite_example_reaches_later_design_writing_and_source_review(self):
        plan = fixtures.example_plan()
        later = plan.episodes[0].model_copy(deep=True)
        later.episode_id, later.prerequisite_episodes = "ep_002", ["ep_001"]
        plan.episodes.append(later)
        example = "Mara placed the letter in the drawer. She opened the drawer later."
        seen = set()
        def model(prompt, output_type, directory, **kwargs):
            if output_type is fixtures.SeriesPlan:
                return plan, {}
            payload = json.loads(prompt.splitlines()[-1])
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            entry = payload.get("episode", {})
            if output_type is TeachingPlan and entry.get("episode_id") == "ep_001":
                result.worked_example.setup = example
            if entry.get("episode_id") == "ep_002" and (
                output_type in (TeachingPlan, TeachingPlanReview, fixtures.ScriptReview) or
                (output_type is fixtures.EpisodeScript and "series" in payload)):
                inherited = payload["prerequisite_context"]
                self.assertEqual(inherited[0]["teaching_design"]["worked_example"]["setup"], example)
                seen.add(output_type)
            if output_type is fixtures.EpisodeScript:
                result.episode_id = entry["episode_id"]
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(seen, {TeachingPlan, TeachingPlanReview, fixtures.EpisodeScript, fixtures.ScriptReview})
        context_file = self.root / "runs" / run.run_id / "teaching/ep_002/continuity.json"
        self.assertEqual(json.loads(context_file.read_text())[0]["teaching_design"]["worked_example"]["setup"], example)

    def test_context_uses_transitive_prior_plans_and_never_future_or_unreviewed_examples(self):
        from podcast_automate.teaching import prerequisite_context
        plan = fixtures.example_plan()
        for index in (2, 3, 4):
            entry = plan.episodes[0].model_copy(deep=True)
            entry.episode_id = f"ep_{index:03d}"
            entry.prerequisite_episodes = [f"ep_{index-1:03d}"]
            plan.episodes.append(entry)
        work = self.root / "context-test"
        for entry in plan.episodes:
            design = teaching_response(json.dumps({"episode": entry.model_dump()}), TeachingPlan)
            review = {"issues": [], "research_gaps": [], "gap_assessments": []}
            folder = work / "teaching" / entry.episode_id
            write_json(folder / "plan.json", design.model_dump())
            write_json(folder / "review.json", review)
            write_json(folder / "checkpoint.json", {"design": design.model_dump(), "review": review})
        write_json(work / "teaching/ep_002/checkpoint.json", {"design": {}, "review": None})
        rows = prerequisite_context(plan, plan.episodes[2], work)
        self.assertEqual([r["episode_id"] for r in rows], ["ep_001", "ep_002"])
        self.assertEqual(rows[0]["status"], "reviewed_teaching_plan")
        self.assertEqual(rows[1]["status"], "outline_only")
        self.assertNotIn("teaching_design", rows[1])
        self.assertEqual(rows[0]["established_terms"], ["candidate", "energy"])
        self.assertNotIn("established_terms", rows[1])

    def test_only_the_nearest_prerequisite_comes_in_full_and_a_recorded_full_context_stays(self):
        """Asimov, 2026-10-02: the finale built on every earlier episode and got 474,000 characters of prerequisite
        context, repeated in its design, polish, review and every repair. Only the nearest prerequisite comes in full
        now; every other one brings its question, series_role, findings, destination and spoken terms. A run whose
        teaching stage recorded the full form keeps it, because its saved script reviews are bound to it."""
        from podcast_automate.teaching import prerequisite_context
        plan = fixtures.example_plan()
        for index in (2, 3, 4):
            entry = plan.episodes[0].model_copy(deep=True)
            entry.episode_id, entry.series_role = f"ep_{index:03d}", f"Episode {index} adds its part."
            entry.prerequisite_episodes = [f"ep_{n:03d}" for n in range(1, index)]
            plan.episodes.append(entry)
        work = self.root / "finale-context"
        review = {"issues": [], "research_gaps": [], "gap_assessments": []}
        for entry in plan.episodes[:3]:
            design = teaching_response(json.dumps({"episode": entry.model_dump()}), TeachingPlan)
            folder = work / "teaching" / entry.episode_id
            write_json(folder / "plan.json", design.model_dump())
            write_json(folder / "review.json", review)
            write_json(folder / "checkpoint.json", {"design": design.model_dump(), "review": review})
        first, finale = plan.episodes[0], plan.episodes[3]
        rows = prerequisite_context(plan, finale, work)
        self.assertEqual([r["episode_id"] for r in rows], ["ep_001", "ep_002", "ep_003"])
        self.assertEqual(["outline" in r and "teaching_design" in r for r in rows], [False, False, True])
        self.assertEqual(rows[0], {"episode_id": "ep_001", "title": first.title, "status": "reviewed_teaching_plan",
                                   "context": "summary", "central_question": first.central_question,
                                   "series_role": first.series_role, "finding_ids": first.finding_ids,
                                   "destination": "Explain which candidate is preferred and why.",
                                   "established_terms": ["candidate", "energy"]})
        self.assertEqual(rows[1]["series_role"], "Episode 2 adds its part.")
        self.assertEqual(prerequisite_context(plan, finale, work), rows, "the same folder gives the same rows")
        # The earlier form, as a teaching stage before this change recorded it, stays for that run.
        write_json(work / "teaching/ep_004/continuity.json", [{"episode_id": "ep_001", "outline": {}},
                                                               {"episode_id": "ep_002", "outline": {}}])
        recorded = prerequisite_context(plan, finale, work)
        self.assertTrue(all("outline" in r and "teaching_design" in r and "context" not in r for r in recorded))
        self.assertEqual(recorded[2], rows[2])
        self.assertLess(len(json.dumps(rows)), len(json.dumps(recorded)))

    def test_established_terms_use_the_spoken_names_and_never_reach_the_first_episode(self):
        from podcast_automate.teaching import prerequisite_context, spoken_terms
        plan = fixtures.example_plan()
        second = plan.episodes[0].model_copy(deep=True)
        second.episode_id, second.prerequisite_episodes = "ep_002", ["ep_001"]
        plan.episodes.append(second)
        work = self.root / "terms-test"
        design = teaching_response(json.dumps({"episode": plan.episodes[0].model_dump()}), TeachingPlan)
        design.concepts[0].terms = ["Kandidat", "möglicher Ausgang"]
        review = {"issues": [], "research_gaps": [], "gap_assessments": []}
        folder = work / "teaching/ep_001"
        write_json(folder / "plan.json", design.model_dump())
        write_json(folder / "review.json", review)
        write_json(folder / "checkpoint.json", {"design": design.model_dump(), "review": review})
        self.assertEqual(spoken_terms(design.concepts), ["Kandidat", "möglicher Ausgang", "energy"])
        self.assertEqual(prerequisite_context(plan, plan.episodes[0], work), [])
        rows = prerequisite_context(plan, second, work)
        self.assertEqual(rows[0]["established_terms"], ["Kandidat", "möglicher Ausgang", "energy"])

    def test_changed_prerequisite_example_invalidates_the_cached_design_review(self):
        from podcast_automate.scripting import load_research
        _, dossier, _, _, sources = load_research(self.root, self.fixture.config)
        entry = fixtures.example_plan().episodes[0]
        calls = []
        def invoke(prompt, schema, version):
            calls.append((schema, prompt))
            return teaching_response(prompt, schema)
        folder = self.root / "design"
        for example in ("The first example.", "The first example.", "The changed example."):
            build_teaching_plan(self.fixture.config, entry, dossier, sources, invoke, folder,
                                continuity=[{"example": example}])
        self.assertEqual([schema for schema, _ in calls], [TeachingPlan, TeachingPlanReview, TeachingPlanReview])
        self.assertIn("The changed example.", calls[-1][1])

    def test_bad_design_repairs_are_bounded_across_resume(self):
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingPlanReview:
                result.issues = ["The plan introduces results before the task and needed concepts."]
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
            calls = len(self.fixture.calls)
            resumed = run_script(self.root, resume=True)
        self.assertEqual(run.stages["teaching"].error.code, "teaching_design_failed")
        self.assertEqual(len(self.fixture.calls), calls)
        self.assertEqual(resumed.status, "blocked")
        self.assertEqual(self.fixture.calls.count(TeachingPlanRepair), 1)
        self.assertEqual(self.fixture.calls.count(TeachingPlanReview), 4)

    def test_a_design_that_keeps_its_defects_is_designed_anew_with_the_editors_note(self):
        """Ontologies, 2026-09-28: ep_001 kept reading SPARQL keywords aloud after two repairs and the focused
        correction, and the stop offered only a new outline. With the editor's note the next resume designs that
        episode anew with fresh correction rounds; the note reaches the design and its review, the stopped design
        is kept aside, and the approved outline stays."""
        from podcast_automate.run_budget import request_teaching_redesign
        from podcast_automate.script_pipeline import failed_teaching
        note = "Read no query language aloud; spell out every abbreviation the first time."
        designs, reviews = [], []

        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            payload = json.loads(prompt.splitlines()[-1])
            if output_type is TeachingPlan and "episode" in payload:
                designs.append(payload.get("editor_note"))
            if output_type is TeachingPlanReview:
                reviews.append(payload.get("editor_note"))
                if payload.get("editor_note") != note:
                    result.issues = ["Scene 4 reads raw SPARQL keywords aloud."]
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
            work = self.root / "runs" / run.run_id
            plan_before = (work / "series_plan.json").read_bytes()
            self.assertEqual(run.stages["teaching"].error.code, "teaching_design_failed")
            self.assertEqual(failed_teaching(work), {"episode_id": "ep_001", "title": fixtures.example_plan().episodes[0].title})
            for episode, text in (("ep_999", note), ("ep_001", "  ")):
                with self.assertRaises(AppError) as refused:
                    request_teaching_redesign(self.root, run.run_id, episode, text)
                self.assertEqual(refused.exception.code, "invalid_redesign_request")
            request_teaching_redesign(self.root, run.run_id, "ep_001", note)
            resumed = run_script(self.root, resume=True)
            calls = len(self.fixture.calls)
            again = run_script(self.root, resume=True)
        self.assertEqual((resumed.status, again.status), ("completed", "completed"))
        self.assertEqual(len(self.fixture.calls), calls, "the adopted request starts no second redesign")
        self.assertEqual(designs, [None, note], "one fresh design with the note after the stopped one")
        self.assertEqual(reviews[-1], note)
        self.assertEqual(reviews.count(note), 1, "the new design passes its first review")
        folder = work / "teaching/ep_001"
        self.assertTrue((folder / "redesign_01/checkpoint.json").exists())
        self.assertTrue((folder / "redesign_01/focused_repair.json").exists())
        self.assertEqual([(row["note"], row["archive"]) for row in json.loads((folder / "redesign.json").read_text(encoding="utf-8"))],
                         [(note, "redesign_01")])
        self.assertEqual((work / "series_plan.json").read_bytes(), plan_before)
        self.assertIsNone(failed_teaching(work))
        with self.assertRaises(AppError):
            request_teaching_redesign(self.root, run.run_id, "ep_001", note)

    def test_a_follow_up_review_blocks_only_on_earlier_points_the_note_or_critical_defects(self):
        """Ontologies, 2026-09-28: each redesign of ep_001 passed the points raised before and failed on new
        details, so the review never settled. After the first review, a review may block only on an earlier issue
        still unresolved, on the editor's note or on a new critical defect, each with its basis; every other new
        observation is an advisory the writer receives, and the design passes."""
        from podcast_automate.script_pipeline import WRITE_EPISODE_VERSION
        first = "Scene 1 introduces results before the task."
        new = "Scene 2 uses 'reasoner' before introducing it."
        reviews, writing = [], []

        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingPlanReview:
                payload = json.loads(prompt.splitlines()[-1])
                reviews.append((payload.get("previous_issues"), "issue_basis" in prompt.splitlines()[-2]))
                if len(reviews) == 1:
                    result.issues = [first]
                elif len(reviews) == 2:
                    result.issues = [new]  # a new, non-critical point without a basis: corrected, not accepted
                else:
                    result.issues, result.advisories = [], [new]
            if output_type is EpisodeScript and kwargs.get("prompt_version") == WRITE_EPISODE_VERSION:
                writing.append(prompt)
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["teaching"].error)
        self.assertEqual([previous for previous, _ in reviews], [None, [first], [first]])
        self.assertEqual(self.fixture.calls.count(TeachingPlanReview), 3, "one correction attempt for the missing basis")
        folder = self.root / "runs" / run.run_id / "teaching/ep_001"
        self.assertEqual(json.loads((folder / "review_scope.json").read_text(encoding="utf-8")), [first])
        self.assertEqual(json.loads((folder / "review.json").read_text(encoding="utf-8"))["advisories"], [new])
        self.assertTrue(writing and all("teaching_design_review.advisories" in prompt for prompt in writing))
        self.assertIn(new, writing[0].splitlines()[-1])

    def test_code_sets_what_a_follow_up_design_review_may_block_on(self):
        """2026-10-02: a follow-up review blocked on every issue whatever basis it gave. The code checks the basis
        now: an issue marked previous must hold an earlier issue's text, editor_note needs an editor's note, a
        critical defect still blocks. Everything else is an advisory for the writer and never joins the scope."""
        from podcast_automate.teaching import scoped_review
        earlier = "Scene 1 introduces results before the task."
        still = earlier + " The task is still named only in scene 2."
        relabelled = "Scene 2 leaves 'reasoner' unexplained."
        noted, wrong = "Scene 3 reads SPARQL aloud.", "Scene 3 misstates the cited mechanism."
        review = TeachingPlanReview(research_gaps=[], gap_assessments=[], advisories=["Pacing is brisk."],
                                    issues=[earlier, still, relabelled, noted, wrong],
                                    issue_basis=["previous", "previous", "previous", "editor_note", "factual_error"])
        scoped = scoped_review(review, [earlier])
        self.assertEqual(scoped.issues, [earlier, still, wrong])
        self.assertEqual(scoped.issue_basis, ["previous", "previous", "factual_error"])
        self.assertEqual(scoped.advisories, ["Pacing is brisk.", relabelled, noted])
        self.assertEqual(scoped_review(scoped, [earlier]), scoped, "applied twice it changes nothing")
        self.assertEqual(scoped_review(review, [earlier], "Read no query language aloud.").issues,
                         [earlier, still, noted, wrong])
        self.assertIs(scoped_review(review, []), review, "a first review sets the scope")
        # A repetition word for word needs no basis, and it is recorded as previous.
        bare = TeachingPlanReview(research_gaps=[], gap_assessments=[], issues=["scene 1 introduces results before the task"])
        self.assertEqual(scoped_review(bare, [earlier]).issue_basis, ["previous"])

    def test_a_relabelled_new_point_in_a_follow_up_review_becomes_an_advisory_and_the_design_passes(self):
        """A follow-up review that calls a new, non-critical point previous no longer blocks the design."""
        first = "Scene 1 introduces results before the task."
        new = "Scene 2 uses 'reasoner' before introducing it."
        reviews = []

        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingPlanReview:
                reviews.append(json.loads(prompt.splitlines()[-1]).get("previous_issues"))
                result.issues, result.issue_basis = ([first], []) if len(reviews) == 1 else ([new], ["previous"])
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["teaching"].error)
        self.assertEqual(reviews, [None, [first]])
        folder = self.root / "runs" / run.run_id / "teaching/ep_001"
        saved = json.loads((folder / "review.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["issues"], saved["advisories"]), ([], [new]))
        self.assertEqual(json.loads((folder / "review_scope.json").read_text(encoding="utf-8")), [first])

    def test_series_goal_reaches_the_design_its_review_and_the_script_reviews_but_not_the_listener(self):
        """2026-10-02: the teaching layer never saw the brief's series_goal and kept designing the Asimov episodes
        around studies and their results. The design, its review, the editorial and the teaching review read it now;
        the listener answers from the dialogue alone. Without a goal every payload stays as it was."""
        from podcast_automate.scripting import load_research
        aim = {"understand": 3, "evaluate": 2, "apply": 0}
        _, dossier, _, _, sources = load_research(self.root, self.fixture.config)
        entry = fixtures.example_plan().episodes[0]
        payloads = {}

        def invoke(prompt, schema, version):
            payloads.setdefault(schema, json.loads(prompt.splitlines()[-1]))
            return teaching_response(prompt, schema)
        config = self.fixture.config.model_copy(update={"series_goal": aim})
        design, _ = build_teaching_plan(config, entry, dossier, sources, invoke, self.root / "goal")
        self.assertEqual(payloads[TeachingPlan]["brief"]["series_goal"], aim)
        self.assertEqual(payloads[TeachingPlanReview]["brief"]["series_goal"], aim)
        args = dict(audience="Adults", prior_knowledge="None", depth="Explain the comparison")
        assess_teaching(fixtures.example_script(), design, invoke, self.root / "goal-review", series_goal=aim, **args)
        self.assertEqual(payloads[EditorialReview]["series_goal"], aim)
        self.assertEqual(payloads[TeachingReview]["series_goal"], aim)
        self.assertNotIn("series_goal", payloads[ListenerReadback])
        payloads.clear()
        build_teaching_plan(self.fixture.config, entry, dossier, sources, invoke, self.root / "no-goal")
        self.assertNotIn("series_goal", payloads[TeachingPlan]["brief"])
        self.assertNotIn("series_goal", payloads[TeachingPlanReview]["brief"])

    def test_a_worked_example_needs_no_misconception_or_limit_and_saved_plans_read_as_before(self):
        """2026-10-02: the contract demanded a misconception, its correction and a limit for every worked example,
        which pushed each theory episode toward a study with a measured result. They are optional now; a saved plan
        that has them validates, dumps and renders exactly as before."""
        from podcast_automate.teaching import render_teaching_plan
        entry = fixtures.example_plan().episodes[0]
        saved = self.design().model_dump()
        old = TeachingPlan.model_validate(saved)
        self.assertEqual(old.model_dump(), saved)
        self.assertIn("\n\nMögliche Fehlvorstellung: Lower is always worse.\n\nThis score uses the lower-is-better "
                      "convention.\n\nGrenze: Scores alone do not provide probabilities.\n\n## Synthese und Übertragung",
                      render_teaching_plan(old))
        example = {key: value for key, value in saved["worked_example"].items()
                   if key not in ("misconception", "correction", "limits")}
        qualitative = TeachingPlan.model_validate({**saved, "worked_example": example})
        self.assertEqual(validate_teaching_plan(qualitative, entry), [])
        rendered = render_teaching_plan(qualitative)
        self.assertNotIn("Fehlvorstellung", rendered)
        self.assertNotIn("Grenze", rendered)
        self.assertIn("lower one because it indicates fit.\n\n## Synthese und Übertragung", rendered)
        half = TeachingPlan.model_validate({**saved, "worked_example": {**example, "misconception": "Lower is worse."}})
        self.assertTrue(any("misconception together with its correction" in e for e in validate_teaching_plan(half, entry)))

    def test_the_arc_is_optional_in_the_contract_and_a_plan_saved_before_it_reads_as_before(self):
        """2026-10-06: the design plans a narrative arc (one big idea, a held-back answer, a first answer, a turning point
        a finding brings about, payoff and callback). Its fields are optional, so a plan saved before validates, dumps,
        hashes and renders exactly as before; where a plan names its turn, the turn rests on the episode's findings."""
        from podcast_automate.teaching import render_teaching_plan
        entry = fixtures.example_plan().episodes[0]
        saved = self.design().model_dump()
        old = TeachingPlan.model_validate(saved)
        self.assertEqual((old.model_dump(), digest(old.model_dump())), (saved, digest(saved)))
        self.assertFalse(set(TeachingPlan.LATER) & set(saved))
        self.assertNotIn("Spannungsbogen", render_teaching_plan(old))
        arc = {"big_idea": "A score decides between candidates.", "hook_question": "Why would lower be better?",
               "first_answer": "A higher score sounds better.", "turning_point": "The energy convention turns it around.",
               "turning_finding_ids": ["f_energy"], "payoff": "Lower energy means a better fit.",
               "callback": "The two candidates from the opening."}
        self.assertEqual(set(arc), set(TeachingPlan.LATER))
        planned = TeachingPlan.model_validate({**saved, **arc})
        self.assertEqual(validate_teaching_plan(planned, entry), [])
        self.assertEqual({key: planned.model_dump()[key] for key in arc}, arc)
        rendered = render_teaching_plan(planned)
        self.assertIn("## Spannungsbogen\n\nGroße Idee: A score decides between candidates.\n\n"
                      "Leitfrage: Why would lower be better?\n\n", rendered)
        self.assertIn("Rückgriff auf den Anfang: The two candidates from the opening.\n\n## Lernziele", rendered)
        for broken, message in (({"turning_finding_ids": []}, "together with the findings"),
                                ({"turning_point": ""}, "together with the findings"),
                                ({"turning_finding_ids": ["f_unknown"]}, "findings assigned to this episode")):
            with self.subTest(broken=broken):
                errors = validate_teaching_plan(planned.model_copy(update=broken), entry)
                self.assertTrue(any(message in error for error in errors), errors)

    def test_the_review_scope_starts_from_the_designs_a_redesign_set_aside(self):
        from podcast_automate.teaching import review_scope
        folder = self.root / "scope"
        self.assertEqual(review_scope(folder), [])
        write_json(folder / "redesign_01/checkpoint.json", {"review": {"issues": ["Reads SPARQL aloud.", "OMG unexplained."]}})
        write_json(folder / "redesign_02/checkpoint.json", {"review": {"issues": ["OMG unexplained.", "Scene 1 overloaded."]}})
        write_json(folder / "redesign_03/checkpoint.json", {"review": None})
        self.assertEqual(review_scope(folder), ["Reads SPARQL aloud.", "OMG unexplained.", "Scene 1 overloaded."])
        write_json(folder / "review_scope.json", ["Kept as saved."])
        self.assertEqual(review_scope(folder), ["Kept as saved."])

    def test_focused_correction_continues_approved_job_without_another_click(self):
        from podcast_automate.scripting import outline_hash
        issue = "Explain why lower energy is preferred."
        correction = "Lower energy represents greater compatibility, so the lower-energy candidate is preferred."
        reviews = []
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingPlanRepair:
                result.design.scenes[0].reasoning_steps.append(correction)
                result.corrections[0].revised_passages = [correction]
            if output_type is TeachingPlanReview:
                payload = json.loads(prompt.splitlines()[-1])
                reviews.append(payload)
                self.assertNotIn("corrections", payload)
                if correction not in payload["design"]["scenes"][0]["reasoning_steps"]:
                    result.issues = [issue]
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            planned = run_script(self.root, plan_only=True)
            work = self.root / "runs" / planned.run_id
            plan_hash = outline_hash(work)
            run = run_script(self.root, resume=True, approved_plan_hash=plan_hash)
        self.assertEqual(run.status, "completed")
        self.assertEqual(outline_hash(work), plan_hash)
        self.assertEqual(json.loads((work / "plan_approval.json").read_text())["plan_hash"], plan_hash)
        self.assertEqual(len(reviews), 4)
        self.assertEqual(self.fixture.calls.count(TeachingPlanRepair), 1)
        self.assertIn(correction, (work / "teaching/ep_001/plan.md").read_text())
        self.assertTrue(any(path.endswith("focused_repair.json") for path in run.stages["teaching"].outputs))
        self.assertFalse(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])

    def test_legacy_exhausted_checkpoint_gets_one_focused_repair_and_reuses_it_after_interruption(self):
        from podcast_automate.scripting import load_research
        from podcast_automate.storage import digest
        from podcast_automate.teaching import DESIGN_VERSION, design_prompt
        config = self.fixture.config
        _, dossier, _, _, sources = load_research(self.root, config)
        entry = fixtures.example_plan().episodes[0]
        folder = self.root / "design"
        design = self.design()
        write_json(folder / "checkpoint.json", {
            "input_hash": digest({"version": DESIGN_VERSION, "prompt": design_prompt(config, entry, dossier, sources)}),
            "design": design.model_dump(), "review": {"issues": ["Explain the mechanism."],
            "research_gaps": [], "gap_assessments": []}, "repairs": 2})
        calls = []
        def invoke(prompt, schema, version):
            calls.append(schema)
            if schema is TeachingPlanReview and calls.count(schema) == 1:
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return teaching_response(prompt, schema)
        with self.assertRaises(AppError):
            build_teaching_plan(config, entry, dossier, sources, invoke, folder)
        build_teaching_plan(config, entry, dossier, sources, invoke, folder)
        self.assertEqual(calls, [TeachingPlanRepair, TeachingPlanReview, TeachingPlanReview])
        self.assertTrue(json.loads((folder / "checkpoint.json").read_text())["focused_repair"])

    def test_focused_repair_cannot_claim_missing_or_invented_corrections(self):
        for invalid in ("missing_issue", "invented_quote"):
            with self.subTest(invalid=invalid):
                from podcast_automate.scripting import load_research
                config = self.fixture.config
                _, dossier, _, _, sources = load_research(self.root, config)
                entry = fixtures.example_plan().episodes[0]
                def invoke(prompt, schema, version):
                    result = teaching_response(prompt, schema)
                    if schema is TeachingPlanReview:
                        result.issues = ["Explain the mechanism.", "Explain the limitation."]
                    if schema is TeachingPlanRepair:
                        if invalid == "missing_issue":
                            result.corrections.pop()
                        else:
                            result.corrections[0].revised_passages = ["This invented quote is absent from the design."]
                    return result
                folder = self.root / invalid
                with self.assertRaises(AppError) as raised:
                    build_teaching_plan(config, entry, dossier, sources, invoke, folder)
                self.assertEqual(raised.exception.code, "invalid_teaching_repair")
                self.assertFalse((folder / "plan.json").exists())

    def test_known_research_limit_requires_explicit_scope_decision(self):
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingPlan:
                result.research_gaps = [ResearchGap(question="Is the algorithm always optimal?",
                    why_needed="A global guarantee is unknown, but the episode only explains how to compare two scores.")]
            if output_type is TeachingPlanReview:
                result.gap_assessments[0].required_for_objective = False
                result.gap_assessments[0].reason = "No general optimality claim is made or required for the stated comparison goal."
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertIn("optimality", (self.root / "episodes/ep_001/teaching_plan.md").read_text(encoding="utf-8"))

    def test_quoted_review_pass_cannot_hide_a_missing_criterion(self):
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingReview:
                result.checks.pop()
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.stages["review"].error.code, "invalid_teaching_review")
        # The answer and two corrections, each told what was missing; then the stop.
        self.assertEqual(self.fixture.calls.count(TeachingReview), 3)
        self.assertIn("Fehlend: spoken_clarity.", run.stages["review"].error.message)
        self.assertFalse((self.root / "episodes/ep_001/script.md").exists())

    def test_a_review_that_skips_a_criterion_once_is_asked_again_and_the_run_completes(self):
        """Until 2026-09-28 one malformed review ended a script run, and a resume replayed it."""
        prompts = []
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingReview:
                prompts.append(prompt)
                if len(prompts) == 1:
                    result.checks.pop()
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(prompts), 2)
        self.assertNotIn("Lehrprüfung lässt Kriterien", prompts[0])
        self.assertIn("Lehrprüfung lässt Kriterien oder Lernziele aus. Fehlend: spoken_clarity.", prompts[1])
        self.assertEqual(prompts[1].splitlines()[-1], prompts[0].splitlines()[-1], "the payload stays the last line")

    def test_a_readback_saved_before_its_check_is_asked_again_instead_of_replayed(self):
        calls = []
        def invoke(prompt, output_type, version):
            calls.append(output_type)
            return teaching_response(prompt, output_type)
        script, design = fixtures.example_script(), self.design()
        args = dict(audience="Adults", prior_knowledge="None", depth="Explain the comparison")
        folder = self.root / "stored_before_check"
        assess_teaching(script, design, invoke, folder, **args)
        # A readback that skips its only objective, stored with a matching stamp as older code wrote it.
        listener = next(folder.glob("*/listener.json"))
        stamp = listener.with_name("listener.checkpoint.json")
        broken = {**json.loads(listener.read_text(encoding="utf-8")), "answers": []}
        with self.assertRaises(AppError):
            validate_readback(ListenerReadback.model_validate(broken), design, script)
        write_json(listener, broken)
        write_json(stamp, {**json.loads(stamp.read_text(encoding="utf-8")), "digest": digest(broken)})
        calls.clear()
        issues, report, _ = assess_teaching(script, design, invoke, folder, **args)
        self.assertEqual((issues, report["status"]), ([], "passed"))
        self.assertEqual(calls, [ListenerReadback], "the unchanged later reviews are reused")

    def test_listener_and_editorial_ask_at_once_in_a_parallel_run_and_keep_the_episode(self):
        from podcast_automate.call_activity import CALL_SUBJECT
        # Both independent reviews must be in flight together: a sequential order never meets at the barrier.
        meeting, seen = threading.Barrier(2, timeout=10), []

        def invoke(prompt, output_type, version):
            if output_type in (ListenerReadback, EditorialReview):
                meeting.wait()
            seen.append((output_type.__name__, CALL_SUBJECT.get()))
            return teaching_response(prompt, output_type)
        args = dict(audience="Adults", prior_knowledge="None", depth="Explain the comparison")
        token = CALL_SUBJECT.set("ep_001")
        try:
            issues, report, _ = assess_teaching(fixtures.example_script(), self.design(), invoke,
                                                self.root / "parallel", parallel=True, **args)
        finally:
            CALL_SUBJECT.reset(token)
        self.assertEqual((issues, report["status"]), ([], "passed"))
        self.assertEqual(sorted(seen[:2]), [("EditorialReview", "ep_001"), ("ListenerReadback", "ep_001")])
        self.assertEqual(seen[2], ("TeachingReview", "ep_001"), "the teaching review reads the listener's answers")

    def test_invented_supporting_quote_is_rejected(self):
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingReview:
                result.checks[0].evidence[0].quote = "This explanation is not in the actual script."
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.stages["review"].error.code, "invalid_teaching_evidence")
        self.assertFalse((self.root / "episodes/ep_001/script.yaml").exists())

    def test_learning_gaps_are_repaired_then_noted_despite_other_positive_model_reviews(self):
        """A learning gap the listener readback finds drives the script repairs even when every other review passes.
        Until 2026-09-29 it then stopped the run; the user chose that, with the repairs spent, a depth point becomes
        a note the reader sees before approving audio. A resume asks nothing again."""
        gap = "The answer is named but the comparison is never explained."

        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ListenerReadback:
                result.answers[0].missing_explanations = [gap]
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
            count = len(self.fixture.calls)
            resumed = run_script(self.root, resume=True)
        self.assertEqual((run.status, resumed.status), ("completed", "completed"))
        self.assertEqual(len(self.fixture.calls), count)
        notes = json.loads((self.root / "runs" / run.run_id / "reviews/ep_001_accepted_notes.json").read_text(encoding="utf-8"))
        self.assertTrue(any(gap in note["reason"] for note in notes))
        self.assertEqual({note["category"] for note in notes} - {"depth", "clarity", "dialogue"}, set())

    def test_reader_never_receives_expected_answers_or_dossier(self):
        captured = []
        def model(prompt, output_type, directory, **kwargs):
            if output_type is ListenerReadback:
                captured.append(json.loads(prompt.splitlines()[-1]))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            self.assertEqual(run_script(self.root).status, "completed")
        self.assertEqual(set(captured[0]), {"audience", "prior_knowledge", "script", "questions"})
        self.assertEqual(set(captured[0]["questions"][0]), {"objective_id", "question"})

    def test_editorial_review_has_series_metadata_but_no_teaching_answers_and_can_override_other_passes(self):
        captured = []
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is EditorialReview:
                captured.append(json.loads(prompt.splitlines()[-1]))
                result.checks[4].verdict = "fail"
                result.checks[4].reason = "The speakers alternate essay paragraphs without responding to each other's arguments."
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        # The editorial failure overrides the other passes and drives the repairs; with them spent it is a noted
        # dialogue point since 2026-09-29, not a stop.
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        notes = json.loads((self.root / "runs" / run.run_id / "reviews/ep_001_accepted_notes.json").read_text(encoding="utf-8"))
        self.assertTrue(any("essay paragraphs" in note["reason"] for note in notes))
        self.assertEqual(set(captured[0]), {"audience", "prior_knowledge", "depth", "series_context", "script"})
        plan = fixtures.example_plan()
        self.assertEqual(captured[0]["series_context"], episode_series_context(plan, plan.episodes[0]))

    def test_reported_gap_cannot_be_silently_dropped_by_examiner(self):
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ListenerReadback:
                result.answers[0].missing_explanations = ["The comparison rule is absent."]
            if output_type is TeachingReview:
                result.objectives[0].gap_assessments = []
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.stages["review"].error.code, "invalid_teaching_review")

    def test_explicitly_out_of_scope_gap_does_not_force_unrelated_detail(self):
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ListenerReadback:
                result.answers[0].missing_explanations = ["No derivation of the hardware's electrical consumption."]
            if output_type is TeachingReview:
                result.objectives[0].gap_assessments[0].required_for_objective = False
                result.objectives[0].gap_assessments[0].reason = "Hardware power is unrelated to comparing candidate scores."
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")

    def test_assess_teaching_reports_a_dismissed_gap_with_objective_and_reason(self):
        gap = "No derivation of the hardware's electrical consumption."
        reason = "Hardware power is unrelated to comparing candidate scores."
        def invoke(prompt, output_type, version):
            result = teaching_response(prompt, output_type)
            if output_type is ListenerReadback:
                result.answers[0].missing_explanations = [gap]
            if output_type is TeachingReview:
                result.objectives[0].gap_assessments[0].required_for_objective = False
                result.objectives[0].gap_assessments[0].reason = reason
            return result
        args = dict(audience="Adults", prior_knowledge="None", depth="Explain the comparison")
        issues, report, _ = assess_teaching(fixtures.example_script(), self.design(), invoke,
                                            self.root / "dismissed", **args)
        self.assertEqual(issues, [])
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["dismissed_gaps"], [{"stage": "teaching_review", "objective_id": "goal_compare",
                                                    "gap": gap, "reason": reason}])

    def test_build_teaching_plan_records_the_design_time_dismissal(self):
        from podcast_automate.scripting import load_research
        config = self.fixture.config
        _, dossier, _, _, context = load_research(self.root, config)
        entry = fixtures.example_plan().episodes[0]
        question = "Is the algorithm always optimal?"
        reason = "No general optimality claim is made or required for the stated comparison goal."
        def invoke(prompt, schema, version):
            result = teaching_response(prompt, schema)
            if schema is TeachingPlan:
                result.research_gaps = [ResearchGap(question=question, why_needed="A global guarantee is unknown.")]
            if schema is TeachingPlanReview:
                result.gap_assessments[0].required_for_objective = False
                result.gap_assessments[0].reason = reason
            return result
        folder = self.root / "design-dismissal"
        _, files = build_teaching_plan(config, entry, dossier, context, invoke, folder)
        self.assertIn(folder / "dismissed_gaps.json", files)
        self.assertEqual(json.loads((folder / "dismissed_gaps.json").read_text(encoding="utf-8")),
                         [{"stage": "teaching_design", "objective_id": None, "gap": question, "reason": reason}])

    def test_dismissed_gaps_of_both_stages_reach_the_quality_report_and_the_review_notes(self):
        from podcast_automate.studio_scripts import review_notes
        design_gap, design_reason = "Is the algorithm always optimal?", "No optimality claim is made."
        listener_gap, listener_reason = "No derivation of the hardware's electrical consumption.", "Unrelated to the comparison."
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is TeachingPlan:
                result.research_gaps = [ResearchGap(question=design_gap, why_needed="A global guarantee is unknown.")]
            if output_type is TeachingPlanReview:
                result.gap_assessments[0].required_for_objective = False
                result.gap_assessments[0].reason = design_reason
            if output_type is ListenerReadback:
                result.answers[0].missing_explanations = [listener_gap]
            if output_type is TeachingReview:
                result.objectives[0].gap_assessments[0].required_for_objective = False
                result.objectives[0].gap_assessments[0].reason = listener_reason
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        expected = [{"stage": "teaching_design", "objective_id": None, "gap": design_gap, "reason": design_reason},
                    {"stage": "teaching_review", "objective_id": "goal_compare", "gap": listener_gap,
                     "reason": listener_reason}]
        episode = read_yaml(self.root / "reports/script_quality.yaml")["episodes"]["ep_001"]
        self.assertEqual(episode["dismissed_gaps"], expected)
        self.assertEqual(review_notes(episode)["dismissed_gaps"], expected)

    def test_quota_after_readback_reuses_it_and_changed_text_invalidates_it(self):
        calls, pause = [], True
        def invoke(prompt, output_type, version):
            nonlocal pause
            calls.append(output_type)
            if output_type is TeachingReview and pause:
                pause = False
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return teaching_response(prompt, output_type)
        args = dict(audience="Adults", prior_knowledge="None", depth="Explain the comparison")
        script, design = fixtures.example_script(), self.design()
        directory = self.root / "evaluation"
        with self.assertRaises(AppError):
            assess_teaching(script, design, invoke, directory, **args)
        issues, _, _ = assess_teaching(script, design, invoke, directory, **args)
        self.assertEqual(issues, [])
        self.assertEqual(calls.count(ListenerReadback), 1)
        script.segments[-1].text += " A changed explanation."
        assess_teaching(script, design, invoke, directory, **args)
        self.assertEqual(calls.count(ListenerReadback), 2)

    def test_changed_editorial_policy_rechecks_only_its_cached_verdict(self):
        calls = []
        def invoke(prompt, output_type, version):
            calls.append(output_type)
            return teaching_response(prompt, output_type)
        script, design = fixtures.example_script(), self.design()
        args = dict(audience="Adults", prior_knowledge="None", depth="Explain the comparison")
        folder = self.root / "editorial_cache"
        assess_teaching(script, design, invoke, folder, **args)
        calls.clear()
        with patch("podcast_automate.teaching.EDITORIAL_REVIEW_VERSION", "changed-editorial-policy"):
            assess_teaching(script, design, invoke, folder, **args)
            assess_teaching(script, design, invoke, folder, **args)
        self.assertEqual(calls, [EditorialReview])

    def test_legacy_checks_without_prompt_binding_are_not_reused(self):
        calls = []
        def invoke(prompt, output_type, version):
            calls.append(output_type)
            return teaching_response(prompt, output_type)
        script, design = fixtures.example_script(), self.design()
        args = dict(audience="Adults", prior_knowledge="None", depth="Explain the comparison")
        folder = self.root / "legacy_editorial_cache"
        assess_teaching(script, design, invoke, folder, **args)
        for path in folder.glob("*/*.checkpoint.json"):
            saved = json.loads(path.read_text(encoding="utf-8"))
            saved.pop("prompt_hash")
            write_json(path, saved)
        calls.clear()
        assess_teaching(script, design, invoke, folder, **args)
        self.assertEqual(calls, [ListenerReadback, EditorialReview, TeachingReview])

    def test_damaged_teaching_artifact_blocks_audio_even_with_approval(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run = run_script(self.root)
        write_json(self.root / "runs" / run.run_id / "teaching/ep_001/plan.json", {})
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("must not render")):
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, episode="ep_001", approve_audio=True)
        self.assertEqual(caught.exception.code, "invalid_script")

    def test_teaching_report_is_published_with_no_human_approval_claim(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            self.assertEqual(run_script(self.root).status, "completed")
        report = read_yaml(self.root / "reports/script_quality.yaml")["episodes"]["ep_001"]["teaching_review"]
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["human_learning_validated"])
        self.assertTrue((self.root / "episodes/ep_001/teaching_plan.md").exists())
        self.assertFalse(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])

    def test_review_version_change_cannot_reuse_old_completed_checks(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run_script(self.root)
        with patch("podcast_automate.scripting.TEACHING_VERSION", "a_new_review_version"):
            with self.assertRaises(AppError) as caught:
                run_script(self.root, resume=True)
        self.assertEqual(caught.exception.code, "inputs_changed")


if __name__ == "__main__":
    unittest.main()
