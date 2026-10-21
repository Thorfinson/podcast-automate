"""ElevenLabs' Text to Dialogue as a speech route (D-176) and a project's own audio choice (D-177). No request leaves a
test: the opener is patched, and an answer is a sine as long as its text takes to say."""
import base64
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from podcast_automate.elevenlabs_speech import (ELEVENLABS_ENDPOINT, MAX_PASSAGE_CHARACTERS, REQUEST_CHARACTERS,
                                                ElevenLabsSpeech, account_voices, check_elevenlabs_rows,
                                                passage_context, passage_turns, plan)
from podcast_automate.episode_audio import run_episode_audio
from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript, Segment
from podcast_automate.scripting import run_script
from podcast_automate.speech import ELEVENLABS_MODEL, GEMINI_MODEL, OWN_AUDIO, AudioChoice, selected_audio
from podcast_automate.storage import load_project, read_yaml, write_json
from tests import script_fixtures as fixtures
from tests.test_google_speech import pcm, segments

PAULI, THOMAS = "S87WJpstXs0Ge2ukRiXJ", "VHYWoxffK1pFlM1dtRb0"
VOICES = {"host_a": PAULI, "host_b": THOMAS}
KEY = "sk_" + "0123456789abcdef" * 3


def dialogue_answer(request, *args, **kwargs):
    """A take as long as the request's turns take to say at 16 characters per second, with ElevenLabs' headers."""
    body = json.loads(request.data)
    text = " ".join(item["text"] for item in body["inputs"])
    data = json.dumps({"audio_base64": base64.b64encode(pcm(max(1.0, len(text) / 16))).decode(),
                       "voice_segments": [{"voice_id": item["voice_id"], "dialogue_input_index": index}
                                          for index, item in enumerate(body["inputs"])]}).encode()
    response = io.BytesIO(data)
    response.headers = {"character-cost": "416", "request-id": "req-1"}
    response.status = 200
    return response


def refusal(code, body):
    return HTTPError(ELEVENLABS_ENDPOINT, code, "refused", {}, io.BytesIO(json.dumps(body).encode()))


