import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from podcast_automate import subscriptions
from podcast_automate.errors import AppError
from podcast_automate.models import RuntimeSettings
from podcast_automate.subscriptions import (choose_subscription, claude_quota, claude_quota_state, codex_quota,
                                            normalize_codex_limits, quota_overview, record_claude_success,
                                            record_quota_failure)

# A stand-in for the Codex app-server account RPCs observed on 2026-09-19 (docs/claude-backend-plan.md).
CODEX_SERVER = r'''
import json, os, sys
limits = json.loads(os.environ.get("PLA_CODEX_LIMITS", "{}"))
assert not any(k in os.environ for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"))
assert "app-server" in sys.argv
def emit(value): print(json.dumps(value), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialized": continue
    if method == "initialize":
        emit({"id": 1, "method": "client/ping", "params": {}})  # a server request the client must decline
        emit({"id": request["id"], "result": {}})
    elif method == "account/read":
        emit({"id": request["id"], "result": {"account": {"type": limits.get("account", "chatgpt"), "email": "private@example.org", "planType": "prolite"}}})
    elif method == "account/rateLimits/read":
        window = {"usedPercent": limits.get("used", 100), "windowDurationMins": 10080, "resetsAt": limits.get("resets", 1790109078)}
        rate = {"limitId": "codex", "primary": window, "secondary": None, "credits": {"hasCredits": False},
                "planType": "prolite", "spendControlReached": False,
                "rateLimitReachedType": "rate_limit_reached" if limits.get("used", 100) >= 100 else None}
        emit({"id": request["id"], "result": {"ordinaryUsageAllowed": limits.get("used", 100) < 100, "rateLimits": rate,
              "rateLimitsByLimitId": {"codex": rate}, "accountId": "acc-private"}})
'''

FAKE_CLAUDE = r'''
import json, os, sys
mode = os.environ.get("PLA_CLAUDE_LOGIN", "ok")
if sys.argv[1:] == ["auth", "status", "--json"]:
    print(json.dumps({"loggedIn": mode != "logout", "authMethod": "apiKey" if mode == "api" else "claude.ai",
                      "subscriptionType": "max", "email": "private@example.org", "orgId": "org-private"}))
elif sys.argv[1:] == ["--version"]:
    print("2.1.283 (Claude Code)")
'''


def snapshot(provider, *, available, usable=True, resets_at=None, reason=None):
    return {"provider": provider, "available": available, "usable": usable, "resets_at": resets_at,
            "blocked_until": resets_at if provider == "claude_code" else None, "reason": reason,
            "plan": "max" if provider == "claude_code" else "prolite", "windows": [], "login": {}}


class SubscriptionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Store ä"
        self.root.mkdir()
        (self.root / "codex_server.py").write_text(CODEX_SERVER, encoding="utf-8")
        (self.root / "claude_fake.py").write_text(FAKE_CLAUDE, encoding="utf-8")
        env = patch.dict(os.environ, {"PLA_SUBSCRIPTIONS_STORE": str(self.root / "subscriptions.json")})
        env.start()
        self.addCleanup(env.stop)
        codex = patch("podcast_automate.subscriptions.executable_command",
                      return_value=[sys.executable, str(self.root / "codex_server.py")])
        codex.start()
        self.addCleanup(codex.stop)
        claude = patch("podcast_automate.subscriptions.claude_command",
                       return_value=[sys.executable, str(self.root / "claude_fake.py")])
        claude.start()
        self.addCleanup(claude.stop)
        self.settings = RuntimeSettings()
        self.seconds = 1_790_000_000

    def clock(self):
        return self.seconds

    def test_codex_rate_limits_are_normalized_cached_and_refreshed(self):
        with patch.dict(os.environ, {"PLA_CODEX_LIMITS": json.dumps({"used": 100, "resets": 1790109078})}):
            first = codex_quota(self.settings, clock=self.clock)
        self.assertFalse(first["available"])
        self.assertTrue(first["usable"])
        self.assertEqual(first["plan"], "prolite")
        self.assertEqual(first["reason"], "rate_limit_reached")
        self.assertEqual(first["resets_at"], datetime.fromtimestamp(1790109078, timezone.utc).isoformat())
        self.assertEqual(first["windows"][0]["window_minutes"], 10080)
        self.assertEqual(first["windows"][0]["used_percent"], 100)
        with patch.dict(os.environ, {"PLA_CODEX_LIMITS": json.dumps({"used": 12})}):
            self.seconds += 119
            self.assertEqual(codex_quota(self.settings, clock=self.clock), first)
            self.seconds += 2
            refreshed = codex_quota(self.settings, clock=self.clock)
        self.assertTrue(refreshed["available"])
        self.assertIsNone(refreshed["resets_at"])
        self.assertIsNone(refreshed["reason"])
        with patch.dict(os.environ, {"PLA_CODEX_LIMITS": json.dumps({"used": 100})}):
            self.assertTrue(codex_quota(self.settings, clock=self.clock)["available"])
            self.assertFalse(codex_quota(self.settings, refresh=True, clock=self.clock)["available"])
        store = (self.root / "subscriptions.json").read_text(encoding="utf-8")
        self.assertNotIn("private@example.org", store)
        self.assertNotIn("acc-private", store)

    def test_codex_without_subscription_or_cli_is_unusable_not_exhausted(self):
        with patch.dict(os.environ, {"PLA_CODEX_LIMITS": json.dumps({"account": "apikey"})}):
            quota = codex_quota(self.settings, refresh=True, clock=self.clock)
        self.assertEqual((quota["usable"], quota["reason"]), (False, "subscription_required"))
        with patch("podcast_automate.subscriptions.executable_command",
                   side_effect=AppError("fehlt", code="codex_missing", status="blocked")):
            quota = codex_quota(self.settings, refresh=True, clock=self.clock)
        self.assertEqual((quota["usable"], quota["reason"]), (False, "codex_missing"))
        raw = {"account": {"type": "chatgpt"}, "rate_limits": {"rateLimits": {
            "primary": {"usedPercent": 40, "windowDurationMins": 300, "resetsAt": 1790000600},
            "secondary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": 1790109078}}}}
        normalized = normalize_codex_limits(raw, checked_at="now")
        self.assertFalse(normalized["available"])
        self.assertEqual(normalized["reason"], "window_exhausted")
        self.assertEqual(normalized["resets_at"], datetime.fromtimestamp(1790109078, timezone.utc).isoformat())

    def test_claude_login_block_and_release(self):
        quota = claude_quota(refresh=True, clock=self.clock)
        self.assertTrue(quota["available"])
        self.assertEqual(quota["plan"], "max")
        self.assertEqual(quota["login"]["cli_version"], "2.1.283")
        until = datetime.fromtimestamp(self.seconds, timezone.utc) + timedelta(hours=5)
        error = AppError("Claude-Abo-Kontingent erreicht", code="claude_quota_exhausted", status="waiting_for_quota",
                         details={"blocked_until": until.isoformat(), "reason": "opus_limit",
                                  "message_excerpt": "You've hit your Opus limit"})
        block = record_quota_failure("claude_code", error, clock=self.clock)
        self.assertEqual(block["reason"], "opus_limit")
        blocked = claude_quota(clock=self.clock)
        self.assertFalse(blocked["available"])
        self.assertTrue(blocked["usable"])
        self.assertEqual(blocked["blocked_until"], until.isoformat())
        self.assertEqual(blocked["reason"], "opus_limit")
        self.seconds += 5 * 3600 + 1
        self.assertIsNone(claude_quota_state(clock=self.clock))
        self.assertTrue(claude_quota(clock=self.clock)["available"])
        record_quota_failure("claude_code", AppError("You've hit your weekly limit", code="claude_quota_exhausted",
                                                     status="waiting_for_quota"), clock=self.clock)
        self.assertEqual(claude_quota(clock=self.clock)["reason"], "weekly_limit")
        record_claude_success({"status": "allowed", "window": "five_hour", "resets_at": "2026-09-19T12:00:00+00:00"},
                              clock=self.clock)
        released = claude_quota(clock=self.clock)
        self.assertTrue(released["available"])
        self.assertEqual(released["last_rate_limit"]["window"], "five_hour")
        with patch.dict(os.environ, {"PLA_CLAUDE_LOGIN": "api"}):
            self.assertEqual(claude_quota(refresh=True, clock=self.clock)["reason"], "subscription_required")
        with patch.dict(os.environ, {"PLA_CLAUDE_LOGIN": "logout"}):
            self.assertEqual(claude_quota(refresh=True, clock=self.clock)["reason"], "authentication_required")
        with patch("podcast_automate.subscriptions.claude_command",
                   side_effect=AppError("fehlt", code="claude_missing", status="blocked")):
            self.assertEqual(claude_quota(refresh=True, clock=self.clock)["reason"], "claude_missing")
        self.assertNotIn("private@example.org", (self.root / "subscriptions.json").read_text(encoding="utf-8"))
        self.assertNotIn("org-private", (self.root / "subscriptions.json").read_text(encoding="utf-8"))

    def test_overview_lines_name_windows_resets_and_login(self):
        with patch.dict(os.environ, {"PLA_CODEX_LIMITS": json.dumps({"used": 100, "resets": 1790109078})}):
            overview = quota_overview(self.settings, clock=self.clock)
        self.assertIn("Codex-Abo (prolite): Wochenfenster 100 % verbraucht (Reset ", overview["lines"][0])
        self.assertIn("kein Kontingent", overview["lines"][0])
        self.assertIn("Claude-Abo (max): angemeldet über claude.ai · Claude Code 2.1.283 · bereit", overview["lines"][1])
        self.assertTrue(overview["any_usable"])
        self.assertTrue(overview["any_available"])


class ChoiceRuleTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"PLA_SUBSCRIPTIONS_STORE": str(Path(tempfile.mkdtemp()) / "subscriptions.json")})
        env.start()
        self.addCleanup(env.stop)
        self.candidates = {"codex_cli": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
                           "claude_code": {"model": "claude-opus-5", "reasoning_effort": "high"}}
        self.settings = RuntimeSettings()

    def choose(self, codex, claude, **kwargs):
        with patch.object(subscriptions, "codex_quota", return_value=codex), \
                patch.object(subscriptions, "claude_quota", return_value=claude):
            return choose_subscription(self.settings, self.candidates, **kwargs)

    def test_rule_table(self):
        soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        later = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        codex_ok = snapshot("codex_cli", available=True)
        codex_out = snapshot("codex_cli", available=False, resets_at=later, reason="rate_limit_reached")
        codex_missing = snapshot("codex_cli", available=False, usable=False, reason="codex_missing")
        claude_ok = snapshot("claude_code", available=True)
        claude_blocked = snapshot("claude_code", available=False, resets_at=soon, reason="opus_limit")
        claude_missing = snapshot("claude_code", available=False, usable=False, reason="authentication_required")
        choice = self.choose(codex_ok, claude_ok)
        self.assertEqual((choice["provider"], choice["model"], choice["reason"]), ("codex_cli", "gpt-6-astra", "codex_available"))
        choice = self.choose(codex_out, claude_ok)
        self.assertEqual((choice["provider"], choice["reasoning_effort"]), ("claude_code", "high"))
        self.assertIn("codex_exhausted_until", choice["reason"])
        self.assertEqual(choice["snapshots"]["codex_cli"]["reason"], "rate_limit_reached")
        choice = self.choose(codex_missing, claude_ok)
        self.assertEqual(choice["provider"], "claude_code")
        self.assertIn("codex_unavailable (codex_missing)", choice["reason"])
        self.assertEqual(self.choose(codex_ok, claude_blocked)["provider"], "codex_cli")
        self.assertEqual(self.choose(codex_ok, claude_ok, exclude=["codex_cli"])["provider"], "claude_code")
        self.assertEqual(self.choose(codex_ok, claude_ok, prefer="claude_code")["provider"], "claude_code")
        with self.assertRaises(AppError) as paused:
            self.choose(codex_out, claude_blocked)
        self.assertEqual((paused.exception.code, paused.exception.status), ("subscriptions_exhausted", "waiting_for_quota"))
        self.assertEqual(paused.exception.details["earliest_provider"], "claude_code")
        self.assertEqual(paused.exception.details["earliest_reset"], soon)
        self.assertIn("Frühester Reset", str(paused.exception))
        self.assertIn("Claude", str(paused.exception))
        with self.assertRaises(AppError) as paused:
            self.choose(codex_out, claude_missing)
        self.assertEqual(paused.exception.status, "waiting_for_quota")
        self.assertEqual(paused.exception.details["earliest_provider"], "codex_cli")
        with self.assertRaises(AppError) as blocked:
            self.choose(codex_missing, claude_missing)
        self.assertEqual((blocked.exception.code, blocked.exception.status), ("subscription_required", "blocked"))

    def test_expired_codex_cache_is_re_read_before_choosing(self):
        calls = []

        def codex(settings, *, refresh=False, clock=None):
            calls.append(refresh)
            return snapshot("codex_cli", available=True)

        with patch.object(subscriptions, "codex_quota", side_effect=codex), \
                patch.object(subscriptions, "claude_quota", return_value=snapshot("claude_code", available=True)):
            choose_subscription(self.settings, self.candidates)
            choose_subscription(self.settings, self.candidates, refresh=True)
        self.assertEqual(calls, [False, True])


if __name__ == "__main__":
    unittest.main()
