"""Speech through ElevenLabs' Text to Dialogue API, both hosts in one request (D-176).

The listening round of 2026-10-19 (.studio/hoerproben, Orlagau episode 1) tried ElevenLabs v4 with the voices of the
user's own account: a stretch of dialogue goes to ElevenLabs as one list of turns, each with its voice, and the answer
carries the audio with the start and end of every turn. As on Google's route (google_speech) such a stretch is a
passage of consecutive segments of one chapter and scene, cut on the script text alone, but at most
MAX_PASSAGE_CHARACTERS: ElevenLabs advises at most 2,000 characters per dialogue request, and the tags and the other
host's reactions add some.

The expression layer's reading is Gemini's (expression.py: ``<sigh>``, ``<short pause>``, ``|mhm|``). It is spoken in
ElevenLabs' own words: a tag becomes its square-bracket counterpart (``[sighs]``, ``[short pause]``), a reaction a
short turn of the other host, and each role's style (speech.RoleStyles) a leading tag of its first turn in a passage.
The sound is 24 kHz mono 16-bit PCM, as on every other route.
"""
from __future__ import annotations

import base64
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
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from pydantic import SecretStr

from .errors import AppError
from .expression import BACKCHANNEL, TAG
from .google_speech import balanced_cuts, heard_texts
from .openrouter import NoRedirect, refusal_reason
from .qwen_worker import spoken_settings
from .speech import (DEFAULT_STYLES, ELEVENLABS_MODEL, ELEVENLABS_MODELS, ELEVENLABS_PROVIDER, VOICE_ID, cached_audio,
                     evict, recorded_pcm, take_defect, throttle, wait_for_throttle)
from .storage import digest, file_hash, inside, write_json

ELEVENLABS_ENDPOINT = "https://api.elevenlabs.io/v1/text-to-dialogue/with-timestamps"
ELEVENLABS_SPEECH_VERSION = "elevenlabs_tts.v1"
MAX_PASSAGE_CHARACTERS = 1600
MAX_PASSAGE_TURNS = 20
PASSAGE_RULE = {"version": "elevenlabs_passages.v1", "max_characters": MAX_PASSAGE_CHARACTERS,
                "max_turns": MAX_PASSAGE_TURNS}
# ElevenLabs' advice for one dialogue request (Text to Dialogue API, 2026-10-19); a passage over it is refused here.
REQUEST_CHARACTERS = 2000
# The context ElevenLabs takes from the passage before and after (previous_text, future_text).
CONTEXT_CHARACTERS = 100
LANGUAGE_CODES = {"de-DE": "de", "en-US": "en"}
# One seed for every request: a passage asked again sounds as close as ElevenLabs allows (best effort).
SEED = 42
RESPONSE_LIMIT = 64 * 1024 * 1024
# A 429 is either the plan's limit on parallel requests or ElevenLabs being busy; both pass with waiting.
RATE_LIMIT_RETRIES = 8
TRANSIENT_RETRIES = 2
TRANSIENT_BACKOFF_SECONDS = 3.0
TRANSIENT_CODES = {500, 502, 503, 504}
CACHE = "cache/audio/elevenlabs"
# Gemini's vocal events (expression.ALLOWED_TAGS) in ElevenLabs' words.
TAGS = {"<short pause>": "[short pause]", "<long pause>": "[long pause]", "<breath>": "[inhales]",
        "<exhales>": "[exhales]", "<sigh>": "[sighs]", "<phew>": "[relieved sigh]", "<laugh>": "[laughs]",
        "<chuckle>": "[chuckles]", "<giggle>": "[giggles]", "<gasp>": "[gasps]", "<tsk>": "[tsks]",
        "<throat-clearing>": "[clears throat]"}


def shared_throttle(root):
    """ElevenLabs' own throttle for every project of the Studio, apart from Gemini's: a 429 there must not slow the
    other route (speech.shared_throttle)."""
    return Path(root).parent / ".elevenlabs_throttle.json"


def eleven_text(text):
    """A reading in ElevenLabs' words: each Gemini tag as its counterpart, an unknown one dropped."""
    return " ".join(TAG.sub(lambda match: " " + TAGS.get(match.group(0), "") + " ", text).split())


