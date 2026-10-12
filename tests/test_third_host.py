"""A third host (D-174): the brief, the script checks and prompts, the recording's voices and passages, and that a
project of two hosts stays exactly as it was."""
import json
import shutil
import unittest
from unittest.mock import patch

from podcast_automate.content_text import text as wording
from podcast_automate.episode_audio import render_export_notes, run_episode_audio, voices_line
from podcast_automate.google_speech import (MAX_PASSAGE_SPEAKERS, PASSAGE_RULE, check_google_rows, passage_rule,
                                            plan_passages, request_body)
from podcast_automate.models import (EpisodeScript, Segment, ThirdHost, TopicBrief, host_labels, host_roles_of,
                                     host_voices)
from podcast_automate.polishing import HOST_ROLES, cast_note, host_roles
from podcast_automate.script_advisories import advisories, dialogue_shape, partner_share, turns
from podcast_automate.script_checks import script_metrics, validate_script
from podcast_automate.scripting import run_script
from podcast_automate.speech import THIRD_VOICES, AudioChoice, audio_catalog, selected_audio
from podcast_automate.storage import bound_brief, project_hash, read_yaml, write_json, write_yaml
from tests import script_fixtures as fixtures
from tests.test_google_speech import segments, spoken_answer

EA_NASIR = {"name": "Ea-nasir", "role": "A copper trader from Ur, 3750 years old: sly, warm, astonished by today.",
            "voice": "Dylan"}


def three_hosts(**update):
    return TopicBrief(**{"topic": "Geld", "voice_profile": {"host_a": "Ryan", "host_b": "Serena"},
                         "third_host": EA_NASIR, **update})


def with_third(script):
    """``script`` with a short line of host_c after its first segment, in the same scene."""
    first = script.segments[0]
    added = Segment(segment_id="seg_c01", scene_id=first.scene_id, chapter_id=first.chapter_id, speaker_id="host_c",
                    text="Bei uns in Ur schrieb man so etwas auf Ton.")
    return script.model_copy(update={"segments": [first, added, *script.segments[1:]]})


class BriefTests(unittest.TestCase):
    def test_two_hosts_dump_and_hash_as_before_the_field(self):
        brief = TopicBrief(topic="Geld")
        self.assertNotIn("third_host", brief.model_dump(mode="json"))
        self.assertEqual(host_roles_of(brief), ("host_a", "host_b"))
        self.assertEqual(host_labels(brief), {"host_a": "Host A", "host_b": "Host B"})
        self.assertEqual(host_voices(brief), brief.voice_profile)
        # The research lane binds a snapshot from before the field as two hosts, so its run stays resumable.
        snapshot = brief.model_dump(mode="json")
        self.assertEqual(project_hash(bound_brief(three_hosts(), snapshot, "research")),
                         project_hash(bound_brief(TopicBrief(topic="Geld", voice_profile=three_hosts().voice_profile),
                                                  snapshot, "research")))

    def test_a_third_host_has_its_own_voice_and_name_and_changes_the_hash(self):
        brief = three_hosts(host_names={"host_a": "Mara", "host_b": "Juri"})
        self.assertEqual(brief.model_dump(mode="json")["third_host"], EA_NASIR)
        self.assertNotEqual(project_hash(brief), project_hash(TopicBrief(topic="Geld",
                                                                         voice_profile=brief.voice_profile)))
        self.assertEqual(host_roles_of(brief), ("host_a", "host_b", "host_c"))
        self.assertEqual(host_labels(brief), {"host_a": "Mara", "host_b": "Juri", "host_c": "Ea-nasir"})
        self.assertEqual(host_voices(brief), {"host_a": "Ryan", "host_b": "Serena", "host_c": "Dylan"})
        with self.assertRaises(ValueError):
            three_hosts(third_host={**EA_NASIR, "voice": "Serena"})
        with self.assertRaises(ValueError):
            three_hosts(host_names={"host_a": "Ea-Nasir", "host_b": "Juri"})
        with self.assertRaises(ValueError):
            ThirdHost(name="Ea{nasir}", role="x")
        self.assertEqual(ThirdHost(name="Ea-nasir", role="Kaufmann").voice, "Dylan")


