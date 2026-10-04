import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from podcast_automate.codex import CodexAdapter, classify_failure, codex_reset, executable_command
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
if mode == "retry_then_fail":
    # A rate-limit retry the turn outlived, then a failure of its own that is no quota.
    print(json.dumps({"type": "error", "message": "rate limit reached; retrying 1/5"}))
    print("(node:14290) Warning: a deprecated API is used", file=sys.stderr)
    print(json.dumps({"type": "turn.failed", "error": {"message": "stream disconnected before completion"}}))
    sys.exit(1)
if mode == "quota_reset":
    print(json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit. Try again in 2 days 3 hours."}}))
    sys.exit(1)
topic = json.loads(sys.stdin.read().splitlines()[-1])["topic"]
schema = json.loads(pathlib.Path(args[args.index("--output-schema")+1]).read_text())
assert schema["additionalProperties"] is False
response = pathlib.Path(args[args.index("--output-last-message")+1])
if mode == "invalid":
    response.write_text('{"topic": "incomplete"}', encoding="utf-8")
elif mode == "garbled":
    response.write_text('{"topic": "cut off', encoding="utf-8")
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
        self.adapter = CodexAdapter(RuntimeSettings(text_timeout_seconds=3), transport="exec")
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
        import json
        for mode, expected in (("quota", "quota_exhausted"), ("invalid", "rejected_output"),
                               ("garbled", "invalid_model_output"),
                               ("incomplete", "codex_failed"), ("late_failure", "quota_exhausted")):
            with self.subTest(mode=mode), patch.dict(os.environ, {"PLA_TEST_MODE": mode}):
                with self.assertRaises(AppError) as error:
                    self.adapter.probe("Thema", self.root / mode)
                self.assertEqual(error.exception.code, expected)
                receipt = json.loads((self.root / mode / "failure.json").read_text(encoding="utf-8"))
                self.assertEqual(receipt["code"], expected)
                if mode == "invalid":
                    # A parsed answer the contract rejects is charged, kept and correctable.
                    self.assertEqual(error.exception.status, "blocked")
                    self.assertTrue(all(set(item) == {"loc", "msg", "type"} for item in receipt["validation_errors"]))
                    self.assertEqual(json.loads((self.root / mode / "rejected_output.json").read_text(encoding="utf-8")),
                                     {"topic": "incomplete"})
                    self.assertEqual(error.exception.details["payload"], {"topic": "incomplete"})
                if mode == "garbled":
                    # No JSON answer is not an answer: the call is a plain failure with the text kept.
                    self.assertFalse((self.root / mode / "rejected_output.json").exists())
                    self.assertEqual((self.root / mode / "rejected_output.txt").read_text(encoding="utf-8"), '{"topic": "cut off')

    def test_only_the_turns_own_failure_is_classified_and_a_named_reset_is_kept(self):
        """2026-10-02: an earlier rate-limit retry in the same turn made a later stream failure a quota pause."""
        with patch.dict(os.environ, {"PLA_TEST_MODE": "retry_then_fail"}), self.assertRaises(AppError) as error:
            self.adapter.probe("Thema", self.root / "retry_then_fail")
        self.assertEqual((error.exception.code, error.exception.status), ("codex_failed", "failed"))
        # A failure no class names carries Codex's own reason, in the stop and in its receipt (2026-10-04).
        self.assertIn(": stream disconnected before completion.", str(error.exception))
        receipt = json.loads((self.root / "retry_then_fail/failure.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["provider_message"], "stream disconnected before completion")
        with patch.dict(os.environ, {"PLA_TEST_MODE": "quota_reset"}), self.assertRaises(AppError) as error:
            self.adapter.probe("Thema", self.root / "quota_reset")
        self.assertEqual((error.exception.code, error.exception.details["provider"]), ("quota_exhausted", "codex_cli"))
        until = datetime.fromisoformat(error.exception.details["blocked_until"])
        self.assertLess(abs(until - datetime.now(timezone.utc) - timedelta(days=2, hours=3)), timedelta(minutes=5))
        self.assertIn("Voraussichtlich wieder verfügbar ab", str(error.exception))

    def test_failure_classes_read_the_category_before_whole_words(self):
        now = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
        cases = [
            # (failure text, the turn's failure object, expected code)
            ("(node:14290) Warning: something", None, "codex_failed"),
            ("Unexpected quotation in the answer", None, "codex_failed"),
            ("usage_limit_reached", None, "quota_exhausted"),
            ("HTTP 429 Too Many Requests", None, "quota_exhausted"),
            ("401 Unauthorized", None, "authentication_required"),
            ("x", {"message": "x", "codexErrorInfo": "usageLimitExceeded"}, "quota_exhausted"),
            ("x", {"message": "x", "codexErrorInfo": {"responseTooManyFailedAttempts": {"httpStatusCode": 429}}},
             "quota_exhausted"),
            ("x", {"message": "x", "codexErrorInfo": "unauthorized"}, "authentication_required"),
            # The app-server's category decides: a server error that mentions a rate limit is no quota.
            ("rate limit while retrying", {"codexErrorInfo": "internalServerError"}, "codex_failed"),
            ("usage limit", {"codexErrorInfo": "other"}, "quota_exhausted"),
        ]
        for message, error, code in cases:
            with self.subTest(message=message, error=error):
                self.assertEqual(classify_failure(message, error=error, now=now).code, code)
        # Codex's own reason for an unnamed failure is kept without credentials and at most 300 characters long.
        named = classify_failure("x", error={"message": "Model not on this plan; token=abc123 Bearer xyz"}, now=now)
        self.assertEqual(named.details["provider_message"], "Model not on this plan; token=[entfernt] Bearer [entfernt]")
        self.assertNotIn("abc123", str(named))
        self.assertEqual(len(classify_failure("x", error={"message": "y" * 1000}, now=now).details["provider_message"]), 300)
        # Without a turn failure the last line of the CLI's error output is the reason; none at all leaves it out.
        self.assertEqual(classify_failure("noise\nconnection refused\n").details["provider_message"], "connection refused")
        self.assertEqual((str(classify_failure("")), classify_failure("").details),
                         ("Codex-Aufruf fehlgeschlagen. Verbindung, Abo und CLI-Konfiguration prüfen.", {}))
        local = now.astimezone()
        relative = classify_failure("You've hit your usage limit. Try again in 1 day 2 hours 30 minutes.", now=now)
        self.assertEqual(relative.details["blocked_until"], (now + timedelta(days=1, hours=2, minutes=30)).isoformat())
        dated = classify_failure("You've hit your usage limit. Try again at Oct 4th, 2026 3:04 PM.", now=now)
        self.assertEqual(datetime.fromisoformat(dated.details["blocked_until"]),
                         local.replace(month=10, day=4, hour=15, minute=4, second=0, microsecond=0))
        today = codex_reset("try again at 11:59 PM", now=now)
        self.assertEqual(today.astimezone().strftime("%H:%M"), "23:59")
        self.assertTrue(now < today <= now + timedelta(days=1))
        self.assertNotIn("blocked_until", classify_failure("usage_limit_reached", now=now).details)

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
            kwargs["on_stderr_line"]("TLS connection reset; retrying; token=test-only-secret")
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
        diagnostics = (directory / "diagnostics.json").read_text(encoding="utf-8")
        self.assertIn('"category": "connection"', diagnostics)
        self.assertNotIn("test-only-secret", diagnostics)


if __name__ == "__main__":
    unittest.main()
