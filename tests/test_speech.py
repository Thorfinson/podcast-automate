import io
import json
import time
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
from podcast_automate.speech import (RATE_LIMIT_RETRIES, SPOKEN_TAG, AudioChoice, GEMINI_MODEL, GEMINI_VOICES, GeminiSpeech,
                                    PausePolicy, SPEECH_ENDPOINT, SPEECH_VERSION, audio_catalog, audio_generation_record,
                                    same_audio_generation, speech_settings, split_input)
from podcast_automate.models import TopicBrief
from podcast_automate.storage import digest, file_hash, file_lock, read_yaml, write_json, write_yaml
from podcast_automate.studio_worker import perform
from tests import script_fixtures as fixtures
from tests.test_audio import tone


# Two seconds of an audible tone (±4096, -18 dBFS). Until 2026-10-02 the fixture was ±16, which the speech health
# gate rightly takes for a take without audible speech.
SOUND = b"\x00\x10\x00\xf0"
PCM = SOUND * 24000


def take(seconds, *, silence=0.0):
    """Gemini PCM of ``seconds`` of sound, with ``silence`` seconds of digital silence in its middle."""
    half = SOUND * round(seconds * 6000)
    return half + b"\0\0" * round(silence * 24000) + half


def response(pcm=PCM, content_type="audio/pcm", generation="gen-test"):
    result = io.BytesIO(pcm)
    result.status = 200
    result.headers = {"Content-Type":content_type, "X-Generation-Id":generation}
    return result


