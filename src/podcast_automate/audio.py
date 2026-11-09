from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import wave
from array import array
from pathlib import Path

from .content_text import mp3_comment, text as wording
from .errors import AppError
from .models import ROLE_LABELS, EpisodeScript, TopicBrief
from .process import run_process
from .spoken_forms import SpokenForms, spoken_text
from .storage import file_hash, inside, write_json, atomic_text


def worker_path() -> Path:
    return Path(__file__).with_name("qwen_worker.py")


# Silence as the speech health gate (speech.take_defect) and the pause trim below measure it: 20 ms windows whose
# peak stays under -45 dBFS. A read-only scan of 1,620 Gemini takes of the 29 Sep exports (2026-10-02) found with
# this measure no silence over 1.3 s in an untagged take and 3.5 to 7.3 s after a <long pause>.
SILENCE_WINDOW_SECONDS = 0.02
SILENCE_LEVEL = 184
# A segment with a pause tag keeps at most this much of a longer silence (dead air after a <long pause>).
TRIM_ABOVE_SECONDS = 1.5
TRIM_TO_SECONDS = 1.2
PAUSE_TAGS = ("<short pause>", "<long pause>")
# The second loudness pass: the measured linear gain to -16 LUFS, then a limiter for the peaks. loudnorm's own
# linear mode fell back to dynamic compression without saying so in 29 of 30 exports of 29 Sep, because the gain
# would have lifted their true peak above -1.5 dBTP. The limiter works at four times the sample rate, so it also
# catches the peaks between samples, with its ceiling 0.5 dB under the target for the MP3 encoder. A 40-minute mix
# of Gemini takes came out at -16.0 LUFS and -1.9 dBTP, -2.1 dBTP after MP3 encoding, in 17 s (2026-10-02).
LIMITER_CEILING_DB = -2.0
# D-154 (EU AI Act Art. 50(2)): every exported MP3 says in its own tags that it was made with AI. DIGITAL_SOURCE_TYPE
# is the IPTC digital source type for media a trained model generated. FFmpeg writes a key that has no ID3v2.3 frame
# of its own as a TXXX frame named by the key; FFmpeg 9.0 writes "comment" so as well, not as a COMM frame.
DIGITAL_SOURCE_TYPE = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"
# A podcast's music in the montage (music.py, D-189), in bars of the piece: alone for four bars before the first word,
# then under the speech for two and out over two more; at a chapter change two bars between the chapters, the new
# chapter's first word on the bar after them while the music fades under it over one; at the end in under the last
# three bars of speech, up half a second after the last word, four bars on and out over two. Under speech it sits
# 14 dB lower, alone 3 LU under the speech. The listening samples of 2026-11-08 (Geld ep_001), as the user chose them.
MUSIC_INTRO_BARS, MUSIC_INTRO_UNDER_BARS, MUSIC_INTRO_FADE_BARS = 4, 2, 2
MUSIC_CHAPTER_BARS = 2
MUSIC_OUTRO_LEAD_BARS, MUSIC_OUTRO_ON_BARS, MUSIC_OUTRO_FADE_BARS = 3, 4, 2
MUSIC_UNDER = 0.2
MUSIC_BELOW_SPEECH_LU = 3.0


def ai_marking(language, purpose):
    """The tags that mark an exported MP3 as AI-generated, its comment in the podcast's language (D-154)."""
    return {"comment": mp3_comment(language, purpose), "DIGITAL_SOURCE_TYPE": DIGITAL_SOURCE_TYPE,
            "AI_GENERATED": "true"}


def marking_args(language, purpose):
    """The FFmpeg output options that write ai_marking into an MP3, for every encoder of synthetic speech."""
    return [arg for key, value in ai_marking(language, purpose).items() for arg in ("-metadata", f"{key}={value}")]


