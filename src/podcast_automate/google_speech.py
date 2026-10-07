"""Gemini speech straight from Google's Gemini API, both hosts in one request (D-133, D-134).

The listening rounds of 2026-10-06 (.studio/hoerproben) chose this over OpenRouter's speech, which records every
segment on its own: a stretch of dialogue goes to Google in one request and Gemini speaks both roles in turn, each with
a short style, and the listener's reactions the expression layer placed (``|mhm|``) are spoken by the other host. Such
a stretch is a passage: consecutive segments of one chapter and scene, at most MAX_PASSAGE_CHARACTERS of script text and
MAX_PASSAGE_TURNS segments, cut on the script text alone, so a spoken form or a tag never moves a boundary. Chapter
marks stay measured, and a failure costs one passage. Request and answer were verified live on 2026-10-06 with
gemini-3.8-flash-tts; a 3.7-minute passage of 20 turns came back in one answer.
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import tempfile
import time
import wave
from http.client import HTTPException, IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from pydantic import SecretStr

from .errors import AppError
from .expression import BACKCHANNEL
from .openrouter import NoRedirect, refusal_reason
from .qwen_worker import spoken_settings
from .speech import (DEFAULT_STYLES, GEMINI_MODEL, GEMINI_MODELS, GEMINI_VOICES, cached_audio, evict, recorded_pcm,
                     shared_throttle, take_defect, throttle, wait_for_throttle)
from .spoken_forms import SpokenForms, spoken_text
from .storage import digest, file_hash, inside, write_json

GOOGLE_SPEECH_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
GOOGLE_SPEECH_VERSION = "google_gemini_tts.v1"
# Each role's short style comes from the audio choice (speech.RoleStyles, presets in speech.STYLE_PRESETS), with
# "unhurried" added where the project's language asks for a calm pace (AudioChoice.spoken_styles).
# The names the request gives the two speakers; never spoken. Gender-neutral, so a name never hints at who explains.
SPEAKER_NAMES = {"host_a": "Alex", "host_b": "Robin"}
MAX_PASSAGE_CHARACTERS = 3600
MAX_PASSAGE_TURNS = 20
PASSAGE_RULE = {"version": "dialogue_passages.v1", "max_characters": MAX_PASSAGE_CHARACTERS,
                "max_turns": MAX_PASSAGE_TURNS}
# A passage of 3.7 minutes answers with about 15 MB of JSON (base64 WAV).
RESPONSE_LIMIT = 64 * 1024 * 1024
# A 429 waits Google's retryDelay (else a doubling pause), shared by every parallel recording, about eight times.
RATE_LIMIT_RETRIES = 8
TRANSIENT_RETRIES = 2
TRANSIENT_BACKOFF_SECONDS = 3.0
TRANSIENT_CODES = {500, 502, 503, 504}
CACHE = "cache/audio/google"


def google_model(model):
    """Google's id of a speech model from GEMINI_MODELS: the same name without OpenRouter's ``google/``."""
    return model.removeprefix("google/")


def plan_passages(segments):
    """The passages of ``segments`` as lists of their indices. A passage never crosses a chapter or scene. Each run of
    one scene is cut into as few passages as the bounds allow, at the segment boundaries nearest equal shares of its
    text, so no short remnant is left; a single segment over the bounds is a passage of its own."""
    groups, current = [], []
    for index, segment in enumerate(segments):
        if current and (segments[current[-1]].chapter_id, segments[current[-1]].scene_id) != (
                segment.chapter_id, segment.scene_id):
            groups.append(current)
            current = []
        current.append(index)
    if current:
        groups.append(current)
    passages = []
    for group in groups:
        sizes = [len(segments[index].text) for index in group]
        count = max(1, math.ceil(sum(sizes) / MAX_PASSAGE_CHARACTERS), math.ceil(len(group) / MAX_PASSAGE_TURNS))
        while True:
            cuts = balanced_cuts(sizes, count)
            parts = [group[start:end] for start, end in zip([0, *cuts], [*cuts, len(group)])]
            if count >= len(group) or all(len(part) == 1 or (
                    len(part) <= MAX_PASSAGE_TURNS
                    and sum(len(segments[index].text) for index in part) <= MAX_PASSAGE_CHARACTERS) for part in parts):
                break
            count += 1
        passages.extend(parts)
    return passages


def balanced_cuts(sizes, count):
    """``count - 1`` increasing cut positions into ``sizes``, each at the boundary nearest its equal share."""
    total, cumulative = sum(sizes), []
    running = 0
    for size in sizes[:-1]:
        running += size
        cumulative.append(running)
    cuts, previous = [], 0
    for number in range(1, min(count, len(sizes))):
        target = total * number / count
        # Leave room for the cuts still to come: each later passage keeps at least one segment.
        lowest, highest = previous + 1, len(sizes) - (min(count, len(sizes)) - number)
        position = min(range(lowest, highest + 1), key=lambda cut: (abs(cumulative[cut - 1] - target), cut))
        cuts.append(position)
        previous = position
    return cuts


def passage_turns(segments, spoken):
    """The turns of a passage as ``(speaker_id, heard text)``: consecutive segments of one host joined."""
    turns = []
    for segment in segments:
        heard = " ".join(spoken[segment.segment_id].split())
        if turns and turns[-1][0] == segment.speaker_id:
            turns[-1] = (segment.speaker_id, turns[-1][1] + " " + heard)
        else:
            turns.append((segment.speaker_id, heard))
    return turns


def passage_settings(segments, spoken, voices, language, model=GEMINI_MODEL, styles=None):
    """The cache key and stored record of one passage: its script text, the spoken form where it differs (the same rule
    as every engine, D-087), the voices, styles and names of the hosts who speak in it, and the request format."""
    roles = sorted({segment.speaker_id for segment in segments})
    styles = styles if styles is not None else DEFAULT_STYLES
    return {"provider": "google_gemini_tts", "model": model, "adapter_version": GOOGLE_SPEECH_VERSION,
            "language": language, "voices": {role: voices[role] for role in roles},
            "styles": {role: styles[role] for role in roles},
            "names": {role: SPEAKER_NAMES[role] for role in roles},
            "segments": [{"speaker_id": segment.speaker_id, "text": segment.text,
                          **spoken_settings(segment.text, spoken[segment.segment_id])} for segment in segments],
            "format": "pcm", "sample_rate": 24000, "channels": 1, "sample_width": 2}


def request_body(turns, voices, model, styles=None):
    """The interactions request of one passage: one item per turn with its speaker and style; Google's conversational
    mode when both hosts speak, else the one voice. A listener reaction needs the other host, so a passage of one host
    speaks without its reactions."""
    styles = styles if styles is not None else DEFAULT_STYLES
    roles = list(dict.fromkeys(role for role, _ in turns))
    two = len(roles) > 1
    content = []
    for role, text in turns:
        metadata = {**({"speaker": SPEAKER_NAMES[role]} if two else {}),
                    **({"style": styles[role]} if styles[role] else {})}
        content.append({"type": "text", "text": text if two else " ".join(BACKCHANNEL.sub(" ", text).split()),
                        **({"annotations": [{"type": "speech_metadata", **metadata}]} if metadata else {})})
    speech = ({"mode": "conversational", "speakers": [{"speaker": SPEAKER_NAMES[role], "voice": voices[role]}
                                                      for role in roles]}
              if two else [{"voice": voices[roles[0]]}])
    return {"model": google_model(model), "input": [{"type": "user_input", "content": content}],
            "response_format": {"type": "audio"}, "generation_config": {"speech_config": speech}}


def answer_pcm(data):
    """The 24 kHz mono 16-bit PCM of an answer: the last audio item of its model output, a base64 WAV."""
    audio = [item for step in (data.get("steps") or []) if isinstance(step, dict) and step.get("type") == "model_output"
             for item in (step.get("content") or []) if isinstance(item, dict) and item.get("type") == "audio"]
    if not audio or not isinstance(audio[-1].get("data"), str):
        raise AppError("Google lieferte keine Audiodaten, nur Text oder eine leere Antwort. Es wurde nichts gespeichert.",
                       code="invalid_audio", status="blocked")
    try:
        raw = base64.b64decode(audio[-1]["data"], validate=True)
    except ValueError:
        raise AppError("Google lieferte unlesbare Audiodaten.", code="invalid_audio", status="blocked") from None
    if raw[:4] != b"RIFF":
        # Headerless 16-bit PCM is accepted only when Google names its rate.
        if not re.search(r"rate=24000", str(audio[-1].get("mime_type") or audio[-1].get("mimeType") or "")):
            raise AppError("Google lieferte Audio in einem unerwarteten Format.", code="invalid_audio", status="blocked")
        return raw
    try:
        with wave.open(io.BytesIO(raw), "rb") as stream:
            if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) != (1, 2, 24000):
                raise AppError("Google lieferte Audio in einem unerwarteten Format (erwartet 24 kHz, mono, 16 Bit).",
                               code="invalid_audio", status="blocked")
            return stream.readframes(stream.getnframes())
    except (wave.Error, EOFError):
        raise AppError("Google lieferte eine beschädigte WAV-Datei.", code="invalid_audio", status="blocked") from None


def retry_delay(body):
    """Google's RetryInfo.retryDelay of a 429 in seconds ("27s"), or None."""
    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body or "")
    return float(match[1]) if match else None