def passage_turns(segments, heard, styles):
    """The turns of a passage as ``(speaker_id, text)``: a reaction ``|mhm|`` becomes a short turn of the other host
    ("Mhm."), consecutive turns of one host are joined, and each host's first turn opens with its style as a tag."""
    turns = []
    for segment in segments:
        other = "host_b" if segment.speaker_id == "host_a" else "host_a"
        for index, part in enumerate(re.split(r"\|([^|<>\n]{1,20})\|", heard[segment.segment_id])):
            if index % 2:
                word = part.strip()
                turns.append((other, word[:1].upper() + word[1:] + "."))
            elif part.strip():
                turns.append((segment.speaker_id, eleven_text(part)))
    merged = []
    for role, text in turns:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + " " + text)
        else:
            merged.append((role, text))
    styled = set()
    for index, (role, text) in enumerate(merged):
        if role not in styled and styles.get(role) and len(text) > 20:
            merged[index] = (role, f"[{styles[role]}] {text}")
            styled.add(role)
    return merged


def plain(text):
    """Text without any tag or reaction, for the context of a neighbouring passage."""
    return " ".join(BACKCHANNEL.sub(" ", TAG.sub(" ", text)).split())


def passage_context(passages, number, segments, heard):
    """What ElevenLabs hears of the passages around passage ``number`` (0-based) in the same chapter, at most
    CONTEXT_CHARACTERS each. A chapter records on its own (episode_audio's synthesis) and the whole episode is checked
    afterwards, so the context never reaches across a chapter."""
    chapter = segments[passages[number][0]].chapter_id

    def text(index):
        if not 0 <= index < len(passages) or segments[passages[index][0]].chapter_id != chapter:
            return ""
        return plain(" ".join(heard[segments[i].segment_id] for i in passages[index]))
    return {"previous_text": text(number - 1)[-CONTEXT_CHARACTERS:], "future_text": text(number + 1)[:CONTEXT_CHARACTERS]}


def passage_settings(segments, spoken, voices, language, model=ELEVENLABS_MODEL, styles=None, context=None):
    """The cache key and stored record of one passage: as on Google's route (google_speech.passage_settings), with the
    context of the passages around it, which shapes the delivery too."""
    roles = sorted({segment.speaker_id for segment in segments})
    styles = styles if styles is not None else DEFAULT_STYLES
    return {"provider": ELEVENLABS_PROVIDER, "model": model, "adapter_version": ELEVENLABS_SPEECH_VERSION,
            "language": language, "voices": {role: voices[role] for role in roles},
            "styles": {role: styles[role] for role in roles},
            "segments": [{"speaker_id": segment.speaker_id, "text": segment.text,
                          **spoken_settings(segment.text, spoken[segment.segment_id])} for segment in segments],
            "context": context or {}, "format": "pcm", "sample_rate": 24000, "channels": 1, "sample_width": 2}


def request_body(turns, voices, model, language, context=None):
    body = {"inputs": [{"text": text, "voice_id": voices[role]} for role, text in turns], "model_id": model,
            "language_code": LANGUAGE_CODES[language], "seed": SEED}
    body.update({key: value for key, value in (context or {}).items() if value})
    return body


