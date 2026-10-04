"""Independent audio choices and OpenRouter's binary Gemini TTS endpoint."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import wave
from array import array
from http.client import HTTPException, IncompleteRead
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from pydantic import Field, SecretStr, model_serializer, model_validator

from . import studio_settings
from .audio import PAUSE_TAGS, silent_runs
from .errors import AppError
from .models import Contract, HostVoices
from .openrouter import NoRedirect, api_failure
from .qwen_worker import spoken_settings
from .spoken_forms import SpokenForms, spoken_text
from .storage import digest, file_hash, file_lock, inside, read_text, write_json

# The Gemini speech models on offer, the newest family only; switching or adding one is an entry
# here. Verified against OpenRouter's public models API (output_modalities=speech) on 2026-09-26:
# both offer the thirty voices below. The first is the default, and a choice saved without a
# model means it.
GEMINI_MODELS = {"google/gemini-3.8-flash-tts": "Gemini 3.8 Flash TTS",
                 "google/gemini-3.8-flash-lite-tts": "Gemini 3.8 Flash Lite TTS"}
GEMINI_MODEL = next(iter(GEMINI_MODELS))
SPEECH_ENDPOINT = "https://openrouter.ai/api/v1/audio/speech"
SPEECH_VERSION = "openrouter_gemini_tts.v1"
QWEN_VOICES = ("Aiden", "Vivian", "Ryan", "Serena", "Uncle_Fu", "Ono_Anna", "Sohee", "Eric", "Dylan")
# Verified against OpenRouter's public models API, supported_voices; see VOICES_VERIFIED_ON.
VOICES_VERIFIED_ON = "2026-09-26"
GEMINI_VOICES = ("Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome",
    "Algenib", "Rasalgethi", "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima",
    "Achird", "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat")


class PausePolicy(Contract):
    """Minimum silence at a transition, in milliseconds; a longer planned pause is kept."""
    same_speaker_ms: int = Field(default=250, ge=0, le=10000)
    speaker_change_ms: int = Field(default=450, ge=0, le=10000)
    chapter_break_ms: int = Field(default=900, ge=0, le=10000)


class AudioChoice(Contract):
    provider: Literal["qwen3_local", "openrouter_gemini_tts"] = "qwen3_local"
    voices: HostVoices = Field(
        default_factory=lambda: {"host_a": "Aiden", "host_b": "Vivian"})
    pauses: PausePolicy = Field(default_factory=PausePolicy)
    model: Literal[tuple(GEMINI_MODELS)] = GEMINI_MODEL
    # Inline audio tags placed by the text model before a Gemini recording (expression.py); Gemini only.
    expression: bool = False

    @model_validator(mode="after")
    def validate_voices(self):
        available = QWEN_VOICES if self.provider == "qwen3_local" else GEMINI_VOICES
        if (set(self.voices) != {"host_a", "host_b"} or len(set(self.voices.values())) != 2 or
                any(voice not in available for voice in self.voices.values())):
            raise ValueError("Zwei unterschiedliche Stimmen des gewählten Audioanbieters auswählen.")
        if self.provider == "qwen3_local":
            self.model = GEMINI_MODEL  # The speech model applies to Gemini only.
            self.expression = False
        return self

    @model_serializer(mode="wrap")
    def omit_default_model(self, handler):
        """A choice on the default model dumps as choices did before the model was selectable, so
        stored choices keep their hashes and the approvals made for them."""
        data = handler(self)
        if data.get("model") == GEMINI_MODEL:
            del data["model"]
        if not data.get("expression"):
            data.pop("expression", None)  # Choices without the expression layer hash as before it existed.
        return data

    @property
    def remote(self):
        return self.provider == "openrouter_gemini_tts"


def selected_audio(root, config):
    """The Studio's audio choice. A Gemini choice records expression unless it says otherwise: the user chose
    that every Gemini recording gets its expression layer automatically (2026-09-29). The workspace settings' choice
    holds for every project where they set one (studio_settings)."""
    data = studio_settings.section(root, "audio")
    if not isinstance(data, dict):
        path = root / "studio/audio.json"
        if not path.exists():
            return AudioChoice(voices=config.voice_profile)
        data = json.loads(path.read_text(encoding="utf-8"))
    data = dict(data) if isinstance(data, dict) else data
    if isinstance(data, dict) and data.get("provider") == "openrouter_gemini_tts":
        data.setdefault("expression", True)
    return AudioChoice.model_validate(data)


def audio_catalog():
    return {
        "qwen3_local": {"label": "Qwen · auf diesem Computer", "voices": QWEN_VOICES,
                        "defaults": {"host_a": "Aiden", "host_b": "Vivian"}},
        "openrouter_gemini_tts": {"label": "Gemini TTS · OpenRouter", "models": GEMINI_MODELS, "default_model": GEMINI_MODEL,
                        "voices": GEMINI_VOICES, "defaults": {"host_a": "Sadaltager", "host_b": "Aoede"}},
    }


def speech_settings(text, voice, language, spoken=None, model=GEMINI_MODEL):
    """``text`` is the reviewed script; ``spoken_text`` is what was sent, present only when it differs.

    The dict is the cache key and the stored record. A segment without a spoken form keeps the
    exact composition it had before spoken forms existed, so earlier cache entries stay valid.
    """
    return {"provider": "openrouter_gemini_tts", "model": model,
            "adapter_version": SPEECH_VERSION, "voice": voice, "language": language,
            "text": text, "format": "pcm", "sample_rate": 24000, "channels": 1, "sample_width": 2,
            **spoken_settings(text, spoken)}


def audio_generation_record(choice):
    """The stored form of a choice: a default pause policy is left out, so it hashes as before.

    ``same_audio_generation`` reads a record without ``pauses`` as today's defaults; a policy
    that differs from the defaults is stored and therefore changes the input hash.
    """
    data = choice.model_dump()
    if choice.pauses == PausePolicy():
        data.pop("pauses")
    return data


def same_audio_generation(stored, current):
    """A choice saved before the pause policy existed means today's default pauses."""
    if stored == current:
        return True
    try:
        # The expression layer adds tags, not another voice or model: a recording made without it stays current.
        first, second = (AudioChoice.model_validate(value).model_dump() for value in (stored, current))
        first.pop("expression", None)
        second.pop("expression", None)
        return first == second
    except (ValueError, TypeError):
        return False


