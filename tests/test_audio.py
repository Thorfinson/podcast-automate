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

from podcast_automate.audio import assemble, audio_info, chapter_metadata, embedded_chapters, ffmpeg, metadata_value, run_tts
from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.speech import PausePolicy
from podcast_automate.spoken_forms import SpokenForms
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

    def test_pause_minimums_apply_per_transition_and_a_longer_planned_pause_is_kept(self):
        script = self.script.model_copy(deep=True)
        # Four segments: same speaker, speaker change, chapter break, then the end.
        for segment in script.segments[:3]:
            segment.chapter_id = segment.scene_id = "dialog"
        script.segments[0].speaker_id = script.segments[1].speaker_id = "host_a"
        script.segments[2].speaker_id = "host_b"
        for segment in script.segments:
            segment.pause_after_ms = 100
        script.segments[1].pause_after_ms = 2000
        pauses = PausePolicy()
        produced = assemble(script, self.paths, self.root / "paused", pauses=pauses)
        self.assertEqual(len(produced), 5)
        timeline = json.loads((self.root / "paused/timeline.json").read_text())["segments"]
        self.assertEqual([row["pause_reason"] for row in timeline],
                         ["same_speaker", "speaker_change", "chapter_break", "episode_end"])
        self.assertEqual([row["pause_ms"] for row in timeline],
                         [pauses.same_speaker_ms, 2000, pauses.chapter_break_ms, 100])
        for row in timeline[:-1]:
            measured = round((row["end_seconds"] - row["speech_end_seconds"]) * 1000)
            self.assertAlmostEqual(measured, row["pause_ms"], delta=2)
        report = json.loads((self.root / "paused/audio_report.json").read_text())
        self.assertEqual(report["pause_policy"], pauses.model_dump())
        self.assertAlmostEqual(report["applied_pause_seconds"], (250 + 2000 + 900 + 100) / 1000, places=3)
        # The produced MP3, not the timeline's own arithmetic: four one-second tones plus the
        # planned pauses would be 6.3 s; the two raised pauses add 0.15 s and 0.8 s.
        measured = float(audio_info(self.root / "paused/audio.mp3")["format"]["duration"])
        planned = 4 + (100 + 2000 + 100 + 100) / 1000
        raised = ((pauses.same_speaker_ms - 100) + (pauses.chapter_break_ms - 100)) / 1000
        self.assertGreaterEqual(measured + 0.1, planned + raised)
        self.assertAlmostEqual(measured, planned + raised, delta=0.1)

    def test_without_a_policy_the_planned_pauses_are_used_unchanged(self):
        assemble(self.script, self.paths, self.root / "unchanged")
        timeline = json.loads((self.root / "unchanged/timeline.json").read_text())["segments"]
        self.assertEqual([row["pause_ms"] for row in timeline],
                         [s.pause_after_ms for s in self.script.segments])
        self.assertIsNone(json.loads((self.root / "unchanged/audio_report.json").read_text())["pause_policy"])

    def test_chapters_are_written_into_the_mp3_and_read_back(self):
        script = self.script.model_copy(deep=True)
        assemble(script, self.paths, self.root / "chaptered")
        saved = json.loads((self.root / "chaptered/chapters.json").read_text(encoding="utf-8"))
        self.assertEqual([row["title"] for row in saved["chapters"]],
                         [c.title for c in script.chapters])
        written = embedded_chapters(self.root / "chaptered/audio.mp3")
        self.assertEqual([row["title"] for row in written], [c.title for c in script.chapters])
        self.assertAlmostEqual(written[1]["start_seconds"], saved["chapters"][1]["start_seconds"], delta=0.05)
        self.assertTrue(json.loads((self.root / "chaptered/audio_report.json").read_text())["chapters_embedded"])

    def test_chapter_titles_with_metadata_characters_survive_the_round_trip(self):
        # FFmpeg's metadata file reserves '=', ';', '#', the backslash and the newline.
        self.assertEqual(metadata_value("a=b;c#d\\e\nf"), "a\\=b\\;c\\#d\\\\e\\\nf")
        script = self.script.model_copy(deep=True)
        script.title = "Titel = mit; Zeichen #1 \\ Ende"
        script.chapters[0].title = "Teil 1: x=y; #1 \\ Ende"
        script.chapters[1].title = "Zweiter; Teil = #2"
        metadata = chapter_metadata(script.title, [{"title": script.chapters[0].title, "start_seconds": 0, "end_seconds": 1}])
        self.assertIn("title=Titel \\= mit\\; Zeichen \\#1 \\\\ Ende", metadata)
        self.assertIn("title=Teil 1: x\\=y\\; \\#1 \\\\ Ende", metadata)
        assemble(script, self.paths, self.root / "escaped")
        written = embedded_chapters(self.root / "escaped/audio.mp3")
        self.assertEqual([row["title"] for row in written], [c.title for c in script.chapters])
        self.assertTrue(json.loads((self.root / "escaped/audio_report.json").read_text())["chapters_embedded"])
        # The report compares the titles that came back, not only how many chapters there are.
        wrong = [{**row, "title": "anders"} for row in written]
        with patch("podcast_automate.audio.embedded_chapters", return_value=wrong):
            assemble(script, self.paths, self.root / "mismatch")
        self.assertFalse(json.loads((self.root / "mismatch/audio_report.json").read_text())["chapters_embedded"])

    def test_the_transcript_names_roles_or_host_names_never_the_voice_preset(self):
        assemble(self.script, self.paths, self.root / "roles")
        transcript = (self.root / "roles/transcript.md").read_text(encoding="utf-8")
        self.assertIn("**Host A:**", transcript)
        self.assertNotIn("**Aiden:**", transcript)
        assemble(self.script, self.paths, self.root / "named", labels={"host_a": "Mara", "host_b": "Jonas"})
        named = (self.root / "named/transcript.md").read_text(encoding="utf-8")
        self.assertIn("**Mara:**", named)
        self.assertIn("**Jonas:**", named)

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

        def synthesize(config, script, root, work, **kwargs):
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
