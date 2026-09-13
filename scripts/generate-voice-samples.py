"""Render the nine Qwen CustomVoice presets in English and/or German."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from podcast_automate.audio import assemble, worker_path
from podcast_automate.models import Chapter, EpisodeScript, Segment
from podcast_automate.process import run_process
from podcast_automate.storage import (atomic_text, file_hash, inside, load_project,
                                      project_lock, write_json)


# Verified against the installed Qwen3-TTS-12Hz-0.6B-CustomVoice model config.
VOICES = ("Ryan", "Serena", "Aiden", "Vivian", "Uncle_Fu", "Ono_Anna", "Sohee", "Eric", "Dylan")
PASSAGES = {
    "en-US": (
        "English",
        "Welcome to our podcast. How does machine learning work? "
        "Let's explore the idea step by step, with clear explanations and practical examples.",
    ),
    "de-DE": (
        "German",
        "Willkommen zu unserem Podcast. Wie funktioniert Maschinenlernen? "
        "Wir erklären die Idee Schritt für Schritt, mit verständlichen Erklärungen und praktischen Beispielen.",
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", type=Path, required=True,
                        help="Existing project whose Qwen runtime settings will be used")
    parser.add_argument("--output-dir", type=Path, default=Path("projects/voice-samples"))
    parser.add_argument("--languages", nargs="+", choices=PASSAGES, default=list(PASSAGES))
    args = parser.parse_args()
    config = load_project(args.project_dir.resolve())
    root = args.output_dir.resolve()
    languages = list(dict.fromkeys(args.languages))
    with project_lock(root):
        write_json(root / "settings.json", {
            "runtime": config.runtime.model_dump(mode="json"), "voices": list(VOICES),
            "passages": {locale: PASSAGES[locale][1] for locale in languages},
        })
        samples = []
        for locale in languages:
            language, passage = PASSAGES[locale]
            work = root / "work" / locale
            request, response = work / "tts_request.json", work / "tts_report.json"
            write_json(request, {
                "runtime": config.runtime.model_dump(mode="json"), "language": language,
                "voices": {voice.lower(): voice for voice in VOICES},
                "segments": [{"segment_id": voice.lower(), "speaker_id": voice.lower(),
                              "text": passage} for voice in VOICES],
                "cache_dir": str(root / "cache/audio"),
            })
            response.unlink(missing_ok=True)
            print(f"Rendering {len(VOICES)} {language} voices; completed WAVs are cached.", flush=True)
            result = run_process(
                [config.runtime.tts_python, str(worker_path()), "--request", str(request),
                 "--output", str(response)], timeout=config.runtime.tts_timeout_seconds)
            report = json.loads(response.read_text(encoding="utf-8"))
            if result.returncode or "error" in report:
                raise RuntimeError(report.get("error", "Qwen worker failed"))
            rows = report["segments"]
            if [row["segment_id"] for row in rows] != [voice.lower() for voice in VOICES]:
                raise RuntimeError("Worker report does not match the requested voices")
            for voice, row in zip(VOICES, rows, strict=True):
                wav = inside(root / "cache/audio", row["path"])
                if file_hash(wav) != row["sha256"]:
                    raise RuntimeError(f"Invalid cached WAV for {voice}")
                if row["settings"]["voice"] != voice or row["settings"]["language"] != language:
                    raise RuntimeError(f"Incorrect voice or language for {voice}")
                script = EpisodeScript(
                    episode_id=f"voice_{voice.lower()}", title=f"{voice} - {language}",
                    purpose="technical_probe",
                    chapters=[Chapter(chapter_id="sample", title=voice)],
                    segments=[Segment(segment_id="sample", scene_id="sample", chapter_id="sample",
                                      speaker_id="host_a", text=passage, pause_after_ms=300)],
                )
                output = root / locale / voice.lower()
                assemble(script, [wav], output, language=locale, max_seconds=120)
                audio_report = json.loads((output / "audio_report.json").read_text(encoding="utf-8"))
                # This is an audition of one host with a different preset in each file.
                atomic_text(output / "transcript.md", f"# {voice} - {language}\n\n{passage}\n")
                samples.append({
                    "voice": voice, "language": locale,
                    "mp3": (output / "audio.mp3").relative_to(root).as_posix(),
                    "sha256": file_hash(output / "audio.mp3"),
                    "duration_seconds": audio_report["duration_seconds"],
                    "render_seconds": row["render_seconds"], "cache_hit": row["cache_hit"],
                    "model_revision": report["model_revision"],
                })
                write_json(root / "samples.json", {"samples": samples})
                print(f"Ready: {voice} / {language} / {audio_report['duration_seconds']:.2f}s", flush=True)
        comparisons = []
        for locale in languages:
            language, passage = PASSAGES[locale]
            language_samples = [sample for sample in samples if sample["language"] == locale]
            script = EpisodeScript(
                episode_id="all_voices", title=f"All nine Qwen voices - {language}",
                purpose="technical_probe",
                chapters=[Chapter(chapter_id=voice.lower(), title=voice) for voice in VOICES],
                segments=[Segment(segment_id=voice.lower(), scene_id=voice.lower(),
                                  chapter_id=voice.lower(), speaker_id="host_a", text=passage,
                                  pause_after_ms=1000) for voice in VOICES],
            )
            output = root / locale / "all-voices"
            assemble(script, [root / sample["mp3"] for sample in language_samples], output,
                     language=locale)
            chapters = json.loads((output / "chapters.json").read_text(encoding="utf-8"))["chapters"]
            audio_report = json.loads((output / "audio_report.json").read_text(encoding="utf-8"))
            comparisons.append({
                "language": locale, "mp3": (output / "audio.mp3").relative_to(root).as_posix(),
                "sha256": file_hash(output / "audio.mp3"),
                "duration_seconds": audio_report["duration_seconds"], "chapters": chapters,
            })
            transcript = [f"# All nine Qwen voices - {language}", ""]
            for voice in VOICES:
                transcript.extend([f"## {voice}", "", passage, ""])
            atomic_text(output / "transcript.md", "\n".join(transcript))
        write_json(root / "samples.json", {"samples": samples, "comparisons": comparisons})
        lines = ["# Qwen voice samples", "",
                 "All nine built-in voices reading the same passage in each language. "
                 "The model, seed and loudness target are the same for every clip.", ""]
        for comparison in comparisons:
            language = PASSAGES[comparison["language"]][0]
            lines.append(f"- [Play all {language} voices]({comparison['mp3']}) "
                         f"({comparison['duration_seconds']:.1f}s)")
        lines.extend(["", "Times in the table identify each voice in the continuous tracks.", "",
                 "| Voice setting | " + " | ".join(PASSAGES[locale][0] for locale in languages) + " |",
                 "| --- | " + " | ".join("---" for _ in languages) + " |"])
        for voice in VOICES:
            cells = []
            for locale in languages:
                sample = next(s for s in samples if s["voice"] == voice and s["language"] == locale)
                comparison = next(c for c in comparisons if c["language"] == locale)
                chapter = next(c for c in comparison["chapters"] if c["title"] == voice)
                start = int(chapter["start_seconds"])
                cells.append(f"[Listen ({sample['duration_seconds']:.1f}s)]({sample['mp3']}) "
                             f"/ {start // 60}:{start % 60:02d}")
            lines.append(f"| `{voice}` | " + " | ".join(cells) + " |")
        lines.extend(["", "Use the voice setting above in `voice_profile.host_a` or "
                      "`voice_profile.host_b` in your project's `project.yaml`. "
                      "Choose two different voices, then start a new audio probe.", ""])
        for locale in languages:
            lines.extend([f"## {PASSAGES[locale][0]} passage", "", PASSAGES[locale][1], ""])
        atomic_text(root / "README.md", "\n".join(lines))
        print(f"Listening index: {root / 'README.md'}", flush=True)


if __name__ == "__main__":
    main()