def spoken(request, **kwargs):
    """A take as long as the request's text takes to say at 16 characters per second, the measured median."""
    text = " ".join(SPOKEN_TAG.sub(" ", json.loads(request.data)["input"]).split())
    return response(take(max(1.0, len(text) / 16)))


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Resolved, as the paths storage.inside() returns are (an 8.3 TEMP differs from its long form).
        self.cache = Path(self.temp.name).resolve()
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
        # 503 left this list on 2026-10-02: a gateway or overload answer is now asked again twice (next test).
        for code in (302, 400, 401, 402, 500):
            with self.subTest(code=code), patch("podcast_automate.speech.build_opener") as build:
                build.return_value.open.side_effect = HTTPError(SPEECH_ENDPOINT, code, "test-key", {}, io.BytesIO(b"test-key"))
                with self.assertRaises(AppError) as raised:
                    self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
                self.assertEqual(build.return_value.open.call_count, 1)
                self.assertNotIn("test-key", str(raised.exception))
        self.assertEqual(list(self.cache.glob("*.wav")), [])

    def test_a_refused_recording_names_openrouters_reason(self):
        """2026-10-04: the Transformer recordings stopped on the key's credit limit as „Key-Berechtigungen und
        Anbieterregeln prüfen“. A 403 keeps OpenRouter's own short reason, without the key (D-132)."""
        body = json.dumps({"error": {"code": 403, "message": "Key limit exceeded (test-key)"}}).encode()
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = HTTPError(SPEECH_ENDPOINT, 403, "Forbidden", {}, io.BytesIO(body))
            with self.assertRaises(AppError) as raised:
                self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
        self.assertEqual(raised.exception.code, "openrouter_forbidden")
        self.assertEqual(raised.exception.details["provider_message"], "Key limit exceeded ([Zugangsdaten entfernt])")
        self.assertNotIn("test-key", str(raised.exception))

    def test_a_rate_limit_throttles_every_recording_and_is_asked_again(self):
        """The user's choice of 2026-09-29: start every approved episode at once and throttle on 429 instead of stopping.
        The pause is shared through the cache folder, so a second recording waits as well."""
        limited = lambda retry_after=None: HTTPError(SPEECH_ENDPOINT, 429, "quota", {"Retry-After": retry_after} if retry_after else {},
                                                     io.BytesIO(b"test-key"))
        with patch("podcast_automate.speech.build_opener") as build, patch("podcast_automate.speech.time.sleep") as pause:
            build.return_value.open.side_effect = [limited("7"), limited(), response()]
            path = self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
            self.assertTrue(path.exists())
            self.assertEqual(build.return_value.open.call_count, 3)
            waits = [call.args[0] for call in pause.call_args_list]
            self.assertTrue(any(6 < wait <= 7 for wait in waits), waits)
            shared = json.loads((self.cache / "throttle.json").read_text(encoding="utf-8"))
            self.assertEqual(shared["attempt"], 2)
            # Another recording started now waits for the shared pause before its first request.
            pause.reset_mock()
            write_json(self.cache / "throttle.json", {"until": time.time() + 30, "attempt": 1})
            build.return_value.open.side_effect = [response()]
            GeminiSpeech("test-key").synthesize("Servus", "Aoede", "de-DE", self.cache)
            self.assertTrue(pause.call_args_list and 25 < pause.call_args_list[0].args[0] <= 30)
            # A limit that outlasts every retry stops the recording as before, without echoing the key.
            build.return_value.open.side_effect = [limited() for _ in range(RATE_LIMIT_RETRIES + 1)]
            with self.assertRaises(AppError) as stopped:
                GeminiSpeech("test-key").synthesize("Grüß dich", "Aoede", "de-DE", self.cache)
        self.assertEqual((stopped.exception.code, stopped.exception.status), ("openrouter_rate_limit", "waiting_for_quota"))
        self.assertNotIn("test-key", str(stopped.exception))

    def test_a_rate_limit_in_one_project_slows_every_project_and_never_shortens_a_pause(self):
        """2026-10-02: the throttle was per project folder, while the Studio's limit on parallel recordings is global."""
        from podcast_automate.speech import run_gemini_tts, shared_throttle, throttle
        projects = self.cache / "projects"
        first, second = projects / "first", projects / "second"
        shared = shared_throttle(first)
        self.assertEqual((shared, shared_throttle(second)), (projects / ".gemini_throttle.json",) * 2)
        limited = HTTPError(SPEECH_ENDPOINT, 429, "quota", {"Retry-After": "20"}, io.BytesIO())
        with patch("podcast_automate.speech.build_opener") as build, patch("podcast_automate.speech.time.sleep") as pause:
            build.return_value.open.side_effect = [limited, response()]
            GeminiSpeech("test-key", throttle_file=shared).synthesize("Hallo", "Aoede", "de-DE", first / "cache/audio/gemini")
            self.assertFalse((first / "cache/audio/gemini/throttle.json").exists())
            pause.reset_mock()
            build.return_value.open.side_effect = [response()]
            GeminiSpeech("test-key", throttle_file=shared).synthesize("Servus", "Aoede", "de-DE", second / "cache/audio/gemini")
            self.assertTrue(pause.call_args_list and 15 < pause.call_args_list[0].args[0] <= 20)
            # An episode recording uses the shared file of its projects folder.
            config = TopicBrief(topic="Gemeinsame Drossel")
            script = fixtures.example_script()
            write_json(shared, {"until": time.time() + 30, "attempt": 1})
            pause.reset_mock()
            build.return_value.open.side_effect = lambda *a, **k: response()
            choice = AudioChoice(provider="openrouter_gemini_tts", voices={"host_a": "Aoede", "host_b": "Puck"})
            (second / "work").mkdir(parents=True)
            run_gemini_tts(config, script, second, second / "work", choice, "test-key")
            self.assertTrue(pause.call_args_list and 25 < pause.call_args_list[0].args[0] <= 30)
        # A shorter pause never replaces a longer one, and the update holds the throttle's lock.
        write_json(shared, {"until": time.time() + 100, "attempt": 1})
        with patch("podcast_automate.speech.file_lock", wraps=file_lock) as lock:
            throttle(None, "5", 2, path=shared)
        lock.assert_called_once_with(projects / ".gemini_throttle.lock", timeout=30)
        saved = json.loads(shared.read_text(encoding="utf-8"))
        self.assertGreater(saved["until"], time.time() + 90)
        self.assertEqual(saved["attempt"], 2)

    def test_a_gateway_error_a_cut_transfer_or_a_reset_is_asked_again_twice(self):
        """2026-10-02: one 502 among about 1,800 requests stopped a whole episode."""
        from http.client import IncompleteRead, RemoteDisconnected
        failing = lambda code: HTTPError(SPEECH_ENDPOINT, code, "test-key", {}, io.BytesIO(b"test-key"))
        cases = {"502": [failing(502), failing(503), response()], "504": [failing(504), response()],
                 "cut": [IncompleteRead(b"x", 10), response()], "reset": [URLError(ConnectionResetError()), response()],
                 "disconnected": [RemoteDisconnected("closed"), response()]}
        for name, answers in cases.items():
            with self.subTest(name), patch("podcast_automate.speech.build_opener") as build, \
                    patch("podcast_automate.speech.time.sleep") as pause:
                build.return_value.open.side_effect = answers
                self.assertTrue(self.engine.synthesize("Hallo " + name, "Aoede", "de-DE", self.cache).is_file())
                self.assertEqual(build.return_value.open.call_count, len(answers))
                self.assertEqual([call.args[0] for call in pause.call_args_list], [3.0, 6.0][:len(answers) - 1])
        # A third failure stops the recording as before, without echoing the provider's message.
        with patch("podcast_automate.speech.build_opener") as build, patch("podcast_automate.speech.time.sleep"):
            build.return_value.open.side_effect = [failing(503), failing(502), failing(503)]
            with self.assertRaises(AppError) as stopped:
                self.engine.synthesize("Grüß dich", "Aoede", "de-DE", self.cache)
            self.assertEqual(build.return_value.open.call_count, 3)
        self.assertEqual(stopped.exception.code, "openrouter_unavailable")
        self.assertNotIn("test-key", str(stopped.exception))
        # A timeout is not transient here: the request may still be running at the provider.
        with patch("podcast_automate.speech.build_opener") as build, patch("podcast_automate.speech.time.sleep"):
            build.return_value.open.side_effect = [TimeoutError(), response()]
            with self.assertRaises(AppError) as stopped:
                self.engine.synthesize("Bis gleich", "Aoede", "de-DE", self.cache)
            self.assertEqual((build.return_value.open.call_count, stopped.exception.code), (1, "openrouter_connection"))

    def test_an_implausible_take_is_recorded_once_more_and_then_stops_with_its_segment(self):
        """The health gate (2026-10-02): Asimov ep_013 s3_09, 1,248 characters spoken in 0.37 s, passed every check
        and was exported. A take of 80 characters or more must come at 8 to 25 characters per second."""
        from podcast_automate.speech import run_gemini_tts
        text = "Diese Erklärung ist lang genug, damit die Sprechgeschwindigkeit geprüft wird, und sie endet hier. " * 2
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = [response(take(0.37)), response(take(len(text) / 16))]
            path = self.engine.synthesize(text, "Aoede", "de-DE", self.cache)
            self.assertEqual(build.return_value.open.call_count, 2)
        with wave.open(str(path), "rb") as wav:
            self.assertAlmostEqual(wav.getnframes() / 24000, len(text) / 16, delta=0.01)
        # Twice implausible: nothing is cached, and the stop names the segment and both measurements.
        script = fixtures.example_script()
        script.segments[0].text = text.strip()
        config = TopicBrief(topic="Gesundheitstest")
        choice = AudioChoice(provider="openrouter_gemini_tts", voices={"host_a": "Aoede", "host_b": "Puck"})
        root = self.cache / "projects/example"
        (root / "work").mkdir(parents=True)
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = [response(take(0.37)), response(take(60))]
            with self.assertRaises(AppError) as stopped:
                run_gemini_tts(config, script, root, root / "work", choice, "test-key")
        self.assertEqual((stopped.exception.code, stopped.exception.status), ("invalid_speech", "blocked"))
        self.assertIn(f"Abschnitt {script.segments[0].segment_id}:", str(stopped.exception))
        self.assertIn("Zeichen pro Sekunde", str(stopped.exception))
        self.assertEqual(stopped.exception.details["segment_id"], script.segments[0].segment_id)
        self.assertEqual(list((root / "cache/audio/gemini").glob("*.wav")), [])

    def test_silence_needs_a_pause_tag_and_an_inaudible_take_fails(self):
        from podcast_automate.speech import take_defect
        short = "Kurz gesagt: so ist es."
        self.assertEqual(take_defect(take(2, silence=2.4), short), "")
        self.assertIn("3.0 s Stille", take_defect(take(2, silence=3.0), short))
        self.assertEqual(take_defect(take(2, silence=7.3), "<long pause> " + short), "")
        self.assertIn("Stille", take_defect(take(2, silence=7.3), "<sigh> " + short))
        self.assertIn("keine hörbare Sprache", take_defect(b"\x10\x00\xf0\xff" * 24000, short))
        # The rate counts speech only: a tagged pause of 7 s does not make 100 characters look slow.
        hundred = "x" * 100
        self.assertEqual(take_defect(take(100 / 16, silence=7), "<long pause> " + hundred), "")
        for seconds, plausible in ((100 / 8, True), (100 / 25, True), (100 / 7, False), (100 / 27, False)):
            with self.subTest(seconds=seconds):
                self.assertEqual(take_defect(take(seconds), hundred) == "", plausible)
        # Below 80 characters the rate is not judged: "Ja." may take a second.
        self.assertEqual(take_defect(take(3), "x" * 79), "")

    def test_a_cached_take_that_fails_the_gate_is_recorded_again_on_the_next_render(self):
        text = "Ein Abschnitt, der vor dem Gesundheitstest abgeschnitten aufgenommen und so gespeichert wurde. " * 2
        settings = speech_settings(text, "Aoede", "de-DE")
        path = self.cache / (digest(settings) + ".wav")
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            wav.writeframes(take(0.37))
        write_json(path.with_suffix(".json"), {"settings": settings, "sha256": file_hash(path)})
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = spoken
            self.assertEqual(self.engine.synthesize(text, "Aoede", "de-DE", self.cache), path)
            self.assertEqual(build.return_value.open.call_count, 1)
            # The new take passes and is served from the cache from now on.
            self.engine.synthesize(text, "Aoede", "de-DE", self.cache)
            self.assertEqual(build.return_value.open.call_count, 1)
        with wave.open(str(path), "rb") as wav:
            self.assertGreater(wav.getnframes() / 24000, 10)

    def test_a_segment_lock_file_is_removed_once_nobody_holds_or_awaits_it(self):
        """2026-10-02: 830 and 551 lock files were left in two projects' cache/audio/gemini/locks."""
        import os
        from podcast_automate.parallel_speech import LockedGeminiSpeech, release_lock_files
        locks = self.cache / "locks"
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response()
            LockedGeminiSpeech("test-key").synthesize("Hallo", "Aoede", "de-DE", self.cache)
        # Windows refuses to delete a lock file a waiting worker has open; elsewhere a deleted one could be held
        # twice, so it stays there.
        self.assertEqual(len(list(locks.glob("*.lock"))), 0 if os.name == "nt" else 1)
        stale, held = locks / "stale.lock", locks / "held.lock"
        stale.touch()
        with held.open("a+b"):
            release_lock_files(self.cache)
            self.assertTrue(held.exists())
        self.assertEqual(stale.exists(), os.name != "nt")

    def test_an_account_limited_to_zero_data_retention_names_the_privacy_setting(self):
        body = (b'{"error":{"message":"0 endpoints out of 1 requested are available matching your guardrail restrictions '
                b'and data policy. ZDR violation (account settings): 1 endpoint excluded test-key","code":404}}')
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = HTTPError(SPEECH_ENDPOINT, 404, "Not Found", {}, io.BytesIO(body))
            with self.assertRaises(AppError) as raised:
                self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
        self.assertEqual(raised.exception.code, "openrouter_privacy")
        self.assertIn("openrouter.ai/settings/privacy", str(raised.exception))
        self.assertNotIn("test-key", str(raised.exception))
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = HTTPError(SPEECH_ENDPOINT, 404, "Not Found", {}, io.BytesIO(b"no such model"))
            with self.assertRaises(AppError) as raised:
                self.engine.synthesize("Hallo", "Aoede", "de-DE", self.cache)
        self.assertEqual(raised.exception.code, "openrouter_speech_request")

    def test_long_turn_is_split_only_for_request_size_without_losing_characters(self):
        text = ("Ein Gedanke führt zum nächsten. " * 230) + "Das ist der Schluss."
        chunks = split_input(text)
        self.assertEqual("".join(chunks), text)
        self.assertTrue(all(len(part) <= 6000 for part in chunks))
        with patch("podcast_automate.speech.build_opener") as build:
            # Each part is answered with a take of plausible length, which the health gate requires since 2026-10-02.
            build.return_value.open.side_effect = spoken
            result = self.engine.synthesize(text, "Sadaltager", "de-DE", self.cache)
            self.assertEqual(build.return_value.open.call_count, len(chunks))
        with wave.open(str(result), "rb") as wav:
            self.assertEqual(wav.getnframes(), sum(len(take(len(part.strip()) / 16)) // 2 for part in chunks))
        # The joined take is served from the cache again without a request.
        with patch("podcast_automate.speech.build_opener") as build:
            self.assertEqual(self.engine.synthesize(text, "Sadaltager", "de-DE", self.cache), result)
            build.assert_not_called()

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

    def test_the_spoken_form_changes_the_cache_key_and_the_verifier(self):
        from podcast_automate.errors import AppError as Error
        from podcast_automate.models import Chapter, EpisodeScript, Segment
        from podcast_automate.speech import check_gemini_rows
        from podcast_automate.spoken_forms import SpokenForms
        plain = speech_settings("Auf H800.", "Aoede", "de-DE")
        spoken = speech_settings("Auf H800.", "Aoede", "de-DE", "Auf Ha achthundert.")
        # Without a spoken form the settings are the exact dict the adapter hashed before spoken
        # forms existed, so every earlier Gemini cache entry keeps its key and is not paid twice.
        self.assertEqual(plain, {"provider": "openrouter_gemini_tts", "model": GEMINI_MODEL,
            "adapter_version": SPEECH_VERSION, "voice": "Aoede", "language": "de-DE", "text": "Auf H800.",
            "format": "pcm", "sample_rate": 24000, "channels": 1, "sample_width": 2})
        self.assertEqual(plain, speech_settings("Auf H800.", "Aoede", "de-DE", "Auf H800."))
        self.assertEqual(spoken, {**plain, "spoken_text": "Auf Ha achthundert."})
        self.assertNotEqual(digest(plain), digest(spoken))
        script = EpisodeScript(episode_id="ep_001", title="T", purpose="deep_dive",
            chapters=[Chapter(chapter_id="c", title="C")],
            segments=[Segment(segment_id="seg_001", scene_id="c", chapter_id="c",
                              speaker_id="host_a", text="Auf H800.")])
        table = SpokenForms(entries=[{"written": "H800", "spoken": "Ha achthundert"}])
        wav = self.cache / "cache/audio/gemini/x.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(b"audio")
        report = {"segments": [{"segment_id": "seg_001", "path": "gemini/x.wav", "sha256": file_hash(wav),
                                "settings": spoken}]}
        choice = AudioChoice(provider="openrouter_gemini_tts", voices={"host_a": "Aoede", "host_b": "Puck"})
        # The verifier compares the spoken form the table produces today, not the script text.
        with self.assertRaises(Error) as caught:
            check_gemini_rows(self.cache, script, report, choice, "de-DE")
        self.assertEqual(caught.exception.code, "invalid_audio")
        # With the same table the settings agree and the row is accepted.
        self.assertEqual(check_gemini_rows(self.cache, script, report, choice, "de-DE", table=table), [wav])
        # A row written before spoken forms existed carries no spoken_text and stays valid while
        # no form applies; the same table that would change the sound rejects it.
        legacy = {"segments": [{"segment_id": "seg_001", "path": "gemini/x.wav", "sha256": file_hash(wav),
                                "settings": plain}]}
        self.assertEqual(check_gemini_rows(self.cache, script, legacy, choice, "de-DE"), [wav])
        with self.assertRaises(Error):
            check_gemini_rows(self.cache, script, legacy, choice, "de-DE", table=table)

    def test_the_cache_record_keeps_the_script_text_and_the_request_carries_the_spoken_form(self):
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response()
            path = self.engine.synthesize("Auf H800.", "Aoede", "de-DE", self.cache, spoken="Auf Ha achthundert.")
            payload = json.loads(build.return_value.open.call_args.args[0].data)
            # The same script text without the form is another cache entry, not a hit.
            self.engine.synthesize("Auf H800.", "Aoede", "de-DE", self.cache)
            self.assertEqual(build.return_value.open.call_count, 2)
        self.assertEqual(payload["input"], "Auf Ha achthundert.")
        record = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["settings"]
        self.assertEqual((record["text"], record["spoken_text"]), ("Auf H800.", "Auf Ha achthundert."))
        self.assertEqual(path.name, digest(speech_settings("Auf H800.", "Aoede", "de-DE", "Auf Ha achthundert.")) + ".wav")

    def test_a_default_pause_policy_is_left_out_of_the_stored_choice(self):
        plain = AudioChoice(provider="openrouter_gemini_tts", voices={"host_a": "Aoede", "host_b": "Puck"})
        record = audio_generation_record(plain)
        # The record a run hashes and a decision stores is the pre-policy shape, so approvals and
        # input hashes made before the policy existed keep matching.
        self.assertEqual(record, {"provider": "openrouter_gemini_tts", "voices": {"host_a": "Aoede", "host_b": "Puck"}})
        self.assertTrue(same_audio_generation(record, plain.model_dump()))
        self.assertTrue(same_audio_generation(plain.model_dump(), record))
        slower = plain.model_copy(update={"pauses": PausePolicy(chapter_break_ms=1800)})
        self.assertEqual(audio_generation_record(slower)["pauses"]["chapter_break_ms"], 1800)
        self.assertFalse(same_audio_generation(record, audio_generation_record(slower)))
        self.assertFalse(same_audio_generation(record, None))

    def test_catalog_validates_provider_voices_and_has_all_thirty_gemini_choices(self):
        self.assertEqual(len(set(GEMINI_VOICES)), 30)
        self.assertEqual(audio_catalog()["openrouter_gemini_tts"]["defaults"], {"host_a":"Sadaltager","host_b":"Aoede"})
        for voices in ({"host_a":"Aiden","host_b":"Vivian"}, {"host_a":"Aoede","host_b":"Aoede"}):
            with self.assertRaises(ValidationError):
                AudioChoice(provider="openrouter_gemini_tts", voices=voices)

    def test_the_chosen_model_is_requested_and_keys_its_own_cache_entry(self):
        lite = GeminiSpeech("test-key", model="google/gemini-3.8-flash-lite-tts")
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response()
            default = self.engine.synthesize("Ein Beispiel.", "Aoede", "de-DE", self.cache)
            path = lite.synthesize("Ein Beispiel.", "Aoede", "de-DE", self.cache)
            again = lite.synthesize("Ein Beispiel.", "Aoede", "de-DE", self.cache)
            models = [json.loads(call.args[0].data)["model"] for call in build.return_value.open.call_args_list]
        self.assertEqual(GEMINI_MODEL, "google/gemini-3.8-flash-tts")
        self.assertEqual(models, [GEMINI_MODEL, "google/gemini-3.8-flash-lite-tts"])
        self.assertNotEqual(default, path)
        self.assertEqual(path, again)
        record = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))["settings"]
        self.assertEqual(record, speech_settings("Ein Beispiel.", "Aoede", "de-DE", model="google/gemini-3.8-flash-lite-tts"))
        # The retired model is no longer offered.
        with self.assertRaises(AppError):
            GeminiSpeech("test-key", model="google/gemini-3.1-flash-tts-preview")

    def test_a_choice_without_a_model_means_the_default_and_keeps_its_stored_shape(self):
        from podcast_automate.models import Chapter, EpisodeScript, Segment
        from podcast_automate.speech import check_gemini_rows
        saved = {"provider": "openrouter_gemini_tts", "voices": {"host_a": "Aoede", "host_b": "Puck"}}
        default = AudioChoice.model_validate(saved)
        # Choices saved before the model was selectable dump exactly as before, so stored choices,
        # their hashes and the approvals made for them stay valid.
        self.assertEqual(default.model, GEMINI_MODEL)
        self.assertEqual(default.model_dump(), {**saved, "pauses": PausePolicy().model_dump()})
        self.assertEqual(AudioChoice(**saved, model=GEMINI_MODEL).model_dump(), default.model_dump())
        self.assertEqual(audio_generation_record(default), saved)
        lite = AudioChoice(**saved, model="google/gemini-3.8-flash-lite-tts")
        self.assertEqual(AudioChoice.model_validate(lite.model_dump()), lite)
        self.assertEqual(audio_generation_record(lite), {**saved, "model": "google/gemini-3.8-flash-lite-tts"})
        # Approvals and resumes compare these records, so a model switch is a new audio choice.
        self.assertFalse(same_audio_generation(saved, lite.model_dump()))
        with self.assertRaises(ValidationError):
            AudioChoice(**saved, model="google/gemini-3.1-flash-tts-preview")
        # The model applies to Gemini only; a local choice never carries one.
        local = AudioChoice(provider="qwen3_local", voices={"host_a": "Aiden", "host_b": "Vivian"},
                            model="google/gemini-3.8-flash-lite-tts")
        self.assertNotIn("model", local.model_dump())
        # A recording made with one model does not verify for the other.
        script = EpisodeScript(episode_id="ep_001", title="T", purpose="deep_dive",
            chapters=[Chapter(chapter_id="c", title="C")],
            segments=[Segment(segment_id="seg_001", scene_id="c", chapter_id="c",
                              speaker_id="host_a", text="Hallo.")])
        wav = self.cache / "cache/audio/gemini/x.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        wav.write_bytes(b"audio")
        report = {"segments": [{"segment_id": "seg_001", "path": "gemini/x.wav", "sha256": file_hash(wav),
                                "settings": speech_settings("Hallo.", "Aoede", "de-DE")}]}
        self.assertEqual(check_gemini_rows(self.cache, script, report, default, "de-DE"), [wav])
        with self.assertRaises(AppError) as caught:
            check_gemini_rows(self.cache, script, report, lite, "de-DE")
        self.assertEqual(caught.exception.code, "invalid_audio")


class GeminiEpisodeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        # Resolved, as the entry points resolve the root before calling generate_sample and the episode functions.
        self.root = self.fixture.root.resolve()
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
        # The saved choice now also carries the pause policy; the default is filled in.
        self.assertEqual(report["audio_generation"],
                         AudioChoice.model_validate(self.choice).model_dump())
        transcript = (self.root / report["parts"][0]["audio"]).with_name("transcript.md").read_text(encoding="utf-8")
        # Voice presets are not host identities; the transcript carries the roles.
        self.assertIn("**Host A:**", transcript)
        self.assertIn("**Host B:**", transcript)
        self.assertNotIn("**Sadaltager:**", transcript)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_explicit_local_choice_still_uses_qwen_and_keeps_the_script(self):
        from tests.test_episode_audio import EpisodeAudioTests
        local_fixture = EpisodeAudioTests()
        local_fixture.calls, local_fixture.spoken = [], []
        before = file_hash(self.root / "episodes/ep_001/script.yaml")
        local = {"provider":"qwen3_local", "voices":{"host_a":"Ryan","host_b":"Serena"}}
        with patch("podcast_automate.episode_audio.run_tts", side_effect=local_fixture.synthesize), \
             patch("podcast_automate.episode_audio.run_gemini_tts", side_effect=AssertionError("No cloud call for Qwen")):
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True, audio_choice=local)
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(local_fixture.calls), 1)
        self.assertEqual(before, file_hash(self.root / "episodes/ep_001/script.yaml"))
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text())
        self.assertEqual(report["audio_generation"], AudioChoice.model_validate(local).model_dump())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_quota_pause_reuses_finished_segment_and_rejects_changed_voice_on_resume(self):
        # A rate limit that outlasts every throttled retry still pauses the run.
        with patch("podcast_automate.speech.build_opener") as build, patch("podcast_automate.speech.time.sleep"):
            build.return_value.open.side_effect = [response(self.pcm), *[HTTPError(SPEECH_ENDPOINT,429,"quota",{},io.BytesIO())
                                                                         for _ in range(RATE_LIMIT_RETRIES + 1)]]
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

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_switching_the_model_renders_every_segment_again_and_binds_the_run(self):
        segments = read_yaml(self.root / "episodes/ep_001/script.yaml")["segments"]
        lite = {**self.choice, "model": "google/gemini-3.8-flash-lite-tts"}
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response(self.pcm)
            first = run_episode_audio(self.root, episode="ep_001", approve_audio=True,
                                      audio_choice=self.choice, api_key="test-key")
            self.assertEqual(first.status, "completed")
            # The approval named the default model, so the switch needs its own approval.
            with self.assertRaises(AppError):
                run_episode_audio(self.root, episode="ep_001", audio_choice=lite, api_key="test-key")
            # No cache entry is shared between models: every segment is requested again.
            second = run_episode_audio(self.root, episode="ep_001", approve_audio=True,
                                       audio_choice=lite, api_key="test-key")
            self.assertEqual(second.status, "completed")
            models = [json.loads(call.args[0].data)["model"] for call in build.return_value.open.call_args_list]
        self.assertEqual(models, [GEMINI_MODEL] * len(segments) + ["google/gemini-3.8-flash-lite-tts"] * len(segments))
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text())
        self.assertEqual(report["audio_generation"]["model"], "google/gemini-3.8-flash-lite-tts")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_one_override_costs_exactly_one_gemini_call_and_the_rest_come_from_cache(self):
        segments = read_yaml(self.root / "episodes/ep_001/script.yaml")["segments"]
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = lambda *a, **k: response(self.pcm)
            first = run_episode_audio(self.root, episode="ep_001", approve_audio=True,
                                      audio_choice=self.choice, api_key="test-key")
            self.assertEqual(first.status, "completed")
            self.assertEqual(build.return_value.open.call_count, len(segments))
            decision = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")
            write_yaml(self.root / "episodes/ep_001/audio_review.yaml",
                       {**decision, "spoken_overrides": {segments[-1]["segment_id"]: "Es bewertet Möglichkeiten."}})
            # The saved approval stands: no approve_audio, and exactly one segment is synthesised
            # again while every other one is served from the cache the first run filled.
            second = run_episode_audio(self.root, episode="ep_001", audio_choice=self.choice, api_key="test-key")
            self.assertEqual(second.status, "completed")
            self.assertEqual(build.return_value.open.call_count, len(segments) + 1)
            payload = json.loads(build.return_value.open.call_args.args[0].data)
        self.assertEqual(payload["input"], "Es bewertet Möglichkeiten.")
        self.assertNotEqual(first.run_id, second.run_id)
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["run_id"], second.run_id)
        self.assertEqual(report["spoken_overrides"], {segments[-1]["segment_id"]: "Es bewertet Möglichkeiten."})
        rows = json.loads((self.root / "runs" / second.run_id / "tts_report.json").read_text(encoding="utf-8"))["segments"]
        self.assertEqual([row["settings"].get("spoken_text") for row in rows],
                         [None] * (len(segments) - 1) + ["Es bewertet Möglichkeiten."])

    def test_saved_qwen_approval_does_not_authorize_paid_gemini_audio(self):
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
            # The 180-character sample text needs a take of plausible length (speech health gate, 2026-10-02).
            build.return_value.open.side_effect = spoken
            first = perform(self.root, request)
            again = perform(self.root, request)
            self.assertEqual(build.return_value.open.call_count, 1)
            payload = json.loads(build.return_value.open.call_args.args[0].data)
        self.assertIn("Eine gute Erklärung", payload["input"])
        self.assertEqual(first, again)
        self.assertTrue((self.root / first["sample"]["audio"]).is_file())
