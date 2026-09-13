import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.speech import GeminiSpeech
from podcast_automate.voice_samples import (
    SAMPLE_TEXTS, generate_sample, generate_samples, ready_sample, sample_inventory,
)
from tests.test_speech import response


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
class VoiceLibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.projects = Path(temporary.name) / "projects"
        self.root = self.projects / "first"
        self.root.mkdir(parents=True)

    def test_reuse_across_projects_and_restart_needs_no_key_or_encoding(self):
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response()
            first = generate_sample(self.root, "Aoede", "de-DE", "test-key")
            build.assert_called_once()
        saved = ready_sample(self.projects, "Aoede", "de-DE")
        before = saved.stat().st_mtime_ns
        with patch("podcast_automate.speech.build_opener") as build, patch("podcast_automate.voice_samples.ffmpeg") as encode:
            generate_sample(self.projects / "second", "Aoede", "de-DE")
            generate_sample(self.root, "Aoede", "de-DE")
            build.assert_not_called()
            encode.assert_not_called()
        self.assertEqual(saved.stat().st_mtime_ns, before)
        self.assertEqual(saved.read_bytes(), (self.root / first["audio"]).read_bytes())
        self.assertIn("Aoede", sample_inventory(self.projects)["de-DE"])
        self.assertNotIn("Aoede", sample_inventory(self.projects)["en-US"])

    def test_adopts_old_project_speech_without_a_paid_call(self):
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response()
            GeminiSpeech("test-key").synthesize(SAMPLE_TEXTS["de-DE"], "Sadaltager", "de-DE", self.root / "cache/audio/gemini")
        with patch("podcast_automate.speech.build_opener") as build:
            generate_sample(self.projects / "second", "Sadaltager", "de-DE")
            build.assert_not_called()
        self.assertIsNotNone(ready_sample(self.projects, "Sadaltager", "de-DE"))

    def test_corrupt_mp3_rebuilt_from_saved_wav_and_model_change_is_not_reused(self):
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response()
            generate_sample(self.root, "Aoede", "de-DE", "test-key")
        saved = ready_sample(self.projects, "Aoede", "de-DE")
        saved.write_bytes(b"damaged")
        self.assertNotIn("Aoede", sample_inventory(self.projects)["de-DE"])
        with patch("podcast_automate.speech.build_opener") as build:
            generate_sample(self.root, "Aoede", "de-DE")
            build.assert_not_called()
        self.assertIsNotNone(ready_sample(self.projects, "Aoede", "de-DE"))
        with patch("podcast_automate.speech.SPEECH_VERSION", "new-adapter"):
            self.assertIsNone(ready_sample(self.projects, "Aoede", "de-DE"))

    def test_batch_failure_preserves_finished_voices_and_resume_skips_them(self):
        requested, progress = [], []
        def call(request, **kwargs):
            requested.append(json.loads(request.data)["voice"])
            if len(requested) == 2:
                raise AppError("Quota reached", code="quota", status="waiting_for_quota")
            return response()
        with patch("podcast_automate.voice_samples.GEMINI_VOICES", ("Zephyr", "Puck", "Aoede")), patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = call
            with self.assertRaises(AppError):
                generate_samples(self.root, "de-DE", "test-key", progress.append)
            self.assertEqual(requested, ["Zephyr", "Puck"])
            result = generate_samples(self.root, "de-DE", "test-key", progress.append)
            self.assertEqual(requested, ["Zephyr", "Puck", "Puck", "Aoede"])
            self.assertEqual(result["completed"], 3)
            build.reset_mock()
            generate_samples(self.projects / "second", "de-DE")
            build.assert_not_called()
        self.assertEqual(progress[-1]["completed_segments"], 3)


if __name__ == "__main__":
    unittest.main()