class ElevenLabsSpeech:
    def __init__(self, api_key=None, *, timeout=600, model=ELEVENLABS_MODEL, throttle_file=None):
        key = (api_key if api_key is not None else os.environ.get("ELEVENLABS_API_KEY", "")).strip()
        if key and (len(key) > 512 or any(not 33 <= ord(c) <= 126 for c in key)):
            raise AppError("Ungültiger ElevenLabs-Key.", code="invalid_elevenlabs_key", status="blocked")
        if model not in ELEVENLABS_MODELS:
            raise AppError("Unbekanntes ElevenLabs-Sprachmodell.", code="invalid_speech", status="blocked")
        self._key = SecretStr(key)
        self.timeout = timeout
        self.model = model
        self.throttle_file = throttle_file

    def require_key(self):
        if not self._key.get_secret_value():
            raise AppError("Für ElevenLabs-Audio den ElevenLabs-Key im Studio hinterlegen.",
                           code="elevenlabs_key_required", status="blocked")

    def synthesize(self, segments, spoken, voices, language, cache, styles=None, context=None):
        """One passage of ``segments``, as ``spoken`` maps them (the reading in Gemini's tags), with ``context`` from
        the passages around it; returns its cached WAV."""
        styles = styles if styles is not None else DEFAULT_STYLES
        if (not segments or language not in LANGUAGE_CODES
                or any(not VOICE_ID.fullmatch(voices.get(segment.speaker_id) or "") or not segment.text.strip()
                       or not spoken[segment.segment_id].strip() for segment in segments)):
            raise AppError("Ungültiger Text, Sprache oder ElevenLabs-Stimme.", code="invalid_speech")
        turns = passage_turns(segments, spoken, styles)
        if sum(len(text) for _, text in turns) > REQUEST_CHARACTERS:
            raise AppError(f"Ein Abschnitt ist länger als die {REQUEST_CHARACTERS} Zeichen, die ElevenLabs je Anfrage "
                           "verträgt.", code="invalid_speech")
        secret = self._key.get_secret_value()
        heard = " ".join(spoken[segment.segment_id] for segment in segments)
        if secret and (secret in heard or any(secret in segment.text for segment in segments)):
            raise AppError("Der API-Key darf nicht im gesprochenen Text stehen.", code="credential_in_prompt",
                           status="blocked")
        settings = passage_settings(segments, spoken, voices, language, self.model, styles, context)
        path = cache / (digest(settings) + ".wav")
        if cached_audio(path, settings):
            if not take_defect(recorded_pcm(path), heard):
                return path
            evict(path)
        self.require_key()
        started = time.monotonic()
        body = request_body(turns, voices, self.model, language, context)
        pcm, record = self.request(body, cache)
        if defect := take_defect(pcm, heard):
            pcm, record = self.request(body, cache)
            if again := take_defect(pcm, heard):
                raise AppError(f"ElevenLabs lieferte zweimal eine unplausible Aufnahme. Zuerst: {defect} Dann: {again} "
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
            "elapsed_seconds": round(time.monotonic() - started, 3), "speech_quality_verified": False, **record})
        return path

    def request(self, body, cache):
        """One take: the PCM of ElevenLabs' answer and what it reports (credits, request id, the turns' times). The
        key goes only in the header."""
        secret = self._key.get_secret_value()
        url = ELEVENLABS_ENDPOINT + "?" + urlencode({"output_format": "pcm_24000"})
        request = Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"xi-api-key": secret, "Content-Type": "application/json"}, method="POST")
        attempt = retried = 0
        while True:
            wait_for_throttle(cache, path=self.throttle_file)
            try:
                with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                    raw = response.read(RESPONSE_LIMIT + 1)
                    headers = {key.lower(): value for key, value in response.headers.items()}
                break
            except HTTPError as exc:
                try:
                    text = exc.read(65536).decode("utf-8", "replace")
                except (OSError, ValueError):
                    text = ""
                exc.close()
                if exc.code == 429 and attempt < RATE_LIMIT_RETRIES:
                    attempt += 1
                    throttle(cache, exc.headers.get("Retry-After") if exc.headers else None, attempt,
                             path=self.throttle_file)
                    continue
                if exc.code in TRANSIENT_CODES and retried < TRANSIENT_RETRIES:
                    retried += 1
                    time.sleep(TRANSIENT_BACKOFF_SECONDS * retried)
                    continue
                raise elevenlabs_failure(exc.code, text, secret) from None
            except (TimeoutError, URLError, OSError, HTTPException) as exc:
                reason = exc.reason if isinstance(exc, URLError) else exc
                if isinstance(reason, (IncompleteRead, ConnectionResetError)) and retried < TRANSIENT_RETRIES:
                    retried += 1
                    time.sleep(TRANSIENT_BACKOFF_SECONDS * retried)
                    continue
                raise AppError("Verbindung zu ElevenLabs unterbrochen. Fertige Abschnitte bleiben gespeichert; später "
                               "fortsetzen.", code="elevenlabs_connection", status="blocked") from None
        if len(raw) > RESPONSE_LIMIT or (secret and secret.encode() in raw):
            raise AppError("Unerwartete Antwort von ElevenLabs statt Audio.", code="invalid_audio", status="blocked")
        try:
            data = json.loads(raw)
            pcm = base64.b64decode(data["audio_base64"], validate=True)
        except (ValueError, UnicodeDecodeError, KeyError, TypeError):
            raise AppError("ElevenLabs lieferte keine lesbare Antwort.", code="invalid_audio", status="blocked") from None
        if not pcm or len(pcm) % 2 or not any(pcm):
            raise AppError("ElevenLabs lieferte leeres oder ungültiges Audio.", code="invalid_audio", status="blocked")
        record = {"credits": headers.get("character-cost"), "request_id": headers.get("request-id"),
                  "voice_segments": data.get("voice_segments") if isinstance(data.get("voice_segments"), list) else []}
        return pcm, record


