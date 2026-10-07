import json
import os
import unittest
from pathlib import Path
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

    def subscriptions_store(self):
        """The settings page reads Claude's extra-usage switch from the quota store; never the home one in a test."""
        patcher = patch.dict(os.environ, {"PLA_SUBSCRIPTIONS_STORE": str(Path(self.temp.name) / "subscriptions.json")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_preset_is_saved_on_the_settings_page_and_a_proposal_keeps_it(self):
        """Since 2026-10-03 the text model is a setting for every project. Before, a preset button in the chat sent the
        choice to the partner, and only an applied proposal saved it, for that one project."""
        self.subscriptions_store()
        boot = json.loads(self.request("/api/bootstrap")[1])
        # Sonnet 5.5 at high joined as its own preset next to Opus 5.5 (2026-09-29); Sonnet and Opus on the user's
        # Anthropic key followed (2026-10-07, D-145).
        self.assertEqual(len(boot["text_catalog"]["presets"]), 11)
        for preset in TEXT_PRESETS:
            with self.subTest(preset=preset["id"]):
                view = json.loads(self.request("/api/settings")[1])
                chosen = TextChoice(**text_preset(preset["id"])).normalized()
                settings = {**view["settings"], "text": chosen}
                if chosen["provider"] in {"openrouter", "claude_api"}:
                    # A text model billed to a key is saved only with a money limit (D-146).
                    unlimited = {key: value for key, value in settings["research_limits"].items() if key != "cost_usd"}
                    status, body, _ = self.request("/api/settings", {"settings": {**settings, "research_limits": unlimited},
                                                                     "hash": view["hash"], "claude_extra_usage": False})
                    self.assertEqual((status, json.loads(body)["code"]), (400, "cost_limit_required"))
                    settings["research_limits"] = {**unlimited, "cost_usd": 25.0}
                status, body, _ = self.request("/api/settings", {"settings": settings,
                                                                 "hash": view["hash"], "claude_extra_usage": False})
                self.assertEqual(status, 200, body)
                detail = json.loads(self.request("/api/projects/example")[1])
                self.assertEqual(detail["text"], chosen)
                previous = load_project(self.root).model_dump()
                # The partner's own model output names another model; a proposal carries the brief only.
                proposal = BriefProposal(message="Modellvorschlag", topic=previous["topic"],
                    central_question="What should we learn?", prior_knowledge="", depth_request="Deep",
                    focus_questions=[], excluded_topics=[], text=TextChoice(provider="codex_cli", model="gpt-5.5"))

                def model(prompt, *args, **kwargs):
                    data = json.loads(prompt.splitlines()[-1])
                    self.assertNotIn("text_catalog", data)
                    self.assertNotIn("requested_text", data)
                    return proposal, {}
                with patch("podcast_automate.studio_worker.CodexAdapter.structured", side_effect=model):
                    perform(self.root, {"action": "assistant", "text": {}, "message": "Use another model"})
                detail = json.loads(self.request("/api/projects/example")[1])
                self.assertIsNone(detail["chat"][-1]["text"])
                receipt = {k: detail[k] for k in ("proposal_hash", "config_hash", "audio_hash", "execution_hash")}
                self.assertEqual(self.request("/api/projects/example/apply_proposal", receipt)[0], 200)
                after = json.loads(self.request("/api/projects/example")[1])
                self.assertEqual(after["text"], chosen)
                self.assertTrue(after["proposal_applied"])
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
        # An invalid text model is refused on the settings page (2026-10-03), before anything is saved or started.
        self.subscriptions_store()
        view = json.loads(self.request("/api/settings")[1])
        with patch("podcast_automate.studio.subprocess.Popen") as launch:
            status, _, _ = self.request("/api/settings", {"settings": {**view["settings"], "text": {
                "provider": "codex_cli", "model": "openai/gpt-6-astra-pro"}}, "hash": view["hash"], "claude_extra_usage": False})
        self.assertEqual(status, 400)
        launch.assert_not_called()
        self.assertFalse((self.workspace / "projects" / ".studio-settings.json").exists())
