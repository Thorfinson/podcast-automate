import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from podcast_automate import storage, subscriptions
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

    def test_a_paused_run_resumes_at_the_failing_providers_reset_else_backs_off(self):
        """2026-10-02: a Codex error without details took Claude's reset or a fixed half hour, and three automatic
        resumes were spent within 90 minutes of a Codex week limit."""
        now = datetime.fromtimestamp(self.seconds, timezone.utc)
        codex_reset, claude_reset = now + timedelta(hours=3), now + timedelta(days=2)
        quota = lambda **details: AppError("Kontingent", code="quota_exhausted", status="waiting_for_quota",  # noqa: E731
                                           details=details)
        # Nothing known: 30 minutes, then one hour, then two, whichever provider failed.
        self.assertEqual([subscriptions.quota_retry_at(quota(), clock=self.clock, attempt=n) for n in (0, 1, 2)],
                         [(now + timedelta(minutes=m)).isoformat() for m in (30, 60, 120)])
        subscriptions.update_store("codex_cli", {"snapshot": {"resets_at": codex_reset.isoformat()}})
        record_quota_failure("claude_code", AppError("Claude", code="claude_quota_exhausted", status="waiting_for_quota",
                             details={"blocked_until": claude_reset.isoformat(), "reason": "seven_day"}), clock=self.clock)
        self.assertEqual(subscriptions.quota_retry_at(quota(provider="claude_code"), clock=self.clock), claude_reset.isoformat())
        self.assertEqual(subscriptions.quota_retry_at(quota(provider="codex_cli"), clock=self.clock), codex_reset.isoformat())
        named = now + timedelta(hours=7)
        self.assertEqual(subscriptions.quota_retry_at(quota(provider="codex_cli", blocked_until=named.isoformat()),
                                                      clock=self.clock), named.isoformat())
        both = AppError("leer", code="subscriptions_exhausted", status="waiting_for_quota",
                        details={"earliest_reset": None, "earliest_provider": None})
        self.assertEqual(subscriptions.quota_retry_at(both, clock=self.clock), codex_reset.isoformat())
        # A reset in the past is no answer; the backoff applies.
        self.seconds += 4 * 3600
        self.assertEqual(subscriptions.quota_retry_at(quota(provider="codex_cli"), clock=self.clock, attempt=1),
                         (datetime.fromtimestamp(self.seconds, timezone.utc) + timedelta(hours=1)).isoformat())

    def test_an_unusable_subscription_is_passed_over_until_its_note_expires_or_a_call_succeeds(self):
        candidates = {"codex_cli": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
                      "claude_code": {"model": "claude-sonnet-5-5", "reasoning_effort": "high"}}
        with patch.object(subscriptions, "codex_quota", return_value=snapshot("codex_cli", available=True)), \
                patch.object(subscriptions, "claude_quota", return_value=snapshot("claude_code", available=True)):
            self.assertEqual(choose_subscription(self.settings, candidates, prefer="claude_code", clock=self.clock)["provider"],
                             "claude_code")
            note = subscriptions.record_unavailable(
                "claude_code", AppError("Anmeldung", code="authentication_required", status="blocked"), clock=self.clock)
            self.assertEqual(note["reason"], "authentication_required")
            choice = choose_subscription(self.settings, candidates, prefer="claude_code", clock=self.clock)
            self.assertEqual(choice["provider"], "codex_cli")
            self.assertIn("claude_unavailable (authentication_required)", choice["reason"])
            self.seconds += subscriptions.UNAVAILABLE_SECONDS + 1
            self.assertEqual(choose_subscription(self.settings, candidates, prefer="claude_code", clock=self.clock)["provider"],
                             "claude_code")
            subscriptions.record_unavailable("claude_code", AppError("alt", code="claude_version"), clock=self.clock)
            record_claude_success(None, clock=self.clock)
            self.assertIsNone(subscriptions.unavailable_state("claude_code", clock=self.clock))

    def test_a_login_checked_after_the_note_ends_it_and_a_noted_provider_is_checked_afresh(self):
        """2026-10-02 review: after 'claude auth login', the connection check, the doctor and the next resume still
        reported Claude unusable until the ten-minute note ran out."""
        candidates = {"codex_cli": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
                      "claude_code": {"model": "claude-sonnet-5-5", "reasoning_effort": "high"}}
        login = AppError("Anmeldung", code="authentication_required", status="blocked")
        note = subscriptions.record_unavailable("claude_code", login, clock=self.clock)
        detected = subscriptions.parse_iso(note["detected_at"])

        def claude(seconds, usable=True):
            checked = (detected + timedelta(seconds=seconds)).isoformat()
            return {**snapshot("claude_code", available=usable, usable=usable), "checked_at": checked}
        self.assertFalse(subscriptions.with_unavailable("claude_code", claude(-5), clock=self.clock)["usable"],
                         "the login check from before the failing call does not end the note")
        self.assertTrue(subscriptions.with_unavailable("claude_code", claude(5), clock=self.clock)["available"])
        self.assertEqual(subscriptions.with_unavailable("claude_code", claude(5, usable=False), clock=self.clock)["reason"],
                         "authentication_required")
        # Too old a CLI for one model is no login reason: the login check knows no model, so the note stands.
        subscriptions.record_unavailable("claude_code", AppError("alt", code="claude_version"), clock=self.clock)
        self.assertFalse(subscriptions.with_unavailable("claude_code", claude(5), clock=self.clock)["usable"])
        subscriptions.record_unavailable("claude_code", login, clock=self.clock)
        refreshed = []

        def fresh(*, refresh=False, clock=None):
            refreshed.append(refresh)
            return claude(5)
        with patch.object(subscriptions, "codex_quota", return_value=snapshot("codex_cli", available=True)), \
                patch.object(subscriptions, "claude_quota", side_effect=fresh):
            choice = choose_subscription(self.settings, candidates, prefer="claude_code", clock=self.clock)
        self.assertEqual((choice["provider"], refreshed), ("claude_code", [True]))

    def test_extra_usage_passes_over_a_noted_claude_block_while_it_is_on(self):
        """2026-10-03: the user bought Claude usage beyond the weekly window; the Studio kept the noted block and paused
        every run as 'both subscriptions exhausted' although Claude would have answered."""
        until = datetime.fromtimestamp(self.seconds, timezone.utc) + timedelta(hours=20)
        record_quota_failure("claude_code", AppError("Wochenlimit", code="claude_quota_exhausted", status="waiting_for_quota",
                             details={"blocked_until": until.isoformat(), "reason": "seven_day"}), clock=self.clock)
        login = {"logged_in": True, "auth_method": "claude.ai", "version_supported": True, "subscription": "max",
                 "checked_at": datetime.fromtimestamp(self.seconds, timezone.utc).isoformat()}
        with patch.object(subscriptions, "claude_login", return_value=login):
            self.assertFalse(claude_quota(clock=self.clock)["available"])
            subscriptions.set_claude_extra_usage(True)
            snapshot = claude_quota(clock=self.clock)
            self.assertEqual((snapshot["available"], snapshot.get("extra_usage")), (True, True))
            self.assertIn("Zusatzkontingent", subscriptions.describe_snapshot("claude_code", snapshot))
            self.assertEqual(claude_quota_state(clock=self.clock)["blocked_until"], until.isoformat(), "the block stays noted")
            subscriptions.set_claude_extra_usage(False)
            self.assertFalse(claude_quota(clock=self.clock)["available"])

    def test_a_success_clears_only_a_block_noted_before_its_call_started(self):
        """2026-10-03: parallel calls ran into Claude's weekly limit; one that had started before the block finished after
        it and cleared it, and the run paused until Codex's reset a week away instead of Claude's the next day."""
        until = datetime.fromtimestamp(self.seconds, timezone.utc) + timedelta(hours=20)
        started = datetime.fromtimestamp(self.seconds, timezone.utc).isoformat()
        self.seconds += 60
        weekly = AppError("Wochenlimit", code="claude_quota_exhausted", status="waiting_for_quota",
                          details={"blocked_until": until.isoformat(), "reason": "seven_day"})
        record_quota_failure("claude_code", weekly, clock=self.clock)
        self.seconds += 60
        record_claude_success({"status": "allowed_warning", "window": "seven_day"}, clock=self.clock, started_at=started)
        self.assertEqual(claude_quota_state(clock=self.clock)["blocked_until"], until.isoformat(), "noted while it ran")
        self.assertEqual(subscriptions.read_store()["claude_code"]["last_rate_limit"]["window"], "seven_day")
        later = datetime.fromtimestamp(self.seconds, timezone.utc).isoformat()
        record_claude_success(None, clock=self.clock, started_at=later)
        self.assertIsNone(claude_quota_state(clock=self.clock), "a call started after the block ends it")

    def test_the_store_is_read_through_a_concurrent_rename(self):
        until = datetime.fromtimestamp(self.seconds, timezone.utc) + timedelta(hours=5)
        record_quota_failure("claude_code", AppError("Claude", code="claude_quota_exhausted", status="waiting_for_quota",
                             details={"blocked_until": until.isoformat(), "reason": "five_hour"}), clock=self.clock)
        real, reads = Path.read_text, []

        def flaky(path, *args, **kwargs):
            reads.append(path)
            if len(reads) == 1:
                raise PermissionError(13, "sharing violation")
            return real(path, *args, **kwargs)

        with patch.object(storage, "SHARING_VIOLATIONS", True), patch.object(storage.time, "sleep"), \
                patch.object(Path, "read_text", flaky):
            # A read on the instant of another process's rename used to return {} and hide the block.
            self.assertEqual(claude_quota_state(clock=self.clock)["blocked_until"], until.isoformat())
        self.assertEqual(len(reads), 2)

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