def elevenlabs_failure(code, body, secret):
    """The error of a refused request, with ElevenLabs' short message kept, without credentials (refusal_reason)."""
    reason = refusal_reason(body, (secret,))
    details = {"provider_message": reason} if reason else {}
    suffix = f": {reason}" if reason else ""
    if "quota_exceeded" in (body or "") or code == 402:
        return AppError("Das ElevenLabs-Guthaben reicht nicht" + suffix + ". Credits aufladen, dann fortsetzen; fertige "
                        "Abschnitte bleiben gespeichert.", code="elevenlabs_credits", status="blocked", details=details)
    if code in {401, 403}:
        return AppError("ElevenLabs hat den Key nicht angenommen" + suffix + ". Einen gültigen ElevenLabs-Key "
                        "hinterlegen; fertige Abschnitte bleiben gespeichert.", code="elevenlabs_authentication",
                        status="blocked", details=details)
    if code == 429:
        return AppError("ElevenLabs ist ausgelastet oder die Zahl gleichzeitiger Anfragen erreicht" + suffix + ". Später "
                        "fortsetzen; fertige Abschnitte bleiben gespeichert.", code="elevenlabs_busy", status="blocked",
                        details=details)
    if code in {400, 404, 413, 422}:
        return AppError("ElevenLabs hat die Audioanfrage abgewiesen" + suffix + ". Modell, Stimmen und Textlänge prüfen.",
                        code="elevenlabs_speech_request", status="blocked", details=details)
    return AppError("ElevenLabs ist vorübergehend nicht erreichbar. Später fortsetzen; fertige Abschnitte bleiben "
                    "gespeichert.", code="elevenlabs_unavailable", status="blocked")


VOICES_ENDPOINT = "https://api.elevenlabs.io/v2/voices"
VOICE_LABELS = ("gender", "age", "accent", "language", "use_case", "descriptive")


def account_voices(api_key=None, *, timeout=30, pages=5):
    """The voices the account can speak with (GET /v2/voices), its own before ElevenLabs' defaults: id, name, kind,
    the labels that help choosing, whether German is verified, and the collections (the user's groups) it is in."""
    key = (api_key if api_key is not None else os.environ.get("ELEVENLABS_API_KEY", "")).strip()
    if not key:
        raise AppError("Für ElevenLabs-Stimmen den ElevenLabs-Key im Studio hinterlegen.",
                       code="elevenlabs_key_required", status="blocked")
    voices, token = [], None
    for _ in range(pages):
        query = {"page_size": 100, "include_total_count": "false", **({"next_page_token": token} if token else {})}
        request = Request(VOICES_ENDPOINT + "?" + urlencode(query), headers={"xi-api-key": key}, method="GET")
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                page = json.loads(response.read(8 * 1024 * 1024))
        except HTTPError as exc:
            text = exc.read(65536).decode("utf-8", "replace") if exc.fp else ""
            exc.close()
            raise elevenlabs_failure(exc.code, text, key) from None
        except (TimeoutError, URLError, OSError, HTTPException, ValueError):
            raise AppError("ElevenLabs ist nicht erreichbar.", code="elevenlabs_connection", status="blocked") from None
        for voice in page.get("voices") or []:
            if not isinstance(voice, dict) or not VOICE_ID.fullmatch(str(voice.get("voice_id") or "")):
                continue
            labels = voice.get("labels") if isinstance(voice.get("labels"), dict) else {}
            voices.append({"voice_id": voice["voice_id"], "name": str(voice.get("name") or voice["voice_id"])[:80],
                           "category": voice.get("category"),
                           "labels": {name: str(labels[name])[:40] for name in VOICE_LABELS if labels.get(name)},
                           "german": any(str(row.get("language") or "").startswith("de")
                                         for row in voice.get("verified_languages") or [] if isinstance(row, dict)),
                           "collections": [str(c) for c in voice.get("collection_ids") or []]})
        token = page.get("next_page_token")
        if not page.get("has_more") or not token:
            break
    return sorted(voices, key=lambda v: (v["category"] == "premade", v["name"].casefold()))