def split_input(text, limit=6000):
    """A request-size guard, preserving every character and never inserting a speaker change."""
    parts = []
    while len(text) > limit:
        boundaries = list(re.finditer(r"[.!?][\s]+", text[:limit]))
        end = boundaries[-1].end() if boundaries else text.rfind(" ", 0, limit) + 1
        end = end if end > 0 else limit
        parts.append(text[:end])
        text = text[end:]
    if text:
        parts.append(text)
    return parts


def cached_audio(path, settings):
    metadata = path.with_suffix(".json")
    try:
        data = json.loads(metadata.read_text(encoding="utf-8"))
        return data if data["settings"] == settings and data["sha256"] == file_hash(path) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def evict(path):
    """Remove a cache entry, its record first, so a half-removed entry is never taken for a valid one."""
    path.with_suffix(".json").unlink(missing_ok=True)
    path.unlink(missing_ok=True)


# The health gate of every Gemini take (2026-10-02). A take of 80 characters or more is spoken at 8 to 25 characters
# per second, counted from its first to its last audible sound without the silences over a second; the read-only
# scan of 1,620 takes behind audio.SILENCE_LEVEL measured p1 12.9, median 16.6 and p99 20.4, and found one take of
# 1,248 characters cut to 0.37 s (Asimov ep_013 s3_09, exported unnoticed). A silence over 2.5 s needs a pause tag.
HEALTH_MIN_CHARACTERS = 80
HEALTH_RATE = (8.0, 25.0)
HEALTH_SILENCE_SECONDS = 2.5
SAMPLE_RATE = 24000
# The inline tags of the expression layer (expression.TAG) are performed, not read, so they are not characters.
SPOKEN_TAG = re.compile(r"<[^<>\n]{1,40}>")


def take_defect(pcm, heard):
    """Why a take of ``heard`` (24 kHz mono PCM) cannot be what the engine was asked to say; empty if plausible."""
    samples = array("h", pcm)
    total = len(samples)
    runs = silent_runs(samples, SAMPLE_RATE)
    if sum(length for _, length in runs) >= total:
        return "Die Aufnahme enthält keine hörbare Sprache."
    long = [length / SAMPLE_RATE for _, length in runs if length > HEALTH_SILENCE_SECONDS * SAMPLE_RATE]
    if len(long) > sum(heard.count(tag) for tag in PAUSE_TAGS):
        return f"Die Aufnahme enthält {max(long):.1f} s Stille, die kein Pausen-Tag erklärt."
    characters = len(" ".join(SPOKEN_TAG.sub(" ", heard).split()))
    if characters >= HEALTH_MIN_CHARACTERS:
        edges = sum(length for start, length in runs if start == 0 or start + length >= total)
        pauses = sum(length for start, length in runs
                     if 0 < start and start + length < total and length > SAMPLE_RATE)
        rate = characters * SAMPLE_RATE / (total - edges - pauses)
        if not HEALTH_RATE[0] <= rate <= HEALTH_RATE[1]:
            return (f"{characters} Zeichen in {total / SAMPLE_RATE:.2f} s Audio, also {rate:.1f} Zeichen pro Sekunde; "
                    f"plausibel sind {HEALTH_RATE[0]:g} bis {HEALTH_RATE[1]:g}.")
    return ""


