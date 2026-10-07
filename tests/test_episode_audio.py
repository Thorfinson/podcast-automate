import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.episode_audio import check_rows, run_episode_audio
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript, TopicBrief
from podcast_automate.qwen_worker import spoken_settings
from podcast_automate.runner import manifest_path, outputs_valid, status
from podcast_automate.script_models import ScenePlan, SeriesPlan
from podcast_automate.scripting import run_script
from podcast_automate.speech import AudioChoice, PausePolicy
from podcast_automate.storage import digest, file_hash, project_hash, read_yaml, write_json, write_yaml
from tests import script_fixtures as fixtures
from tests.test_audio import tone


class EpisodeAudioTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        self.calls, self.spoken = [], []
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            self.script_run = run_script(self.root, episode="ep_001")

    def synthesize(self, config, script, root, work, *, table=None, overrides=None):
        from podcast_automate.spoken_forms import SpokenForms, spoken_text
        self.calls.append(script.chapters[0].chapter_id)
        table = table if table is not None else SpokenForms()
        paths, rows = [], []
        for segment in script.segments:
            spoken = spoken_text(segment, table, overrides)
            self.spoken.append(spoken)
            path = root / "cache/audio" / f"{segment.segment_id}.wav"
            tone(path)
            paths.append(path)
            rows.append({"segment_id": segment.segment_id, "path": str(path), "sha256": file_hash(path),
                "settings": {"voice": config.voice_profile[segment.speaker_id], "text": segment.text,
                    **spoken_settings(segment.text, spoken),
                    "language": "German", "revision": config.runtime.tts_revision, "seed": config.runtime.seed,
                    "attention": config.runtime.tts_attention, "device": config.runtime.tts_device,
                    "model": config.runtime.tts_model}})
        write_json(work / "tts_report.json", {"segments": rows})
        return paths

    def legacy_rows(self, script):
        """Rows exactly as the worker wrote them before spoken forms existed: no spoken_text."""
        config, rows = self.fixture.config, []
        for segment in script.segments:
            path = self.root / "cache/audio" / f"{segment.segment_id}.wav"
            tone(path)
            rows.append({"segment_id": segment.segment_id, "path": str(path), "sha256": file_hash(path),
                "settings": {"voice": config.voice_profile[segment.speaker_id], "text": segment.text,
                    "language": "German", "revision": config.runtime.tts_revision, "seed": config.runtime.seed,
                    "attention": config.runtime.tts_attention, "device": config.runtime.tts_device,
                    "model": config.runtime.tts_model}})
        return {"segments": rows}

    def test_a_row_without_spoken_text_verifies_while_nothing_is_spoken_differently(self):
        # An audio run started before spoken forms existed resumes past its finished synthesis:
        # its rows carry no spoken_text, and none is expected while no form or override applies.
        from podcast_automate.spoken_forms import SpokenForms
        script = fixtures.example_script()
        report = self.legacy_rows(script)
        self.assertEqual(len(check_rows(self.root, script, report, self.fixture.config)), len(script.segments))
        table = SpokenForms(entries=[{"written": "model", "spoken": "Modell"}])
        with self.assertRaises(AppError) as caught:
            check_rows(self.root, script, report, self.fixture.config, table=table)
        self.assertEqual(caught.exception.code, "invalid_audio")
        # A row that carries a spoken form no table produces today is rejected as well.
        report["segments"][0]["settings"]["spoken_text"] = "Anders gesprochen."
        with self.assertRaises(AppError):
            check_rows(self.root, script, report, self.fixture.config)

    def test_requires_approval_of_unchanged_script_and_readable_view(self):
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("must not render")):
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, episode="ep_001")
            self.assertEqual(caught.exception.code, "audio_approval_required")
            path = self.root / "episodes/ep_001/script.md"
            path.write_text("A user changed the readable script", encoding="utf-8")
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, episode="ep_001", approve_audio=True)
            self.assertEqual(caught.exception.code, "script_edited")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_cli_render_and_resume_keep_approval_text_and_voices(self):
        before = file_hash(self.root / "episodes/ep_001/script.yaml")
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["audio", str(self.root), "--episode", "ep_001", "--approve-audio",
                                   "--approval-note", "Continue despite editorial reservations", "--json"]), 0)
            self.assertEqual(main(["resume", str(self.root), "--json"]), 0)
            path = manifest_path(self.root)
            saved = read_yaml(path)
            saved["audio_approved"] = False
            write_yaml(path, saved)
            receipt_hash = file_hash(path.parent / "approval.json")
            self.assertEqual(main(["resume", str(self.root), "--approve-audio",
                                   "--approval-note", "ok start now", "--json"]), 0)
            self.assertEqual(file_hash(path.parent / "approval.json"), receipt_hash)
            receipt = next((path.parent / "resume_approvals").glob("*.json"))
            self.assertEqual(json.loads(receipt.read_text())["authorization"], "ok start now")
        self.assertEqual(len(self.calls), 1)
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text())
        self.assertEqual(before, report["script_sha256"])
        self.assertFalse(report["human_listening_reviewed"])
        transcript = (self.root / report["parts"][0]["audio"]).with_name("transcript.md").read_text(encoding="utf-8")
        # Behaviour change of 19 September 2026: a transcript names the speaking role or the
        # configured host name, never the voice preset. episode_framing.txt already said a
        # voice preset is not a host identity; the transcript now says the same.
        self.assertIn("**Host A:**", transcript)
        self.assertIn("**Host B:**", transcript)
        self.assertNotIn("**Aiden:**", transcript)
        self.assertNotIn("Technische Hörprobe", transcript)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("no rewrite")):
            resumed = run_script(self.root, resume=True, run_id=self.script_run.run_id)
        self.assertEqual(resumed.status, "completed")
        self.assertTrue(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_resume_reuses_finished_chapters_after_interruption(self):
        def two_chapters(prompt, output_type, directory, **kwargs):
            result, meta = self.fixture.model(prompt, output_type, directory, **kwargs)
            if output_type is SeriesPlan:
                result.episodes[0].scenes.append(ScenePlan(scene_id="scene_two", title="Second",
                    question="What follows?", purpose="synthesis", finding_ids=["f_energy"],
                    explanation_steps=["Apply the comparison."]))
            if output_type is EpisodeScript:
                result.chapters.append(Chapter(chapter_id="scene_two", title="Second"))
                result.segments[-1].scene_id = result.segments[-1].chapter_id = "scene_two"
            return result, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=two_chapters):
            run_script(self.root, episode="ep_001")

        def interrupted(config, script, root, work, **kwargs):
            if script.chapters[0].chapter_id == "scene_two":
                raise AppError("Interrupted", code="test_interrupt")
            return self.synthesize(config, script, root, work)
        with patch("podcast_automate.episode_audio.run_tts", side_effect=interrupted):
            first = run_episode_audio(self.root, episode="ep_001", approve_audio=True)
        self.assertEqual(first.status, "failed")
        first.audio_approved = False
        write_yaml(manifest_path(self.root, first.run_id), first.model_dump(mode="json"))
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("must stay paused")):
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, resume=True, run_id=first.run_id)
            self.assertEqual(caught.exception.code, "audio_approval_required")
            with patch("podcast_automate.episode_audio.digest", return_value="changed"):
                with self.assertRaises(AppError) as caught:
                    run_episode_audio(self.root, resume=True, run_id=first.run_id, approve_audio=True)
            self.assertEqual(caught.exception.code, "inputs_changed")
            self.assertFalse(read_yaml(manifest_path(self.root, first.run_id))["audio_approved"])
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize):
            second = run_episode_audio(self.root, resume=True, run_id=first.run_id, approve_audio=True)
        self.assertEqual(second.status, "completed")
        self.assertEqual(self.calls, ["scene_example", "scene_two"])
        self.assertTrue(all(outputs_valid(self.root, s) for s in second.stages.values()))

    def render(self, **kwargs):
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize):
            return run_episode_audio(self.root, episode="ep_001", approve_audio=True, **kwargs)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_the_table_reaches_the_engine_while_the_transcript_keeps_the_script(self):
        write_json(self.root / "studio/spoken_forms.json", {"schema_version": "1.0",
            "entries": [{"written": "model", "spoken": "Modell"}]})
        run = self.render()
        self.assertEqual(run.status, "completed")
        self.assertTrue(any("Modell" in spoken for spoken in self.spoken))
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["pronunciation"]["applied"], {"model": 1})
        transcript = (self.root / report["parts"][0]["audio"]).with_name("transcript.md").read_text(encoding="utf-8")
        self.assertIn("What does this model compare?", transcript)
        self.assertNotIn("Modell", transcript)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_one_override_costs_one_synthesis_and_reuses_the_saved_approval(self):
        self.assertEqual(self.render().status, "completed")
        decision = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml",
                   {**decision, "spoken_overrides": {"seg_002": "Es bewertet Möglichkeiten."}})
        self.spoken.clear()
        # No fresh approval: the script hash and the voices are unchanged.
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize):
            second = run_episode_audio(self.root, episode="ep_001")
        self.assertEqual(second.status, "completed")
        self.assertIn("Es bewertet Möglichkeiten.", self.spoken)
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["spoken_overrides"], {"seg_002": "Es bewertet Möglichkeiten."})
        destination = (self.root / report["parts"][0]["audio"]).parent
        show_notes = (destination / "show_notes.md").read_text(encoding="utf-8")
        self.assertIn("seg_002", show_notes)
        self.assertIn("Aussprache-Hinweise", show_notes)
        # Show notes name the hosts by role or configured name; the voice presets are listed apart.
        self.assertIn("Hörfassung mit Host A und Host B.", show_notes)
        self.assertIn("Stimmen: Aiden (Host A) und Vivian (Host B).", show_notes)
        self.assertNotIn("Hörfassung mit Aiden", show_notes)
        self.assertIn("Stimmen: Aiden (Host A) und Vivian (Host B).", (destination / "README.md").read_text(encoding="utf-8"))
        self.assertIn("Unklar", (destination / "listening_sheet.md").read_text(encoding="utf-8"))
        # The audio decision keeps the operator's overrides across the run that used them.
        self.assertEqual(read_yaml(self.root / "episodes/ep_001/audio_review.yaml")["spoken_overrides"],
                         {"seg_002": "Es bewertet Möglichkeiten."})

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_changed_spoken_form_is_rejected_by_the_verifier(self):
        self.assertEqual(self.render().status, "completed")
        work = manifest_path(self.root, self.root and json.loads(
            (self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))["run_id"]).parent
        report = json.loads((work / "tts_report.json").read_text(encoding="utf-8"))
        report["segments"][0]["settings"]["spoken_text"] = "Ein anderer Satz."
        write_json(work / "tts_report.json", report)
        from podcast_automate.episode_audio import check_rows
        from podcast_automate.errors import AppError as Error
        with self.assertRaises(Error) as caught:
            check_rows(self.root, EpisodeScript.model_validate(
                json.loads((work / "approved_script.json").read_text(encoding="utf-8"))),
                report, self.fixture.config)
        self.assertEqual(caught.exception.code, "invalid_audio")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_legacy_approval_without_a_pause_policy_still_counts(self):
        from podcast_automate.speech import AudioChoice
        chosen = AudioChoice(voices=self.fixture.config.voice_profile).model_dump()
        self.assertEqual(self.render(audio_choice=chosen).status, "completed")
        decision = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")
        legacy = {k: v for k, v in decision["audio_generation"].items() if k != "pauses"}
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml", {**decision, "audio_generation": legacy})
        with patch("podcast_automate.episode_audio.run_tts", side_effect=self.synthesize):
            self.assertEqual(run_episode_audio(self.root, episode="ep_001",
                                               audio_choice=chosen).status, "completed")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_default_pause_policy_is_stored_and_hashed_as_before_the_policy_existed(self):
        chosen = AudioChoice(voices=self.fixture.config.voice_profile)
        run = self.render(audio_choice=chosen.model_dump())
        self.assertEqual(run.status, "completed")
        inputs = json.loads((manifest_path(self.root, run.run_id).parent / "inputs.json").read_text(encoding="utf-8"))
        # The hashed record and the stored decision have the shape they had before pauses existed.
        self.assertEqual(inputs["audio_generation"], {"provider": "qwen3_local", "voices": chosen.voices})
        self.assertNotIn("pauses", read_yaml(self.root / "episodes/ep_001/audio_review.yaml")["audio_generation"])
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("cached synthesis")):
            self.assertEqual(run_episode_audio(self.root, resume=True, run_id=run.run_id).status, "completed")
        slower = chosen.model_copy(update={"pauses": PausePolicy(chapter_break_ms=1800)})
        self.assertEqual(self.render(audio_choice=slower.model_dump()).status, "completed")
        stored = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")["audio_generation"]
        self.assertEqual(stored["pauses"]["chapter_break_ms"], 1800)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_an_episode_is_one_mp3_whatever_its_length(self):
        """The user's rule, 2026-10-04: one episode is one MP3. Until then an episode over 30 minutes was split into
        parts (Ontologies: seven episodes of 33 to 37 minutes came in two). The montage gets the whole script and no
        length limit; the script check bounds an episode before the recording."""
        from podcast_automate import episode_audio as module
        calls, original = [], module.assemble

        def recording(script, paths, output, **kwargs):
            calls.append(([s.segment_id for s in script.segments], output, kwargs.get("max_seconds")))
            return original(script, paths, output, **kwargs)
        with patch("podcast_automate.episode_audio.assemble", side_effect=recording):
            run = self.render()
        self.assertEqual(run.status, "completed")
        script = EpisodeScript.model_validate(read_yaml(self.root / "episodes/ep_001/script.yaml"))
        destination = self.root / "exports/ep_001" / run.run_id
        self.assertEqual(calls, [([s.segment_id for s in script.segments], destination, None)])
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        self.assertEqual([p["audio"] for p in report["parts"]], [f"exports/ep_001/{run.run_id}/audio.mp3"])
        self.assertEqual((destination / "playlist.m3u").read_text(encoding="utf-8"), "#EXTM3U\naudio.mp3\n")
        self.assertIn("[Folge anhören](audio.mp3)", (destination / "README.md").read_text(encoding="utf-8"))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_an_english_podcast_exports_english_files_and_marks_its_mp3_in_english(self):
        """D-153 and D-154 (2026-10-07): the export of an English project is written in English, and its MP3 says in
        its tags, in English, that a model wrote the script and synthetic voices speak it."""
        from podcast_automate.audio import DIGITAL_SOURCE_TYPE, audio_info
        config = self.fixture.config.model_copy(update={"language": "en-US"})
        write_yaml(self.root / "project.yaml", config.model_dump(mode="json"))
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            self.assertEqual(run_script(self.root, episode="ep_001").status, "completed")

        def english(config, script, root, work, **kwargs):
            paths = self.synthesize(config, script, root, work, **kwargs)
            report = json.loads((work / "tts_report.json").read_text(encoding="utf-8"))
            for row in report["segments"]:
                row["settings"]["language"] = "English"
            write_json(work / "tts_report.json", report)
            return paths
        with patch("podcast_automate.episode_audio.run_tts", side_effect=english):
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True)
        self.assertEqual(run.status, "completed")
        destination = self.root / "exports/ep_001" / run.run_id
        notes = (destination / "show_notes.md").read_text(encoding="utf-8")
        self.assertIn("Audio version with Host A and Host B.\nVoices: Aiden (Host A) and Vivian (Host B).", notes)
        self.assertIn("\n## Chapters\n\n- 0:00 A concrete comparison\n", notes)
        self.assertIn("\n## Transparency note\n\nThis output is a source-bound synthesis.", notes)
        sheet = (destination / "listening_sheet.md").read_text(encoding="utf-8")
        self.assertIn("| Time | Chapter | Unclear | Attention lost | Pronunciation |", sheet)
        self.assertIn("\n## Transparency note\n", sheet)
        readme = (destination / "README.md").read_text(encoding="utf-8")
        self.assertIn("\nFirst audio version for listening review.\n", readme)
        self.assertIn("- [Listen to the episode](audio.mp3) – ", readme)
        tags = audio_info(destination / "audio.mp3")["format"]["tags"]
        self.assertEqual((tags["comment"], tags["DIGITAL_SOURCE_TYPE"], tags["AI_GENERATED"]),
                         ("AI-generated: script written by a language model, speech made with synthetic voices.",
                          DIGITAL_SOURCE_TYPE, "true"))
        for text in (notes, sheet, readme):
            for german in ("Kapitel", "Stimmen", "Hörprüfung", "Zeitmarken", "Folge"):
                self.assertNotIn(german, text)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_german_export_readme_reads_as_before(self):
        """Pinned on 2026-10-07 before content_text.py (D-153): a German project's README.md byte for byte."""
        run = self.render()
        destination = self.root / "exports/ep_001" / run.run_id
        report = json.loads((self.root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        minutes = report["parts"][0]["duration_seconds"] / 60
        self.assertEqual((destination / "README.md").read_text(encoding="utf-8"),
                         "# A model compares possibilities\n\nErste Audiofassung zur Hörprüfung.\n"
                         "Stimmen: Aiden (Host A) und Vivian (Host B).\n\n"
                         f"- [Folge anhören](audio.mp3) – {minutes:.2f} Minuten\n\n"
                         "Der gesamte freigegebene Text ist in Skriptreihenfolge enthalten.\n"
                         "Die technische Montage ersetzt keine Hörprüfung von Aussprache und Natürlichkeit.\n")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_changed_pause_policy_needs_a_fresh_approval(self):
        self.assertEqual(self.render().status, "completed")
        from podcast_automate.speech import AudioChoice
        louder = AudioChoice(voices=self.fixture.config.voice_profile,
                             pauses={"same_speaker_ms": 900, "speaker_change_ms": 900, "chapter_break_ms": 1800})
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("must not render")):
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, episode="ep_001", audio_choice=louder.model_dump())
        self.assertEqual(caught.exception.code, "audio_approval_required")

    def test_wrong_voice_is_rejected_before_assembly(self):
        def wrong_voice(config, script, root, work, **kwargs):
            paths = self.synthesize(config, script, root, work, **kwargs)
            report = json.loads((work / "tts_report.json").read_text())
            report["segments"][0]["settings"]["voice"] = "wrong"
            write_json(work / "tts_report.json", report)
            return paths
        with patch("podcast_automate.episode_audio.run_tts", side_effect=wrong_voice), \
             patch("podcast_automate.episode_audio.assemble", side_effect=AssertionError("must not assemble")):
            result = run_episode_audio(self.root, episode="ep_001", approve_audio=True)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.stages["synthesis"].error.code, "invalid_audio")