def daily_quota(body):
    """Whether a 429 is a per-day quota (QuotaFailure's quotaId), which waiting minutes cannot lift."""
    return bool(re.search(r'"quotaId"\s*:\s*"[^"]*PerDay', body or ""))


class GoogleSpeech:
    def __init__(self, api_key=None, *, timeout=600, model=GEMINI_MODEL, throttle_file=None):
        """``throttle_file`` is the rate-limit file every recording of the workspace shares (speech.shared_throttle)."""
        key = (api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")).strip()
        if key and (len(key) > 512 or any(not 33 <= ord(c) <= 126 for c in key)):
            raise AppError("Ungültiger Google-Key.", code="invalid_google_key", status="blocked")
        if model not in GEMINI_MODELS:
            raise AppError("Unbekanntes Gemini-Sprachmodell.", code="invalid_speech", status="blocked")
        self._key = SecretStr(key)
        self.timeout = timeout
        self.model = model
        self.throttle_file = throttle_file

    def require_key(self):
        if not self._key.get_secret_value():
            raise AppError("Für Gemini-Audio den Google-Key im Studio hinterlegen.", code="google_key_required",
                           status="blocked")

    def synthesize(self, segments, spoken, voices, language, cache, styles=None):
        """One passage of ``segments``, as ``spoken`` maps them, in ``styles`` (speech.RoleStyles as a dict, the
        default styles when None); returns its cached WAV."""
        styles = styles if styles is not None else DEFAULT_STYLES
        if (not segments or language not in {"de-DE", "en-US"}
                or any(voices.get(segment.speaker_id) not in GEMINI_VOICES or not segment.text.strip()
                       or not spoken[segment.segment_id].strip() for segment in segments)):
            raise AppError("Ungültiger Text, Sprache oder Gemini-Stimme.", code="invalid_speech")
        turns = passage_turns(segments, spoken)
        secret = self._key.get_secret_value()
        heard = " ".join(text for _, text in turns)
        if secret and (secret in heard or any(secret in segment.text for segment in segments)):
            raise AppError("Der API-Key darf nicht im gesprochenen Text stehen.", code="credential_in_prompt",
                           status="blocked")
        settings = passage_settings(segments, spoken, voices, language, self.model, styles)
        path = cache / (digest(settings) + ".wav")
        if cached_audio(path, settings):
            if not take_defect(recorded_pcm(path), heard):
                return path
            # A cached take that fails the health gate is never replayed (speech.py, 2026-10-02).
            evict(path)
        self.require_key()
        started = time.monotonic()
        body = request_body(turns, voices, self.model, styles)
        pcm = self.request(body, cache)
        if defect := take_defect(pcm, heard):
            pcm = self.request(body, cache)
            if again := take_defect(pcm, heard):
                raise AppError(f"Gemini lieferte zweimal eine unplausible Aufnahme. Zuerst: {defect} Dann: {again} "
                               "Fertige Passagen bleiben gespeichert; Fortsetzen nimmt diese Passage neu auf.",
                               code="invalid_speech", status="blocked")
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            wav = Path(temporary) / "audio.wav"
            with wave.open(str(wav), "wb") as output:
                output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                output.writeframes(pcm)
            wav.replace(path)
        write_json(path.with_suffix(".json"), {"settings": settings, "sha256": file_hash(path),
            "elapsed_seconds": round(time.monotonic() - started, 3), "speech_quality_verified": False})
        return path

    def request(self, body, cache):
        """One take: the checked PCM of Google's answer. The key goes only in the header, never in the URL."""
        secret = self._key.get_secret_value()
        request = Request(GOOGLE_SPEECH_ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"x-goog-api-key": secret, "Content-Type": "application/json"}, method="POST")
        attempt = retried = 0
        while True:
            wait_for_throttle(cache, path=self.throttle_file)
            try:
                with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                    raw = response.read(RESPONSE_LIMIT + 1)
                break
            except HTTPError as exc:
                try:
                    text = exc.read(65536).decode("utf-8", "replace")
                except (OSError, ValueError):
                    text = ""
                exc.close()
                if exc.code == 429 and not daily_quota(text) and attempt < RATE_LIMIT_RETRIES:
                    attempt += 1
                    delay = retry_delay(text)
                    throttle(cache, delay if delay is not None else (exc.headers.get("Retry-After") if exc.headers
                                                                     else None), attempt, path=self.throttle_file)
                    continue
                if exc.code in TRANSIENT_CODES and retried < TRANSIENT_RETRIES:
                    retried += 1
                    time.sleep(TRANSIENT_BACKOFF_SECONDS * retried)
                    continue
                raise google_failure(exc.code, text, secret) from None
            except (TimeoutError, URLError, OSError, HTTPException) as exc:
                reason = exc.reason if isinstance(exc, URLError) else exc
                if isinstance(reason, (IncompleteRead, ConnectionResetError)) and retried < TRANSIENT_RETRIES:
                    retried += 1
                    time.sleep(TRANSIENT_BACKOFF_SECONDS * retried)
                    continue
                raise AppError("Verbindung zu Google unterbrochen. Fertige Passagen bleiben gespeichert; später "
                               "fortsetzen.", code="google_connection", status="blocked") from None
        if len(raw) > RESPONSE_LIMIT or (secret and secret.encode() in raw):
            raise AppError("Unerwartete Antwort von Google statt Audio.", code="invalid_audio", status="blocked")
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise AppError("Google lieferte keine lesbare Antwort.", code="invalid_audio", status="blocked") from None
        pcm = answer_pcm(data if isinstance(data, dict) else {})
        if not pcm or len(pcm) % 2 or not any(pcm):
            raise AppError("Google lieferte leeres oder ungültiges Audio.", code="invalid_audio", status="blocked")
        return pcm


