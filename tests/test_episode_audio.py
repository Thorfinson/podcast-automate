import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.episode_audio import check_rows, partition_audio, run_episode_audio, segment_durations
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript, TopicBrief
from podcast_automate.qwen_worker import spoken_settings
from podcast_automate.runner import manifest_path, outputs_valid, status
from podcast_automate.script_models import ScenePlan, SeriesPlan
from podcast_automate.scripting import run_script
from podcast_automate.speech import AudioChoice, PausePolicy
from podcast_automate.storage import digest, file_hash, project_hash, read_yaml, write_json, write_yaml
from tests import script_fixtures as fixtures
from tests.test_audio import tone


class EpisodeAudioTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        self.calls, self.spoken = [], []
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            self.script_run = run_script(self.root, episode="ep_001")

    def synthesize(self, config, script, root, work, *, table=None, overrides=None):
        from podcast_automate.spoken_forms import SpokenForms, spoken_text
        self.calls.append(script.chapters[0].chapter_id)
        table = table if table is not None else SpokenForms()
        paths, rows = [], []
        for segment in script.segments:
            spoken = spoken_text(segment, table, overrides)
            self.spoken.append(spoken)
            path = root / "cache/audio" / f"{segment.segment_id}.wav"
            tone(path)
            paths.append(path)
            rows.append({"segment_id": segment.segment_id, "path": str(path), "sha256": file_hash(path),
                "settings": {"voice": config.voice_profile[segment.speaker_id], "text": segment.text,
                    **spoken_settings(segment.text, spoken),
                    "language": "German", "revision": config.runtime.tts_revision, "seed": config.runtime.seed,
                    "attention": config.runtime.tts_attention, "device": config.runtime.tts_device,
                    "model": config.runtime.tts_model}})
        write_json(work / "tts_report.json", {"segments": rows})
        return paths

    def legacy_rows(self, script):
        """Rows exactly as the worker wrote them before spoken forms existed: no spoken_text."""
        config, rows = self.fixture.config, []
        for segment in script.segments:
            path = self.root / "cache/audio" / f"{segment.segment_id}.wav"
            tone(path)
            rows.append({"segment_id": segment.segment_id, "path": str(path), "sha256": file_hash(path),
                "settings": {"voice": config.voice_profile[segment.speaker_id], "text": segment.text,
                    "language": "German", "revision": config.runtime.tts_revision, "seed": config.runtime.seed,
                    "attention": config.runtime.tts_attention, "device": config.runtime.tts_device,
                    "model": config.runtime.tts_model}})
        return {"segments": rows}

    def test_a_row_without_spoken_text_verifies_while_nothing_is_spoken_differently(self):
        # An audio run started before spoken forms existed resumes past its finished synthesis:
        # its rows carry no spoken_text, and none is expected while no form or override applies.
        from podcast_automate.spoken_forms import SpokenForms
        script = fixtures.example_script()
        report = self.legacy_rows(script)
        self.assertEqual(len(check_rows(self.root, script, report, self.fixture.config)), len(script.segments))
        table = SpokenForms(entries=[{"written": "model", "spoken": "Modell"}])
        with self.assertRaises(AppError) as caught:
            check_rows(self.root, script, report, self.fixture.config, table=table)
        self.assertEqual(caught.exception.code, "invalid_audio")
        # A row that carries a spoken form no table produces today is rejected as well.
        report["segments"][0]["settings"]["spoken_text"] = "Anders gesprochen."
        with self.assertRaises(AppError):
            check_rows(self.root, script, report, self.fixture.config)

    def test_requires_approval_of_unchanged_script_and_readable_view(self):
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("must not render")):
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, episode="ep_001")
            self.assertEqual(caught.exception.code, "audio_approval_required")
            path = self.root / "episodes/ep_001/script.md"
            path.write_text("A user changed the readable script", encoding="utf-8")
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, episode="ep_001", approve_audio=True)
            self.assertEqual(caught.exception.code, "script_edited")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_cli_render_and_resume_keep_approval_text_and_voices(self):
        before = file_hash(self.root / "episodes/ep_001/script.yaml")
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["audio", str(self.root), "--episode", "ep_001", "--approve-audio",
                                   "--approval-note", "Continue despite editorial reservations", "--json"]), 0)
            self.assertEqual(main(["resume", str(self.root), "--json"]), 0)
            path = manifest_path(self.root)
            saved = read_yaml(path)
            saved["audio_approved"] = False
            write_yaml(path, saved)
            receipt_hash = file_hash(path.parent / "approval.json")
            self.assertEqual(main(["resume", str(self.root), "--approve-audio",
                                   "--approval-note", "ok start now", "--json"]), 0)
            self.assertEqual(file_hash(path.parent / "approval.json"), receipt_hash)
            receipt = next((path.parent / "resume_approvals").glob("*.json"))
            self.assertEqual(json.loads(receipt.read_text())["authorization"], "ok start now")
        self.assertEqual(len(self.calls), 1)
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text())
        self.assertEqual(before, report["script_sha256"])
        self.assertFalse(report["human_listening_reviewed"])
        transcript = (self.root / report["parts"][0]["audio"]).with_name("transcript.md").read_text(encoding="utf-8")
        # Behaviour change of 19 September 2026: a transcript names the speaking role or the
        # configured host name, never the voice preset. episode_framing.txt already said a
        # voice preset is not a host identity; the transcript now says the same.
        self.assertIn("**Host A:**", transcript)
        self.assertIn("**Host B:**", transcript)
        self.assertNotIn("**Aiden:**", transcript)
        self.assertNotIn("Technische Hörprobe", transcript)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("no rewrite")):
            resumed = run_script(self.root, resume=True, run_id=self.script_run.run_id)
        self.assertEqual(resumed.status, "completed")
        self.assertTrue(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_resume_reuses_finished_chapters_after_interruption(self):
        def two_chapters(prompt, output_type, directory, **kwargs):
            result, meta = self.fixture.model(prompt, output_type, directory, **kwargs)
            if output_type is SeriesPlan:
                result.episodes[0].scenes.append(ScenePlan(scene_id="scene_two", title="Second",
                    question="What follows?", purpose="synthesis", finding_ids=["f_energy"],
                    explanation_steps=["Apply the comparison."]))
            if output_type is EpisodeScript:
                result.chapters.append(Chapter(chapter_id="scene_two", title="Second"))
                result.segments[-1].scene_id = result.segments[-1].chapter_id = "scene_two"
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=two_chapters):
            run_script(self.root, episode="ep_001")

        def interrupted(config, script, root, work, **kwargs):
            if script.chapters[0].chapter_id == "scene_two":
                raise AppError("Interrupted", code="test_interrupt")
            return self.synthesize(config, script, root, work)
        with patch("podcast_automate.episode_audio.run_tts", side_effect=interrupted):
            first = run_episode_audio(self.root, episode="ep_001", approve_audio=True)
        self.assertEqual(first.status, "failed")
        first.audio_approved = False
        write_yaml(manifest_path(self.root, first.run_id), first.model_dump(mode="json"))
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("must stay paused")):
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, resume=True, run_id=first.run_id)
            self.assertEqual(caught.exception.code, "audio_approval_required")
            with patch("podcast_automate.episode_audio.digest", return_value="changed"):
                with self.assertRaises(AppError) as caught:
                    run_episode_audio(self.root, resume=True, run_id=first.run_id, approve_audio=True)
            self.assertEqual(caught.exception.code, "inputs_changed")
            self.assertFalse(read_yaml(manifest_path(self.root, first.run_id))["audio_approved"])
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize):
            second = run_episode_audio(self.root, resume=True, run_id=first.run_id, approve_audio=True)
        self.assertEqual(second.status, "completed")
        self.assertEqual(self.calls, ["scene_example", "scene_two"])
        self.assertTrue(all(outputs_valid(self.root, s) for s in second.stages.values()))

    def render(self, **kwargs):
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize):
            return run_episode_audio(self.root, episode="ep_001", approve_audio=True, **kwargs)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_the_table_reaches_the_engine_while_the_transcript_keeps_the_script(self):
        write_json(self.root / "studio/spoken_forms.json", {"schema_version": "1.0",
            "entries": [{"written": "model", "spoken": "Modell"}]})
        run = self.render()
        self.assertEqual(run.status, "completed")
        self.assertTrue(any("Modell" in spoken for spoken in self.spoken))
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["pronunciation"]["applied"], {"model": 1})
        transcript = (self.root / report["parts"][0]["audio"]).with_name("transcript.md").read_text(encoding="utf-8")
        self.assertIn("What does this model compare?", transcript)
        self.assertNotIn("Modell", transcript)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_one_override_costs_one_synthesis_and_reuses_the_saved_approval(self):
        self.assertEqual(self.render().status, "completed")
        decision = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml",
                   {**decision, "spoken_overrides": {"seg_002": "Es bewertet Möglichkeiten."}})
        self.spoken.clear()
        # No fresh approval: the script hash and the voices are unchanged.
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize):
            second = run_episode_audio(self.root, episode="ep_001")
        self.assertEqual(second.status, "completed")
        self.assertIn("Es bewertet Möglichkeiten.", self.spoken)
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["spoken_overrides"], {"seg_002": "Es bewertet Möglichkeiten."})
        destination = (self.root / report["parts"][0]["audio"]).parent
        show_notes = (destination / "show_notes.md").read_text(encoding="utf-8")
        self.assertIn("seg_002", show_notes)
        self.assertIn("Aussprache-Hinweise", show_notes)
        # Show notes name the hosts by role or configured name; the voice presets are listed apart.
        self.assertIn("Hörfassung mit Host A und Host B.", show_notes)
        self.assertIn("Stimmen: Aiden (Host A) und Vivian (Host B).", show_notes)
        self.assertNotIn("Hörfassung mit Aiden", show_notes)
        self.assertIn("Stimmen: Aiden (Host A) und Vivian (Host B).", (destination / "README.md").read_text(encoding="utf-8"))
        self.assertIn("Unklar", (destination / "listening_sheet.md").read_text(encoding="utf-8"))
        # The audio decision keeps the operator's overrides across the run that used them.
        self.assertEqual(read_yaml(self.root / "episodes/ep_001/audio_review.yaml")["spoken_overrides"],
                         {"seg_002": "Es bewertet Möglichkeiten."})

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_changed_spoken_form_is_rejected_by_the_verifier(self):
        self.assertEqual(self.render().status, "completed")
        work = manifest_path(self.root, self.root and json.loads(
            (self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))["run_id"]).parent
        report = json.loads((work / "tts_report.json").read_text(encoding="utf-8"))
        report["segments"][0]["settings"]["spoken_text"] = "Ein anderer Satz."
        write_json(work / "tts_report.json", report)
        from podcast_automate.episode_audio import check_rows
        from podcast_automate.errors import AppError as Error
        with self.assertRaises(Error) as caught:
            check_rows(self.root, EpisodeScript.model_validate(
                json.loads((work / "approved_script.json").read_text(encoding="utf-8"))),
                report, self.fixture.config)
        self.assertEqual(caught.exception.code, "invalid_audio")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_legacy_approval_without_a_pause_policy_still_counts(self):
        from podcast_automate.speech import AudioChoice
        chosen = AudioChoice(voices=self.fixture.config.voice_profile).model_dump()
        self.assertEqual(self.render(audio_choice=chosen).status, "completed")
        decision = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")
        legacy = {k: v for k, v in decision["audio_generation"].items() if k != "pauses"}
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml", {**decision, "audio_generation": legacy})
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize):
            self.assertEqual(run_episode_audio(self.root, episode="ep_001",
                                               audio_choice=chosen).status, "completed")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_default_pause_policy_is_stored_and_hashed_as_before_the_policy_existed(self):
        chosen = AudioChoice(voices=self.fixture.config.voice_profile)
        run = self.render(audio_choice=chosen.model_dump())
        self.assertEqual(run.status, "completed")
        inputs = json.loads((manifest_path(self.root, run.run_id).parent / "inputs.json").read_text(encoding="utf-8"))
        # The hashed record and the stored decision have the shape they had before pauses existed.
        self.assertEqual(inputs["audio_generation"], {"provider": "qwen3_local", "voices": chosen.voices})
        self.assertNotIn("pauses", read_yaml(self.root / "episodes/ep_001/audio_review.yaml")["audio_generation"])
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("cached synthesis")):
            self.assertEqual(run_episode_audio(self.root, resume=True, run_id=run.run_id).status, "completed")
        slower = chosen.model_copy(update={"pauses": PausePolicy(chapter_break_ms=1800)})
        self.assertEqual(self.render(audio_choice=slower.model_dump()).status, "completed")
        stored = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")["audio_generation"]
        self.assertEqual(stored["pauses"]["chapter_break_ms"], 1800)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_the_pipeline_sizes_parts_with_the_pause_policy_it_will_apply(self):
        from podcast_automate import episode_audio as module
        seen, original = [], module.partition_audio

        def recording(script, durations, max_seconds):
            seen.append(list(durations))
            return original(script, durations, max_seconds)
        choice = AudioChoice(voices=self.fixture.config.voice_profile,
                             pauses={"same_speaker_ms": 5000, "speaker_change_ms": 5000, "chapter_break_ms": 5000})
        with patch("podcast_automate.episode_audio.partition_audio", side_effect=recording):
            self.assertEqual(self.render(audio_choice=choice.model_dump()).status, "completed")
        script = EpisodeScript.model_validate(read_yaml(self.root / "episodes/ep_001/script.yaml"))
        # One-second tones: the first segment counts the 5 s minimum of its transition, the last
        # one keeps its planned pause because nothing follows it.
        self.assertEqual([round(d, 3) for d in seen[0]], [6.0, 1 + script.segments[-1].pause_after_ms / 1000])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_changed_pause_policy_needs_a_fresh_approval(self):
        self.assertEqual(self.render().status, "completed")
        from podcast_automate.speech import AudioChoice
        louder = AudioChoice(voices=self.fixture.config.voice_profile,
                             pauses={"same_speaker_ms": 900, "speaker_change_ms": 900, "chapter_break_ms": 1800})
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("must not render")):
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, episode="ep_001", audio_choice=louder.model_dump())
        self.assertEqual(caught.exception.code, "audio_approval_required")

    def test_wrong_voice_is_rejected_before_assembly(self):
        def wrong_voice(config, script, root, work, **kwargs):
            paths = self.synthesize(config, script, root, work, **kwargs)
            report = json.loads((work / "tts_report.json").read_text())
            report["segments"][0]["settings"]["voice"] = "wrong"
            write_json(work / "tts_report.json", report)
            return paths
        with patch("podcast_automate.episode_audio.run_tts", side_effect=wrong_voice), \
             patch("podcast_automate.episode_audio.assemble", side_effect=AssertionError("must not assemble")):
            result = run_episode_audio(self.root, episode="ep_001", approve_audio=True)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.stages["synthesis"].error.code, "invalid_audio")