class ListeningSheetTests(unittest.TestCase):
    def test_an_episode_in_parts_names_the_part_of_every_row(self):
        """2026-10-02: each part's times start at 0:00, so a two-part sheet listed "0:00" twice without saying where."""
        from podcast_automate.episode_audio import render_listening_sheet
        script = fixtures.example_script()
        chapters = [{"part": 1, "timestamp": "0:00", "title": "Erstes"}, {"part": 1, "timestamp": "12:30", "title": "Zweites"},
                    {"part": 2, "timestamp": "0:00", "title": "Drittes"}]
        sheet = render_listening_sheet(script, chapters, language="de-DE")
        self.assertIn("| Teil | Zeit | Kapitel | Unklar | Aufmerksamkeit verloren | Aussprache |", sheet)
        self.assertIn("| 1 | 0:00 | Erstes | | | |", sheet)
        self.assertIn("| 2 | 0:00 | Drittes | | | |", sheet)
        # A single part keeps the sheet as it was.
        single = render_listening_sheet(script, chapters[:2], language="de-DE")
        self.assertIn("| Zeit | Kapitel | Unklar | Aufmerksamkeit verloren | Aussprache |", single)
        self.assertIn("| 12:30 | Zweites | | | |", single)
        self.assertNotIn("Teil", single)


