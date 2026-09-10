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
if "OPENAI_API_KEY" in os.environ or "CODEX_API_KEY" in os.environ:
    sys.exit(55)
if args == ["login", "status"]:
    print("Logged in using API key: hidden" if mode == "api" else "Logged in using ChatGPT",
          file=sys.stderr)
    sys.exit(0)
if args == ["--version"]:
    print("codex-cli test-version")
    sys.exit(0)
if mode == "timeout":
    time.sleep(20)
if mode == "quota":
    print(json.dumps({"type": "turn.failed", "error": {"message": "usage_limit_reached"}}))
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
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only", "CODEX_API_KEY": "test-only"}):
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

    def test_windows_npm_shim_is_resolved_without_shell(self):
        shim = self.root / "codex.cmd"
        script = self.root / "node_modules/@openai/codex/bin/codex.js"
        script.parent.mkdir(parents=True)
        script.write_text("// fixture")
        with patch("podcast_automate.codex.shutil.which",
                   side_effect=[str(shim), "node.exe"]):
            self.assertEqual(executable_command("codex"), ["node.exe", str(script)])

    def test_process_timeout_is_actionable(self):
        with self.assertRaises(AppError) as error:
            run_process([sys.executable, "-c", "import time; time.sleep(20)"], timeout=1)
        self.assertEqual(error.exception.code, "timeout")


if __name__ == "__main__":
    unittest.main()