class ScriptTests(unittest.TestCase):
    def test_the_script_check_holds_a_script_to_the_projects_hosts(self):
        entry, script = fixtures.example_plan().episodes[0], fixtures.example_script()
        self.assertEqual(validate_script(script, entry, check_duration=False), [])
        # Two hosts keep the sentence the writer has always been told.
        alone = script.model_copy(update={"segments": [s for s in script.segments if s.speaker_id == "host_a"]})
        self.assertIn("Use both hosts in the dialogue.", validate_script(alone, entry, check_duration=False))
        three = ("host_a", "host_b", "host_c")
        self.assertTrue(any("host_c" in error for error in validate_script(script, entry, check_duration=False,
                                                                            roles=three)))
        self.assertEqual(validate_script(with_third(script), entry, check_duration=False, roles=three), [])
        # host_c in a project of two hosts is a defect the writer corrects.
        self.assertTrue(validate_script(with_third(script), entry, check_duration=False))

    def test_the_cast_reaches_three_host_prompts_only(self):
        self.assertEqual(cast_note(TopicBrief(topic="Geld")), "")
        self.assertIs(host_roles(TopicBrief(topic="Geld")), HOST_ROLES)
        note = cast_note(three_hosts())
        self.assertTrue(note.startswith("THREE HOSTS."))
        data = json.loads(note.splitlines()[1])
        self.assertEqual(data["third_host"], {"speaker_id": "host_c", "name": "Ea-nasir", "role": EA_NASIR["role"]})
        self.assertEqual(host_roles(three_hosts())["host_c"], EA_NASIR["role"])
        self.assertEqual(host_roles(three_hosts())["host_a"], HOST_ROLES["host_a"])

    def test_both_partners_count_toward_the_partner_share(self):
        script = with_third(fixtures.example_script())
        heard = turns(script)
        words = {speaker: count for speaker, _, count in heard}
        self.assertAlmostEqual(partner_share(heard), (words["host_b"] + words["host_c"]) / sum(words.values()))
        self.assertEqual(dialogue_shape(fixtures.example_script())["partner_share"],
                         round(partner_share(turns(fixtures.example_script())), 2))
        long = script.model_copy(update={"segments": [script.segments[0].model_copy(
            update={"text": "Wort " * 400}), *script.segments[1:]]})
        entry = fixtures.example_plan().episodes[0]
        rows = advisories(long, entry, script_metrics(long), language="de-DE")
        self.assertIn("low_partners_share", [row["code"] for row in rows])
        self.assertNotIn("low_partner_share", [row["code"] for row in rows])


