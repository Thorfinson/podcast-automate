import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.codex import CodexAdapter, executable_command
from podcast_automate.errors import AppError
from podcast_automate.models import RuntimeSettings
from podcast_automate.process import run_process


FAKE_CODEX = r'''
import json, os, pathlib, sys, time
args = sys.argv[1:]
mode = os.environ.get("PLA_TEST_MODE", "ok")
if any(key in os.environ for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENROUTER_API_KEY")):
    sys.exit(55)
if args == ["login", "status"]:
    print("Logged in using API key: hidden" if mode == "api" else "Logged in using ChatGPT",
          file=sys.stderr)
    sys.exit(0)
if args == ["--version"]:
    print("codex-cli test-version")
    sys.exit(0)
if mode == "explicit":
    assert args[args.index("--model")+1] == "gpt-6-astra"
    assert 'model_reasoning_effort="xhigh"' in args
    assert '--ignore-user-config' in args
if mode == "timeout":
    time.sleep(20)
if mode == "quota":
    print(json.dumps({"type": "turn.failed", "error": {"message": "usage_limit_reached"}}))
    sys.exit(1)
if mode == "schema":
    print(json.dumps({"type": "turn.failed", "error": {"message": "invalid_json_schema: propertyNames is not permitted. test-only-secret"}}))
    sys.exit(1)
topic = json.loads(sys.stdin.read().splitlines()[-1])["topic"]
schema = json.loads(pathlib.Path(args[args.index("--output-schema")+1]).read_text())
assert schema["additionalProperties"] is False
response = pathlib.Path(args[args.index("--output-last-message")+1])
if mode == "invalid":
    response.write_text('{"topic": "incomplete"}', encoding="utf-8")
else:
    response.write_text(json.dumps({"topic": topic, "focus_questions": ["Wie und warum?"],
                                   "note": "Keine Quellenrecherche."}), encoding="utf-8")
if mode == "retry":
    print(json.dumps({"type": "error", "message": "temporary rate limit; retrying"}))
if mode == "search":
    assert 'web_search="live"' in args
    assert 'features.shell_tool=false' in args
    print(json.dumps({"type": "item.completed", "item": {"id": "search_1", "type": "web_search", "query": "actual query"}}))
if mode != "incomplete":
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 8}}))
if mode == "late_failure":
    print(json.dumps({"type": "turn.failed", "error": {"message": "usage_limit_reached"}}))
'''


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Spaces & Umlaut ä"
        self.root.mkdir()
        fake = self.root / "codex fake.py"
        fake.write_text(FAKE_CODEX, encoding="utf-8")
        self.adapter = CodexAdapter(RuntimeSettings(text_timeout_seconds=3))
        self.command = patch.object(self.adapter, "command", return_value=[sys.executable, str(fake)])
        self.command.start()
        self.addCleanup(self.command.stop)

    def test_subscription_call_uses_stdin_and_scrubs_api_keys(self):
        topic = 'Gradienten; $(echo nope) & "quoted" ' + chr(96) + 'echo nope' + chr(96)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only", "CODEX_API_KEY": "test-only",
                                     "OPENROUTER_API_KEY": "test-only"}):
            result, metadata = self.adapter.probe(topic, self.root / "request dir")
        self.assertEqual(result.topic, topic)
        self.assertEqual(metadata["auth_mode"], "chatgpt")
        self.assertEqual(metadata["usage"]["input_tokens"], 12)
        self.assertFalse(metadata["research_performed"])
        self.assertFalse((self.root / "request dir/response.pending.json").exists())

    def test_api_login_is_blocked(self):
        with patch.dict(os.environ, {"PLA_TEST_MODE": "api"}), self.assertRaises(AppError) as error:
            self.adapter.probe("Thema", self.root / "request")
        self.assertEqual(error.exception.code, "subscription_required")

    def test_model_and_effort_are_explicit_even_when_user_config_is_ignored(self):
        self.adapter.settings = self.adapter.settings.model_copy(update={"codex_model": "gpt-6-astra"})
        self.adapter.reasoning_effort = "xhigh"
        with patch.dict(os.environ, {"PLA_TEST_MODE": "explicit"}):
            _, metadata = self.adapter.probe("Thema", self.root / "explicit")
        self.assertEqual(metadata["requested_model"], "gpt-6-astra")
        self.assertEqual(metadata["requested_reasoning_effort"], "xhigh")

    def test_invalid_reasoning_and_model_are_rejected_before_start(self):
        with self.assertRaises(AppError):
            CodexAdapter(RuntimeSettings(), reasoning_effort='xhigh"; unsafe')
        with self.assertRaises(AppError):
            CodexAdapter(RuntimeSettings(codex_model="--untrusted-flag"))

    def test_research_requires_observed_search_tool_event(self):
        from podcast_automate.models import TextProbeOutput
        import json
        prompt = json.dumps({"topic": "Thema"})
        with self.assertRaises(AppError) as error:
            self.adapter.structured(prompt, TextProbeOutput, self.root / "no search",
                                    prompt_version="test", search=True)
        self.assertEqual(error.exception.code, "search_not_observed")
        with patch.dict(os.environ, {"PLA_TEST_MODE": "search"}):
            _, metadata = self.adapter.structured(prompt, TextProbeOutput, self.root / "search",
                                                  prompt_version="test", search=True)
        self.assertTrue(metadata["research_performed"])
        self.assertEqual(metadata["web_search_events"], 1)

    def test_quota_is_distinct_from_invalid_output(self):
        for mode, expected in (("quota", "quota_exhausted"), ("invalid", "invalid_model_output"),
                               ("incomplete", "codex_failed"), ("late_failure", "quota_exhausted")):
            with self.subTest(mode=mode), patch.dict(os.environ, {"PLA_TEST_MODE": mode}):
                with self.assertRaises(AppError) as error:
                    self.adapter.probe("Thema", self.root / "request")
                self.assertEqual(error.exception.code, expected)

    def test_recovered_stream_error_does_not_invalidate_success(self):
        with patch.dict(os.environ, {"PLA_TEST_MODE": "retry"}):
            result, _ = self.adapter.probe("Thema", self.root / "request")
        self.assertEqual(result.topic, "Thema")

    def test_schema_error_is_actionable_and_failure_receipt_does_not_copy_provider_details(self):
        import json
        with patch.dict(os.environ, {"PLA_TEST_MODE": "schema"}), self.assertRaises(AppError) as error:
            self.adapter.probe("Thema", self.root / "request")
        self.assertEqual(error.exception.code, "invalid_output_schema")
        self.assertIn("Studio-Anbindung", str(error.exception))
        receipt = (self.root / "request/failure.json").read_text(encoding="utf-8")
        self.assertNotIn("test-only-secret", receipt)
        self.assertEqual(json.loads(receipt)["exit_code"], 1)

    def test_windows_npm_shim_is_resolved_without_shell(self):
        shim = self.root / "codex.cmd"
        script = self.root / "node_modules/@openai/codex/bin/codex.js"
        script.parent.mkdir(parents=True)
        script.write_text("// fixture")
        with patch("podcast_automate.codex.shutil.which",
                   side_effect=[str(shim), "node.exe"]):
            self.assertEqual(executable_command("codex"), ["node.exe", str(script)])

    def installed_extension(self, version, architecture="x86_64", profile=".vscode"):
        path = self.root / profile / "extensions" / f"openai.chatgpt-{version}-win32-x64" / "bin" / f"windows-{architecture}" / "codex.exe"
        path.parent.mkdir(parents=True)
        path.touch()
        return path

    def test_windows_ide_installation_found_without_path_and_updates_are_ordered_numerically(self):
        self.installed_extension("26.9.1")
        newest = self.installed_extension("26.10.1")
        with patch("podcast_automate.codex.shutil.which", return_value=None), \
             patch("podcast_automate.codex.Path.home", return_value=self.root), \
             patch("podcast_automate.codex.platform.system", return_value="Windows"), \
             patch("podcast_automate.codex.platform.machine", return_value="AMD64"):
            self.assertEqual(executable_command("codex"), [str(newest)])
            # An extension update is discovered without changing a project snapshot.
            updated = self.installed_extension("26.11.0")
            self.assertEqual(executable_command("codex.exe"), [str(updated)])

    def test_explicit_missing_executable_is_not_silently_replaced(self):
        self.installed_extension("26.10.1")
        with patch("podcast_automate.codex.shutil.which", return_value=None), \
             patch("podcast_automate.codex.windows_codex_installation") as fallback:
            with self.assertRaises(AppError) as error:
                executable_command(str(self.root / "custom/codex.exe"))
            self.assertEqual(error.exception.code, "codex_missing")
            fallback.assert_not_called()

    def test_path_installation_takes_precedence(self):
        with patch("podcast_automate.codex.shutil.which", return_value="C:/installed/codex.exe"), \
             patch("podcast_automate.codex.windows_codex_installation") as fallback:
            self.assertEqual(executable_command("codex"), [str(Path("C:/installed/codex.exe"))])
            fallback.assert_not_called()

    def test_windows_arm64_uses_matching_insiders_binary(self):
        self.installed_extension("26.11.1")
        arm = self.installed_extension("26.10.1", architecture="arm64", profile=".vscode-insiders")
        with patch("podcast_automate.codex.shutil.which", return_value=None), \
             patch("podcast_automate.codex.Path.home", return_value=self.root), \
             patch("podcast_automate.codex.platform.system", return_value="Windows"), \
             patch("podcast_automate.codex.platform.machine", return_value="ARM64"):
            self.assertEqual(executable_command("codex"), [str(arm)])

    def test_no_discovered_installation_reports_missing_not_login_failure(self):
        with patch("podcast_automate.codex.shutil.which", return_value=None), \
             patch("podcast_automate.codex.Path.home", return_value=self.root), \
             patch("podcast_automate.codex.platform.system", return_value="Windows"):
            with self.assertRaises(AppError) as error:
                executable_command("codex")
            self.assertEqual(error.exception.code, "codex_missing")
            self.assertNotIn("codex login", str(error.exception))

    def test_process_timeout_is_actionable(self):
        with self.assertRaises(AppError) as error:
            run_process([sys.executable, "-c", "import time; time.sleep(20)"], timeout=1)
        self.assertEqual(error.exception.code, "timeout")

    def test_model_timeout_reports_call_limit_and_discards_partial_response(self):
        import json
        directory = self.root / "timed out"

        def expire(*args, **kwargs):
            self.assertEqual(kwargs["timeout"], 3)
            (directory / "response.pending.json").write_text('{"partial":"test-only-secret"}')
            raise AppError("test-only-secret", code="timeout")

        with patch.object(self.adapter, "check_login"), \
                patch("podcast_automate.codex.run_process", side_effect=expire), \
                self.assertRaises(AppError) as error:
            self.adapter.probe("Thema", directory)
        self.assertEqual(error.exception.code, "timeout")
        self.assertIn("3 Sekunden", str(error.exception))
        self.assertIn("einzelne Codex-Aufruf", str(error.exception))
        self.assertFalse((directory / "response.pending.json").exists())
        self.assertFalse((directory / "response.json").exists())
        receipt = (directory / "failure.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(receipt)["timeout_seconds"], 3)
        self.assertNotIn("test-only-secret", receipt)


if __name__ == "__main__":
    unittest.main()
