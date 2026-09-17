import contextlib
import io
import json
import shutil
import unittest
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.episode_audio import partition_audio, run_episode_audio
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript
from podcast_automate.runner import manifest_path, outputs_valid
from podcast_automate.script_models import ScenePlan, SeriesPlan
from podcast_automate.scripting import run_script
from podcast_automate.storage import file_hash, read_yaml, write_json, write_yaml
from tests import script_fixtures as fixtures
from tests.test_audio import tone


class EpisodeAudioTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        self.calls = []
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            self.script_run = run_script(self.root, episode="ep_001")

    def synthesize(self, config, script, root, work):
        self.calls.append(script.chapters[0].chapter_id)
        paths, rows = [], []
        for segment in script.segments:
            path = root / "cache/audio" / f"{segment.segment_id}.wav"
            tone(path)
            paths.append(path)
            rows.append({"segment_id": segment.segment_id, "path": str(path), "sha256": file_hash(path),
                "settings": {"voice": config.voice_profile[segment.speaker_id], "text": segment.text,
                    "language": "German", "revision": config.runtime.tts_revision, "seed": config.runtime.seed,
                    "attention": config.runtime.tts_attention, "device": config.runtime.tts_device,
                    "model": config.runtime.tts_model}})
        write_json(work / "tts_report.json", {"segments": rows})
        return paths

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
        self.assertIn("**Aiden:**", transcript)
        self.assertIn("**Vivian:**", transcript)
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

        def interrupted(config, script, root, work):
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

    def test_wrong_voice_is_rejected_before_assembly(self):
        def wrong_voice(config, script, root, work):
            paths = self.synthesize(config, script, root, work)
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


if __name__ == "__main__":
    unittest.main()