def recorded_pcm(path):
    with wave.open(str(path), "rb") as stream:
        return stream.readframes(stream.getnframes())


# Rate-limit answers (429) one request waits out before its recording stops: pauses of about 5, 10, 20, 40, 60 and
# 60 seconds, or the provider's Retry-After, about three minutes in all.
RATE_LIMIT_RETRIES = 6
# A gateway or overload answer, a cut transfer or a reset connection is asked again twice, after 3 and 6 seconds:
# one 502 among about 1,800 requests used to stop a whole episode (2026-10-02).
TRANSIENT_RETRIES = 2
TRANSIENT_BACKOFF_SECONDS = 3.0
TRANSIENT_CODES = {502, 503, 504}


def transient(exc):
    reason = exc.reason if isinstance(exc, URLError) and not isinstance(exc, HTTPError) else exc
    if isinstance(reason, HTTPError):
        return reason.code in TRANSIENT_CODES
    return isinstance(reason, (IncompleteRead, ConnectionResetError))


def throttle_path(cache):
    return cache / "throttle.json"


def shared_throttle(root):
    """One throttle for every project in the Studio's projects folder, like its limit on parallel recordings: a 429
    in one project slows the recordings of all of them (2026-10-02)."""
    return Path(root).parent / ".gemini_throttle.json"


