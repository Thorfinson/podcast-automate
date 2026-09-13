"""Persistent, shared Gemini audition library. Playback never synthesizes speech."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .audio import ffmpeg
from .errors import AppError
from .speech import GEMINI_VOICES, GeminiSpeech, cached_audio, speech_settings
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
            wav = GeminiSpeech(api_key).synthesize(SAMPLE_TEXTS[language], voice, language, cache)
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