class ProjectHashTests(unittest.TestCase):
    def test_a_brief_without_host_names_hashes_as_it_did_before_the_field_existed(self):
        config = TopicBrief(topic="Hash stability")
        dump = config.model_dump(mode="json")
        self.assertIsNone(dump["host_names"])  # the field serialises as null in project.yaml
        self.assertEqual(project_hash(config), digest({k: v for k, v in dump.items() if k != "host_names"}))
        self.assertNotEqual(project_hash(config), digest(dump))
        named = config.model_copy(update={"host_names": {"host_a": "Mara", "host_b": "Jonas"}})
        self.assertNotEqual(project_hash(named), project_hash(config))
        self.assertEqual(project_hash(named), digest(named.model_dump(mode="json")))

    def test_series_goal_and_recency_leave_every_dump_unchanged_while_unset(self):
        """Added 2026-09-30; a brief without them hashes and dumps exactly as before, also in the Python-mode dump
        that teaching supplements bind to."""
        config = TopicBrief(topic="Hash stability")
        for mode in ("json", "python"):
            self.assertFalse({"series_goal", "recency_months"} & set(config.model_dump(mode=mode)), mode)
        legacy = {k: v for k, v in config.model_dump(mode="json").items() if k != "host_names"}
        self.assertEqual(project_hash(config), digest(legacy))
        aimed = config.model_copy(update={"series_goal": {"understand": 3, "evaluate": 1, "apply": 0},
                                          "recency_months": 6})
        self.assertEqual((aimed.model_dump(mode="json")["series_goal"]["understand"],
                          aimed.model_dump(mode="json")["recency_months"]), (3, 6))
        self.assertNotEqual(project_hash(aimed), project_hash(config))
        # Stored as written and read back unchanged; a goal needs at least one aim above zero.
        self.assertEqual(TopicBrief.model_validate(aimed.model_dump(mode="json")), aimed)
        for wrong in ({"series_goal": {"understand": 0, "evaluate": 0, "apply": 0}},
                      {"series_goal": {"understand": 4, "evaluate": 0, "apply": 0}}, {"recency_months": 0}):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                TopicBrief.model_validate({**config.model_dump(mode="json"), **wrong})

    def test_a_recorded_run_still_matches_its_unchanged_project(self):
        from podcast_automate.models import RunManifest, StageRecord
        from podcast_automate.storage import init_project
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            config = TopicBrief(topic="Recorded before host names existed")
            init_project(root, config)
            recorded = digest({k: v for k, v in config.model_dump(mode="json").items() if k != "host_names"})
            manifest = RunManifest(run_id="run_old", kind="audio_probe", project_hash=recorded, input_hash="x",
                                   stages={"synthesis": StageRecord()})
            write_yaml(manifest_path(root, "run_old"), manifest.model_dump(mode="json"))
            self.assertFalse(status(root, "run_old")["project_changed"])


if __name__ == "__main__":
    unittest.main()
