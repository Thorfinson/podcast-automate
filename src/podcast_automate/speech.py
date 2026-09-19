"""Independent audio choices and OpenRouter's binary Gemini TTS endpoint."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import wave
from http.client import HTTPException
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from pydantic import Field, SecretStr, model_validator

from .errors import AppError
from .models import Contract, HostVoices
from .openrouter import NoRedirect, api_failure
from .qwen_worker import spoken_settings
from .spoken_forms import SpokenForms, spoken_text
from .storage import digest, file_hash, inside, write_json

GEMINI_MODEL = "google/gemini-3.1-flash-tts-preview"
SPEECH_ENDPOINT = "https://openrouter.ai/api/v1/audio/speech"
SPEECH_VERSION = "openrouter_gemini_tts.v1"
QWEN_VOICES = ("Aiden", "Vivian", "Ryan", "Serena", "Uncle_Fu", "Ono_Anna", "Sohee", "Eric", "Dylan")
# Verified against OpenRouter's public models API, supported_voices; see VOICES_VERIFIED_ON.
VOICES_VERIFIED_ON = "2026-09-13"
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

    @model_validator(mode="after")
    def validate_voices(self):
        available = QWEN_VOICES if self.provider == "qwen3_local" else GEMINI_VOICES
        if (set(self.voices) != {"host_a", "host_b"} or len(set(self.voices.values())) != 2 or
                any(voice not in available for voice in self.voices.values())):
            raise ValueError("Zwei unterschiedliche Stimmen des gewählten Audioanbieters auswählen.")
        return self

    @property
    def remote(self):
        return self.provider == "openrouter_gemini_tts"


def selected_audio(root, config):
    path = root / "studio/audio.json"
    return (AudioChoice.model_validate_json(path.read_text(encoding="utf-8")) if path.exists()
            else AudioChoice(voices=config.voice_profile))


def audio_catalog():
    return {
        "qwen3_local": {"label": "Qwen · auf diesem Computer", "voices": QWEN_VOICES,
                        "defaults": {"host_a": "Aiden", "host_b": "Vivian"}},
        "openrouter_gemini_tts": {"label": "Gemini 3.1 Flash TTS · OpenRouter", "model": GEMINI_MODEL,
                        "voices": GEMINI_VOICES, "defaults": {"host_a": "Sadaltager", "host_b": "Aoede"}},
    }


def speech_settings(text, voice, language, spoken=None):
    """``text`` is the reviewed script; ``spoken_text`` is what was sent, present only when it differs.

    The dict is the cache key and the stored record. A segment without a spoken form keeps the
    exact composition it had before spoken forms existed, so earlier cache entries stay valid.
    """
    return {"provider": "openrouter_gemini_tts", "model": GEMINI_MODEL,
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
        return AudioChoice.model_validate(stored).model_dump() == AudioChoice.model_validate(current).model_dump()
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


class GeminiSpeech:
    def __init__(self, api_key=None, *, timeout=600):
        key = (api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")).strip()
        if key and (len(key) > 512 or any(not 33 <= ord(c) <= 126 for c in key)):
            raise AppError("Ungültiger OpenRouter-Key.", code="invalid_key", status="blocked")
        self._key = SecretStr(key)
        self.timeout = timeout

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
        settings = speech_settings(text, voice, language, spoken)
        path = cache / (digest(settings) + ".wav")
        if cached_audio(path, settings):
            return path
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
        payload = {"model": GEMINI_MODEL, "input": heard, "voice": voice, "response_format": "pcm"}
        request = Request(SPEECH_ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json",
                     "X-OpenRouter-Title": "Podcast Automate"}, method="POST")
        started = time.monotonic()
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
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code in {400, 404, 413, 422}:
                raise AppError("Gemini-TTS-Anfrage abgewiesen. Verfügbarkeit des Modells, Stimme und Textlänge prüfen.",
                               code="openrouter_speech_request", status="blocked") from None
            raise api_failure(code) from None
        except (TimeoutError, URLError, OSError, HTTPException):
            raise AppError("Gemini-Audioverbindung unterbrochen. Fertige Abschnitte bleiben gespeichert; später fortsetzen.",
                           code="openrouter_connection", status="blocked") from None
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


def run_gemini_tts(config, script, root, work, choice, api_key=None, *, table=None, overrides=None):
    engine = GeminiSpeech(api_key, timeout=config.runtime.tts_timeout_seconds)
    table = table if table is not None else SpokenForms()
    rows, paths = [], []
    for segment in script.segments:
        write_json(work / "tts_progress.json", {"completed_segments": len(rows),
            "total_segments": len(script.segments), "current_segment": segment.segment_id})
        voice = choice.voices[segment.speaker_id]
        spoken = spoken_text(segment, table, overrides)
        path = engine.synthesize(segment.text, voice, config.language, root / "cache/audio/gemini", spoken=spoken)
        paths.append(path)
        rows.append({"segment_id": segment.segment_id, "path": path.relative_to(root / "cache/audio").as_posix(),
            "sha256": file_hash(path), "settings": speech_settings(segment.text, voice, config.language, spoken)})
    write_json(work / "tts_report.json", {"segments": rows})
    write_json(work / "tts_progress.json", {"completed_segments": len(rows), "total_segments": len(rows)})
    return paths


def check_gemini_rows(root, script, report, choice, language, *, table=None, overrides=None):
    rows = report.get("segments", [])
    table = table if table is not None else SpokenForms()
    if [row.get("segment_id") for row in rows] != [s.segment_id for s in script.segments]:
        raise AppError("Gemini-Audio passt nicht zum Skript.", code="invalid_audio")
    paths = []
    for row, segment in zip(rows, script.segments, strict=True):
        path = inside(root / "cache/audio", row["path"])
        expected = speech_settings(segment.text, choice.voices[segment.speaker_id], language,
                                   spoken_text(segment, table, overrides))
        if row.get("settings") != expected or file_hash(path) != row.get("sha256"):
            raise AppError("Gemini-Text, Stimme oder Audiodatei geändert.", code="invalid_audio")
        paths.append(path)
    return paths
