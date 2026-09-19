import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from podcast_automate import claude_code
from podcast_automate.claude_code import (ClaudeCodeAdapter, claude_block_window, claude_command,
                                          classify_claude_failure)
from podcast_automate.errors import AppError
from podcast_automate.model_trace import trace_view
from podcast_automate.models import Contract, RuntimeSettings, TextProbeOutput


class Result(Contract):
    reason: str


# A stand-in for the Claude Code CLI 2.1.92 envelope observed on 2026-09-19 (docs/claude-backend-plan.md, Phase 0).
FAKE_CLAUDE = r'''
import json, os, sys, time
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
mode = os.environ.get("PLA_CLAUDE_TEST", "ok")
assert not any(k in os.environ for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "OPENROUTER_API_KEY"))
assert os.environ.get("DISABLE_TELEMETRY") == "1" and os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1"
args = sys.argv[1:]
if args == ["auth", "status", "--json"]:
    print(json.dumps({"loggedIn": mode != "logout", "authMethod": "apiKey" if mode == "api" else "claude.ai",
                      "apiProvider": "firstParty", "subscriptionType": "max", "email": "private@example.org"}))
    sys.exit(0)
if args == ["--version"]:
    print("2.0.1 (Claude Code)" if mode == "old" else "2.1.92 (Claude Code)")
    sys.exit(0)
def value(flag):
    return args[args.index(flag) + 1]
assert args[0] == "-p" and value("--output-format") == "stream-json" and "--verbose" in args
assert "--include-partial-messages" in args and "--no-session-persistence" in args
assert value("--permission-mode") == "dontAsk" and value("--setting-sources") == "" and "--strict-mcp-config" in args
assert "--disable-slash-commands" in args and value("--system-prompt") and float(value("--max-budget-usd")) > 0
assert value("--model") == "claude-opus-5" and value("--effort") == "high"
schema = json.loads(value("--json-schema"))
assert schema["additionalProperties"] is False and "default" not in json.dumps(schema)
search = value("--tools") == "WebSearch,WebFetch"
assert search or value("--tools") == ""
if search:
    assert value("--allowedTools") == "WebSearch,WebFetch"
prompt = sys.stdin.read()
try:
    payload = json.loads(prompt.splitlines()[-1])
except ValueError:
    payload = {}
def emit(event):
    print(json.dumps(event), flush=True)
def delta(index, kind, **fields):
    emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": index, "delta": {"type": kind, **fields}}})
emit({"type": "system", "subtype": "init", "model": "claude-opus-5", "session_id": "s", "tools": []})
if mode == "hang":
    time.sleep(20)
if mode == "budget":
    emit({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "num_turns": 1,
          "errors": ["Reached maximum budget ($0.001) test-only-secret"], "usage": {}})
    sys.exit(1)
emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}})
delta(0, "thinking_delta", thinking="")
delta(1, "input_json_delta", partial_json='{"reason": "Alpha')
time.sleep(.05)
delta(1, "input_json_delta", partial_json=' Beta"}')
if mode == "retry":
    emit({"type": "system", "subtype": "api_retry", "attempt": 1, "error": "rate_limit"})
structured = {"topic": "incomplete"} if mode == "invalid" else (
    {"topic": payload.get("topic", ""), "focus_questions": ["Wie und warum?"], "note": "Keine Quellenrecherche."}
    if "topic" in payload else {"reason": "Alpha Beta"})
emit({"type": "assistant", "message": {"id": "m1", "content": [{"type": "tool_use", "id": "t1", "name": "StructuredOutput", "input": structured}]}})
if search and mode != "nosearch":
    emit({"type": "assistant", "message": {"id": "m1", "content": [{"type": "tool_use", "id": "t2", "name": "WebSearch", "input": {"query": "actual query"}}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "results"}]}})
    emit({"type": "assistant", "message": {"id": "m1", "content": [{"type": "tool_use", "id": "t3", "name": "WebFetch", "input": {"url": "https://example.org/paper", "prompt": "read"}}]}})
resets = int(time.time()) + 3600
emit({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected" if mode == "quota" else "allowed",
      "resetsAt": resets, "rateLimitType": "five_hour"}})
emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "Structured output provided successfully"}]}})
if mode == "quota":
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 2,
          "result": "You've hit your Opus limit test-only-secret", "errors": [], "usage": {}})
    sys.exit(1)
if mode == "quota_text":
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 2,
          "result": "You've hit your weekly limit test-only-secret", "errors": [], "usage": {}})
    sys.exit(1)
if mode == "incomplete":
    sys.exit(0)
emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "stop_reason": "end_turn",
      "total_cost_usd": 0.0087, "session_id": "s",
      "usage": {"input_tokens": 4, "output_tokens": 123, "cache_read_input_tokens": 691, "cache_creation_input_tokens": 849,
                "server_tool_use": {"web_search_requests": 1 if search and mode != "nosearch" else 0}},
      "modelUsage": {"claude-opus-5": {"costUSD": 0.0087, "contextWindow": 200000, "maxOutputTokens": 32000}},
      "structured_output": structured})
'''