class PartitionTests(unittest.TestCase):
    def test_balanced_chapter_parts_preserve_all_segments_in_order(self):
        original = fixtures.example_script()
        segments, chapters = [], []
        for i in range(4):
            chapter = f"chapter_{i}"
            chapters.append(Chapter(chapter_id=chapter, title=chapter))
            segments.append(original.segments[i % 2].model_copy(update={"segment_id": f"segment_{i}",
                "scene_id": chapter, "chapter_id": chapter}))
        script = original.model_copy(update={"chapters": chapters, "segments": segments})
        self.assertEqual(partition_audio(script, [720, 480, 540, 420], 1800), [[0, 1], [2, 3]])
        self.assertEqual(partition_audio(original, [1020, 1020], 1800), [[0], [1]])
        with self.assertRaises(AppError):
            partition_audio(original, [1801, 1], 1800)

    def test_parts_are_sized_with_the_pause_assembly_will_apply(self):
        script = fixtures.example_script().model_copy(deep=True)
        for segment in script.segments:
            segment.pause_after_ms = 0
        with tempfile.TemporaryDirectory() as temporary:
            paths = [Path(temporary) / f"{s.segment_id}.wav" for s in script.segments]
            for path in paths:
                tone(path)  # one second each
            planned = segment_durations(script, paths, None)
            applied = segment_durations(script, paths, PausePolicy(speaker_change_ms=10000))
        self.assertEqual(planned, [1.0, 1.0])
        # The speaker change raises the first pause to the minimum; the episode end keeps 0.
        self.assertEqual(applied, [11.0, 1.0])
        # With the planned pauses alone the episode fits one part; with the pauses assembly will
        # apply it must be split here, before the montage, instead of failing after synthesis.
        self.assertEqual(partition_audio(script, planned, 12), [[0, 1]])
        self.assertEqual(partition_audio(script, applied, 12), [[0], [1]])


