import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript, Segment, TopicBrief
from podcast_automate.research import run_research
from podcast_automate.research_models import DossierReview, ResearchDiscovery, ResearchDossier
from podcast_automate.runner import status
from podcast_automate.script_models import (Dependency, EpisodePlan, ScenePlan, ScriptIssue, ScriptReview, SeriesPlan)
from podcast_automate.scripting import run_script, validate_plan, validate_script
from podcast_automate.storage import init_project, read_yaml, write_yaml
from tests.test_research import HTML, discovery, dossier_from_prompt
from tests.teaching_fixtures import teaching_response
from tests.polishing_fixtures import polish_review
from podcast_automate.polishing import DialoguePolishReview
from podcast_automate.teaching import TeachingPlan, TeachingPlanReview, TeachingPlanRepair, ListenerReadback, TeachingReview, EditorialReview


def example_plan():
    return SeriesPlan(topic="Test topic", central_question="Test topic", explanation_path="Start with a concrete comparison.",
        scope_note="A bounded test plan.", dependencies=[], omitted_findings=[], episodes=[EpisodePlan(
            episode_id="ep_001", title="A model compares possibilities", central_question="How are possibilities compared?",
            target_minutes=0.12, prerequisite_episodes=[], finding_ids=["f_energy"], deferred_questions=["Training remains open."],
            scenes=[ScenePlan(scene_id="scene_example", title="A concrete comparison", question="What is scored?",
                purpose="worked_example", finding_ids=["f_energy"], explanation_steps=["Compare two possibilities.", "Explain the limit."])])])


def example_script():
    return EpisodeScript(episode_id="ep_001", title="A model compares possibilities", purpose="deep_dive",
        chapters=[Chapter(chapter_id="scene_example", title="A concrete comparison")],
        segments=[Segment(segment_id="seg_001", scene_id="scene_example", chapter_id="scene_example",
                          speaker_id="host_a", text="What does this model compare?", knowledge_refs=["f_energy"]),
                  Segment(segment_id="seg_002", scene_id="scene_example", chapter_id="scene_example",
                          speaker_id="host_b", text="It scores possibilities. Here a lower score represents a better fit.", knowledge_refs=["f_energy"])])


class ScriptingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Projekt mit Leerzeichen"
        self.config = TopicBrief(topic="Test topic", voice_profile={"host_a": "Aiden", "host_b": "Vivian"})
        init_project(self.root, self.config)

        def research_model(prompt, output_type, directory, **kwargs):
            if output_type is ResearchDiscovery:
                return discovery(), {"research_performed": True}
            if output_type is ResearchDossier:
                return dossier_from_prompt(prompt), {}
            return DossierReview(issues=[], limitations=[]), {}

        with patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")), \
             patch("podcast_automate.research.CodexAdapter.structured", side_effect=research_model):
            self.research = run_research(self.root)
        self.assertEqual(self.research.status, "completed")
        self.calls = []

    def model(self, prompt, output_type, directory, **kwargs):
        self.calls.append(output_type)
        self.assertFalse(kwargs["search"])
        if output_type is DialoguePolishReview:
            return polish_review(prompt), {}
        if output_type in (TeachingPlan, TeachingPlanReview, TeachingPlanRepair, ListenerReadback, TeachingReview, EditorialReview):
            return teaching_response(prompt, output_type), {}
        if output_type is SeriesPlan:
            return example_plan(), {}
        if output_type is EpisodeScript:
            return example_script(), {}
        return ScriptReview(issues=[], limitations=["A fixture is not a real editorial review."]), {}

    def test_model_and_effort_apply_to_every_text_stage_and_resume_keeps_them(self):
        selections = []
        def selected(adapter, *args, **kwargs):
            selections.append((adapter.settings.codex_model, adapter.reasoning_effort))
            return self.model(*args, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", autospec=True, side_effect=selected):
            first = run_script(self.root, model="gpt-6-astra", reasoning_effort="xhigh")
            self.assertEqual(first.status, "completed")
            second = run_script(self.root, resume=True, run_id=first.run_id)
            with self.assertRaises(AppError) as changed:
                run_script(self.root, resume=True, run_id=first.run_id, reasoning_effort="low")
        self.assertEqual(second.status, "completed")
        self.assertEqual(changed.exception.code, "inputs_changed")
        self.assertEqual(selections, [("gpt-6-astra", "xhigh")] * 10)
        request = json.loads((self.root / "runs" / first.run_id / "script_request.json").read_text(encoding="utf-8"))
        self.assertEqual(request["text_generation"]["reasoning_effort"], "xhigh")

    def test_legacy_run_without_reasoning_field_keeps_its_inputs_and_approval(self):
        from podcast_automate.scripting import text_generation_settings, outline_hash
        def legacy_settings(*args, **kwargs):
            selected = text_generation_settings(*args, **kwargs)
            selected.pop("reasoning_effort", None)
            return selected
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            with patch("podcast_automate.scripting.text_generation_settings", side_effect=legacy_settings):
                first = run_script(self.root, plan_only=True)
            work = self.root / "runs" / first.run_id
            before = (work / "script_request.json").read_bytes()
            approval = outline_hash(work)
            second = run_script(self.root, resume=True, approved_plan_hash=approval)
        self.assertEqual(second.status, "completed")
        self.assertEqual(before, (work / "script_request.json").read_bytes())
        self.assertEqual(approval, outline_hash(work))

    def test_research_to_script_preserves_evidence_and_never_generates_audio(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model), \
             patch("podcast_automate.runner.run_tts", side_effect=AssertionError("No audio before user review")):
            first = run_script(self.root, episode="ep_001")
            resumed = run_script(self.root, resume=True)
        self.assertEqual(first.status, "completed")
        self.assertEqual(resumed.run_id, first.run_id)
        self.assertEqual(self.calls, [SeriesPlan, TeachingPlan, TeachingPlanReview, EpisodeScript,
                                     EpisodeScript, DialoguePolishReview, ScriptReview, ListenerReadback, EditorialReview, TeachingReview])
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])
        self.assertTrue(all(s.attempts == 1 for s in resumed.stages.values()))
        text = (self.root / "episodes/ep_001/script.md").read_text(encoding="utf-8")
        self.assertIn("**Aiden:**", text)
        self.assertIn("**Vivian:**", text)
        self.assertNotIn("src_", text)
        knowledge = read_yaml(self.root / "models/knowledge_model.yaml")
        self.assertEqual(knowledge["research_run_id"], self.research.run_id)
        self.assertTrue(knowledge["claims"][0]["evidence"][0]["reference"].startswith("src_"))
        report = read_yaml(self.root / "reports/script_quality.yaml")
        self.assertFalse(report["audio_generated"])
        self.assertFalse(report["human_reviewed"])
        self.assertFalse(report["complete_series_review"])
        approval = read_yaml(self.root / "episodes/audio_review.yaml")
        self.assertFalse(approval["audio_approved"])
        self.assertEqual(approval["status"], "awaiting_user_script_review")
        self.assertEqual(approval["scripts"]["ep_001"], report["episodes"]["ep_001"]["script_sha256"])

    def test_changed_research_is_blocked_before_model_calls(self):
        (self.root / "research/dossier.yaml").write_text("changed", encoding="utf-8")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            with self.assertRaises(AppError) as raised:
                run_script(self.root)
        self.assertEqual(raised.exception.code, "invalid_research")
        self.assertEqual(self.calls, [])

    def test_unknown_finding_reference_prevents_export(self):
        def model(prompt, output_type, directory, **kwargs):
            value, metadata = self.model(prompt, output_type, directory, **kwargs)
            if output_type is EpisodeScript:
                value.segments[0].knowledge_refs = ["invented_claim"]
            return value, metadata
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.stages["writing"].error.code, "invalid_script")
        self.assertEqual(run.stages["publish"].status, "pending")
        self.assertFalse((self.root / "episodes/ep_001/script.yaml").exists())

    def test_quota_after_writing_resumes_through_cli_without_rewriting(self):
        paused = False
        def model(prompt, output_type, directory, **kwargs):
            nonlocal paused
            if output_type is ScriptReview and not paused:
                paused = True
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            first = run_script(self.root)
            self.assertEqual(first.status, "waiting_for_quota")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["resume", str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls.count(EpisodeScript), 2)
        self.assertEqual(json.loads(output.getvalue())["run"]["kind"], "script")

    def test_persistent_editorial_issues_block_and_keep_retry_limit_on_resume(self):
        def model(prompt, output_type, directory, **kwargs):
            self.calls.append(output_type)
            if output_type is DialoguePolishReview:
                return polish_review(prompt), {}
            if output_type in (TeachingPlan, TeachingPlanReview, ListenerReadback, TeachingReview, EditorialReview):
                return teaching_response(prompt, output_type), {}
            if output_type is ScriptReview:
                return ScriptReview(issues=[ScriptIssue(category="depth", segment_ids=["seg_002"],
                                                       reason="The example is not worked through.")], limitations=[]), {}
            return (example_plan() if output_type is SeriesPlan else example_script()), {}
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
            calls = len(self.calls)
            resumed = run_script(self.root, resume=True)
        self.assertEqual(run.stages["review"].error.code, "script_review_failed")
        self.assertEqual(resumed.status, "blocked")
        self.assertEqual(len(self.calls), calls)
        self.assertFalse((self.root / "episodes/ep_001/script.md").exists())

    def test_changed_project_requires_new_script_run(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run_script(self.root)
            self.config.depth_request = "A changed explanation approach"
            write_yaml(self.root / "project.yaml", self.config.model_dump())
            with self.assertRaises(AppError) as raised:
                run_script(self.root, resume=True)
        self.assertEqual(raised.exception.code, "inputs_changed")

    def test_modified_readable_script_is_reexported_without_model_calls(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run_script(self.root)
            (self.root / "episodes/ep_001/script.md").write_text("broken", encoding="utf-8")
            resumed = run_script(self.root, resume=True)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(len(self.calls), 10)
        self.assertIn("**Aiden:**", (self.root / "episodes/ep_001/script.md").read_text(encoding="utf-8"))

    def test_resume_preserves_manual_changes_to_canonical_script(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run_script(self.root)
            path = self.root / "episodes/ep_001/script.yaml"
            data = read_yaml(path)
            data["segments"][0]["text"] = "A user-edited question."
            write_yaml(path, data)
            before = path.read_bytes()
            with self.assertRaises(AppError) as raised:
                run_script(self.root, resume=True)
        self.assertEqual(raised.exception.code, "script_edited")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.calls), 10)

    def test_revision_keeps_plan_and_snapshots_feedback_then_resumes(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            original = run_script(self.root, episode="ep_001")
            plan_before = (self.root / "models/series_plan.yaml").read_bytes()
            self.config.depth_request = "More direct, less repetition."
            write_yaml(self.root / "project.yaml", self.config.model_dump())
            revised = run_script(self.root, revise="ep_001", feedback="Dial down the hand-holding.")
            resumed = run_script(self.root, resume=True)
        self.assertEqual(revised.status, "completed")
        self.assertEqual(resumed.run_id, revised.run_id)
        self.assertNotEqual(original.run_id, revised.run_id)
        self.assertEqual(self.calls.count(SeriesPlan), 1)
        self.assertEqual(self.calls.count(EpisodeScript), 4)
        self.assertEqual(revised.stages["planning"].attempts, 0)
        self.assertEqual((self.root / "models/series_plan.yaml").read_bytes(), plan_before)
        inputs = json.loads((self.root / "runs" / revised.run_id / "inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(inputs["revision"]["source_run_id"], original.run_id)
        self.assertEqual(inputs["revision"]["feedback"], "Dial down the hand-holding.")
        self.assertEqual(inputs["revision"]["original_script"], example_script().model_dump())
        self.assertFalse(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])

    def test_revision_rejects_changed_saved_plan(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            original = run_script(self.root)
            (self.root / "runs" / original.run_id / "series_plan.json").write_text("changed", encoding="utf-8")
            with self.assertRaises(AppError) as raised:
                run_script(self.root, revise="ep_001", feedback="Tighter.")
        self.assertEqual(raised.exception.code, "invalid_revision")
        self.assertEqual(len(self.calls), 10)

    def test_plan_rejects_cycles_and_forward_prerequisites(self):
        dossier = ResearchDossier.model_validate(read_yaml(self.root / "research/dossier.yaml"))
        plan = example_plan()
        plan.dependencies = [Dependency(before="f_energy", after="f_energy", reason="Invalid self dependency")]
        self.assertTrue(any("cycle" in item for item in validate_plan(plan, dossier)))
        plan = example_plan()
        plan.episodes[0].prerequisite_episodes = ["ep_002"]
        self.assertTrue(any("earlier" in item for item in validate_plan(plan, dossier)))

    def test_script_rejects_spoken_metadata_and_excess_duration(self):
        script = example_script()
        script.segments[0].text = "See https://example.org/source"
        self.assertTrue(any("metadata" in item for item in validate_script(script, example_plan().episodes[0])))
        script.segments[0].text = "word " * 3900
        self.assertTrue(any("30 minutes" in item for item in validate_script(script, example_plan().episodes[0])))

    def test_script_duration_matches_plan_without_treating_slow_estimate_as_measured_audio(self):
        entry = example_plan().episodes[0]
        entry.target_minutes = 29.5
        script = example_script()
        self.assertTrue(any("85%" in item for item in validate_script(script, entry)))
        script.segments[0].text = "word " * 3750
        self.assertEqual(validate_script(script, entry), [])

    def test_revision_can_repair_an_existing_script_that_is_too_short(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            self.assertEqual(run_script(self.root, episode="ep_001").status, "completed")
            path = self.root / "episodes/ep_001/script.yaml"
            original = read_yaml(path)
            for segment in original["segments"]:
                segment["text"] = "Short."
            write_yaml(path, original)
            self.calls.clear()
            revised = run_script(self.root, revise="ep_001", feedback="Develop the missing explanation.")
        self.assertEqual(revised.status, "completed")
        self.assertNotIn(SeriesPlan, self.calls)
        self.assertEqual(len(self.calls), 9)

    def test_later_scenes_can_build_on_earlier_findings_without_allowing_forward_references(self):
        entry = example_plan().episodes[0]
        entry.finding_ids.append("f_consequence")
        entry.scenes.append(ScenePlan(scene_id="scene_consequence", title="A consequence",
            question="What follows?", purpose="synthesis", finding_ids=["f_consequence"],
            explanation_steps=["Apply the earlier comparison."]))
        script = example_script()
        script.chapters.append(Chapter(chapter_id="scene_consequence", title="A consequence"))
        segment = script.segments[-1]
        segment.scene_id = segment.chapter_id = "scene_consequence"
        segment.knowledge_refs = ["f_energy", "f_consequence"]
        self.assertEqual(validate_script(script, entry), [])
        script.segments[0].knowledge_refs.append("f_consequence")
        self.assertTrue(any("previously introduced" in item for item in validate_script(script, entry)))


if __name__ == "__main__":
    unittest.main()