STORE_ENV = patch.dict(os.environ, {"PLA_SUBSCRIPTIONS_STORE": str(Path(tempfile.mkdtemp()) / "subscriptions.json")})


def setUpModule():
    STORE_ENV.start()


def tearDownModule():
    STORE_ENV.stop()


def fake_cli(root):
    script = root / "claude fake.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    return [sys.executable, str(script)]


class ClaudeCodeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Spaces & Umlaut ä"
        self.root.mkdir()
        self.adapter = ClaudeCodeAdapter(RuntimeSettings(text_timeout_seconds=4), model="claude-opus-5", reasoning_effort="high")
        mock = patch.object(self.adapter, "command", return_value=fake_cli(self.root))
        mock.start()
        self.addCleanup(mock.stop)

    def call(self, directory="call", **kwargs):
        return self.adapter.structured("Synthetic test only", Result, self.root / directory, prompt_version="test", **kwargs)

    def test_subscription_call_streams_structured_output_and_scrubs_api_keys(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-only", "ANTHROPIC_AUTH_TOKEN": "test-only",
                                     "OPENAI_API_KEY": "test-only"}):
            result, metadata = self.call()
        self.assertEqual(result.reason, "Alpha Beta")
        self.assertEqual(metadata["provider"], "claude_code")
        self.assertEqual(metadata["auth_mode"], "claude.ai")
        self.assertEqual(metadata["adapter_version"], "claude_code.v1")
        self.assertEqual(metadata["cli_version"], "2.1.92")
        self.assertEqual(metadata["requested_model"], "claude-opus-5")
        self.assertEqual(metadata["actual_model"], "claude-opus-5")
        self.assertEqual(metadata["requested_reasoning_effort"], "high")
        self.assertEqual(metadata["usage"]["input_tokens"], 4)
        self.assertEqual(metadata["usage"]["cache_read_input_tokens"], 691)
        self.assertEqual(metadata["reported_cost_usd"], 0.0087)
        self.assertIn("Gegenwert", metadata["cost_basis"])
        self.assertIsNone(metadata["separately_billed_cost"])
        self.assertEqual(metadata["num_turns"], 2)
        self.assertFalse(metadata["research_performed"])
        self.assertEqual(metadata["rate_limit"]["status"], "allowed")
        self.assertEqual(metadata["rate_limit"]["window"], "five_hour")
        saved = json.loads((self.root / "call/metadata.json").read_text(encoding="utf-8"))
        self.assertNotIn("session_id", saved)
        self.assertEqual(json.loads((self.root / "call/response.json").read_text(encoding="utf-8")), {"reason": "Alpha Beta"})
        self.assertEqual(json.loads((self.root / "call/search_events.json").read_text(encoding="utf-8")), [])
        trace = trace_view(self.root / "call")
        self.assertIn("Einordnung: Alpha Beta", str(trace))
        diagnostics = json.loads((self.root / "call/diagnostics.json").read_text(encoding="utf-8"))
        self.assertEqual(diagnostics["stream_deltas"], 2)
        self.assertIn("first_content_at", diagnostics)
        self.assertEqual(diagnostics["status"], "completed")
        activity = (self.root / "call/activity.json").read_text(encoding="utf-8")
        self.assertIn("Strukturierte Antwort empfangen", activity)

    def test_failures_are_classified_and_receipts_carry_no_provider_text(self):
        expectations = {"quota": ("claude_quota_exhausted", "waiting_for_quota"),
                        "quota_text": ("claude_quota_exhausted", "waiting_for_quota"),
                        "budget": ("claude_budget_cap", "blocked"), "invalid": ("invalid_model_output", "failed"),
                        "logout": ("authentication_required", "blocked"), "api": ("subscription_required", "blocked"),
                        "old": ("claude_version", "blocked"), "incomplete": ("claude_failed", "failed")}
        for mode, (code, status) in expectations.items():
            with self.subTest(mode=mode), patch.dict(os.environ, {"PLA_CLAUDE_TEST": mode}):
                self.adapter._version = None
                with self.assertRaises(AppError) as error:
                    self.call(mode)
                self.assertEqual(error.exception.code, code)
                self.assertEqual(error.exception.status, status)
                self.assertFalse((self.root / mode / "response.json").exists())
                for name in ("failure.json", "diagnostics.json", "activity.json", "model_trace.json"):
                    path = self.root / mode / name
                    if path.exists():
                        self.assertNotIn("test-only-secret", path.read_text(encoding="utf-8"))
        receipt = json.loads((self.root / "invalid/failure.json").read_text(encoding="utf-8"))
        self.assertEqual((receipt["code"], receipt["result_subtype"], receipt["exit_code"]), ("invalid_model_output", "success", 0))
        self.assertTrue(receipt["validation_errors"])
        self.assertTrue(all(set(item) == {"loc", "msg", "type"} for item in receipt["validation_errors"]))
        self.assertEqual(json.loads((self.root / "invalid/rejected_output.json").read_text(encoding="utf-8")),
                         {"topic": "incomplete"})
        with patch.dict(os.environ, {"PLA_CLAUDE_TEST": "quota"}), self.assertRaises(AppError) as error:
            self.call("quota_details")
        until = datetime.fromisoformat(error.exception.details["blocked_until"])
        self.assertLess(abs((until - datetime.now(timezone.utc)) - timedelta(hours=1)), timedelta(minutes=5))
        self.assertEqual(error.exception.details["reason"], "five_hour")
        self.assertIn("wieder verfügbar ab", str(error.exception))
        receipt = json.loads((self.root / "quota_details/failure.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["blocked_until"], error.exception.details["blocked_until"])
        self.assertEqual(receipt["result_subtype"], "error_during_execution")
        with patch.dict(os.environ, {"PLA_CLAUDE_TEST": "quota_text"}), self.assertRaises(AppError) as error:
            self.call("quota_weekly")
        monday = datetime.fromisoformat(error.exception.details["blocked_until"])
        self.assertEqual(monday.weekday(), 0)
        self.assertEqual(error.exception.details["reason"], "weekly_limit")

    def test_research_requires_observed_search_tool_events(self):
        with patch.dict(os.environ, {"PLA_CLAUDE_TEST": "nosearch"}), self.assertRaises(AppError) as error:
            self.call("nosearch", search=True)
        self.assertEqual(error.exception.code, "search_not_observed")
        _, metadata = self.call("search", search=True)
        self.assertTrue(metadata["research_performed"])
        self.assertEqual(metadata["web_search_events"], 2)
        self.assertEqual(metadata["web_search_requests"], 1)
        self.assertEqual(metadata["observed_search_queries"], ["actual query"])
        events = json.loads((self.root / "search/search_events.json").read_text(encoding="utf-8"))
        self.assertEqual(events[0]["action"], {"type": "search", "query": "actual query"})
        self.assertEqual(events[1]["action"]["type"], "open_page")
        activity = (self.root / "search/activity.json").read_text(encoding="utf-8")
        self.assertIn("Websuche gestartet: actual query", activity)
        self.assertIn("Websuche abgeschlossen", activity)
        self.assertIn("example.org", activity)

    def test_retry_notice_is_categorized_and_does_not_invalidate_success(self):
        with patch.dict(os.environ, {"PLA_CLAUDE_TEST": "retry"}):
            result, _ = self.call("retry")
        self.assertEqual(result.reason, "Alpha Beta")
        diagnostics = json.loads((self.root / "retry/diagnostics.json").read_text(encoding="utf-8"))
        self.assertEqual(diagnostics["categories"]["retry"], 1)

    def test_timeout_and_cancel_stop_the_owned_process(self):
        self.adapter.settings = self.adapter.settings.model_copy(update={"text_timeout_seconds": 1})
        with patch.dict(os.environ, {"PLA_CLAUDE_TEST": "hang"}), self.assertRaises(AppError) as error:
            self.call("hang")
        self.assertEqual(error.exception.code, "timeout")
        self.assertIn("einzelne Claude-Aufruf", str(error.exception))
        self.assertEqual(json.loads((self.root / "hang/failure.json").read_text(encoding="utf-8"))["timeout_seconds"], 1)
        self.adapter.cancel_check = lambda: True
        with patch.dict(os.environ, {"PLA_CLAUDE_TEST": "hang"}), self.assertRaises(AppError) as error:
            self.call("cancel")
        self.assertEqual(error.exception.code, "interrupted")

    def test_oversized_schema_never_starts_the_cli(self):
        with patch.object(claude_code, "MAX_SCHEMA_CHARS", 10), self.assertRaises(AppError) as error:
            self.call("oversized")
        self.assertEqual(error.exception.code, "invalid_output_schema")
        self.assertFalse((self.root / "oversized/diagnostics.json").exists())

    def test_probe_keeps_topic_and_invalid_settings_are_rejected_before_start(self):
        result, metadata = self.adapter.probe("Thema ä", self.root / "probe")
        self.assertEqual(result.topic, "Thema ä")
        self.assertIsInstance(result, TextProbeOutput)
        self.assertEqual(metadata["provider"], "claude_code")
        with self.assertRaises(AppError):
            ClaudeCodeAdapter(RuntimeSettings(), reasoning_effort="xhigh")
        with self.assertRaises(AppError):
            ClaudeCodeAdapter(RuntimeSettings(), model="--untrusted-flag")


class ClaudeHelperTests(unittest.TestCase):
    def test_block_window_uses_reported_reset_or_conservative_fallbacks(self):
        now = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)
        until, kind = claude_block_window("limit", rate_limit={"resetsAt": 1789825200, "rateLimitType": "five_hour"}, now=now)
        self.assertEqual(until, datetime.fromtimestamp(1789825200, timezone.utc))
        self.assertEqual(kind, "five_hour")
        until, kind = claude_block_window("You've hit your limit · resets 2026-09-19T14:30:00Z", now=now)
        self.assertEqual(until, datetime(2026, 9, 19, 14, 30, tzinfo=timezone.utc))
        self.assertEqual(kind, "unclear_limit")
        until, _ = claude_block_window("resets 1789825200", now=now)
        self.assertEqual(until, datetime.fromtimestamp(1789825200, timezone.utc))
        until, kind = claude_block_window("You've hit your weekly limit", now=now)
        self.assertEqual((until.weekday(), until.hour, kind), (0, 0, "weekly_limit"))
        self.assertEqual(until, datetime(2026, 9, 21, tzinfo=timezone.utc))
        until, kind = claude_block_window("Opus limit reached", now=now)
        self.assertEqual((until, kind), (now + timedelta(hours=5), "opus_limit"))
        until, kind = claude_block_window("rate limit 429", now=now)
        self.assertEqual((until, kind), (now + timedelta(minutes=30), "unclear_limit"))
        until, _ = claude_block_window("limit resets at 3pm", now=now)
        self.assertGreater(until, now)
        self.assertLessEqual(until - now, timedelta(days=1))

    def test_failure_classes(self):
        self.assertEqual(classify_claude_failure("Reached maximum budget ($1)", subtype="error_max_budget_usd").code, "claude_budget_cap")
        quota = classify_claude_failure("You've hit your limit", subtype="error_during_execution")
        self.assertEqual((quota.code, quota.status), ("claude_quota_exhausted", "waiting_for_quota"))
        limited = classify_claude_failure("", rate_limit={"status": "exceeded", "resetsAt": 1789825200, "rateLimitType": "seven_day"})
        self.assertEqual(limited.details["reason"], "seven_day")
        self.assertEqual(classify_claude_failure("Not logged in").code, "authentication_required")
        self.assertEqual(classify_claude_failure("socket hang up").code, "claude_failed")

    def test_command_resolution_prefers_path_then_home_install_and_unwraps_npm_shims(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            native = home / ".local/bin/claude.exe"
            native.parent.mkdir(parents=True)
            native.touch()
            with patch("podcast_automate.claude_code.shutil.which", return_value=None), \
                    patch("podcast_automate.claude_code.Path.home", return_value=home), \
                    patch("podcast_automate.claude_code.platform.system", return_value="Windows"):
                self.assertEqual(claude_command(), [str(native)])
            shim = home / "claude.cmd"
            script = home / "node_modules/@anthropic-ai/claude-code/cli.js"
            script.parent.mkdir(parents=True)
            script.write_text("// fixture")
            with patch("podcast_automate.claude_code.shutil.which", side_effect=[str(shim), "node.exe"]):
                self.assertEqual(claude_command(), ["node.exe", str(script)])
            with patch("podcast_automate.claude_code.shutil.which", return_value=None), \
                    patch("podcast_automate.claude_code.Path.home", return_value=home / "missing"):
                with self.assertRaises(AppError) as error:
                    claude_command()
                self.assertEqual(error.exception.code, "claude_missing")


if __name__ == "__main__":
    unittest.main()
