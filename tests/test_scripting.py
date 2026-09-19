import contextlib
import io
import json
import unittest
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript
from podcast_automate.research_models import ResearchDossier
from podcast_automate.runner import status
from podcast_automate.script_models import Dependency, ScenePlan, ScriptIssue, ScriptReview, SeriesPlan
from podcast_automate.scripting import run_script, validate_plan, validate_script
from podcast_automate.storage import read_yaml, write_json, write_yaml
from tests import script_fixtures as fixtures
from tests.script_fixtures import example_plan, example_script
from tests.teaching_fixtures import teaching_response
from tests.polishing_fixtures import polish_review
from tests.question_fixtures import script_checks
from podcast_automate.series_review import SeriesReview
from podcast_automate.polishing import DialoguePolishReview
from podcast_automate.teaching import TeachingPlan, TeachingPlanReview, ListenerReadback, TeachingReview, EditorialReview


class ScriptingTests(fixtures.ScriptProjectCase):
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
        self.assertEqual(selections, [("gpt-6-astra", "xhigh")] * 11)
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
                                     EpisodeScript, DialoguePolishReview, ScriptReview, ListenerReadback, EditorialReview, TeachingReview,
                                     SeriesReview])
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])
        self.assertTrue(all(s.attempts == 1 for s in resumed.stages.values()))
        text = (self.root / "episodes/ep_001/script.md").read_text(encoding="utf-8")
        # Behaviour change of 19 September 2026: the readable script names the speaking role,
        # or the configured host name, never the voice preset that will read it aloud.
        self.assertIn("**Host A:**", text)
        self.assertIn("**Host B:**", text)
        self.assertNotIn("**Aiden:**", text)
        self.assertNotIn("src_", text)
        knowledge = read_yaml(self.root / "models/knowledge_model.yaml")
        self.assertEqual(knowledge["research_run_id"], self.research.run_id)
        self.assertTrue(knowledge["claims"][0]["evidence"][0]["reference"].startswith("src_"))
        report = read_yaml(self.root / "reports/script_quality.yaml")
        self.assertEqual(report["episodes"]["ep_001"]["advisories"], [])
        self.assertFalse(report["audio_generated"])
        self.assertFalse(report["human_reviewed"])
        self.assertTrue(report["complete_series_review"])
        approval = read_yaml(self.root / "episodes/audio_review.yaml")
        self.assertFalse(approval["audio_approved"])
        self.assertEqual(approval["status"], "awaiting_user_script_review")
        self.assertEqual(approval["scripts"]["ep_001"], report["episodes"]["ep_001"]["script_sha256"])

    def test_the_script_review_sees_gap_probe_statuses_without_previews(self):
        seen = []
        def model(prompt, output_type, directory, **kwargs):
            if output_type is ScriptReview:
                seen.append(json.loads(prompt.splitlines()[-1]))
            return self.model(prompt, output_type, directory, **kwargs)
        seeded = {"gap_seed": "Which tokenizer curriculum shaped the vocabulary schedule?"}
        with patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value=seeded), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(seen[0]["gap_probes"], [{"gap_id": "gap_seed", "text": seeded["gap_seed"],
                                                  "status": "no_hits", "references": []}])
        self.assertNotIn("preview", json.dumps(seen[0]["gap_probes"]))

    def test_style_notes_change_the_input_hash_and_reach_every_text_payload(self):
        from podcast_automate.scripting import style_notes
        self.assertEqual(style_notes(self.root), "")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            first = run_script(self.root)
        self.assertEqual(first.status, "completed")
        before = json.loads((self.root / "runs" / first.run_id / "inputs.json").read_text(encoding="utf-8"))
        self.assertNotIn("style_notes", before)
        (self.root / "style_notes.md").write_text("Keine Wiederholungen am Kapitelende." + chr(10), encoding="utf-8")
        briefs = []
        def model(prompt, output_type, directory, **kwargs):
            payload = json.loads(prompt.splitlines()[-1])
            if "style_notes" in (payload.get("brief") or {}):
                briefs.append((kwargs["prompt_version"], payload["brief"]["style_notes"]))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            second = run_script(self.root)
        self.assertEqual(second.status, "completed")
        self.assertNotEqual(second.run_id, first.run_id)
        after = json.loads((self.root / "runs" / second.run_id / "inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(after["style_notes"], "Keine Wiederholungen am Kapitelende.")
        # Writing, the dialogue pass and both reviews see the operator's standing corrections.
        self.assertEqual(len(briefs), 4)
        self.assertTrue(all(note == "Keine Wiederholungen am Kapitelende." for _, note in briefs))

    def test_single_group_findings_reach_writing_and_the_script_review_without_unknown_groups(self):
        known = {"finding_id": "f_energy", "research_group": "Vendor Labs", "source_ids": ["src_a"],
                 "unknown_group_source_ids": []}
        unknown = {"finding_id": "f_energy", "research_group": None, "source_ids": ["src_b"],
                   "unknown_group_source_ids": ["src_b"]}
        seen = []
        def model(prompt, output_type, directory, **kwargs):
            payload = json.loads(prompt.splitlines()[-1])
            if "single_group_findings" in payload:
                seen.append((output_type, payload["single_group_findings"]))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.script_pipeline.single_group_findings", return_value=[known, unknown]), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        # The writer and the reviewer only see findings they can attribute to a named group; a
        # row whose group is unknown stays in the research quality report and never becomes an
        # attribution requirement on the script.
        self.assertEqual(seen, [(EpisodeScript, [known]), (ScriptReview, [known])])

    def test_a_missing_attribution_is_a_limitation_and_never_blocks(self):
        note = "Keine hörbare Zuschreibung für f_energy; die Folge nennt nicht, wessen Messung es ist."
        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ScriptReview:
                value.limitations = [note]
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        # Draft and dialogue pass only: a limitation never starts a repair.
        self.assertEqual(self.calls.count(EpisodeScript), 2)
        report = read_yaml(self.root / "reports/script_quality.yaml")
        self.assertEqual(report["episodes"]["ep_001"]["model_review"]["limitations"], [note])

    def test_a_long_cold_open_reaches_the_quality_report_as_an_advisory(self):
        opening = " ".join(["Wort"] * 101)
        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is SeriesPlan:
                # 112 words are 0.86 estimated minutes: within 85 to 120 percent of 0.8, so the
                # only advisory is the opening itself.
                value.episodes[0].target_minutes = 0.8
            if output_type is EpisodeScript:
                value.segments[0].text = opening
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        rows = read_yaml(self.root / "reports/script_quality.yaml")["episodes"]["ep_001"]["advisories"]
        self.assertEqual([(r["code"], r["episode_id"], r["count"], r["segment_ids"]) for r in rows],
                         [("long_cold_open", "ep_001", 101, ["seg_001"])])
        self.assertIn("101", rows[0]["detail"])

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
                                                       reason="The example is not worked through.")], limitations=[], claim_checks=script_checks(prompt)), {}
            return (example_plan() if output_type is SeriesPlan else example_script()), {}
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
            calls = len(self.calls)
            resumed = run_script(self.root, resume=True)
        self.assertEqual(run.stages["review"].error.code, "script_review_failed")
        self.assertEqual(resumed.status, "blocked")
        self.assertEqual(len(self.calls), calls)
        self.assertFalse((self.root / "episodes/ep_001/script.md").exists())

    def test_review_policy_fix_rechecks_latest_draft_without_resetting_used_repairs(self):
        def rejected(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is EpisodeScript and kwargs['prompt_version'] == 'script_review_repair.v1':
                value.segments[-1].text += ' This saved correction must remain.'
            if output_type is ScriptReview:
                value.issues = [ScriptIssue(category='depth', segment_ids=['seg_001'], reason='Missing series metadata.')]
            return value, meta
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=rejected):
            first = run_script(self.root)
        self.assertEqual(first.status, 'blocked')
        checkpoint = self.root / 'runs' / first.run_id / 'reviews/ep_001_checkpoint.json'
        saved = json.loads(checkpoint.read_text(encoding='utf-8'))
        self.assertEqual(saved['repairs'], 3)
        last_draft = saved['draft']
        saved.pop('editorial_review_version')  # A checkpoint made by the previous implementation.
        write_json(checkpoint, saved)
        captured = []
        def repaired_policy(prompt, output_type, directory, **kwargs):
            self.assertIsNot(output_type, EpisodeScript)
            if output_type is ScriptReview:
                captured.append(json.loads(prompt.splitlines()[-1])['script'])
            return self.model(prompt, output_type, directory, **kwargs)
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=repaired_policy):
            resumed = run_script(self.root, resume=True)
        self.assertEqual(resumed.status, 'completed')
        self.assertEqual(captured, [last_draft])
        self.assertEqual(json.loads(checkpoint.read_text(encoding='utf-8'))['repairs'], 3)
        self.assertEqual(read_yaml(self.root / 'episodes/ep_001/script.yaml'), last_draft)

    def test_script_review_version_drift_rechecks_latest_draft_without_resetting_used_repairs(self):
        from podcast_automate.script_checks import SCRIPT_REVIEW_VERSION
        def rejected(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is EpisodeScript and kwargs['prompt_version'] == 'script_review_repair.v1':
                value.segments[-1].text += ' This saved correction must remain.'
            if output_type is ScriptReview:
                value.issues = [ScriptIssue(category='depth', segment_ids=['seg_001'], reason='Missing series metadata.')]
            return value, meta
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=rejected):
            first = run_script(self.root)
        self.assertEqual(first.status, 'blocked')
        checkpoint = self.root / 'runs' / first.run_id / 'reviews/ep_001_checkpoint.json'
        saved = json.loads(checkpoint.read_text(encoding='utf-8'))
        self.assertEqual(saved['repairs'], 3)
        self.assertEqual(saved['script_review_version'], SCRIPT_REVIEW_VERSION)
        last_draft = saved['draft']
        saved['script_review_version'] = 'script_review.v8-evidence'  # The tag before this prompt batch.
        write_json(checkpoint, saved)
        captured = []
        def repaired_policy(prompt, output_type, directory, **kwargs):
            self.assertIsNot(output_type, EpisodeScript)
            if output_type is ScriptReview:
                captured.append(json.loads(prompt.splitlines()[-1])['script'])
            return self.model(prompt, output_type, directory, **kwargs)
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=repaired_policy):
            resumed = run_script(self.root, resume=True)
        self.assertEqual(resumed.status, 'completed')
        self.assertEqual(captured, [last_draft])
        after = json.loads(checkpoint.read_text(encoding='utf-8'))
        self.assertEqual((after['repairs'], after['script_review_version']), (3, SCRIPT_REVIEW_VERSION))
        self.assertEqual(read_yaml(self.root / 'episodes/ep_001/script.yaml'), last_draft)

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
        self.assertEqual(len(self.calls), 11)
        self.assertIn("**Host A:**", (self.root / "episodes/ep_001/script.md").read_text(encoding="utf-8"))

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
        self.assertEqual(len(self.calls), 11)

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
        self.assertEqual(len(self.calls), 11)

    def test_plan_rejects_cycles_and_forward_prerequisites(self):
        dossier = ResearchDossier.model_validate(read_yaml(self.root / "research/dossier.yaml"))
        plan = example_plan()
        plan.dependencies = [Dependency(before="f_energy", after="f_energy", reason="Invalid self dependency")]
        self.assertTrue(any("cycle" in item for item in validate_plan(plan, dossier)))
        plan = example_plan()
        plan.episodes[0].prerequisite_episodes = ["ep_002"]
        self.assertTrue(any("earlier" in item for item in validate_plan(plan, dossier)))

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
        self.assertEqual(len(self.calls), 10)


class ScriptValidationTests(unittest.TestCase):
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
