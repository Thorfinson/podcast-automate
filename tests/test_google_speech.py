import base64
import io
import json
import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from podcast_automate.episode_audio import run_episode_audio
from podcast_automate.errors import AppError
from podcast_automate.google_speech import (GOOGLE_SPEECH_ENDPOINT, MAX_PASSAGE_CHARACTERS, MAX_PASSAGE_TURNS,
                                            RATE_LIMIT_RETRIES, GoogleSpeech, check_google_rows, plan_passages,
                                            request_body)
from podcast_automate.models import EpisodeScript, Segment
from podcast_automate.scripting import run_script
from podcast_automate.speech import DEFAULT_STYLES, AudioChoice
from podcast_automate.storage import read_yaml
from podcast_automate.voice_samples import generate_pair, pair_view
from tests import script_fixtures as fixtures


def segments(*rows):
    """Segments from (chapter, scene, speaker, characters) rows."""
    return [Segment(segment_id=f"s{number:03d}", chapter_id=chapter, scene_id=scene, speaker_id=speaker,
                    text=("Wort " * (length // 5)).strip() + ".") for number, (chapter, scene, speaker, length)
            in enumerate(rows, 1)]


def pcm(seconds):
    """24 kHz mono 16-bit sine, loud enough for the speech health gate and the montage."""
    frames = round(seconds * 24000)
    return struct.pack(f"<{frames}h", *(int(4000 * math.sin(2 * math.pi * 230 * i / 24000)) for i in range(frames)))


def wav_bytes(data, rate=24000, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(data)
    return buffer.getvalue()


def answer(audio):
    body = io.BytesIO(json.dumps({"steps": [{"type": "thought"}, {"type": "model_output", "content": [
        {"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(audio).decode()}]}]}).encode())
    body.status = 200
    return body


def spoken_answer(request, *args, **kwargs):
    """A take as long as the request's turns take to say at 16 characters per second, the measured median."""
    text = " ".join(item["text"] for item in json.loads(request.data)["input"][0]["content"])
    return answer(wav_bytes(pcm(max(1.0, len(text) / 16))))


def refusal(code, body):
    return HTTPError(GOOGLE_SPEECH_ENDPOINT, code, "refused", {}, io.BytesIO(json.dumps(body).encode()))


class PassageTests(unittest.TestCase):
    def test_passages_never_cross_a_chapter_or_scene_and_keep_their_bounds(self):
        rows = [("c1", "c1", "host_a" if n % 2 else "host_b", 400) for n in range(30)] + \
               [("c2", "c2", "host_a", 300), ("c2", "c2", "host_b", 200)] + \
               [("c3", "c3", "host_a", 9000), ("c3", "c3", "host_b", 100)]
        script = segments(*rows)
        passages = plan_passages(script)
        self.assertEqual([i for p in passages for i in p], list(range(len(script))))
        for passage in passages:
            members = [script[i] for i in passage]
            self.assertEqual(len({(s.chapter_id, s.scene_id) for s in members}), 1)
            if len(members) > 1:
                self.assertLessEqual(sum(len(s.text) for s in members), MAX_PASSAGE_CHARACTERS)
                self.assertLessEqual(len(members), MAX_PASSAGE_TURNS)
        # 30 segments of 400 characters: four balanced passages of 7 or 8, no short remnant.
        self.assertEqual([len(p) for p in passages[:4]], [7, 8, 7, 8])
        # A segment over the bound stands alone; the short chapter is one passage.
        self.assertIn([32], passages)
        self.assertIn([30, 31], passages)
        self.assertEqual(plan_passages(script), passages)

    def test_request_carries_speakers_styles_and_reactions_only_in_conversation(self):
        voices = {"host_a": "Erinome", "host_b": "Sadachbia"}
        body = request_body([("host_b", "Warum?"), ("host_a", "Weil es so ist. |mhm| Und mehr.")], voices,
                            "google/gemini-3.8-flash-tts")
        self.assertEqual(body["model"], "gemini-3.8-flash-tts")
        config = body["generation_config"]["speech_config"]
        self.assertEqual(config["mode"], "conversational")
        self.assertEqual(config["speakers"], [{"speaker": "Robin", "voice": "Sadachbia"},
                                              {"speaker": "Alex", "voice": "Erinome"}])
        items = body["input"][0]["content"]
        self.assertEqual(items[1]["annotations"], [{"type": "speech_metadata", "speaker": "Alex",
                                                    "style": DEFAULT_STYLES["host_a"]}])
        self.assertIn("|mhm|", items[1]["text"])
        # One host alone: the single voice, without speaker names and without reactions nobody could speak.
        alone = request_body([("host_a", "Weil es so ist. |mhm| Und mehr.")], voices, "google/gemini-3.8-flash-tts",
                             {"host_a": "", "host_b": ""})
        self.assertEqual(alone["generation_config"]["speech_config"], [{"voice": "Erinome"}])
        self.assertEqual(alone["input"][0]["content"], [{"type": "text", "text": "Weil es so ist. Und mehr."}])


class GoogleSpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name).resolve()
        self.engine = GoogleSpeech("AIza-test-key-0123456789abcdef")
        self.script = segments(("c1", "c1", "host_b", 60), ("c1", "c1", "host_a", 120))
        self.spoken = {s.segment_id: s.text for s in self.script}
        self.voices = {"host_a": "Erinome", "host_b": "Sadachbia"}

    def test_key_only_in_the_header_and_the_cache_answers_the_second_time(self):
        opener = Mock()
        opener.open.side_effect = spoken_answer
        with patch("podcast_automate.google_speech.build_opener", return_value=opener):
            first = self.engine.synthesize(self.script, self.spoken, self.voices, "de-DE", self.cache)
            again = GoogleSpeech().synthesize(self.script, self.spoken, self.voices, "de-DE", self.cache)
            styled = self.engine.synthesize(self.script, self.spoken, self.voices, "de-DE", self.cache,
                                            {"host_a": "calm", "host_b": "calm"})
        self.assertEqual(first, again)
        self.assertNotEqual(first, styled)
        self.assertEqual(opener.open.call_count, 2)
        request = opener.open.call_args_list[0].args[0]
        self.assertEqual(request.full_url, GOOGLE_SPEECH_ENDPOINT)
        self.assertEqual(request.get_header("X-goog-api-key"), "AIza-test-key-0123456789abcdef")
        self.assertNotIn("AIza", request.full_url)
        record = json.loads(first.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertNotIn("AIza-test-key", json.dumps(record))
        with wave.open(str(first), "rb") as stream:
            self.assertEqual((stream.getnchannels(), stream.getframerate()), (1, 24000))

    def test_answers_without_audio_or_in_another_format_store_nothing(self):
        cases = {"text only": io.BytesIO(json.dumps({"steps": [{"type": "model_output", "content": [
                     {"type": "text", "text": "Sorry"}]}]}).encode()),
                 "48 kHz": answer(wav_bytes(pcm(6), rate=48000))}
        for name, reply in cases.items():
            with self.subTest(name), patch("podcast_automate.google_speech.build_opener") as build:
                build.return_value.open.return_value = reply
                with self.assertRaises(AppError) as caught:
                    self.engine.synthesize(self.script, self.spoken, self.voices, "de-DE", self.cache)
                self.assertEqual(caught.exception.code, "invalid_audio")
        self.assertEqual(list(self.cache.glob("*.wav")), [])

    def test_refusals_name_their_cause_and_a_daily_quota_does_not_wait(self):
        cases = [(400, {"error": {"message": "API key not valid.", "details": [{"reason": "API_KEY_INVALID"}]}},
                  "google_authentication", "blocked"),
                 (400, {"error": {"message": "Voice is not supported."}}, "google_speech_request", "blocked"),
                 (429, {"error": {"message": "Quota exceeded.", "details": [
                     {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}]}]}},
                  "google_quota", "waiting_for_quota")]
        for code, body, error, status in cases:
            with self.subTest(error), patch("podcast_automate.google_speech.build_opener") as build, \
                    patch("podcast_automate.google_speech.throttle") as throttle:
                build.return_value.open.side_effect = refusal(code, body)
                with self.assertRaises(AppError) as caught:
                    self.engine.synthesize(self.script, self.spoken, self.voices, "de-DE", self.cache)
                self.assertEqual((caught.exception.code, caught.exception.status), (error, status))
                self.assertEqual(build.return_value.open.call_count, 1)
                throttle.assert_not_called()
                self.assertIn(body["error"]["message"], str(caught.exception))

    def test_a_rate_limit_waits_googles_retry_delay_and_then_records(self):
        limited = {"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                                        "retryDelay": "27s"}]}}
        with patch("podcast_automate.google_speech.build_opener") as build, \
                patch("podcast_automate.google_speech.throttle") as throttle, \
                patch("podcast_automate.google_speech.wait_for_throttle"):
            build.return_value.open.side_effect = [refusal(429, limited), refusal(503, {}),
                                                   spoken_answer(self.request())]
            with patch("podcast_automate.google_speech.time.sleep"):
                path = self.engine.synthesize(self.script, self.spoken, self.voices, "de-DE", self.cache)
        self.assertTrue(path.is_file())
        self.assertEqual(throttle.call_args.args[1], 27.0)
        with patch("podcast_automate.google_speech.build_opener") as build, \
                patch("podcast_automate.google_speech.throttle"), patch("podcast_automate.google_speech.wait_for_throttle"):
            build.return_value.open.side_effect = [refusal(429, limited)] * (RATE_LIMIT_RETRIES + 1)
            with self.assertRaises(AppError) as caught:
                self.engine.synthesize(segments(("c9", "c9", "host_a", 100)), {"s001": "Wort " * 20}, self.voices,
                                       "de-DE", self.cache)
        self.assertEqual(caught.exception.status, "waiting_for_quota")

    def request(self):
        request = Mock()
        request.data = json.dumps(request_body([("host_b", self.script[0].text), ("host_a", self.script[1].text)],
                                               self.voices, "google/gemini-3.8-flash-tts")).encode()
        return request

    def test_a_key_in_the_spoken_text_is_never_sent(self):
        spoken = {**self.spoken, "s002": "AIza-test-key-0123456789abcdef"}
        with patch("podcast_automate.google_speech.build_opener") as build:
            with self.assertRaises(AppError) as caught:
                self.engine.synthesize(self.script, spoken, self.voices, "de-DE", self.cache)
            build.assert_not_called()
        self.assertEqual(caught.exception.code, "credential_in_prompt")


class AudioChoiceTests(unittest.TestCase):
    def test_roles_alternate_in_even_episodes_and_defaults_hash_as_before(self):
        choice = AudioChoice(provider="google_gemini_tts", voices={"host_a": "Erinome", "host_b": "Sadachbia"},
                             alternate_roles=True)
        self.assertEqual(choice.for_episode("ep_001").voices, {"host_a": "Erinome", "host_b": "Sadachbia"})
        self.assertEqual(choice.for_episode("ep_002").voices, {"host_a": "Sadachbia", "host_b": "Erinome"})
        self.assertEqual(choice.for_episode("ep_010").voices["host_a"], "Sadachbia")
        self.assertNotIn("styles", choice.model_dump())
        silent = AudioChoice.model_validate({**choice.model_dump(), "styles": {"host_a": "", "host_b": ""}})
        self.assertEqual(silent.model_dump()["styles"], {"host_a": "", "host_b": ""})
        # An OpenRouter or Qwen choice dumps exactly as before alternation and styles existed.
        old = {"provider": "openrouter_gemini_tts", "voices": {"host_a": "Sadaltager", "host_b": "Aoede"}}
        self.assertEqual(AudioChoice.model_validate(old).model_dump(),
                         {**old, "pauses": {"same_speaker_ms": 250, "speaker_change_ms": 450, "chapter_break_ms": 900}})
        with self.assertRaises(ValueError):
            AudioChoice(provider="google_gemini_tts", voices={"host_a": "Erinome", "host_b": "Sadachbia"},
                        styles={"host_a": "x" * 81, "host_b": ""})


class GoogleEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root.resolve()
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            run_script(self.root)
        self.choice = {"provider": "google_gemini_tts", "voices": {"host_a": "Erinome", "host_b": "Sadachbia"},
                       "alternate_roles": True}

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_an_episode_records_passages_with_the_google_key_and_resumes_without_calls(self):
        script = EpisodeScript.model_validate(read_yaml(self.root / "episodes/ep_001/script.yaml"))
        with patch("podcast_automate.google_speech.build_opener") as build, \
                patch("podcast_automate.speech.build_opener", side_effect=AssertionError("No OpenRouter speech")):
            build.return_value.open.side_effect = spoken_answer
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True, audio_choice=self.choice,
                                    api_key="sk-or-text-key", speech_key="AIza-google-key-0123456789")
            self.assertEqual(run.status, "completed")
            calls = build.return_value.open.call_count
            self.assertEqual(calls, len(plan_passages(script.segments)))
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("X-goog-api-key"), "AIza-google-key-0123456789")
            resumed = run_episode_audio(self.root, resume=True, run_id=run.run_id)
            self.assertEqual(resumed.status, "completed")
            self.assertEqual(build.return_value.open.call_count, calls)
        work = self.root / "runs" / run.run_id
        inputs = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(inputs["speech_version"], "google_gemini_tts.v1")
        self.assertIn("passages", inputs)
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["voices"], {"host_a": "Erinome", "host_b": "Sadachbia"})
        timeline = json.loads((self.root / report["parts"][0]["audio"]).with_name("timeline.json").read_text())
        self.assertEqual(timeline["schema_version"], "1.1")
        self.assertEqual([row["segment_ids"] for row in timeline["segments"]],
                         [[script.segments[i].segment_id for i in p] for p in plan_passages(script.segments)])
        tts = json.loads((work / "tts_report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(check_google_rows(self.root, script, tts, AudioChoice.model_validate(self.choice), "de-DE")),
                         calls)

    def test_without_the_google_key_the_recording_stops_before_any_request(self):
        with patch("podcast_automate.google_speech.build_opener") as build, \
                patch.dict("os.environ", {"GEMINI_API_KEY": ""}):
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True,
                                    audio_choice={**self.choice, "expression": False}, api_key="sk-or-text-key")
            build.assert_not_called()
        self.assertEqual(run.status, "blocked")
        errors = [stage["error"] for stage in run.model_dump(mode="json")["stages"].values() if stage.get("error")]
        self.assertEqual(errors[-1]["code"], "google_key_required")


class PairSampleTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed")
    def test_a_conversation_sample_is_spoken_once_and_then_played_from_the_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            projects = Path(temporary).resolve()
            voices = {"host_a": "Erinome", "host_b": "Sadachbia"}
            self.assertFalse(pair_view(projects, voices, None, "de-DE")["ready"])
            with patch("podcast_automate.google_speech.build_opener") as build:
                build.return_value.open.side_effect = spoken_answer
                first = generate_pair(projects, voices, None, "de-DE", "AIza-google-key-0123456789")
                again = generate_pair(projects, voices, None, "de-DE", "AIza-google-key-0123456789")
                self.assertEqual(build.return_value.open.call_count, 1)
                body = json.loads(build.return_value.open.call_args.args[0].data)
            self.assertTrue(first["ready"])
            self.assertEqual(first, again)
            self.assertEqual(body["generation_config"]["speech_config"]["mode"], "conversational")
            self.assertTrue(any("|mhm|" in item["text"] for item in body["input"][0]["content"]))
            # Another style is another sample.
            self.assertFalse(pair_view(projects, voices, {"host_a": "", "host_b": ""}, "de-DE")["ready"])


if __name__ == "__main__":
    unittest.main()