class PassageTests(unittest.TestCase):
    def test_passages_stay_under_elevenlabs_advice_and_never_cross_a_scene(self):
        script = segments(*[("c1", "c1", "host_a" if n % 2 else "host_b", 400) for n in range(12)],
                          ("c2", "c2", "host_a", 300), ("c2", "c2", "host_b", 200))
        script = EpisodeScript(episode_id="ep_001", title="T", purpose="deep_dive",
                               chapters=[{"chapter_id": "c1", "title": "1"}, {"chapter_id": "c2", "title": "2"}],
                               segments=script)
        passages = plan(script)
        self.assertEqual([i for p in passages for i in p], list(range(len(script.segments))))
        for passage in passages:
            members = [script.segments[i] for i in passage]
            self.assertEqual(len({s.scene_id for s in members}), 1)
            self.assertLessEqual(sum(len(s.text) for s in members), MAX_PASSAGE_CHARACTERS)
        heard = {s.segment_id: s.text for s in script.segments}
        # The context never reaches across a chapter: each chapter records on its own and the episode is checked whole.
        first_of_c2 = next(n for n, p in enumerate(passages) if script.segments[p[0]].chapter_id == "c2")
        self.assertEqual(passage_context(passages, first_of_c2, script.segments, heard)["previous_text"], "")
        self.assertTrue(passage_context(passages, 1, script.segments, heard)["previous_text"])

    def test_gemini_tags_reactions_and_styles_are_spoken_in_elevenlabs_words(self):
        rows = [Segment(segment_id="a", chapter_id="c", scene_id="c", speaker_id="host_a",
                        text="Saalfeld wird 899 genannt. Dazu gehörte ein Hof mit Kapelle."),
                Segment(segment_id="b", chapter_id="c", scene_id="c", speaker_id="host_b", text="Warum dort?")]
        heard = {"a": "<breath> Saalfeld wird 899 genannt. |aha| Dazu gehörte ein Hof mit Kapelle.",
                 "b": "Warum dort? <sigh>"}
        turns = passage_turns(rows, heard, {"host_a": "curious, warm", "host_b": ""})
        self.assertEqual(turns, [("host_a", "[curious, warm] [inhales] Saalfeld wird 899 genannt."), ("host_b", "Aha."),
                                 ("host_a", "Dazu gehörte ein Hof mit Kapelle."), ("host_b", "Warum dort? [sighs]")])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name).resolve()
        self.engine = ElevenLabsSpeech(KEY)
        self.script = segments(("c1", "c1", "host_b", 60), ("c1", "c1", "host_a", 120))
        self.spoken = {s.segment_id: s.text for s in self.script}
        context = {"previous_text": "Vorher.", "future_text": ""}
        self.call = lambda engine: engine.synthesize(self.script, self.spoken, VOICES, "de-DE", self.cache, None, context)

    def test_key_only_in_the_header_and_the_cache_answers_the_second_time(self):
        opener = Mock()
        opener.open.side_effect = dialogue_answer
        with patch("podcast_automate.elevenlabs_speech.build_opener", return_value=opener):
            first = self.call(self.engine)
            again = self.call(ElevenLabsSpeech())
        self.assertEqual((first, opener.open.call_count), (again, 1))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ELEVENLABS_ENDPOINT + "?output_format=pcm_24000")
        self.assertEqual(request.get_header("Xi-api-key"), KEY)
        body = json.loads(request.data)
        self.assertEqual((body["model_id"], body["language_code"], body["previous_text"]), (ELEVENLABS_MODEL, "de", "Vorher."))
        self.assertNotIn("future_text", body, "an empty context is left out")
        self.assertEqual([item["voice_id"] for item in body["inputs"]], [THOMAS, PAULI])
        self.assertTrue(body["inputs"][0]["text"].startswith("[curious, eager to hear what comes next] "))
        record = json.loads(first.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual((record["credits"], record["request_id"], len(record["voice_segments"])), ("416", "req-1", 2))
        self.assertNotIn(KEY, json.dumps(record))

    def test_refusals_name_their_cause_and_a_busy_elevenlabs_is_waited_out(self):
        cases = [(401, {"detail": {"status": "quota_exceeded", "message": "You have 0 credits"}}, "elevenlabs_credits"),
                 (401, {"detail": {"status": "invalid_api_key", "message": "Invalid API key"}}, "elevenlabs_authentication"),
                 (422, {"detail": [{"msg": "text too long"}]}, "elevenlabs_speech_request")]
        for code, body, expected in cases:
            with self.subTest(expected=expected), patch("podcast_automate.elevenlabs_speech.build_opener") as build:
                build.return_value.open.side_effect = refusal(code, body)
                with self.assertRaises(AppError) as stopped:
                    self.call(self.engine)
                self.assertEqual(stopped.exception.code, expected)
        # A 429 (the plan's parallel requests or a busy ElevenLabs) waits and asks again.
        with patch("podcast_automate.elevenlabs_speech.build_opener") as build, \
                patch("podcast_automate.elevenlabs_speech.wait_for_throttle"), \
                patch("podcast_automate.elevenlabs_speech.throttle") as throttled:
            answers = [refusal(429, {"detail": {"status": "system_busy"}})]

            def open_once(request, *args, **kwargs):
                if answers:
                    raise answers.pop()
                return dialogue_answer(request)
            build.return_value.open.side_effect = open_once
            path = self.call(self.engine)
        self.assertTrue(path.is_file())
        self.assertEqual(throttled.call_count, 1)

    def test_a_key_in_the_spoken_text_or_an_overlong_passage_is_never_sent(self):
        spoken = {**self.spoken, self.script[0].segment_id: f"Mein Key ist {KEY}."}
        long = segments(*[("c1", "c1", "host_a" if n % 2 else "host_b", 400) for n in range(6)])
        with patch("podcast_automate.elevenlabs_speech.build_opener") as build:
            with self.assertRaises(AppError) as leaked:
                self.engine.synthesize(self.script, spoken, VOICES, "de-DE", self.cache)
            with self.assertRaises(AppError) as overlong:
                self.engine.synthesize(long, {s.segment_id: s.text for s in long}, VOICES, "de-DE", self.cache)
            build.assert_not_called()
        self.assertEqual(leaked.exception.code, "credential_in_prompt")
        self.assertIn(str(REQUEST_CHARACTERS), str(overlong.exception))

    def test_the_account_voices_come_with_the_users_own_first(self):
        page = {"voices": [{"voice_id": "premade0voice0id0001", "name": "Adam - Deep", "category": "premade",
                            "labels": {"gender": "male"}},
                           {"voice_id": PAULI, "name": "Pauli - Natural", "category": "professional",
                            "labels": {"gender": "female", "language": "de"},
                            "verified_languages": [{"language": "de", "model_id": "eleven_v4"}],
                            "collection_ids": ["podcast"]},
                           {"voice_id": "bad id!", "name": "Broken"}], "has_more": False}
        with patch("podcast_automate.elevenlabs_speech.build_opener") as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(page).encode()
            voices = account_voices(KEY)
            request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header("Xi-api-key"), KEY)
        self.assertEqual([v["voice_id"] for v in voices], [PAULI, "premade0voice0id0001"])
        self.assertEqual((voices[0]["german"], voices[0]["collections"]), (True, ["podcast"]))


