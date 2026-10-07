import json
import unittest
from unittest.mock import patch

from podcast_automate.episode_audio import run_episode_audio, tag_episode
from podcast_automate.errors import AppError
from podcast_automate.expression import (ALLOWED_TAGS, BACKCHANNEL_PROMPT_VERSION, EXPRESSION_PROMPT_VERSION,
                                         EXPRESSION_VERSION, ExpressionPlan, episode_tag_limit, expression_defects,
                                         plan_expression, untagged)
from podcast_automate.runner import manifest_path
from podcast_automate.scripting import run_script
from podcast_automate.speech import AudioChoice, same_audio_generation, selected_audio
from podcast_automate.models import RunManifest
from podcast_automate.storage import file_hash, read_yaml, write_json, write_yaml
from podcast_automate.studio_worker import express_published
from tests import script_fixtures as fixtures
from tests.script_fixtures import example_script
from tests.test_speech import response


def plan(*rows):
    return ExpressionPlan(segments=[{"segment_id": sid, "text": text} for sid, text in rows])


class ExpressionDefectsTests(unittest.TestCase):
    spoken = {"s1": "Und genau hier wird es spannend.", "s2": "Das ist ja fast schon unheimlich.", "s3": "Gut."}

    def test_tags_between_words_that_keep_every_word_pass(self):
        self.assertEqual(expression_defects(plan(("s1", "Und genau hier wird es spannend. <short pause>"),
                                                 ("s2", "<chuckle> Das ist ja fast schon unheimlich.")), self.spoken), [])
        self.assertEqual(untagged("<breath> Gut. <long pause>"), "Gut.")

    def test_a_changed_word_an_unknown_tag_or_a_tag_inside_a_word_fails(self):
        cases = {
            "the words changed": ("s1", "Und genau hier wird es richtig spannend. <sigh>"),
            "only": ("s1", "<whispering> Und genau hier wird es spannend."),
            "never inside one": ("s1", "Und genau hier wird es span<laugh>nend."),
            "carries no tag": ("s1", "Und genau hier wird es spannend."),
            "unknown segment id": ("s9", "<laugh> Gut."),
        }
        for expected, row in cases.items():
            with self.subTest(expected):
                errors = expression_defects(plan(row), self.spoken)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_tags_stay_sparse_per_segment_and_per_episode(self):
        errors = expression_defects(plan(("s1", "<breath> Und genau hier <short pause> wird es spannend. <sigh>")), self.spoken)
        self.assertTrue(any("at most 2 tags per segment" in error for error in errors))
        many = {f"s{n}": "Gut." for n in range(8)}
        rows = [(sid, "<breath> Gut.") for sid in many]
        self.assertEqual(episode_tag_limit(8), 3)
        self.assertTrue(any("At most 3 tags in this episode" in error for error in expression_defects(plan(*rows), many)))

    def test_a_long_pause_never_opens_a_segment(self):
        """All 44 <long pause> tags of the 29 Sep recordings opened their segment, on top of the assembly's pause, and
        four left 3.5 to 7.3 s of dead air (2026-10-02). Inside a segment the tag stays allowed."""
        errors = expression_defects(plan(("s1", "<long pause> Und genau hier wird es spannend.")), self.spoken)
        self.assertTrue(any("never opens a segment" in error for error in errors), errors)
        self.assertEqual(expression_defects(plan(("s2", "Das ist ja <long pause> fast schon unheimlich.")), self.spoken), [])

    def test_listener_reactions_only_for_a_google_recording_inside_a_long_turn(self):
        """The other host's |mhm| (Google's two-host passages, 2026-10-06): one per long turn, inside it, from the
        language's list; a recording that speaks each segment alone has none."""
        long = ("Und genau hier wird es spannend, denn die Suche gewichtet jedes Wort danach, wie selten es in der "
                "ganzen Sammlung vorkommt, und ein seltenes Wort sagt viel mehr über den passenden Abschnitt aus als "
                "ein häufiges, das in jedem zweiten Bericht steht.")
        spoken = {**self.spoken, "s4": long}
        reacted = long.replace("vorkommt, und", "vorkommt, |mhm| und")
        reactions = ("|mhm|", "|aha|")
        self.assertEqual(expression_defects(plan(("s4", reacted)), spoken, reactions), [])
        self.assertEqual(untagged(reacted), long)
        cases = {
            "no listener reactions": (plan(("s4", reacted)), ()),
            "allowed as reactions": (plan(("s4", long.replace("vorkommt, und", "vorkommt, |wow| und"))), reactions),
            "at most one reaction": (plan(("s4", reacted.replace("ganzen Sammlung", "|aha| ganzen Sammlung"))), reactions),
            "30 words or more": (plan(("s1", "Und genau hier |mhm| wird es spannend.")), reactions),
            "never at its start": (plan(("s4", "|mhm| " + long)), reactions),
        }
        for expected, (answer, allowed) in cases.items():
            with self.subTest(expected):
                errors = expression_defects(answer, spoken, allowed)
                self.assertTrue(any(expected in error for error in errors), errors)

    def test_the_studio_names_exactly_the_allowed_tags(self):
        from pathlib import Path
        import re
        script = (Path(__file__).resolve().parents[1] / "src/podcast_automate/web/app.js").read_text(encoding="utf-8")
        table = script[script.index("const EXPRESSION_KINDS"):script.index("function expressionKinds")]
        self.assertEqual(set(re.findall(r'"(<[^"]+>)":"', table)), set(ALLOWED_TAGS))

    def test_the_allowed_tags_are_vocal_events_that_fit_a_factual_podcast(self):
        self.assertIn("<laugh>", ALLOWED_TAGS)
        self.assertIn("<short pause>", ALLOWED_TAGS)
        for unfit in ("<scream>", "<sob>", "<growl>", "<sneeze>", "<yawn>"):
            self.assertNotIn(unfit, ALLOWED_TAGS)