def wait_for_throttle(cache, *, path=None):
    """Wait until the pause a rate limit set has passed. It is shared through ``path`` (the cache folder's own file
    when none is given), so every parallel recording slows down together instead of each pressing on (the user's
    choice, 2026-09-29: start every approved episode at once and throttle on 429)."""
    try:
        until = float(json.loads(read_text(path or throttle_path(cache))).get("until", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return
    delay = until - time.time()
    if delay > 0:
        time.sleep(min(delay, 120))


def throttle(cache, retry_after, attempt, *, path=None):
    """After a 429: every recording waits the provider's Retry-After, else a pause that doubles with each attempt.
    The update holds a lock, so two recordings throttling at once never shorten each other's pause."""
    try:
        seconds = float(retry_after)
    except (TypeError, ValueError):
        seconds = min(60.0, 5.0 * 2 ** (attempt - 1))
    seconds = max(1.0, min(seconds, 120.0))
    path = path or throttle_path(cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_suffix(".lock"), timeout=30):
        try:
            current = float(json.loads(read_text(path)).get("until", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            current = 0.0
        write_json(path, {"until": max(current, time.time() + seconds), "attempt": attempt})


class GeminiSpeech:
    def __init__(self, api_key=None, *, timeout=600, model=GEMINI_MODEL, throttle_file=None):
        """``throttle_file`` is the shared rate-limit file (``shared_throttle``); without it each cache folder has one."""
        key = (api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")).strip()
        if key and (len(key) > 512 or any(not 33 <= ord(c) <= 126 for c in key)):
            raise AppError("Ungültiger OpenRouter-Key.", code="invalid_key", status="blocked")
        if model not in GEMINI_MODELS:
            raise AppError("Unbekanntes Gemini-Sprachmodell.", code="invalid_speech", status="blocked")
        self._key = SecretStr(key)
        self.timeout = timeout
        self.model = model
        self.throttle_file = throttle_file

    def require_key(self):
        if not self._key.get_secret_value():
            raise AppError("Für Gemini-Audio den OpenRouter-Key im Studio hinterlegen.",
                           code="openrouter_key_required", status="blocked")

    def synthesize(self, text, voice, language, cache, spoken=None):
        """``text`` is the script and names the cache entry; ``spoken`` is what the engine hears."""
        heard = spoken if spoken is not None else text
        if voice not in GEMINI_VOICES or language not in {"de-DE", "en-US"} or not text.strip() or not heard.strip():
            raise AppError("Ungültiger Text, Sprache oder Gemini-Stimme.", code="invalid_speech")
        secret = self._key.get_secret_value()
        if secret and (secret in text or secret in heard):
            raise AppError("Der API-Key darf nicht im gesprochenen Text stehen.", code="credential_in_prompt", status="blocked")
        settings = speech_settings(text, voice, language, spoken, self.model)
        path = cache / (digest(settings) + ".wav")
        cached = cached_audio(path, settings)
        # A take joined from request-sized parts was checked part by part when it was made.
        if cached and (cached.get("parts") or not take_defect(recorded_pcm(path), heard)):
            return path
        if cached:
            # A cached take that fails the health gate is never replayed; it is recorded anew (2026-10-02).
            evict(path)
        parts = split_input(heard)
        if len(parts) > 1:
            children = [self.synthesize(part, voice, language, cache) for part in parts]
            cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=cache) as temporary:
                combined = Path(temporary) / "audio.wav"
                with wave.open(str(combined), "wb") as output:
                    output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                    for child in children:
                        with wave.open(str(child), "rb") as stream:
                            while data := stream.readframes(24000):
                                output.writeframesraw(data)
                combined.replace(path)
            write_json(path.with_suffix(".json"), {"settings": settings, "sha256": file_hash(path),
                "parts": [child.name for child in children], "speech_quality_verified": False})
            return path
        self.require_key()
        started = time.monotonic()
        # The health gate: an implausible take is asked for once more, then the recording stops (take_defect).
        pcm, generation = self.request(heard, voice, cache)
        if defect := take_defect(pcm, heard):
            pcm, generation = self.request(heard, voice, cache)
            if again := take_defect(pcm, heard):
                raise AppError(f"Gemini lieferte zweimal eine unplausible Aufnahme. Zuerst: {defect} Dann: {again} "
                               "Fertige Abschnitte bleiben gespeichert; Fortsetzen nimmt diesen Abschnitt neu auf.",
                               code="invalid_speech", status="blocked")
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            wav = Path(temporary) / "audio.wav"
            with wave.open(str(wav), "wb") as output:
                output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                output.writeframes(pcm)
            wav.replace(path)
        write_json(path.with_suffix(".json"), {"settings": settings, "sha256": file_hash(path),
            "generation_id": generation if re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", generation) and secret not in generation else None,
            "elapsed_seconds": round(time.monotonic() - started, 3), "speech_quality_verified": False})
        return path

    def request(self, heard, voice, cache):
        """One take of ``heard``: its checked PCM and the provider's generation id."""
        secret = self._key.get_secret_value()
        payload = {"model": self.model, "input": heard, "voice": voice, "response_format": "pcm"}
        request = Request(SPEECH_ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json",
                     "X-OpenRouter-Title": "Podcast Automate"}, method="POST")
        attempt, retried, failure = 0, 0, None
        while True:
            # A rate limit one recording met makes every parallel recording wait (throttle).
            wait_for_throttle(cache, path=self.throttle_file)
            try:
                with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                    if response.status != 200:
                        raise api_failure(response.status)
                    content_type = response.headers.get("Content-Type", "").lower()
                    if content_type.split(";")[0].strip() not in {"audio/pcm", "audio/l16"}:
                        raise AppError("OpenRouter lieferte kein PCM-Audio. Es wurde kein Abschnitt gespeichert.",
                                       code="invalid_audio", status="blocked")
                    rate = re.search(r"rate\s*=\s*(\d+)", content_type)
                    if rate and rate[1] != "24000":
                        raise AppError("Unerwartete Abtastrate von OpenRouter.", code="invalid_audio")
                    maximum = 32 * 1024 * 1024
                    pcm = response.read(maximum + 1)
                    declared_length = response.headers.get("Content-Length")
                    if declared_length is not None and (not declared_length.isdigit() or int(declared_length) != len(pcm)):
                        raise AppError("Gemini-Audio wurde unvollständig übertragen.", code="invalid_audio", status="blocked")
                    generation = response.headers.get("X-Generation-Id", "")
                break
            except HTTPError as exc:
                if exc.code == 429 and attempt < RATE_LIMIT_RETRIES:
                    attempt += 1
                    throttle(cache, exc.headers.get("Retry-After") if exc.headers else None, attempt,
                             path=self.throttle_file)
                    exc.close()
                    continue
                if transient(exc) and retried < TRANSIENT_RETRIES:
                    retried += 1
                    exc.close()
                    time.sleep(TRANSIENT_BACKOFF_SECONDS * retried)
                    continue
                failure = exc
                break
            except (TimeoutError, URLError, OSError, HTTPException) as exc:
                if transient(exc) and retried < TRANSIENT_RETRIES:
                    retried += 1
                    time.sleep(TRANSIENT_BACKOFF_SECONDS * retried)
                    continue
                raise AppError("Gemini-Audioverbindung unterbrochen. Fertige Abschnitte bleiben gespeichert; später fortsetzen.",
                               code="openrouter_connection", status="blocked") from None
        if failure is not None:
            exc = failure
            code = exc.code
            # Read only to classify, never echoed: a provider's message may carry anything.
            try:
                detail = exc.read(8192).decode("utf-8", "replace").lower()
            except (OSError, ValueError):
                detail = ""
            exc.close()
            if code == 404 and ("zdr" in detail or "data policy" in detail):
                # An account that allows only zero-data-retention endpoints excludes Google's speech endpoint
                # (2026-09-29: "0 endpoints ... ZDR violation (account settings)").
                raise AppError("OpenRouter schließt das Gemini-Sprachmodell wegen der Datenschutz-Einstellung deines Kontos aus: "
                               "Es erlaubt nur Anbieter ohne Datenspeicherung (Zero Data Retention), und Googles Sprachmodell "
                               "gehört nicht dazu. Unter openrouter.ai/settings/privacy freigeben, dann fortsetzen.",
                               code="openrouter_privacy", status="blocked") from None
            if code in {400, 404, 413, 422}:
                raise AppError("Gemini-TTS-Anfrage abgewiesen. Verfügbarkeit des Modells, Stimme und Textlänge prüfen.",
                               code="openrouter_speech_request", status="blocked") from None
            raise api_failure(code) from None
        if not pcm or len(pcm) % 2 or len(pcm) > maximum or not any(pcm):
            raise AppError("OpenRouter lieferte leeres oder ungültiges Audio.", code="invalid_audio", status="blocked")
        # Never persist an accidentally echoed credential, including a mislabeled error body.
        error_body = False
        try:
            error_body = isinstance(json.loads(pcm), (dict, list))
        except (ValueError, UnicodeDecodeError):
            pass
        if secret.encode() in pcm or error_body or pcm.startswith((b"<html", b"<!DOCTYPE")):
            raise AppError("Unerwartete Antwort statt Audio.", code="invalid_audio", status="blocked")
        return pcm, generation


def run_gemini_tts(config, script, root, work, choice, api_key=None, *, table=None, overrides=None, expression=None,
                   engine=None):
    """``expression`` maps a segment id to its spoken text with inline tags (expression.plan_expression); ``engine``
    is the adapter to record with, a ``parallel_speech.LockedGeminiSpeech`` when episodes record in parallel."""
    engine = engine or GeminiSpeech(api_key, timeout=config.runtime.tts_timeout_seconds, model=choice.model,
                                    throttle_file=shared_throttle(root))
    table = table if table is not None else SpokenForms()
    rows, paths = [], []
    for segment in script.segments:
        write_json(work / "tts_progress.json", {"completed_segments": len(rows),
            "total_segments": len(script.segments), "current_segment": segment.segment_id})
        voice = choice.voices[segment.speaker_id]
        spoken = (expression or {}).get(segment.segment_id) or spoken_text(segment, table, overrides)
        try:
            path = engine.synthesize(segment.text, voice, config.language, root / "cache/audio/gemini", spoken=spoken)
        except AppError as exc:
            if exc.code != "invalid_speech":
                raise
            # Named by segment, so the listener knows which passage to check (docs/AUDIO.md).
            raise AppError(f"Abschnitt {segment.segment_id}: {exc}", code=exc.code, status=exc.status,
                           details={**exc.details, "segment_id": segment.segment_id}) from exc
        paths.append(path)
        rows.append({"segment_id": segment.segment_id, "path": path.relative_to(root / "cache/audio").as_posix(),
            "sha256": file_hash(path), "settings": speech_settings(segment.text, voice, config.language, spoken, choice.model)})
    write_json(work / "tts_report.json", {"segments": rows})
    write_json(work / "tts_progress.json", {"completed_segments": len(rows), "total_segments": len(rows)})
    return paths


def check_gemini_rows(root, script, report, choice, language, *, table=None, overrides=None, expression=None):
    rows = report.get("segments", [])
    table = table if table is not None else SpokenForms()
    if [row.get("segment_id") for row in rows] != [s.segment_id for s in script.segments]:
        raise AppError("Gemini-Audio passt nicht zum Skript.", code="invalid_audio")
    paths = []
    for row, segment in zip(rows, script.segments, strict=True):
        path = inside(root / "cache/audio", row["path"])
        spoken = (expression or {}).get(segment.segment_id) or spoken_text(segment, table, overrides)
        expected = speech_settings(segment.text, choice.voices[segment.speaker_id], language, spoken, choice.model)
        if row.get("settings") != expected or file_hash(path) != row.get("sha256"):
            raise AppError("Gemini-Text, Stimme oder Audiodatei geändert.", code="invalid_audio")
        paths.append(path)
    return paths