def google_failure(code, body, secret):
    """The error of a refused request. Google's short message is kept, without credentials (refusal_reason): it names
    the voice, model or quota at fault. A wrong key comes back as 400 API_KEY_INVALID, not 401."""
    reason = refusal_reason(body, (secret,))
    details = {"provider_message": reason} if reason else {}
    suffix = f": {reason}" if reason else ""
    if code in {401, 403} or "API_KEY_INVALID" in (body or ""):
        return AppError("Google hat den Key nicht angenommen" + suffix + ". Einen gültigen Google-Key hinterlegen; "
                        "fertige Passagen bleiben gespeichert.", code="google_authentication", status="blocked",
                        details=details)
    if code == 429:
        return AppError("Das Google-Kontingent für Gemini-Audio ist erschöpft" + suffix + ". Später fortsetzen; "
                        "fertige Passagen bleiben gespeichert.", code="google_quota", status="waiting_for_quota",
                        details=details)
    if code in {400, 404, 413, 422}:
        return AppError("Google hat die Gemini-Audioanfrage abgewiesen" + suffix + ". Modell, Stimme und Textlänge "
                        "prüfen.", code="google_speech_request", status="blocked", details=details)
    return AppError("Google ist vorübergehend nicht erreichbar. Später fortsetzen; fertige Passagen bleiben gespeichert.",
                    code="google_unavailable", status="blocked")