class AudioChoiceTests(unittest.TestCase):
    def test_a_third_voice_is_left_out_until_set_and_must_be_a_voice_of_its_own(self):
        two = {"provider": "google_gemini_tts", "voices": {"host_a": "Erinome", "host_b": "Sadachbia"}}
        self.assertNotIn("third_voice", AudioChoice.model_validate(two).model_dump())
        self.assertNotIn("third_style", AudioChoice.model_validate(two).model_dump())
        chosen = AudioChoice.model_validate({**two, "third_voice": "Algenib", "third_style": "sly and warm"})
        self.assertEqual(chosen.model_dump()["third_voice"], "Algenib")
        for wrong in ("Erinome", "Dylan"):
            with self.assertRaises(ValueError):
                AudioChoice.model_validate({**two, "third_voice": wrong})
        # Only Google takes a style.
        qwen = AudioChoice(voices={"host_a": "Aiden", "host_b": "Vivian"}, third_voice="Dylan", third_style="sly")
        self.assertIsNone(qwen.third_style)
        self.assertEqual(audio_catalog()["qwen3_local"]["third_voices"], THIRD_VOICES["qwen3_local"])

    def test_a_recording_voices_the_third_host_and_alternation_never_moves_it(self):
        choice = AudioChoice(provider="google_gemini_tts", voices={"host_a": "Erinome", "host_b": "Charon"},
                             alternate_roles=True, third_style="sly")
        self.assertIs(choice.for_hosts(TopicBrief(topic="Geld")), choice)
        voiced = choice.for_episode("ep_002").for_hosts(three_hosts())
        # Charon is taken, so the next of Google's third voices speaks.
        self.assertEqual(voiced.voices, {"host_a": "Charon", "host_b": "Erinome", "host_c": "Algenib"})
        self.assertEqual(choice.for_hosts(three_hosts()).for_episode("ep_002").voices["host_c"], "Algenib")
        self.assertEqual(choice.spoken_styles("de-DE")["host_c"], "sly")
        # The choice itself still holds two voices, so approvals and hashes keep their form.
        self.assertEqual(set(choice.model_dump()["voices"]), {"host_a", "host_b"})

    def test_the_workspace_third_voice_leaves_two_host_projects_as_they_were(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            choice = {"provider": "google_gemini_tts", "voices": {"host_a": "Erinome", "host_b": "Sadachbia"},
                      "expression": False}
            write_json(root / "studio/audio.json", {**choice, "third_voice": "Algenib"})
            self.assertEqual(selected_audio(root, TopicBrief(topic="Geld")).model_dump(),
                             AudioChoice.model_validate(choice).model_dump())
            self.assertEqual(selected_audio(root, three_hosts()).third_voice, "Algenib")
            # The CLI's defaults: the brief's voices and the third host's own.
            (root / "studio/audio.json").unlink()
            self.assertEqual(selected_audio(root, three_hosts()).for_hosts(three_hosts()).voices,
                             {"host_a": "Ryan", "host_b": "Serena", "host_c": "Dylan"})


class PassageTests(unittest.TestCase):
    def test_no_passage_holds_more_than_two_hosts_and_two_hosts_are_cut_as_before(self):
        two = segments(*[("c1", "c1", "host_a" if n % 2 else "host_b", 400) for n in range(30)])
        self.assertEqual([len(p) for p in plan_passages(two)[:4]], [7, 8, 7, 8])
        rows = [("c1", "c1", speaker, 200) for speaker in ("host_a", "host_b", "host_a", "host_c", "host_b",
                                                             "host_c", "host_a", "host_b")]
        three = segments(*rows)
        passages = plan_passages(three)
        self.assertEqual([i for p in passages for i in p], list(range(len(three))))
        for passage in passages:
            self.assertLessEqual(len({three[i].speaker_id for i in passage}), MAX_PASSAGE_SPEAKERS)
        # Cut where a third host would join: a, b, a | c, b, c | a, b.
        self.assertEqual(passages, [[0, 1, 2], [3, 4, 5], [6, 7]])
        self.assertIs(passage_rule(EpisodeScript(episode_id="ep_001", title="t", purpose="deep_dive",
                                                 chapters=[{"chapter_id": "c1", "title": "c"}], segments=two)),
                      PASSAGE_RULE)
        rule = passage_rule(EpisodeScript(episode_id="ep_001", title="t", purpose="deep_dive",
                                          chapters=[{"chapter_id": "c1", "title": "c"}], segments=three))
        self.assertEqual(rule, {**PASSAGE_RULE, "max_speakers": 2})

    def test_the_third_host_has_a_speaker_name_and_style_in_a_conversation(self):
        voices = {"host_a": "Erinome", "host_b": "Sadachbia", "host_c": "Algenib"}
        body = request_body([("host_c", "Auf Ton!"), ("host_a", "Genau.")], voices, "google/gemini-3.8-flash-tts",
                            {"host_a": "", "host_b": "", "host_c": "sly"})
        config = body["generation_config"]["speech_config"]
        self.assertEqual(config["speakers"], [{"speaker": "Sam", "voice": "Algenib"},
                                              {"speaker": "Alex", "voice": "Erinome"}])
        self.assertEqual(body["input"][0]["content"][0]["annotations"],
                         [{"type": "speech_metadata", "speaker": "Sam", "style": "sly"}])

    def test_exports_name_the_third_host_and_its_voice(self):
        labels = host_labels(three_hosts())
        voices = {"host_a": "Ryan", "host_b": "Serena", "host_c": "Dylan"}
        self.assertEqual(voices_line(voices, labels, "de-DE"),
                         "Stimmen: Ryan (Host A), Serena (Host B) und Dylan (Ea-nasir).")
        self.assertEqual(voices_line({"host_a": "Ryan", "host_b": "Serena"}, host_labels(TopicBrief(topic="x")),
                                     "en-US"), wording("en-US", "export_voices", voice_a="Ryan", host_a="Host A",
                                                       voice_b="Serena", host_b="Host B"))
        choice = AudioChoice(voices={"host_a": "Ryan", "host_b": "Serena"}).for_hosts(three_hosts())
        self.assertEqual(choice.voices, voices)
        notes = render_export_notes(fixtures.example_script(), [], choice, {}, labels, language="en-US")
        self.assertIn("Audio version with Host A, Host B and Ea-nasir.", notes)


class ExampleProjectTests(unittest.TestCase):
    def test_the_geld_podcast_example_is_a_valid_three_host_gemini_project(self):
        from pathlib import Path
        folder = Path(__file__).resolve().parents[1] / "examples/geld-podcast"
        brief = TopicBrief.model_validate(read_yaml(folder / "project.yaml"))
        self.assertEqual(host_labels(brief), {"host_a": "Mara", "host_b": "Leo", "host_c": "Ea-nasir"})
        choice = AudioChoice.model_validate(json.loads((folder / "audio.json").read_text(encoding="utf-8")))
        self.assertEqual(choice.provider, "google_gemini_tts")
        # The characters keep their voices in every episode.
        self.assertFalse(choice.alternate_roles)
        voiced = choice.for_episode("ep_002").for_hosts(brief)
        self.assertEqual(voiced.voices, {"host_a": "Sulafat", "host_b": "Leda", "host_c": "Algenib"})
        self.assertTrue(all(voiced.spoken_styles("de-DE").values()))


class ThreeHostScriptRunTests(fixtures.ScriptProjectCase):
    """A whole script run of a project with a third host, with the fixture's model answering."""

    def setUp(self):
        super().setUp()
        config = read_yaml(self.root / "project.yaml")
        write_yaml(self.root / "project.yaml", {**config, "third_host": EA_NASIR})
        self.prompts = []

    def model(self, prompt, output_type, directory, **kwargs):
        self.prompts.append(prompt)
        answer, meta = super().model(prompt, output_type, directory, **kwargs)
        if output_type is EpisodeScript:
            answer = with_third(answer)
        return answer, meta

    def test_every_call_is_told_the_cast_and_the_published_script_speaks_three_hosts(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertTrue(self.prompts)
        for prompt in self.prompts:
            self.assertTrue(prompt.startswith("THREE HOSTS."), prompt[:80])
        script = EpisodeScript.model_validate(read_yaml(self.root / "episodes/ep_001/script.yaml"))
        self.assertIn("host_c", {segment.speaker_id for segment in script.segments})
        self.assertIn("**Ea-nasir:** Bei uns in Ur", (self.root / "episodes/ep_001/script.md").read_text(encoding="utf-8"))
        quality = read_yaml(self.root / "reports/script_quality.yaml")
        self.assertEqual(quality["host_roles"]["host_c"], EA_NASIR["role"])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg not installed")
    def test_a_google_recording_speaks_the_third_host_in_passages_of_two(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            self.assertEqual(run_script(self.root).status, "completed")
        root = self.root.resolve()
        script = EpisodeScript.model_validate(read_yaml(root / "episodes/ep_001/script.yaml"))
        choice = {"provider": "google_gemini_tts", "voices": {"host_a": "Erinome", "host_b": "Sadachbia"},
                  "third_voice": "Algenib", "expression": False}
        with patch("podcast_automate.google_speech.build_opener") as build, \
                patch("podcast_automate.speech.build_opener", side_effect=AssertionError("No OpenRouter speech")):
            build.return_value.open.side_effect = spoken_answer
            run = run_episode_audio(root, episode="ep_001", approve_audio=True, audio_choice=choice,
                                    speech_key="AIza-google-key-0123456789")
            self.assertEqual(run.status, "completed")
            bodies = [json.loads(call.args[0].data) for call in build.return_value.open.call_args_list]
        self.assertEqual(len(bodies), len(plan_passages(script.segments)))
        for body in bodies:
            speech = body["generation_config"]["speech_config"]
            self.assertLessEqual(len(speech["speakers"]) if isinstance(speech, dict) else 1, 2)
        voices = [speaker["voice"] for body in bodies if isinstance(body["generation_config"]["speech_config"], dict)
                  for speaker in body["generation_config"]["speech_config"]["speakers"]]
        self.assertIn("Algenib", voices)
        work = root / "runs" / run.run_id
        inputs = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(inputs["passages"]["max_speakers"], 2)
        report = json.loads((root / "episodes/ep_001/audio_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(report["voices"], {"host_a": "Erinome", "host_b": "Sadachbia", "host_c": "Algenib"})
        tts = json.loads((work / "tts_report.json").read_text(encoding="utf-8"))
        voiced = AudioChoice.model_validate(choice).for_hosts(read_yaml_brief(root))
        self.assertEqual(len(check_google_rows(root, script, tts, voiced, "de-DE")), len(bodies))


def read_yaml_brief(root):
    return TopicBrief.model_validate(read_yaml(root / "project.yaml"))


if __name__ == "__main__":
    unittest.main()
