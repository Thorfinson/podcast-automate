import json
import unittest
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.scripting import text_generation_settings
from podcast_automate.storage import load_project
from podcast_automate.studio import BriefProposal, TextChoice
from podcast_automate.studio_worker import perform
from podcast_automate.text_settings import TEXT_PRESETS, text_preset
from tests import test_studio


class TextSelectionTests(unittest.TestCase):
    setUp = test_studio.StudioHttpTests.setUp
    request = test_studio.StudioHttpTests.request

    def test_all_requested_presets_survive_proposal_application_without_changing_audio(self):
        boot = json.loads(self.request("/api/bootstrap")[1])
        self.assertEqual(len(boot["text_catalog"]["presets"]), 5)
        for preset in TEXT_PRESETS:
            with self.subTest(preset=preset["id"]):
                requested = text_preset(preset["id"])
                previous = load_project(self.root).model_dump()
                proposal = BriefProposal(message="Modellvorschlag", topic=previous["topic"],
                    central_question="What should we learn?", prior_knowledge="", depth_request="Deep",
                    focus_questions=[], excluded_topics=[], text=TextChoice())
                def model(prompt, *args, **kwargs):
                    data = json.loads(prompt.splitlines()[-1])
                    self.assertEqual(data["requested_text"], requested)
                    self.assertIn("anthropic/claude-fable-5.1", data["text_catalog"]["openrouter_models"])
                    return proposal, {}
                with patch("podcast_automate.studio_worker.CodexAdapter.structured", side_effect=model):
                    perform(self.root, {"action": "assistant", "text": {}, "message": "Use this model",
                                       "requested_text": requested})
                detail = json.loads(self.request("/api/projects/example")[1])
                self.assertEqual(detail["chat"][-1]["text"]["model"], requested["model"])
                receipt = {k: detail[k] for k in ("proposal_hash", "config_hash", "audio_hash", "execution_hash")}
                self.assertEqual(self.request("/api/projects/example/apply_proposal", receipt)[0], 200)
                after = json.loads(self.request("/api/projects/example")[1])
                for key in ("provider", "model", "reasoning_effort"):
                    self.assertEqual(after["text"][key], requested[key])
                self.assertEqual(after["audio_settings"], detail["audio_settings"])
                self.assertEqual(after["config"]["runtime"], detail["config"]["runtime"])

    def test_max_is_kept_for_deepseek_and_resume_rejects_provider_or_effort_changes(self):
        selected = TextChoice(**text_preset("openrouter_deepseek"))
        kwargs = selected.kwargs()
        self.assertEqual(kwargs["reasoning_effort"], "max")
        saved = text_generation_settings(self.config, **kwargs)
        self.assertEqual(text_generation_settings(self.config, saved=saved), saved)
        self.assertEqual(text_generation_settings(self.config, saved=saved, reasoning_effort="max"), saved)
        with self.assertRaises(AppError):
            text_generation_settings(self.config, saved=saved, reasoning_effort="high")
        with self.assertRaises(AppError):
            TextChoice(provider="openrouter", model=selected.model, reasoning_effort="xhigh").kwargs()

    def test_pro_is_never_silently_downgraded_and_invalid_presets_never_start_workers(self):
        selected = TextChoice(provider="openrouter", model="gpt-6-astra-pro")
        self.assertEqual(selected.normalized()["model"], "openai/gpt-6-astra-pro")
        with self.assertRaises(AppError):
            TextChoice(provider="codex_cli", model="openai/gpt-6-astra-pro").kwargs()
        with patch("podcast_automate.studio.subprocess.Popen") as launch:
            status, _, _ = self.request("/api/projects/example/start", {"action": "assistant", "message": "Use this",
                                                                      "text_preset": "invented"})
        self.assertEqual(status, 400)
        launch.assert_not_called()