def plan(script):
    """The passages of ``script`` as lists of segment indices: Google's rule (google_speech.plan_passages) with
    ElevenLabs' bounds. The rule is repeated here, not given parameters there: every Google recording binds the bytes of
    google_speech.py (episode_audio's worker_sha256), and a changed file stopped Orlagau's 15 waiting Google recordings
    with inputs_changed (2026-10-19)."""
    segments = script.segments
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


def run_elevenlabs_tts(config, script, root, work, choice, api_key=None, *, table=None, overrides=None,
                       expression=None, engine=None):
    """Record ``script`` passage by passage; ``choice.voices`` are this episode's (AudioChoice.for_episode). The report
    has the form of a Google recording's, so the montage treats both alike (google_speech.passage_units)."""
    engine = engine or ElevenLabsSpeech(api_key, timeout=config.runtime.tts_timeout_seconds, model=choice.model,
                                        throttle_file=shared_throttle(root))
    spoken = heard_texts(script, table, overrides, expression)
    styles = choice.spoken_styles(config.language)
    passages = plan(script)
    rows, paths, done = [], [], 0
    for number, indices in enumerate(passages):
        segments = [script.segments[index] for index in indices]
        context = passage_context(passages, number, script.segments, spoken)
        write_json(work / "tts_progress.json", {"completed_segments": done, "total_segments": len(script.segments),
            "current_segment": segments[0].segment_id, "current_passage": number + 1, "total_passages": len(passages)})
        try:
            path = engine.synthesize(segments, spoken, choice.voices, config.language, root / CACHE, styles, context)
        except AppError as exc:
            if exc.code != "invalid_speech":
                raise
            span = segments[0].segment_id + (f" bis {segments[-1].segment_id}" if len(segments) > 1 else "")
            raise AppError(f"Abschnitt {span}: {exc}", code=exc.code, status=exc.status,
                           details={**exc.details, "segment_ids": [s.segment_id for s in segments]}) from exc
        paths.append(path)
        rows.append({"passage": number + 1, "segment_ids": [segment.segment_id for segment in segments],
                     "path": path.relative_to(root / "cache/audio").as_posix(), "sha256": file_hash(path),
                     "settings": passage_settings(segments, spoken, choice.voices, config.language, choice.model,
                                                  styles, context)})
        done += len(segments)
    write_json(work / "tts_report.json", {"passages": rows})
    write_json(work / "tts_progress.json", {"completed_segments": done, "total_segments": done})
    return paths


def check_elevenlabs_rows(root, script, report, choice, language, *, table=None, overrides=None, expression=None):
    """The passage recordings of ``report`` if they are exactly what ``script`` asks for today, else invalid_audio."""
    rows = report.get("passages", [])
    passages = plan(script)
    if [row.get("segment_ids") for row in rows] != [[script.segments[i].segment_id for i in p] for p in passages]:
        raise AppError("ElevenLabs-Audio passt nicht zum Skript.", code="invalid_audio")
    spoken = heard_texts(script, table, overrides, expression)
    styles = choice.spoken_styles(language)
    paths = []
    for number, (row, indices) in enumerate(zip(rows, passages, strict=True)):
        path = inside(root / "cache/audio", row["path"])
        segments = [script.segments[index] for index in indices]
        expected = passage_settings(segments, spoken, choice.voices, language, choice.model, styles,
                                    passage_context(passages, number, script.segments, spoken))
        if row.get("settings") != expected or not path.is_file() or file_hash(path) != row.get("sha256"):
            raise AppError("ElevenLabs-Text, Stimme oder Audiodatei geändert.", code="invalid_audio")
        paths.append(path)
    return paths