class AudioChoiceTests(unittest.TestCase):
    def test_elevenlabs_takes_account_voice_ids_its_own_model_and_styles(self):
        choice = AudioChoice(provider="elevenlabs_tts", voices=VOICES, model=GEMINI_MODEL, alternate_roles=False)
        self.assertTrue(choice.remote)
        self.assertEqual((choice.model, choice.model_dump()["model"]), (ELEVENLABS_MODEL, ELEVENLABS_MODEL))
        styled = AudioChoice(provider="elevenlabs_tts", voices=VOICES, styles={"host_a": "calm", "host_b": ""})
        self.assertEqual(styled.styles.host_a, "calm")
        back = AudioChoice(provider="google_gemini_tts", voices={"host_a": "Erinome", "host_b": "Sadachbia"},
                           model=ELEVENLABS_MODEL)
        self.assertNotIn("model", back.model_dump(), "switched back to Gemini, the default model hashes as before")
        for voices in ({"host_a": "Erinome!", "host_b": THOMAS}, {"host_a": PAULI, "host_b": PAULI}):
            with self.subTest(voices=voices), self.assertRaises(ValueError):
                AudioChoice(provider="elevenlabs_tts", voices=voices)


class ElevenLabsEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root.resolve()
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            run_script(self.root)
        self.choice = {"provider": "elevenlabs_tts", "voices": VOICES, "expression": False}

    def test_a_projects_own_choice_holds_over_the_workspace_settings(self):
        config = load_project(self.root)
        write_json(self.root.parent / ".studio-settings.json",
                   {"audio": {"provider": "google_gemini_tts", "voices": {"host_a": "Erinome", "host_b": "Sadachbia"}}})
        self.assertEqual(selected_audio(self.root, config).provider, "google_gemini_tts")
        write_json(self.root / OWN_AUDIO, AudioChoice.model_validate(self.choice).model_dump())
        own = selected_audio(self.root, config)
        self.assertEqual((own.provider, own.voices), ("elevenlabs_tts", VOICES))
        (self.root / OWN_AUDIO).unlink()
        self.assertEqual(selected_audio(self.root, config).provider, "google_gemini_tts")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_an_episode_records_passages_with_the_elevenlabs_key_and_resumes_without_calls(self):
        script = EpisodeScript.model_validate(read_yaml(self.root / "episodes/ep_001/script.yaml"))
        with patch("podcast_automate.elevenlabs_speech.build_opener") as build, \
                patch("podcast_automate.google_speech.build_opener", side_effect=AssertionError("No Google speech")), \
                patch("podcast_automate.speech.build_opener", side_effect=AssertionError("No OpenRouter speech")):
            build.return_value.open.side_effect = dialogue_answer
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True, audio_choice=self.choice,
                                    speech_key="AIza-google-key-0123456789", elevenlabs_key=KEY)
            self.assertEqual(run.status, "completed")
            calls = build.return_value.open.call_count
            self.assertEqual(calls, len(plan(script)))
            self.assertEqual(build.return_value.open.call_args.args[0].get_header("Xi-api-key"), KEY)
            resumed = run_episode_audio(self.root, resume=True, run_id=run.run_id)
            self.assertEqual((resumed.status, build.return_value.open.call_count), ("completed", calls))
        work = self.root / "runs" / run.run_id
        inputs = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
        self.assertEqual((inputs["speech_version"], inputs["passages"]["version"]),
                         ("elevenlabs_tts.v1", "elevenlabs_passages.v1"))
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        timeline = json.loads((self.root / report["parts"][0]["audio"]).with_name("timeline.json").read_text())
        self.assertEqual(timeline["schema_version"], "1.1")
        self.assertEqual([row["segment_ids"] for row in timeline["segments"]],
                         [[script.segments[i].segment_id for i in p] for p in plan(script)])
        tts = json.loads((work / "tts_report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(check_elevenlabs_rows(self.root, script, tts, AudioChoice.model_validate(self.choice),
                                                   "de-DE")), calls)

    def test_without_the_elevenlabs_key_the_recording_stops_before_any_request(self):
        with patch("podcast_automate.elevenlabs_speech.build_opener") as build, \
                patch.dict("os.environ", {"ELEVENLABS_API_KEY": ""}):
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True, audio_choice=self.choice,
                                    speech_key="AIza-google-key-0123456789")
            build.assert_not_called()
        self.assertEqual(run.status, "blocked")
        errors = [stage["error"] for stage in run.model_dump(mode="json")["stages"].values() if stage.get("error")]
        self.assertEqual(errors[-1]["code"], "elevenlabs_key_required")


if __name__ == "__main__":
    unittest.main()