def silent_runs(samples, rate, channels=1):
    """The silent stretches of interleaved 16-bit ``samples`` as ``(start_frame, frames)``, edges included."""
    window = max(1, round(rate * SILENCE_WINDOW_SECONDS))
    step = window * channels
    total = len(samples) // channels
    runs, start = [], None
    for index in range(0, (total + window - 1) // window):
        chunk = samples[index * step:(index + 1) * step]
        quiet = max(chunk) < SILENCE_LEVEL and -min(chunk) < SILENCE_LEVEL
        if quiet and start is None:
            start = index
        elif not quiet and start is not None:
            runs.append((start * window, (index - start) * window))
            start = None
    if start is not None:
        runs.append((start * window, total - start * window))
    return runs


def shortened_silences(samples, rate, channels=1):
    """``samples`` with every silence longer than TRIM_ABOVE_SECONDS cut to TRIM_TO_SECONDS, keeping both of its
    ends so speech never starts or stops abruptly; returns the samples and the frames removed."""
    keep = round(TRIM_TO_SECONDS * rate)
    result, cursor, removed = array("h"), 0, 0
    for start, length in silent_runs(samples, rate, channels):
        if length <= TRIM_ABOVE_SECONDS * rate:
            continue
        cut_from, cut_to = start + keep // 2, start + length - (keep - keep // 2)
        result.extend(samples[cursor * channels:cut_from * channels])
        removed += cut_to - cut_from
        cursor = cut_to
    if not removed:
        return samples, 0
    result.extend(samples[cursor * channels:])
    return result, removed


def output_loudness(stderr):
    """Integrated loudness and true peak of the encoded signal, from the ebur128 summary of the encoding pass."""
    summary = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else ""
    integrated = re.search(r"\bI:\s*(-?[\d.]+|-inf) LUFS", summary)
    peak = re.search(r"\bPeak:\s*(-?[\d.]+|-inf) dBFS", summary)
    try:
        return {"integrated_lufs": float(integrated[1]), "true_peak_dbtp": float(peak[1])} if integrated and peak else None
    except ValueError:
        return None


def run_tts(config: TopicBrief, script: EpisodeScript, root: Path, work: Path,
            *, table=None, overrides=None) -> list[Path]:
    request, response = work / "tts_request.json", work / "tts_report.json"
    table = table if table is not None else SpokenForms()
    write_json(request, {
        "runtime": config.runtime.model_dump(), "voices": config.voice_profile,
        "language": {"de-DE": "German", "en-US": "English"}[config.language],
        "segments": [{**s.model_dump(), "spoken_text": spoken_text(s, table, overrides)}
                     for s in script.segments],
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


def transition(script, index):
    """What follows this segment: another chapter, the other host, or the same host."""
    if index + 1 >= len(script.segments):
        return "episode_end"
    current, following = script.segments[index], script.segments[index + 1]
    if following.chapter_id != current.chapter_id:
        return "chapter_break"
    return "speaker_change" if following.speaker_id != current.speaker_id else "same_speaker"


def applied_pause(script, index, pauses):
    """The planned pause, raised to the policy minimum for this transition."""
    planned = script.segments[index].pause_after_ms
    kind = transition(script, index)
    if pauses is None or kind == "episode_end":
        return planned, kind
    minimum = {"chapter_break": pauses.chapter_break_ms, "speaker_change": pauses.speaker_change_ms,
               "same_speaker": pauses.same_speaker_ms}[kind]
    return max(planned, minimum), kind


def metadata_value(text):
    """FFmpeg's metadata file escapes '=', ';', '#', '\\' and a newline with a backslash."""
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def chapter_metadata(title, chapters):
    """An FFmpeg metadata input; timings are milliseconds from the measured timeline."""
    lines = [";FFMETADATA1", "title=" + metadata_value(title)]
    for chapter in chapters:
        lines.extend(["", "[CHAPTER]", "TIMEBASE=1/1000",
                      f"START={round(chapter['start_seconds'] * 1000)}",
                      f"END={round(chapter['end_seconds'] * 1000)}",
                      "title=" + metadata_value(chapter["title"])])
    return "\n".join(lines) + "\n"


def embedded_chapters(path: Path) -> list[dict]:
    """Read the chapters back from the produced file; an empty list means none were written."""
    executable = shutil.which("ffprobe")
    if not executable:
        raise AppError("ffprobe fehlt im PATH.", code="ffprobe_missing", status="blocked")
    result = run_process([executable, "-v", "error", "-show_chapters", "-of", "json", str(path)], timeout=30)
    try:
        return [{"title": row.get("tags", {}).get("title", ""),
                 "start_seconds": float(row["start_time"]), "end_seconds": float(row["end_time"])}
                for row in json.loads(result.stdout).get("chapters", [])]
    except (ValueError, KeyError, TypeError):
        return []


def recording_units(script, units):
    """The segment indices each recording covers: one segment each, or the passages ``units`` names by segment id
    (google_speech), which must cover the script in order and never cross a chapter."""
    if units is None:
        return [[index] for index in range(len(script.segments))]
    order = {segment.segment_id: index for index, segment in enumerate(script.segments)}
    groups = [[order.get(segment_id, -1) for segment_id in unit] for unit in units]
    if ([index for group in groups for index in group] != list(range(len(script.segments)))
            or any(not group or len({script.segments[index].chapter_id for index in group}) != 1 for group in groups)):
        raise AppError("Die Passagen passen nicht zum Skript.", code="invalid_audio")
    return groups


def loudness_of(path: Path) -> dict:
    """loudnorm's measurement of ``path`` (input_i and the rest), every value finite."""
    measurement = ffmpeg(["-i", str(path), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    blocks = re.findall(r'\{\s*"input_i".*?\}', measurement, flags=re.DOTALL)
    if not blocks:
        raise AppError("Lautheitsmessung fehlgeschlagen.", code="loudness_failed")
    levels = json.loads(blocks[-1])
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        if not math.isfinite(float(levels[key])):
            raise AppError("Audio ist zu kurz oder ungeeignet für die Lautheitsmessung.",
                           code="loudness_failed")
    return levels


def with_music(work: Path, speech: Path, music: dict, places: list[dict], speech_loudness: float) -> tuple[Path, float]:
    """``speech`` with the music under it at ``places`` (the MUSIC_* rules), and the music's gain in dB. Each place is
    its own input of the piece, looped as often as it is short, so no input is read twice."""
    bar, path = music["bar_seconds"], music["path"]
    gain = speech_loudness - MUSIC_BELOW_SPEECH_LU - float(loudness_of(path)["input_i"])
    length = float(audio_info(path)["format"]["duration"])
    under = MUSIC_UNDER
    inputs, chains = [], []
    for number, place in enumerate(places):
        if place["kind"] == "opening":
            solo = MUSIC_INTRO_BARS * bar
            begin, span = 0.0, (MUSIC_INTRO_BARS + MUSIC_INTRO_UNDER_BARS + MUSIC_INTRO_FADE_BARS) * bar
            envelope = (f"if(lt(t,{solo - 0.5:.3f}),1,if(lt(t,{solo:.3f}),1-{1 - under}*(t-{solo - 0.5:.3f})/0.5,{under}))")
            fades = f"afade=t=out:st={solo + MUSIC_INTRO_UNDER_BARS * bar:.3f}:d={MUSIC_INTRO_FADE_BARS * bar:.3f}"
        elif place["kind"] == "chapter":
            alone = MUSIC_CHAPTER_BARS * bar
            begin, span = music["chapter_from"], alone + bar
            envelope = (f"if(lt(t,{alone - 0.5:.3f}),1,if(lt(t,{alone:.3f}),1-{1 - under}*(t-{alone - 0.5:.3f})/0.5,"
                        f"{under}))")
            fades = f"afade=t=in:d=0.05,afade=t=out:st={alone:.3f}:d={bar:.3f}"
        else:
            lead = place["speech_end_seconds"] - place["start_seconds"]
            begin, span = 0.0, lead + (MUSIC_OUTRO_ON_BARS + MUSIC_OUTRO_FADE_BARS) * bar
            rise = min(1.0, lead) or 0.001
            envelope = (f"if(lt(t,{rise:.3f}),{under}*t/{rise:.3f},if(lt(t,{lead + 0.5:.3f}),{under},"
                        f"if(lt(t,{lead + 1.5:.3f}),{under}+{1 - under}*(t-{lead + 0.5:.3f}),1)))")
            fades = f"afade=t=out:st={lead + MUSIC_OUTRO_ON_BARS * bar:.3f}:d={MUSIC_OUTRO_FADE_BARS * bar:.3f}"
        inputs += ["-stream_loop", str(max(0, math.ceil((begin + span) / length) - 1)), "-i", str(path)]
        delay = round(place["start_seconds"] * 1000)
        chains.append(f"[{number}:a]aformat=sample_rates=44100:channel_layouts=stereo,atrim={begin:.3f}:{begin + span:.3f},"
                      f"asetpts=PTS-STARTPTS,volume={gain:.2f}dB,volume='{envelope}':eval=frame,{fades},"
                      f"adelay=delays={delay}:all=1[m{number}]")
    beds = "".join(f"[m{number}]" for number in range(len(places)))
    graph = (";".join(chains) + f";{beds}amix=inputs={len(places)}:normalize=0:duration=longest:dropout_transition=0[bed];"
             f"[{len(places)}:a][bed]amix=inputs=2:normalize=0:duration=first:dropout_transition=0[out]")
    combined = work / "with_music.wav"
    # Float samples: the sum may pass full scale before the loudness pass and its limiter bring it back.
    ffmpeg([*inputs, "-i", str(speech), "-filter_complex", graph, "-map", "[out]", "-ar", "44100", "-ac", "2",
            "-c:a", "pcm_f32le", str(combined)])
    return combined, gain


def assemble(script: EpisodeScript, paths: list[Path], output: Path,
             *, max_seconds: float | None = None, language: str = "de-DE", labels: dict | None = None,
             pauses=None, progress=None, trim_pauses=(), units=None, tempo=1.0, music=None) -> list[Path]:
    """Mix, measure and encode one episode into one MP3; ``progress(step, done, total)`` reports each step.

    ``paths`` holds one recording per segment, or with ``units`` one per passage of several segments (a two-host
    Google recording, google_speech). Pauses go between recordings only, by the rule of a passage's last segment:
    inside a passage Gemini paces the turns itself. timeline.json then has one row per passage.

    ``max_seconds`` stops a montage longer than that; an episode has no such limit since 2026-10-04 (one episode is
    one MP3), as the script check bounds its length before the recording.

    ``trim_pauses`` names the segments whose spoken text carries a pause tag: their recording's silences over 1.5 s
    are cut to 1.2 s, since a <long pause> left up to 7.3 s of dead air in the 29 Sep exports. Other recordings stay
    as recorded.

    ``tempo`` under 1 slows every recording with its pitch kept (FFmpeg atempo, LanguagePace) before the pauses go in,
    so the pauses keep their length and the timeline and chapters are measured on the slowed audio.

    ``music`` (music.montage_music) plays the podcast's piece by the MUSIC_* rules: its bars before the first word
    and at every chapter change go into the speech track as silence, so the timeline stays measured; a chapter mark
    begins with its music, the first at 0:00, and the last ends with the music after the last word."""
    if not 0.8 <= tempo <= 1.0:
        raise AppError("Das Sprechtempo liegt zwischen 80 und 100 Prozent.", code="invalid_audio")
    report = progress or (lambda step, done=0, total=0: None)
    groups = recording_units(script, units)
    if len(paths) != len(groups):
        raise AppError("Es fehlen Audiosegmente.", code="invalid_audio")
    output.mkdir(parents=True, exist_ok=True)
    timeline, places = [], []
    bar = music["bar_seconds"] if music else 0.0
    with tempfile.TemporaryDirectory(prefix="mix-", dir=output) as temporary:
        work = Path(temporary)
        mixed = work / "mixed.wav"
        position = 0
        with wave.open(str(mixed), "wb") as target:
            target.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
            if music:
                position = round(MUSIC_INTRO_BARS * bar * 44100)
                target.writeframesraw(b"\0" * (position * 4))
                places.append({"kind": "opening", "start_seconds": 0.0, "speech_seconds": position / 44100})
            for number, (group, source) in enumerate(zip(groups, paths, strict=True)):
                members = [script.segments[index] for index in group]
                name = members[0].segment_id + (f" bis {members[-1].segment_id}" if len(members) > 1 else "")
                if not source.is_file():
                    raise AppError(f"Segment fehlt: {name}", code="invalid_audio")
                normalized = work / f"segment_{number}.wav"
                report("normalize", number, len(paths))
                ffmpeg(["-i", str(source), *(["-af", f"atempo={tempo:g}"] if tempo != 1.0 else []),
                        "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(normalized)])
                with wave.open(str(normalized), "rb") as stream:
                    frames = stream.getnframes()
                    start = position
                    peak = trimmed = 0
                    if any(member.segment_id in trim_pauses for member in members):
                        samples, trimmed = shortened_silences(array("h", stream.readframes(frames)), 44100, channels=2)
                        frames -= trimmed
                        peak = max(max(samples, default=0), -min(samples, default=0))
                        target.writeframesraw(samples.tobytes())
                    while chunk := stream.readframes(44100):
                        samples = array("h", chunk)
                        peak = max(peak, max(map(abs, samples), default=0))
                        target.writeframesraw(chunk)
                    if frames == 0 or peak == 0:
                        raise AppError(f"Segment enthält nur Stille: {name}",
                                       code="invalid_audio")
                pause_ms, reason = applied_pause(script, group[-1], pauses)
                pause = round(pause_ms * 44100 / 1000)
                if music and reason == "chapter_break":
                    # Half the pause, then the music's bars; the new chapter's first word on the bar after them.
                    half, bars = pause // 2, round(MUSIC_CHAPTER_BARS * bar * 44100)
                    places.append({"kind": "chapter", "chapter_id": script.segments[group[-1] + 1].chapter_id,
                                   "start_seconds": (start + frames + half) / 44100,
                                   "speech_seconds": (start + frames + half + bars) / 44100})
                    pause = half + bars
                    pause_ms = round(pause * 1000 / 44100)
                elif music and reason == "episode_end":
                    # The ending comes in under the last bars of speech and plays on after the last word.
                    places.append({"kind": "ending", "start_seconds": max(
                                       0.0, (start + frames) / 44100 - MUSIC_OUTRO_LEAD_BARS * bar),
                                   "speech_end_seconds": (start + frames) / 44100})
                    pause = max(pause, round((MUSIC_OUTRO_ON_BARS + MUSIC_OUTRO_FADE_BARS) * bar * 44100))
                    pause_ms = round(pause * 1000 / 44100)
                position += frames + pause
                if max_seconds is not None and position / 44100 > max_seconds:
                    raise AppError("Die Montage überschreitet die erlaubte Länge.", code="duration_exceeded",
                                   status="blocked")
                target.writeframesraw(b"\0" * (pause * 4))
                identity = ({"segment_id": members[0].segment_id, "chapter_id": members[0].chapter_id,
                             "speaker_id": members[0].speaker_id} if units is None else
                            {"segment_ids": [member.segment_id for member in members],
                             "chapter_id": members[0].chapter_id,
                             "speaker_ids": list(dict.fromkeys(member.speaker_id for member in members))})
                timeline.append({
                    **identity, "start_seconds": start / 44100,
                    "speech_end_seconds": (start + frames) / 44100,
                    "end_seconds": position / 44100,
                    "pause_ms": pause_ms, "pause_reason": reason,
                    **({"trimmed_silence_ms": round(trimmed * 1000 / 44100)} if trimmed else {}),
                })
        report("loudness", len(paths), len(paths))
        levels = loudness_of(mixed)
        music_gain = None
        if music:
            # The music is set against the speech's loudness; the loudness pass then measures both together.
            mixed, music_gain = with_music(work, mixed, music, places, float(levels["input_i"]))
            levels = loudness_of(mixed)
        gain = round(-16 - float(levels["input_i"]), 2)
        loudness = (f"volume={gain}dB,aresample=176400,"
                    f"alimiter=limit={10 ** (LIMITER_CEILING_DB / 20):.4f}:level=false:latency=true,"
                    "aresample=44100,ebur128=peak=true:framelog=quiet")
        chapters = []
        # With music a chapter begins with its music, the first at 0:00, and each ends where the next begins.
        bumpers = {place["chapter_id"]: place["start_seconds"] for place in places if place["kind"] == "chapter"}
        for number, chapter in enumerate(script.chapters):
            entries = [row for row in timeline if row["chapter_id"] == chapter.chapter_id]
            chapters.append({
                "chapter_id": chapter.chapter_id, "title": chapter.title,
                "start_seconds": (0.0 if music and number == 0 else
                                  bumpers.get(chapter.chapter_id, entries[0]["start_seconds"])),
                "end_seconds": entries[-1]["end_seconds"],
            })
        if music:
            for current, following in zip(chapters, chapters[1:]):
                current["end_seconds"] = following["start_seconds"]
            chapters[-1]["end_seconds"] = position / 44100
        metadata = work / "chapters.ffmetadata"
        atomic_text(metadata, chapter_metadata(script.title, chapters))
        encoded = work / "audio.mp3"
        report("encode", len(paths), len(paths))
        marking = ai_marking(language, script.purpose)
        encoding = ffmpeg(["-i", str(mixed), "-i", str(metadata), "-map", "0:a", "-map_metadata", "1",
                "-id3v2_version", "3", "-write_id3v1", "1",
                "-af", loudness, "-ar", "44100", "-ac", "2",
                "-c:a", "libmp3lame", "-b:a", "192k", "-metadata", f"title={script.title}",
                *marking_args(language, script.purpose),
                str(encoded)])
        info = audio_info(encoded)
        duration = float(info["format"]["duration"])
        # Read back from the file, as the chapters are: the marking counts only where the MP3 carries it.
        tags = info["format"].get("tags") or {}
        if max_seconds is not None and duration > max_seconds:
            raise AppError("Die gemessene MP3 überschreitet die erlaubte Länge.",
                           code="duration_exceeded", status="blocked")
        written_chapters = embedded_chapters(encoded)
        encoded.replace(output / "audio.mp3")
    write_json(output / "chapters.json", {"version": "1.0", "chapters": chapters,
                                          "embedded": written_chapters})
    write_json(output / "timeline.json", {
        # 1.1: rows of passages (segment_ids, speaker_ids) instead of segments.
        "schema_version": "1.0" if units is None else "1.1", "segments": timeline,
        "pcm_duration_seconds": position / 44100, "mp3_duration_seconds": duration,
        **({"music": places} if music else {}),
    })
    write_json(output / "audio_report.json", {
        "purpose": script.purpose, "duration_seconds": duration,
        "sample_rate": 44100, "channels": 2, "target_lufs": -16,
        "input_loudness": levels, "speech_quality_verified": False,
        # What the second pass did; "linear_gain_limiter" replaced loudnorm's silent fallback on 2026-10-02.
        "loudness_mode": "linear_gain_limiter", "gain_db": gain, "limiter_ceiling_dbfs": LIMITER_CEILING_DB,
        "output_loudness": output_loudness(encoding),
        "pause_policy": pauses.model_dump() if pauses is not None else None,
        **({"tempo": tempo} if tempo != 1.0 else {}),
        "applied_pause_seconds": round(sum(row["pause_ms"] for row in timeline) / 1000, 3),
        "trimmed_silence_seconds": round(sum(row.get("trimmed_silence_ms", 0) for row in timeline) / 1000, 3),
        "chapters_embedded": bool(chapters) and [c["title"] for c in written_chapters] == [c["title"] for c in chapters],
        "ai_marking": marking, "ai_marking_embedded": all(tags.get(key) == value for key, value in marking.items()),
        **({"music": {**music["credit"], "gain_db": round(music_gain, 2), "places": len(places)}} if music else {}),
    })
    notice = wording(language, "probe_notice" if script.purpose == "technical_probe" else "first_version")
    lines = [f"# {script.title}", "", notice, ""]
    for segment in script.segments:
        lines.extend([f"**{(labels or ROLE_LABELS).get(segment.speaker_id, segment.speaker_id)}:** {segment.text}", ""])
    atomic_text(output / "transcript.md", "\n".join(lines))
    return [output / name for name in (
        "audio.mp3", "chapters.json", "timeline.json", "audio_report.json", "transcript.md")]