class PlanExpressionTests(unittest.TestCase):
    def setUp(self):
        self.script = example_script()
        self.spoken = {s.segment_id: s.text for s in self.script.segments}
        self.first = self.script.segments[0]
        self.labels = {"host_a": "Anna", "host_b": "Ben"}

    def test_an_answer_that_changes_a_word_is_corrected_and_the_tags_are_returned(self):
        prompts = []

        def invoke(prompt, schema, version):
            prompts.append(prompt)
            text = self.first.text if len(prompts) > 1 else self.first.text.replace(" ", "  X ", 1)
            return plan((self.first.segment_id, "<breath> " + text))
        tags, rejected = plan_expression(invoke, self.script, self.spoken, language="de-DE", labels=self.labels)
        self.assertEqual((tags, rejected), ({self.first.segment_id: "<breath> " + self.first.text}, ""))
        self.assertEqual(len(prompts), 2)
        payload = json.loads(prompts[0].splitlines()[-1])
        self.assertEqual(payload["allowed_tags"], list(ALLOWED_TAGS))
        self.assertEqual([s["text"] for s in payload["segments"]], list(self.spoken.values()))

    def test_reactions_are_asked_for_only_with_a_list_and_under_their_own_prompt_version(self):
        seen = []

        def invoke(prompt, schema, version):
            seen.append((version, json.loads(prompt.splitlines()[-1]), prompt))
            return plan()
        plan_expression(invoke, self.script, self.spoken, language="de-DE", labels=self.labels)
        plan_expression(invoke, self.script, self.spoken, language="de-DE", labels=self.labels,
                        backchannels=("|mhm|", "|aha|"))
        (plain_version, plain, plain_prompt), (version, data, prompt) = seen
        self.assertEqual(plain_version, EXPRESSION_PROMPT_VERSION)
        self.assertNotIn("allowed_backchannels", plain)
        self.assertNotIn("between pipes", plain_prompt)
        self.assertEqual(version, BACKCHANNEL_PROMPT_VERSION)
        self.assertEqual(data["allowed_backchannels"], ["|mhm|", "|aha|"])
        self.assertIn("between pipes", prompt)

    def test_an_answer_still_invalid_after_its_corrections_leaves_the_episode_without_tags(self):
        calls = []

        def invoke(prompt, schema, version):
            calls.append(version)
            return plan((self.first.segment_id, "<scream> " + self.first.text))
        tags, rejected = plan_expression(invoke, self.script, self.spoken, language="de-DE", labels=self.labels)
        self.assertEqual(tags, {})
        self.assertIn("only", rejected)
        self.assertEqual(len(calls), 3, "the first answer and two corrections")

    def test_an_opening_long_pause_is_removed_without_a_correction_call(self):
        first, second = self.script.segments
        calls, prompts = [], []

        def invoke(prompt, schema, version):
            calls.append(version)
            prompts.append(" ".join(prompt.split()))
            return plan((first.segment_id, "<long pause> " + first.text),
                        (second.segment_id, "<long pause> <sigh> " + second.text))
        tags, rejected = plan_expression(invoke, self.script, self.spoken, language="de-DE", labels=self.labels)
        # The first segment carried only the pause and goes without a tag; the second keeps its sigh.
        self.assertEqual((tags, rejected), ({second.segment_id: "<sigh> " + second.text}, ""))
        self.assertEqual(calls, [EXPRESSION_PROMPT_VERSION])
        self.assertIn("Never put a long pause at a segment's start", prompts[0])

    def test_a_quota_stop_is_not_mistaken_for_an_invalid_answer(self):
        def invoke(prompt, schema, version):
            raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
        with self.assertRaises(AppError) as caught:
            plan_expression(invoke, self.script, self.spoken, language="de-DE", labels=self.labels)
        self.assertEqual(caught.exception.code, "quota_exhausted")


class ExpressionRecordingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            self.assertEqual(run_script(self.root).status, "completed")

    def test_a_studio_gemini_choice_records_expression_and_keeps_its_approvals(self):
        voices = {"host_a": "Sadaltager", "host_b": "Aoede"}
        write_json(self.root / "studio/audio.json", {"provider": "openrouter_gemini_tts", "voices": voices})
        self.assertTrue(selected_audio(self.root, self.fixture.config).expression)
        write_json(self.root / "studio/audio.json", {"provider": "openrouter_gemini_tts", "voices": voices, "expression": False})
        self.assertFalse(selected_audio(self.root, self.fixture.config).expression)
        plain = AudioChoice(provider="openrouter_gemini_tts", voices=voices)
        self.assertNotIn("expression", plain.model_dump(), "a choice without the layer hashes as before it existed")
        self.assertTrue(same_audio_generation(plain.model_dump(), plain.model_copy(update={"expression": True}).model_dump()))
        self.assertFalse(AudioChoice(voices={"host_a": "Aiden", "host_b": "Vivian"}, expression=True).expression)

    def test_a_gemini_recording_speaks_the_tagged_text_once_and_reuses_the_tags_on_resume(self):
        first, second = example_script().segments
        tagged = "<chuckle> " + first.text
        prompts = []

        class Pool:
            def __init__(self, runtime, text_generation, api_key=None):
                pass

            def structured(self, prompt, schema, directory, prompt_version):
                prompts.append(prompt_version)
                return schema(segments=[{"segment_id": first.segment_id, "text": tagged}]), {}
        sent = []

        def audio(request, **kwargs):
            sent.append(json.loads(request.data)["input"])
            return response()

        def assemble(script, paths, folder, **kwargs):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "audio.mp3").write_bytes(b"test-audio")
            write_json(folder / "audio_report.json", {"duration_seconds": 3})
            return [folder / "audio.mp3", folder / "audio_report.json"]
        choice = AudioChoice(provider="openrouter_gemini_tts", voices={"host_a": "Sadaltager", "host_b": "Aoede"},
                             expression=True)
        with patch("podcast_automate.episode_audio.AdapterPool", Pool),              patch("podcast_automate.speech.build_opener") as build,              patch("podcast_automate.episode_audio.assemble", side_effect=assemble):
            build.return_value.open.side_effect = audio
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True, audio_choice=choice, api_key="test-key")
            self.assertEqual(run.status, "completed")
            self.assertEqual(list(run.stages), ["expression", "synthesis", "assembly", "publish"])
            self.assertEqual((prompts, sent), ([EXPRESSION_PROMPT_VERSION], [tagged, second.text]))
            saved = json.loads((manifest_path(self.root, run.run_id).parent / "expression.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["segments"], {first.segment_id: tagged})
            build.return_value.open.side_effect = AssertionError("No new speech call on resume")
            resumed = run_episode_audio(self.root, resume=True, run_id=run.run_id)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(prompts, [EXPRESSION_PROMPT_VERSION], "the tags are placed once")

    def gemini(self):
        choice = AudioChoice(provider="openrouter_gemini_tts", voices={"host_a": "Sadaltager", "host_b": "Aoede"},
                             expression=True)
        write_json(self.root / "studio/audio.json", choice.model_dump())
        return choice

    def placing(self, rows, calls):
        class Pool:
            def __init__(self, runtime, text_generation, api_key=None):
                pass

            def structured(self, prompt, schema, directory, prompt_version):
                calls.append(prompt_version)
                return schema(segments=[{"segment_id": key, "text": text} for key, text in rows.items()]), {}
        return Pool

    def recording(self, choice, sent, **kwargs):
        def audio(request, **options):
            sent.append(json.loads(request.data)["input"])
            return response()

        def assemble(script, paths, folder, **options):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "audio.mp3").write_bytes(b"test-audio")
            write_json(folder / "audio_report.json", {"duration_seconds": 3})
            return [folder / "audio.mp3", folder / "audio_report.json"]
        with patch("podcast_automate.episode_audio.AdapterPool", side_effect=AssertionError("no model call")),              patch("podcast_automate.speech.build_opener") as build,              patch("podcast_automate.episode_audio.assemble", side_effect=assemble):
            build.return_value.open.side_effect = audio
            return run_episode_audio(self.root, episode="ep_001", approve_audio=True, audio_choice=choice,
                                     api_key="test-key", **kwargs)

    def test_tags_placed_for_reading_are_spoken_as_read_and_bind_the_approval(self):
        """The user's wish of 2026-09-29: the expression belongs in the script check, so the recording speaks
        exactly the tags the reader saw, and tags placed anew afterwards need a new reading."""
        choice = self.gemini()
        first, second = example_script().segments
        tagged, calls = "<chuckle> " + first.text, []
        with patch("podcast_automate.episode_audio.AdapterPool", self.placing({first.segment_id: tagged}, calls)):
            record = tag_episode(self.root, "ep_001")
        self.assertEqual((record["segments"], calls), ({first.segment_id: tagged}, [EXPRESSION_PROMPT_VERSION]))
        read_hash = file_hash(self.root / "episodes/ep_001/expression.json")
        with self.assertRaises(AppError) as stale:
            self.recording(choice, [], expected_expression_hash="0" * 64)
        self.assertEqual(stale.exception.code, "script_edited")
        sent = []
        run = self.recording(choice, sent, expected_expression_hash=read_hash)
        self.assertEqual((run.status, sent), ("completed", [tagged, second.text]))
        used = json.loads((manifest_path(self.root, run.run_id).parent / "expression.json").read_text(encoding="utf-8"))
        self.assertEqual((used["segments"], used["source"]), ({first.segment_id: tagged}, "reading"))

    def test_assembly_shortens_the_dead_air_of_segments_with_a_pause_tag_only(self):
        """2026-10-02: a <long pause> left up to 7.3 s of silence; assembly now cuts it to 1.2 s (audio.assemble)."""
        choice = self.gemini()
        first, second = example_script().segments
        head, tail = first.text.split(" ", 1)
        tagged = {first.segment_id: f"{head} <long pause> {tail}", second.segment_id: "<sigh> " + second.text}
        with patch("podcast_automate.episode_audio.AdapterPool", self.placing(tagged, [])):
            tag_episode(self.root, "ep_001")
        seen = {}

        def assemble(script, paths, folder, **options):
            seen.update(options)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "audio.mp3").write_bytes(b"test-audio")
            write_json(folder / "audio_report.json", {"duration_seconds": 3})
            return [folder / "audio.mp3", folder / "audio_report.json"]
        with patch("podcast_automate.speech.build_opener") as build, \
                patch("podcast_automate.episode_audio.assemble", side_effect=assemble):
            build.return_value.open.side_effect = lambda *a, **k: response()
            run = run_episode_audio(self.root, episode="ep_001", approve_audio=True, audio_choice=choice, api_key="test-key")
        self.assertEqual(run.status, "completed")
        self.assertEqual(seen["trim_pauses"], {first.segment_id})

    def test_a_segment_whose_spoken_form_changed_after_reading_is_spoken_without_its_tag(self):
        choice = self.gemini()
        first, second = example_script().segments
        with patch("podcast_automate.episode_audio.AdapterPool", self.placing({first.segment_id: "<sigh> " + first.text}, [])):
            tag_episode(self.root, "ep_001")
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml", {"spoken_overrides": {first.segment_id: "Was vergleicht es?"}})
        sent = []
        self.assertEqual(self.recording(choice, sent).status, "completed")
        self.assertEqual(sent, ["Was vergleicht es?", second.text])

    def test_a_finished_script_run_places_the_tags_of_a_gemini_project_for_reading(self):
        manifest = RunManifest.model_validate(read_yaml(manifest_path(self.root)))
        first = example_script().segments[0]
        calls = []
        # Recorded with Qwen: no tags, since Qwen would read them aloud.
        with patch("podcast_automate.episode_audio.AdapterPool", self.placing({first.segment_id: "<laugh> " + first.text}, calls)):
            self.assertIsNone(express_published(self.root, manifest))
            self.gemini()
            placed = express_published(self.root, manifest, "test-key")
            self.assertIsNone(express_published(self.root, manifest), "tags already placed are not placed again")
        self.assertEqual((placed, calls), ({"ep_001": {"tags": 1, "rejected": ""}}, [EXPRESSION_PROMPT_VERSION]))
        progress = json.loads((self.root / "studio/expression/progress.json").read_text(encoding="utf-8"))
        self.assertEqual((progress["status"], progress["done"], progress["total"], progress["run_id"]),
                         ("completed", 1, 1, manifest.run_id))
        self.assertTrue((self.root / "episodes/ep_001/expression.json").is_file())


if __name__ == "__main__":
    unittest.main()