class ProjectHashTests(unittest.TestCase):
    def test_a_brief_without_host_names_hashes_as_it_did_before_the_field_existed(self):
        config = TopicBrief(topic="Hash stability")
        dump = config.model_dump(mode="json")
        self.assertIsNone(dump["host_names"])  # the field serialises as null in project.yaml
        self.assertEqual(project_hash(config), digest({k: v for k, v in dump.items() if k != "host_names"}))
        self.assertNotEqual(project_hash(config), digest(dump))
        named = config.model_copy(update={"host_names": {"host_a": "Mara", "host_b": "Jonas"}})
        self.assertNotEqual(project_hash(named), project_hash(config))
        self.assertEqual(project_hash(named), digest(named.model_dump(mode="json")))

    def test_a_recorded_run_still_matches_its_unchanged_project(self):
        from podcast_automate.models import RunManifest, StageRecord
        from podcast_automate.storage import init_project
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            config = TopicBrief(topic="Recorded before host names existed")
            init_project(root, config)
            recorded = digest({k: v for k, v in config.model_dump(mode="json").items() if k != "host_names"})
            manifest = RunManifest(run_id="run_old", kind="audio_probe", project_hash=recorded, input_hash="x",
                                   stages={"synthesis": StageRecord()})
            write_yaml(manifest_path(root, "run_old"), manifest.model_dump(mode="json"))
            self.assertFalse(status(root, "run_old")["project_changed"])


if __name__ == "__main__":
    unittest.main()
