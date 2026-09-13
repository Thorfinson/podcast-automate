from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import wave
from array import array
from pathlib import Path

from .errors import AppError
from .models import EpisodeScript, TopicBrief
from .process import run_process
from .storage import file_hash, inside, write_json, atomic_text


def worker_path() -> Path:
    return Path(__file__).with_name("qwen_worker.py")


def run_tts(config: TopicBrief, script: EpisodeScript, root: Path, work: Path) -> list[Path]:
    request, response = work / "tts_request.json", work / "tts_report.json"
    write_json(request, {
        "runtime": config.runtime.model_dump(), "voices": config.voice_profile,
        "language": {"de-DE": "German", "en-US": "English"}[config.language],
        "segments": [s.model_dump() for s in script.segments],
        "cache_dir": str(root / "cache/audio"),
        "progress_file": str(work / "tts_progress.json"),
    })
    response.unlink(missing_ok=True)
    result = run_process(
        [config.runtime.tts_python, str(worker_path()), "--request", str(request),
         "--output", str(response)], timeout=config.runtime.tts_timeout_seconds)
    try:
        report = json.loads(response.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("invalid report")
    except (OSError, ValueError) as exc:
        raise AppError("TTS-Prozess lieferte keinen gültigen Bericht. Python-Umgebung prüfen.",
                       code="tts_worker_failed") from exc
    if result.returncode or "error" in report:
        raise AppError(report.get("error", "TTS-Prozess fehlgeschlagen."), code="tts_worker_failed")
    rows = report.get("segments", [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise AppError("TTS-Bericht enthält ungültige Segmentdaten.", code="invalid_audio")
    if [row.get("segment_id") for row in rows] != [s.segment_id for s in script.segments]:
        raise AppError("TTS-Bericht enthält nicht alle angeforderten Segmente.", code="invalid_audio")
    paths = []
    for row in rows:
        path = inside(root / "cache/audio", row["path"])
        if not path.is_file() or file_hash(path) != row.get("sha256"):
            raise AppError("TTS-Segment fehlt oder stimmt nicht mit seinem Hash überein.",
                           code="invalid_audio")
        paths.append(path)
    return paths


def ffmpeg_command() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise AppError("FFmpeg fehlt im PATH.", code="ffmpeg_missing", status="blocked")
    return executable


def ffmpeg(args: list[str]) -> str:
    result = run_process([ffmpeg_command(), "-nostdin", "-hide_banner", "-y", *args], timeout=300)
    if result.returncode:
        raise AppError("FFmpeg konnte das Audio nicht verarbeiten.", code="audio_processing_failed")
    return result.stderr


def audio_info(path: Path) -> dict:
    executable = shutil.which("ffprobe")
    if not executable:
        raise AppError("ffprobe fehlt im PATH.", code="ffprobe_missing", status="blocked")
    result = run_process(
        [executable, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        timeout=30)
    try:
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        if result.returncode or not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid duration")
    except (ValueError, KeyError) as exc:
        raise AppError("Audiodatei ist nicht lesbar oder hat keine gültige Laufzeit.",
                       code="invalid_audio") from exc
    return data


def assemble(script: EpisodeScript, paths: list[Path], output: Path,
             *, max_seconds: float = 1800, language: str = "de-DE", voices: dict | None = None) -> list[Path]:
    if len(paths) != len(script.segments):
        raise AppError("Es fehlen Audiosegmente.", code="invalid_audio")
    output.mkdir(parents=True, exist_ok=True)
    timeline = []
    with tempfile.TemporaryDirectory(prefix="mix-", dir=output) as temporary:
        work = Path(temporary)
        mixed = work / "mixed.wav"
        position = 0
        with wave.open(str(mixed), "wb") as target:
            target.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
            for index, (segment, source) in enumerate(zip(script.segments, paths, strict=True)):
                if not source.is_file():
                    raise AppError(f"Segment fehlt: {segment.segment_id}", code="invalid_audio")
                normalized = work / f"segment_{index}.wav"
                ffmpeg(["-i", str(source), "-ar", "44100", "-ac", "2",
                        "-c:a", "pcm_s16le", str(normalized)])
                with wave.open(str(normalized), "rb") as stream:
                    frames = stream.getnframes()
                    start = position
                    peak = 0
                    while chunk := stream.readframes(44100):
                        samples = array("h", chunk)
                        peak = max(peak, max(map(abs, samples), default=0))
                        target.writeframesraw(chunk)
                    if frames == 0 or peak == 0:
                        raise AppError(f"Segment enthält nur Stille: {segment.segment_id}",
                                       code="invalid_audio")
                pause = round(segment.pause_after_ms * 44100 / 1000)
                position += frames + pause
                if position / 44100 > max_seconds:
                    raise AppError("Folge ist zu lang. Die automatische Aufteilung folgt im Serien-Meilenstein.",
                                   code="duration_exceeded", status="blocked")
                target.writeframesraw(b"\0" * (pause * 4))
                timeline.append({
                    "segment_id": segment.segment_id, "chapter_id": segment.chapter_id,
                    "speaker_id": segment.speaker_id, "start_seconds": start / 44100,
                    "speech_end_seconds": (start + frames) / 44100,
                    "end_seconds": position / 44100,
                })
        measurement = ffmpeg([
            "-i", str(mixed), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-",
        ])
        blocks = re.findall(r'\{\s*"input_i".*?\}', measurement, flags=re.DOTALL)
        if not blocks:
            raise AppError("Lautheitsmessung fehlgeschlagen.", code="loudness_failed")
        levels = json.loads(blocks[-1])
        for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
            if not math.isfinite(float(levels[key])):
                raise AppError("Audio ist zu kurz oder ungeeignet für die Lautheitsmessung.",
                               code="loudness_failed")
        loudnorm = (
            f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={levels['input_i']}"
            f":measured_TP={levels['input_tp']}:measured_LRA={levels['input_lra']}"
            f":measured_thresh={levels['input_thresh']}:offset={levels['target_offset']}:linear=true"
        )
        encoded = work / "audio.mp3"
        ffmpeg(["-i", str(mixed), "-af", loudnorm, "-ar", "44100", "-ac", "2",
                "-c:a", "libmp3lame", "-b:a", "192k", "-metadata", f"title={script.title}",
                str(encoded)])
        info = audio_info(encoded)
        duration = float(info["format"]["duration"])
        if duration > max_seconds:
            raise AppError("Die gemessene MP3 überschreitet die Folgenlänge.",
                           code="duration_exceeded", status="blocked")
        encoded.replace(output / "audio.mp3")
    chapters = []
    for chapter in script.chapters:
        entries = [row for row in timeline if row["chapter_id"] == chapter.chapter_id]
        chapters.append({
            "chapter_id": chapter.chapter_id, "title": chapter.title,
            "start_seconds": entries[0]["start_seconds"],
            "end_seconds": entries[-1]["end_seconds"],
        })
    write_json(output / "chapters.json", {"version": "1.0", "chapters": chapters})
    write_json(output / "timeline.json", {
        "schema_version": "1.0", "segments": timeline,
        "pcm_duration_seconds": position / 44100, "mp3_duration_seconds": duration,
    })
    write_json(output / "audio_report.json", {
        "purpose": script.purpose, "duration_seconds": duration,
        "sample_rate": 44100, "channels": 2, "target_lufs": -16,
        "input_loudness": levels, "speech_quality_verified": False,
    })
    if script.purpose == "technical_probe":
        notice = ("Technical voice sample; not a researched podcast episode." if language == "en-US"
                  else "Technische Hörprobe; keine recherchierte Podcastfolge.")
    else:
        notice = ("First audio version for listening review." if language == "en-US"
                  else "Erste Audiofassung zur Hörprüfung.")
    lines = [f"# {script.title}", "", notice, ""]
    for segment in script.segments:
        lines.extend([f"**{(voices or {}).get(segment.speaker_id, segment.speaker_id)}:** {segment.text}", ""])
    atomic_text(output / "transcript.md", "\n".join(lines))
    return [output / name for name in (
        "audio.mp3", "chapters.json", "timeline.json", "audio_report.json", "transcript.md")]
