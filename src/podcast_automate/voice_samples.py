"""Persistent, shared Gemini audition library. Playback never synthesizes speech.

Two kinds of sample: one voice reading a short text (OpenRouter, the voice library), and a short conversation of two
voices in their styles through Google (google_speech), as a Google recording would speak them; the settings page plays
the conversation of the selection it shows (the user's wish of 2026-10-06: "man will ja immer mal was anderes")."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .audio import ffmpeg
from .errors import AppError
from .expression import untagged
from .google_speech import GoogleSpeech, passage_settings
from .models import Segment
from .speech import AudioChoice, GEMINI_MODEL, GEMINI_VOICES, GeminiSpeech, cached_audio, shared_throttle, speech_settings
from .storage import digest, file_hash, inside, project_lock, write_json

SAMPLE_TEXTS = {
    "de-DE": "Eine gute Erklärung beginnt mit einer Frage. Warum funktioniert etwas so, wie es funktioniert? "
             "Gehen wir der Sache auf den Grund: mit einem konkreten Beispiel, Schritt für Schritt.",
    "en-US": "A good explanation begins with a question. Why does something work the way it does? "
             "Let's get to the heart of it, using a concrete example, one step at a time.",
}


def sample_settings(voice, language):
    if voice not in GEMINI_VOICES or language not in SAMPLE_TEXTS:
        raise AppError("Gemini-Stimme und Sprache auswählen.", code="invalid_voice")
    return speech_settings(SAMPLE_TEXTS[language], voice, language)


def library(projects):
    return inside(projects, "voice-samples/gemini")


def sample_path(projects, voice, language):
    fingerprint = digest(sample_settings(voice, language))
    return inside(library(projects), f"{language}/{voice}/{fingerprint}/audio.mp3")


def ready_sample(projects, voice, language):
    path = sample_path(projects, voice, language)
    try:
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if (metadata.get("settings") == sample_settings(voice, language)
                and path.stat().st_size > 0 and metadata.get("sha256") == file_hash(path)):
            return path
    except (OSError, ValueError, AttributeError):
        pass
    return None


def sample_inventory(projects):
    return {language: {voice: {"url": f"/samples/gemini/{language}/{voice}"}
            for voice in GEMINI_VOICES if ready_sample(projects, voice, language)}
            for language in SAMPLE_TEXTS}


def generate_sample(root, voice, language, api_key=None):
    projects = root.parent
    settings = sample_settings(voice, language)
    shared = library(projects)
    with project_lock(shared):
        target = ready_sample(projects, voice, language)
        if target is None:
            cache = shared / "cache"
            cache.mkdir(parents=True, exist_ok=True)
            wav = cache / (digest(settings) + ".wav")
            if not cached_audio(wav, settings):
                # Adopt verified recordings made before the shared library existed.
                for candidate in projects.glob(f"*/cache/audio/gemini/{wav.name}"):
                    if cached_audio(candidate, settings):
                        shutil.copyfile(candidate, wav)
                        shutil.copyfile(candidate.with_suffix(".json"), wav.with_suffix(".json"))
                        break
            engine = GeminiSpeech(api_key, throttle_file=shared_throttle(root))
            wav = engine.synthesize(SAMPLE_TEXTS[language], voice, language, cache)
            target = sample_path(projects, voice, language)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                encoded = Path(temporary) / "audio.mp3"
                ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "192k", str(encoded)])
                encoded.replace(target)
            write_json(target.with_suffix(".json"), {"settings": settings, "sha256": file_hash(target)})
        # Keep the existing single-sample result and media URLs compatible.
        alias = inside(root, f"studio/samples/{language}/{voice}/audio.mp3")
        if not alias.is_file() or file_hash(alias) != file_hash(target):
            alias.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, alias)
    return {"voice": voice, "language": language, "audio": alias.relative_to(root).as_posix()}


# The conversation sample: the questioner opens, the expert answers with a pause tag and the questioner's reaction
# inside the answer, the way a Google recording with its expression layer sounds.
PAIR_TEXTS = {
    "de-DE": [("host_b", "Eine Sache lässt mich nicht los. Warum bleibt eine gute Erklärung hängen, eine Liste von "
                         "Fakten aber nicht?"),
              ("host_a", "Weil eine gute Erklärung mit einer Frage beginnt, die einen wirklich interessiert. <short pause> "
                         "Dann beantwortet jeder Schritt etwas, das man sich ohnehin gefragt hat, |mhm| und plötzlich "
                         "haben die Fakten einen Platz."),
              ("host_b", "Einen Platz für die Fakten. Das gefällt mir. Und was kommt als Nächstes?"),
              ("host_a", "Als Nächstes probieren wir es an einem echten Beispiel aus, Schritt für Schritt.")],
    "en-US": [("host_b", "Here's what I keep wondering. Why does a good explanation stick, while a list of facts just "
                         "slips away?"),
              ("host_a", "Because a good explanation starts with a question you actually care about. <short pause> Then "
                         "every step answers something you were already wondering, |mhm| and suddenly the facts have a "
                         "place to land."),
              ("host_b", "A place to land. I like that. So what comes next?"),
              ("host_a", "Next, we try it out on a real example, one step at a time.")],
}


def pair_script(language):
    """The sample conversation as segments of one passage and the tagged text each is spoken as."""
    if language not in PAIR_TEXTS:
        raise AppError("Sprache für die Gesprächsprobe auswählen.", code="invalid_voice")
    segments = [Segment(segment_id=f"probe_{number}", scene_id="probe", chapter_id="probe", speaker_id=speaker,
                        text=untagged(text)) for number, (speaker, text) in enumerate(PAIR_TEXTS[language], 1)]
    return segments, {segment.segment_id: text for segment, (_, text) in zip(segments, PAIR_TEXTS[language])}


def pair_choice(voices, styles):
    """The two voices and styles as a valid Google choice, or invalid_voice."""
    try:
        return AudioChoice(provider="google_gemini_tts", voices=voices, styles=styles if styles is not None else {})
    except (ValueError, TypeError):
        raise AppError("Zwei unterschiedliche Gemini-Stimmen und kurze Stilangaben wählen.", code="invalid_voice") from None


def pair_settings(voices, styles, language):
    choice = pair_choice(voices, styles)
    segments, spoken = pair_script(language)
    return passage_settings(segments, spoken, choice.voices, language, GEMINI_MODEL, choice.styles.model_dump())


def pair_path(projects, voices, styles, language):
    fingerprint = digest(pair_settings(voices, styles, language))
    return inside(projects, f"voice-samples/google/{language}/{fingerprint}/audio.mp3")


def ready_pair(projects, voices, styles, language):
    """The finished conversation sample of this selection, or None."""
    path = pair_path(projects, voices, styles, language)
    return ready_file(path, pair_settings(voices, styles, language))


def ready_pair_file(projects, language, fingerprint):
    """A finished conversation sample by its fingerprint (the URL the settings page plays), or None."""
    try:
        path = inside(projects, f"voice-samples/google/{language}/{fingerprint}/audio.mp3")
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        settings = metadata.get("settings")
        return ready_file(path, settings) if digest(settings) == fingerprint else None
    except (OSError, ValueError, AttributeError, AppError):
        return None


def ready_file(path, settings):
    try:
        metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        if (metadata.get("settings") == settings and path.stat().st_size > 0
                and metadata.get("sha256") == file_hash(path)):
            return path
    except (OSError, ValueError, AttributeError):
        pass
    return None


def pair_view(projects, voices, styles, language):
    """What the settings page needs for a selection: the sample's URL and whether it exists yet."""
    path = pair_path(projects, voices, styles, language)
    return {"url": f"/samples/google/{language}/{path.parent.name}",
            "ready": ready_pair(projects, voices, styles, language) is not None}


