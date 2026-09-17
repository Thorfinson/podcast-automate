import io
import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from pydantic import ValidationError

from podcast_automate.episode_audio import run_episode_audio
from podcast_automate.errors import AppError
from podcast_automate.scripting import run_script
from podcast_automate.speech import (AudioChoice, GEMINI_MODEL, GEMINI_VOICES, GeminiSpeech,
                                    SPEECH_ENDPOINT, audio_catalog, split_input)
from podcast_automate.storage import file_hash, write_json
from podcast_automate.studio_worker import perform
from tests import script_fixtures as fixtures
from tests.test_audio import tone


PCM = b"\x10\x00\xf0\xff" * 24000


def response(pcm=PCM, content_type="audio/pcm", generation="gen-test"):
    result = io.BytesIO(pcm)
    result.status = 200
    result.headers = {"Content-Type":content_type, "X-Generation-Id":generation}
    return result


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)
        self.engine = GeminiSpeech("test-key")

    def test_uses_binary_speech_endpoint_and_caches_voice_specific_wav_without_key(self):
        opener = Mock()
        opener.open.side_effect = lambda *a, **k: response()
        with patch("podcast_automate.speech.build_opener", return_value=opener):
            first = self.engine.synthesize("Ein konkretes Beispiel.", "Sadaltager", "de-DE", self.cache)
            again = GeminiSpeech().synthesize("Ein konkretes Beispiel.", "Sadaltager", "de-DE", self.cache)
            other = self.engine.synthesize("Ein konkretes Beispiel.", "Aoede", "de-DE", self.cache)
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertEqual(opener.open.call_count, 2)
        request = opener.open.call_args_list[0].args[0]
        self.assertEqual(request.full_url, SPEECH_ENDPOINT)
        self.assertEqual(json.loads(request.data), {"model":GEMINI_MODEL, "input":"Ein konkretes Beispiel.",
                                                  "voice":"Sadaltager", "response_format":"pcm"})
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        with wave.open(str(first), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (24000,1,2))
            self.assertEqual(wav.readframes(wav.getnframes()), PCM)
        for file in self.cache.rglob("*"):
            if file.is_file():
                self.assertNotIn(b"test-key", file.read_bytes())

    def test_bad_responses_and_interrupted_requests_never_enter_cache(self):
        for reply in [response(b""), response(b"x"), response(b"\0\0"), response(b"{}", "application/json"),
                      response(b'{"error":true} '), response(PCM, "audio/pcm;rate=16000")]:
            with self.subTest(reply=reply), patch("podcast_automate.speech.build_opener") as build:
                build.return_value.open.return_value = reply
                with self.assertRaises(AppError):
                    self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
                self.assertEqual(list(self.cache.glob("*.wav")), [])
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = URLError("contains test-key")
            with self.assertRaises(AppError) as raised:
                self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
        self.assertNotIn("test-key", str(raised.exception))

    def test_http_failures_do_not_retry_or_echo_provider_messages(self):
        for code in (302, 400, 401, 402, 429, 503):
            with self.subTest(code=code), patch("podcast_automate.speech.build_opener") as build:
                build.return_value.open.side_effect = HTTPError(SPEECH_ENDPOINT, code, "test-key", {}, io.BytesIO(b"test-key"))
                with self.assertRaises(AppError) as raised:
                    self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
                self.assertEqual(build.return_value.open.call_count, 1)
                self.assertNotIn("test-key", str(raised.exception))
        self.assertEqual(list(self.cache.glob("*.wav")), [])

    def test_long_turn_is_split_only_for_request_size_without_losing_characters(self):
        text = ("Ein Gedanke führt zum nächsten. " * 230) + "Das ist der Schluss."
        chunks = split_input(text)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(part) <= 6000 for part in chunks))
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response()
            result = self.engine.synthesize(text, "Sadaltager", "de-DE", self.cache)
            self.assertEqual(build.return_value.open.call_count, len(chunks))
        with wave.open(str(result), "rb") as wav:
            self.assertEqual(wav.getnframes(), len(PCM)//2 * len(chunks))

    def test_key_in_input_and_missing_key_block_before_network(self):
        with patch("podcast_automate.speech.build_opener") as build:
            for engine, text in ((self.engine, "test-key is secret"), (GeminiSpeech(""), "Hallo")):
                with self.assertRaises(AppError):
                    engine.synthesize(text, "Aoede", "de-DE", self.cache)
            build.assert_not_called()

    def test_corrupt_cache_is_regenerated(self):
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response()
            path = self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
            path.write_bytes(b"corrupted")
            self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
            self.assertEqual(build.return_value.open.call_count, 2)

    def test_incomplete_declared_length_is_rejected_but_pcm_brace_sample_is_valid(self):
        incomplete = response()
        incomplete.headers["Content-Length"] = str(len(PCM) + 2)
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.return_value = incomplete
            with self.assertRaises(AppError):
                self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
            build.return_value.open.return_value = response(b"{\0" + PCM)
            path = self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
            self.assertTrue(path.is_file())

    def test_catalog_validates_provider_voices_and_has_all_thirty_gemini_choices(self):
        self.assertEqual(len(set(GEMINI_VOICES)), 30)
        self.assertEqual(audio_catalog()["openrouter_gemini_tts"]["defaults"], {"host_a":"Sadaltager","host_b":"Aoede"})
        for voices in ({"host_a":"Aiden","host_b":"Vivian"}, {"host_a":"Aoede","host_b":"Aoede"}):
            with self.assertRaises(ValidationError):
                AudioChoice(provider="openrouter_gemini_tts", voices=voices)


class GeminiEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            run_script(self.root)
        self.choice = {"provider":"openrouter_gemini_tts", "voices":{"host_a":"Sadaltager","host_b":"Aoede"}}
        # A real tone permits the existing FFmpeg silence/loudness checks to run.
        wav = self.root / "fixture.wav"
        tone(wav)
        with wave.open(str(wav), "rb") as stream:
            self.pcm = stream.readframes(stream.getnframes())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_existing_script_gets_gemini_voices_and_resume_needs_no_gpu_or_rewrite(self):
        before = file_hash(self.root / "episodes/ep_001/script.yaml")
        with patch("podcast_automate.speech.build_opener") as build, \
             patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("Qwen must not run")), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("No rewrite")):
            build.return_value.open.side_effect = lambda *a, **k: response(self.pcm)
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True,
                                    audio_choice=self.choice, api_key="test-key")
            self.assertEqual(run.status, "completed")
            calls = build.return_value.open.call_count
            write_json(self.root / "studio/audio.json", {"provider":"qwen3_local", "voices":{"host_a":"Aiden","host_b":"Vivian"}})
            resumed = run_episode_audio(self.root, resume=True, run_id=run.run_id)
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(build.return_value.open.call_count, calls)
        self.assertEqual(before, file_hash(self.root / "episodes/ep_001/script.yaml"))
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text())
        self.assertEqual(report["audio_generation"], self.choice)
        transcript = (self.root / report["parts"][0]["audio"]).with_name("transcript.md").read_text(encoding="utf-8")
        self.assertIn("**Sadaltager:**", transcript)
        self.assertIn("**Aoede:**", transcript)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_explicit_local_choice_still_uses_qwen_and_keeps_the_script(self):
        from tests.test_episode_audio import EpisodeAudioTests
        local_fixture = EpisodeAudioTests()
        local_fixture.calls = []
        before = file_hash(self.root / "episodes/ep_001/script.yaml")
        local = {"provider":"qwen3_local", "voices":{"host_a":"Ryan","host_b":"Serena"}}
        with patch("podcast_automate.episode_audio.run_tts", side_effect=local_fixture.synthesize), \
             patch("podcast_automate.episode_audio.run_gemini_tts", side_effect=AssertionError("No cloud call for Qwen")):
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True, audio_choice=local)
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(local_fixture.calls), 1)
        self.assertEqual(before, file_hash(self.root / "episodes/ep_001/script.yaml"))
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text())
        self.assertEqual(report["audio_generation"], local)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_quota_pause_reuses_finished_segment_and_rejects_changed_voice_on_resume(self):
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = [response(self.pcm), HTTPError(SPEECH_ENDPOINT,429,"quota",{},io.BytesIO())]
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True, audio_choice=self.choice, api_key="test-key")
            self.assertEqual(run.status, "waiting_for_quota")
            different = {**self.choice, "voices":{"host_a":"Charon","host_b":"Aoede"}}
            with self.assertRaises(AppError):
                run_episode_audio(self.root, resume=True, run_id=run.run_id, audio_choice=different)
            build.return_value.open.reset_mock(side_effect=True)
            build.return_value.open.side_effect = lambda *a, **k: response(self.pcm)
            completed = run_episode_audio(self.root, resume=True, run_id=run.run_id, api_key="rotated-key")
            self.assertEqual(completed.status, "completed")
            self.assertEqual(build.return_value.open.call_count, 1)

    def test_saved_qwen_approval_does_not_authorize_paid_gemini_audio(self):
        from podcast_automate.storage import write_yaml
        write_yaml(self.root / "episodes/audio_review.yaml", {"audio_approved":True,
            "scripts":{"ep_001":file_hash(self.root / "episodes/ep_001/script.yaml")}})
        with patch("podcast_automate.speech.build_opener") as build:
            with self.assertRaises(AppError) as denied:
                run_episode_audio(self.root, episode="ep_001", audio_choice=self.choice)
            self.assertEqual(denied.exception.code, "audio_approval_required")
            build.assert_not_called()

    def test_gemini_connection_check_does_not_require_local_qwen(self):
        write_json(self.root / "studio/audio.json", self.choice)
        with patch("podcast_automate.studio_worker.inspect", return_value={"ready":True,"checks":[]}) as check:
            result = perform(self.root, {"action":"check", "text":{}, "api_key":"test-key"})
        self.assertFalse(check.call_args.kwargs["include_tts"])
        self.assertTrue(result["checks"]["ready"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
    def test_voice_sample_uses_selected_language_and_reuses_cached_speech(self):
        request = {"action":"audio_sample", "text":{}, "voice":"Aoede", "language":"de-DE", "api_key":"test-key"}
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response(self.pcm)
            first = perform(self.root, request)
            again = perform(self.root, request)
            self.assertEqual(build.return_value.open.call_count, 1)
            payload = json.loads(build.return_value.open.call_args.args[0].data)
        self.assertIn("Eine gute Erklärung", payload["input"])
        self.assertEqual(first, again)
        self.assertTrue((self.root / first["sample"]["audio"]).is_file())
