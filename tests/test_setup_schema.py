"""Regression for the actual structured setup response, not just a toy schema."""
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from pydantic import ValidationError

from podcast_automate.codex import CodexAdapter
from podcast_automate.models import RuntimeSettings, TopicBrief
from podcast_automate.openrouter import strict_schema
from podcast_automate.speech import AudioChoice
from podcast_automate.studio import BriefProposal


class SetupSchemaTests(unittest.TestCase):
    def assert_supported(self, node):
        if isinstance(node, dict):
            self.assertNotIn("propertyNames", node)
            self.assertNotIn("default", node)
            if node.get("type") == "object":
                self.assertIs(node.get("additionalProperties"), False)
                self.assertEqual(set(node["required"]), set(node["properties"]))
            for child in node.values():
                self.assert_supported(child)
        elif isinstance(node, list):
            for child in node:
                self.assert_supported(child)

    def assert_voice_fields(self, schema):
        voices = schema["$defs"]["HostVoices"]
        self.assertEqual(set(voices["properties"]), {"host_a", "host_b"})
        self.assertEqual(set(voices["required"]), {"host_a", "host_b"})
        self.assertIs(voices["additionalProperties"], False)

    def test_openrouter_receives_closed_explicit_voice_fields(self):
        schema = strict_schema(BriefProposal)
        self.assert_supported(schema)
        self.assert_voice_fields(schema)

    def test_codex_receives_supported_complete_setup_schema_and_validates_response(self):
        proposal = BriefProposal(message="Ein Vorschlag", topic="Test", central_question="Warum?",
            prior_knowledge="", depth_request="Tief", focus_questions=[], excluded_topics=[],
            audio_settings=AudioChoice(voices={"host_a": "Aiden", "host_b": "Vivian"}))
        def respond(args, **kwargs):
            if args[-2:] == ["login", "status"]:
                return CompletedProcess(args, 0, "", "Logged in using ChatGPT")
            if args[-1] == "--version":
                return CompletedProcess(args, 0, "test-version", "")
            schema = json.loads(Path(args[args.index("--output-schema")+1]).read_text())
            self.assert_supported(schema)
            self.assert_voice_fields(schema)
            Path(args[args.index("--output-last-message")+1]).write_text(proposal.model_dump_json(), encoding="utf-8")
            return CompletedProcess(args, 0, '{"type":"turn.completed","usage":{}}', "")
        with tempfile.TemporaryDirectory() as folder, patch.object(CodexAdapter, "command", return_value=["codex"]), \
             patch("podcast_automate.codex.run_process", side_effect=respond):
            output, _ = CodexAdapter(RuntimeSettings()).structured("Propose setup.", BriefProposal, Path(folder),
                                                                  prompt_version="regression")
        self.assertEqual(output.audio_settings.voices, {"host_a": "Aiden", "host_b": "Vivian"})

    def test_voice_dictionaries_keep_their_stored_shape_and_validation(self):
        valid = {"host_a": "Aiden", "host_b": "Vivian"}
        self.assertEqual(TopicBrief(topic="Test", voice_profile=valid).model_dump()["voice_profile"], valid)
        for voices in ({"host_a": "Aiden"}, {**valid, "host_c": "Ryan"},
                       {"host_a": "Aiden", "host_b": "Aiden"}, {"host_a": "", "host_b": "Vivian"}):
            with self.subTest(voices=voices), self.assertRaises(ValidationError):
                AudioChoice(voices=voices)