def generate_pair(projects, voices, styles, language, api_key=None):
    """Speak the sample conversation of this selection through Google, once; later requests play the saved file.
    ``projects`` is the Studio's projects folder, where the library lives next to the projects."""
    settings = pair_settings(voices, styles, language)
    choice = pair_choice(voices, styles)
    shared = inside(projects, "voice-samples/google")
    with project_lock(shared):
        target = ready_pair(projects, voices, styles, language)
        if target is None:
            segments, spoken = pair_script(language)
            # The rate-limit file every recording of the workspace shares (speech.shared_throttle).
            engine = GoogleSpeech(api_key, throttle_file=shared_throttle(shared.parent))
            wav = engine.synthesize(segments, spoken, choice.voices, language, shared / "cache", choice.styles.model_dump())
            target = pair_path(projects, voices, styles, language)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                encoded = Path(temporary) / "audio.mp3"
                ffmpeg(["-i", str(wav), "-c:a", "libmp3lame", "-b:a", "192k", str(encoded)])
                encoded.replace(target)
            write_json(target.with_suffix(".json"), {"settings": settings, "sha256": file_hash(target)})
    return {"language": language, "voices": choice.voices, **pair_view(projects, voices, styles, language)}


def generate_samples(root, language, api_key=None, progress=None):
    # Validate before any work; a failure preserves every completed recording.
    sample_settings(GEMINI_VOICES[0], language)
    completed = sum(bool(ready_sample(root.parent, voice, language)) for voice in GEMINI_VOICES)
    for voice in GEMINI_VOICES:
        if ready_sample(root.parent, voice, language):
            continue
        if progress:
            progress({"completed_segments": completed, "total_segments": len(GEMINI_VOICES), "current_voice": voice})
        generate_sample(root, voice, language, api_key)
        completed += 1
    if progress:
        progress({"completed_segments": completed, "total_segments": len(GEMINI_VOICES)})
    return {"language": language, "completed": completed, "total": len(GEMINI_VOICES)}
