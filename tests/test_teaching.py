import json
import unittest
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.editorial import TERMINOLOGY, TEACHING_SCOPE
from podcast_automate.episode_audio import run_episode_audio
from podcast_automate.storage import read_yaml, write_json
from podcast_automate.scripting import run_script
from podcast_automate.teaching import (TeachingPlan, TeachingPlanReview, TeachingPlanRepair, ListenerReadback, TeachingReview, EditorialReview,
    ResearchGap, assess_teaching, build_teaching_plan, validate_teaching_plan)
from tests import test_scripting as fixtures
from tests.teaching_fixtures import teaching_response


class TeachingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ScriptingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
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
        self.assertTrue(all(TERMINOLOGY in prompt for _, prompt in prompts))
        self.assertTrue(all(TEACHING_SCOPE in prompt for schema, prompt in prompts
                            if schema in (TeachingPlan, TeachingPlanReview, TeachingReview, EditorialReview)))

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
        with patch("podcast_automate.teaching.TERMINOLOGY", TERMINOLOGY + " New guidance."):
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
             patch("podcast_automate.scripting.research_foundations", side_effect=AppError(
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
        self.assertFalse((self.root / "episodes/ep_001/script.md").exists())

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

    def test_learning_gaps_block_despite_other_positive_model_reviews(self):
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ListenerReadback:
                result.answers[0].missing_explanations = ["The answer is named but the comparison is never explained."]
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
            count = len(self.fixture.calls)
            resumed = run_script(self.root, resume=True)
        self.assertEqual(run.stages["review"].error.code, "script_review_failed")
        self.assertEqual(len(self.fixture.calls), count)
        self.assertEqual(resumed.status, "blocked")
        self.assertFalse((self.root / "episodes/ep_001/script.md").exists())

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

    def test_editorial_review_is_blind_to_plan_and_can_override_other_passes(self):
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
        self.assertEqual(run.stages["review"].error.code, "script_review_failed")
        self.assertEqual(set(captured[0]), {"audience", "prior_knowledge", "depth", "script"})
        self.assertFalse((self.root / "episodes/ep_001/script.md").exists())

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
