import json
import math
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from podcast_automate.audio import assemble, audio_info, ffmpeg, run_tts
from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.runner import probe_script, run_probe
from podcast_automate.storage import file_hash, init_project, write_json


def tone(path: Path, *, silent=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 22050
    samples = [0 if silent else int(4000 * math.sin(2 * math.pi * 230 * i / rate))
               for i in range(rate)]
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(struct.pack(f"<{len(samples)}h", *samples))


class TtsLanguageTests(unittest.TestCase):
    def test_project_language_reaches_worker_request(self):
        for locale, language in (("de-DE", "German"), ("en-US", "English")):
            with self.subTest(locale=locale), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config = TopicBrief(topic="Voice comparison", language=locale)
                script = probe_script(locale)

                def worker(command, *, timeout):
                    request = json.loads((root / "tts_request.json").read_text(encoding="utf-8"))
                    self.assertEqual(request["language"], language)
                    self.assertEqual(request["voices"], config.voice_profile)
                    rows = []
                    for segment in request["segments"]:
                        path = root / "cache/audio" / f"{segment['segment_id']}.wav"
                        tone(path)
                        rows.append({"segment_id": segment["segment_id"], "path": str(path),
                                     "sha256": file_hash(path)})
                    write_json(root / "tts_report.json", {"segments": rows})
                    return subprocess.CompletedProcess(command, 0)

                with patch("podcast_automate.audio.run_process", side_effect=worker):
                    self.assertEqual(len(run_tts(config, script, root, root)), 4)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Audio mit Leerzeichen & ä"
        self.root.mkdir()
        self.script = probe_script()
        self.paths = []
        for index in range(len(self.script.segments)):
            path = self.root / f"source {index}.wav"
            tone(path)
            self.paths.append(path)

    def test_montage_real_mp3_and_measured_chapters(self):
        output = self.root / "result"
        produced = assemble(self.script, self.paths, output)
        self.assertEqual(len(produced), 5)
        info = audio_info(output / "audio.mp3")
        audio_stream = next(s for s in info["streams"] if s["codec_type"] == "audio")
        self.assertEqual(audio_stream["codec_name"], "mp3")
        self.assertEqual(audio_stream["channels"], 2)
        self.assertEqual(int(audio_stream["sample_rate"]), 44100)
        measured = ffmpeg(["-i", str(output / "audio.mp3"), "-af",
                           "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
        levels = json.loads(re.findall(r'\{\s*"input_i".*?\}', measured, flags=re.DOTALL)[-1])
        self.assertAlmostEqual(float(levels["input_i"]), -16, delta=0.6)
        expected = 4 + sum(s.pause_after_ms for s in self.script.segments) / 1000
        self.assertAlmostEqual(float(info["format"]["duration"]), expected, delta=0.1)
        timeline = json.loads((output / "timeline.json").read_text())
        self.assertAlmostEqual(timeline["segments"][1]["start_seconds"], 1.45)
        chapters = json.loads((output / "chapters.json").read_text())["chapters"]
        self.assertAlmostEqual(chapters[1]["start_seconds"], 3.25)
        self.assertIn(self.script.segments[0].text, (output / "transcript.md").read_text(encoding="utf-8"))

    def test_missing_silent_and_overlong_audio_are_blocked(self):
        for case in ("missing", "silent", "overlong"):
            with self.subTest(case=case):
                paths = list(self.paths)
                if case == "missing":
                    paths[0] = self.root / "missing.wav"
                elif case == "silent":
                    paths[0] = self.root / "silent.wav"
                    tone(paths[0], silent=True)
                output = self.root / case
                with self.assertRaises(AppError):
                    assemble(self.script, paths, output, max_seconds=1 if case == "overlong" else 1800)
                self.assertFalse((output / "audio.mp3").exists())

    def test_resume_after_montage_failure_reuses_synthesis(self):
        project = self.root / "project"
        init_project(project, TopicBrief(topic="Voice sample", language="en-US"))

        def synthesize(config, script, root, work):
            self.assertEqual(config.language, "en-US")
            self.assertEqual(script.title, "Podcast Automate – Technical Voice Sample")
            rows, paths = [], []
            for index, segment in enumerate(script.segments):
                path = root / "cache/audio" / f"cached_{index}.wav"
                tone(path)
                paths.append(path)
                rows.append({"segment_id": segment.segment_id, "path": str(path),
                             "sha256": file_hash(path)})
            write_json(work / "tts_report.json", {"segments": rows})
            return paths

        with patch("podcast_automate.runner.run_tts", side_effect=synthesize) as synth:
            with patch("podcast_automate.runner.assemble",
                       side_effect=AppError("Unterbrochen", code="test_failure")):
                first = run_probe(project, kind="audio_probe", approve_audio=True)
            self.assertEqual(first.status, "failed")
            self.assertEqual(first.stages["synthesis"].status, "completed")
            second = run_probe(project, resume=True)
            third = run_probe(project, resume=True)
        self.assertEqual(second.status, "completed")
        self.assertEqual(third.run_id, first.run_id)
        self.assertEqual(synth.call_count, 1)
        self.assertEqual(third.stages["assembly"].attempts, 2)
        transcript = (project / "probes/audio" / third.run_id / "transcript.md").read_text(encoding="utf-8")
        self.assertIn("Technical voice sample; not a researched podcast episode.", transcript)
        self.assertIn(probe_script("en-US").segments[0].text, transcript)


if __name__ == "__main__":
    unittest.main()