def heard_texts(script, table=None, overrides=None, expression=None):
    """What each segment is spoken as: its tagged text from the expression layer, else its spoken form."""
    table = table if table is not None else SpokenForms()
    return {segment.segment_id: (expression or {}).get(segment.segment_id) or spoken_text(segment, table, overrides)
            for segment in script.segments}


def run_google_tts(config, script, root, work, choice, api_key=None, *, table=None, overrides=None, expression=None,
                   engine=None):
    """Record ``script`` passage by passage; ``choice.voices`` are this episode's (AudioChoice.for_episode).
    ``engine`` is a ``parallel_speech.LockedGoogleSpeech`` when episodes record in parallel."""
    engine = engine or GoogleSpeech(api_key, timeout=config.runtime.tts_timeout_seconds, model=choice.model,
                                    throttle_file=shared_throttle(root))
    spoken = heard_texts(script, table, overrides, expression)
    styles = choice.spoken_styles(config.language)
    passages = plan_passages(script.segments)
    rows, paths, done = [], [], 0
    for number, indices in enumerate(passages, 1):
        segments = [script.segments[index] for index in indices]
        write_json(work / "tts_progress.json", {"completed_segments": done, "total_segments": len(script.segments),
            "current_segment": segments[0].segment_id, "current_passage": number, "total_passages": len(passages)})
        try:
            path = engine.synthesize(segments, spoken, choice.voices, config.language, root / CACHE, styles)
        except AppError as exc:
            if exc.code != "invalid_speech":
                raise
            # Named by its segments, so the listener knows which stretch to check (docs/AUDIO.md).
            span = segments[0].segment_id + (f" bis {segments[-1].segment_id}" if len(segments) > 1 else "")
            raise AppError(f"Passage {span}: {exc}", code=exc.code, status=exc.status,
                           details={**exc.details, "segment_ids": [s.segment_id for s in segments]}) from exc
        paths.append(path)
        rows.append({"passage": number, "segment_ids": [segment.segment_id for segment in segments],
                     "path": path.relative_to(root / "cache/audio").as_posix(), "sha256": file_hash(path),
                     "settings": passage_settings(segments, spoken, choice.voices, config.language, choice.model,
                                                  styles)})
        done += len(segments)
    write_json(work / "tts_report.json", {"passages": rows})
    write_json(work / "tts_progress.json", {"completed_segments": done, "total_segments": done})
    return paths


def check_google_rows(root, script, report, choice, language, *, table=None, overrides=None, expression=None):
    """The passage recordings of ``report`` if they are exactly what ``script`` asks for today, else invalid_audio."""
    rows = report.get("passages", [])
    passages = plan_passages(script.segments)
    if [row.get("segment_ids") for row in rows] != [[script.segments[i].segment_id for i in p] for p in passages]:
        raise AppError("Gemini-Audio passt nicht zum Skript.", code="invalid_audio")
    spoken = heard_texts(script, table, overrides, expression)
    paths = []
    for row, indices in zip(rows, passages, strict=True):
        path = inside(root / "cache/audio", row["path"])
        segments = [script.segments[index] for index in indices]
        expected = passage_settings(segments, spoken, choice.voices, language, choice.model,
                                    choice.spoken_styles(language))
        if row.get("settings") != expected or not path.is_file() or file_hash(path) != row.get("sha256"):
            raise AppError("Gemini-Text, Stimme oder Audiodatei geändert.", code="invalid_audio")
        paths.append(path)
    return paths


def passage_units(report):
    """The segment ids of each recorded passage, in order (audio.assemble's ``units``)."""
    return [list(row["segment_ids"]) for row in report.get("passages", [])]
