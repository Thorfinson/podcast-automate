"""Subscription quota snapshots and the per-call provider rule. Never credentials, never raw model output.

The store lives at ``~/.podcast-automate/subscriptions.json`` (override: ``PLA_SUBSCRIPTIONS_STORE``).
It holds the last Codex rate-limit snapshot, the last Claude login facts, the last Claude rate-limit
event and any active Claude block. Workers, the Studio server and the status monitor share it through
the lock file next to it. Codex can report its windows before a call; Claude cannot, so a Claude
block starts with the first limit error and ends at the reset time that error named.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .call_activity import clean_status
from .claude_code import (MINIMUM_CLI_VERSION, claude_block_window, claude_command, claude_environment,
                          format_local, login_status, parse_version, version_text)
from .codex import executable_command, subscription_environment
from .codex_stream import read_rate_limits
from .errors import AppError
from .process import run_process
from .storage import file_lock, write_json

CODEX_CACHE_SECONDS = 120
LOGIN_CACHE_SECONDS = 600
QUERY_TIMEOUT = 20
WINDOW_LABELS = {10080: "Wochenfenster", 300: "5-Stunden-Fenster"}
REASON_LABELS = {
    "codex_missing": "Codex CLI nicht gefunden", "claude_missing": "Claude Code nicht gefunden",
    "missing_executable": "CLI nicht startbar", "authentication_required": "nicht angemeldet",
    "subscription_required": "keine Abo-Anmeldung", "timeout": "Kontingentabfrage ohne Antwort",
    "codex_failed": "Kontingentabfrage fehlgeschlagen", "rate_limit_reached": "Limit erreicht",
    "window_exhausted": "Fenster ausgeschöpft", "spend_control_reached": "Ausgabengrenze erreicht",
    "usage_not_allowed": "Nutzung derzeit nicht erlaubt", "weekly_limit": "Wochenlimit",
    "opus_limit": "Opus-Limit", "session_limit": "Sitzungslimit", "unclear_limit": "Limit",
    "five_hour": "5-Stunden-Fenster", "seven_day": "Wochenfenster", "unsupported_version": "CLI zu alt",
}
_refresh = threading.Lock()


def store_path() -> Path:
    override = os.environ.get("PLA_SUBSCRIPTIONS_STORE")
    return Path(override) if override else Path.home() / ".podcast-automate" / "subscriptions.json"


def read_store() -> dict:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update_store(section: str, values: dict, *, remove=()) -> dict:
    path = store_path()
    with file_lock(path.with_name(".subscriptions.lock"), timeout=10):
        data = read_store()
        current = data.get(section) if isinstance(data.get(section), dict) else {}
        current.update(values)
        for key in remove:
            current.pop(key, None)
        data[section] = current
        data["version"] = 1
        write_json(path, data)
    return current


def iso_at(seconds) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def iso_from_epoch(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return iso_at(value)


def parse_iso(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_text(value) -> str | None:
    parsed = parse_iso(value)
    return format_local(parsed) if parsed else None


def normalize_codex_limits(raw: dict, *, checked_at: str) -> dict:
    """Public quota facts from ``account/read`` and ``account/rateLimits/read``; no IDs or e-mail."""
    account = raw.get("account") if isinstance(raw.get("account"), dict) else {}
    limits = raw.get("rate_limits") if isinstance(raw.get("rate_limits"), dict) else {}
    plan = account.get("planType") if isinstance(account.get("planType"), str) else None
    if account.get("type") != "chatgpt":
        return {"provider": "codex_cli", "available": False, "usable": False, "reason": "subscription_required",
                "plan": plan, "windows": [], "resets_at": None, "checked_at": checked_at}
    top = limits.get("rateLimits") if isinstance(limits.get("rateLimits"), dict) else {}
    by_id = limits.get("rateLimitsByLimitId") if isinstance(limits.get("rateLimitsByLimitId"), dict) else {}
    sources = [entry for entry in by_id.values() if isinstance(entry, dict)] or ([top] if top else [])
    windows, reached, spend = [], None, False
    for entry in sources:
        plan = entry.get("planType") if isinstance(entry.get("planType"), str) else plan
        reached = reached or (entry.get("rateLimitReachedType") if isinstance(entry.get("rateLimitReachedType"), str) else None)
        spend = spend or bool(entry.get("spendControlReached"))
        for name in ("primary", "secondary"):
            window = entry.get(name)
            if not isinstance(window, dict):
                continue
            used = window.get("usedPercent")
            minutes = window.get("windowDurationMins")
            windows.append({"limit": entry.get("limitId") if isinstance(entry.get("limitId"), str) else "codex",
                            "name": name,
                            "used_percent": used if isinstance(used, (int, float)) and not isinstance(used, bool) else None,
                            "window_minutes": minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else None,
                            "resets_at": iso_from_epoch(window.get("resetsAt"))})
    exhausted = [w for w in windows if w["used_percent"] is not None and w["used_percent"] >= 100]
    allowed = limits.get("ordinaryUsageAllowed")
    available = not exhausted and not reached and not spend and allowed is not False
    resets = sorted(w["resets_at"] for w in (exhausted or windows) if w["resets_at"])
    reason = None if available else (reached or ("spend_control_reached" if spend else None) or
                                     ("window_exhausted" if exhausted else "usage_not_allowed"))
    return {"provider": "codex_cli", "available": available, "usable": True, "reason": reason, "plan": plan,
            "windows": windows, "resets_at": resets[0] if resets and not available else None,
            "checked_at": checked_at}


def codex_quota(settings, *, refresh=False, clock=time.time) -> dict:
    """Cached rate-limit snapshot, re-read at most every two minutes unless ``refresh`` is set."""
    def fresh():
        cached = read_store().get("codex_cli") or {}
        snapshot = cached.get("snapshot")
        if (not refresh and isinstance(snapshot, dict) and
                clock() - float(cached.get("checked_epoch") or 0) < CODEX_CACHE_SECONDS):
            return snapshot
        return None

    snapshot = fresh()
    if snapshot is not None:
        return snapshot
    with _refresh:
        snapshot = fresh()
        if snapshot is not None:
            return snapshot
        checked_at = iso_at(clock())
        try:
            raw = read_rate_limits(executable_command(settings.codex_executable), env=subscription_environment(),
                                   timeout=QUERY_TIMEOUT)
            snapshot = normalize_codex_limits(raw, checked_at=checked_at)
        except AppError as exc:
            snapshot = {"provider": "codex_cli", "available": False, "usable": False, "reason": exc.code,
                        "plan": None, "windows": [], "resets_at": None, "checked_at": checked_at}
        update_store("codex_cli", {"checked_at": checked_at, "checked_epoch": clock(), "snapshot": snapshot})
        return snapshot


def claude_login(*, refresh=False, clock=time.time) -> dict:
    """Installed CLI version and login method, cached for ten minutes; nothing personal is kept."""
    cached = read_store().get("claude_code") or {}
    login = cached.get("login")
    if (not refresh and isinstance(login, dict) and
            clock() - float(cached.get("login_epoch") or 0) < LOGIN_CACHE_SECONDS):
        return login
    checked_at = iso_at(clock())
    try:
        command = claude_command()
        status = login_status(command, timeout=QUERY_TIMEOUT)
        version = run_process(command + ["--version"], timeout=QUERY_TIMEOUT, env=claude_environment())
        parsed = parse_version(version.stdout) if version.returncode == 0 else None
        login = {"installed": True, **status, "cli_version": version_text(parsed),
                 "version_supported": parsed is not None and parsed >= MINIMUM_CLI_VERSION,
                 "error": None, "checked_at": checked_at}
    except AppError as exc:
        login = {"installed": exc.code != "claude_missing", "logged_in": False, "auth_method": None,
                 "subscription": None, "api_provider": None, "cli_version": None, "version_supported": False,
                 "error": exc.code, "checked_at": checked_at}
    update_store("claude_code", {"login": login, "login_epoch": clock()})
    return login


def claude_quota_state(*, clock=time.time) -> dict | None:
    """The active block entry, or None once its ``blocked_until`` has passed."""
    block = (read_store().get("claude_code") or {}).get("block")
    if not isinstance(block, dict):
        return None
    until = parse_iso(block.get("blocked_until"))
    if until is None or until.timestamp() <= clock():
        return None
    return block


def claude_quota(*, refresh=False, clock=time.time) -> dict:
    login = claude_login(refresh=refresh, clock=clock)
    block = claude_quota_state(clock=clock)
    usable = bool(login.get("logged_in")) and login.get("auth_method") == "claude.ai" and bool(login.get("version_supported"))
    if usable:
        reason = (block.get("reason") or "unclear_limit") if block else None
    elif login.get("error"):
        reason = login["error"]
    elif login.get("logged_in") and login.get("auth_method") != "claude.ai":
        reason = "subscription_required"
    elif login.get("logged_in"):
        reason = "unsupported_version"
    else:
        reason = "authentication_required"
    return {"provider": "claude_code", "available": usable and block is None, "usable": usable,
            "plan": login.get("subscription"),
            "login": {key: login.get(key) for key in ("logged_in", "auth_method", "subscription", "cli_version",
                                                     "version_supported")},
            "blocked_until": block.get("blocked_until") if block else None,
            "resets_at": block.get("blocked_until") if block else None, "reason": reason,
            "last_rate_limit": (read_store().get("claude_code") or {}).get("last_rate_limit"),
            "checked_at": login.get("checked_at")}


def record_quota_failure(provider, error, *, settings=None, clock=time.time):
    """Codex: drop the cache and re-read. Claude: note the block until the reset the error named."""
    if provider == "codex_cli":
        return codex_quota(settings, refresh=True, clock=clock) if settings is not None else None
    if provider == "claude_code":
        details = getattr(error, "details", None) or {}
        until = parse_iso(details.get("blocked_until"))
        reason = details.get("reason")
        if until is None:
            until, reason = claude_block_window(str(error), now=datetime.fromtimestamp(clock(), timezone.utc))
        block = {"blocked_until": until.isoformat(), "reason": reason, "detected_at": iso_at(clock()),
                 "message_excerpt": clean_status(details.get("message_excerpt") or str(error), 160)}
        update_store("claude_code", {"block": block})
        return block
    return None


def quota_retry_at(error, *, clock=time.time) -> str:
    """When a paused run may be resumed automatically: the reset the error named, else the latest
    Codex snapshot or Claude block, else a conservative half hour. Always in the future."""
    details = getattr(error, "details", None) or {}
    candidates = [details.get("earliest_reset"), details.get("blocked_until"),
                  ((read_store().get("codex_cli") or {}).get("snapshot") or {}).get("resets_at"),
                  (claude_quota_state(clock=clock) or {}).get("blocked_until")]
    for value in candidates:
        parsed = parse_iso(value)
        if parsed and parsed.timestamp() > clock():
            return parsed.isoformat()
    return iso_at(clock() + 1800)


def record_claude_success(rate_limit=None, *, clock=time.time):
    """A completed Claude call ends any block and keeps the CLI's latest window facts."""
    values = {}
    if isinstance(rate_limit, dict):
        values["last_rate_limit"] = {"status": rate_limit.get("status"), "window": rate_limit.get("window"),
                                     "resets_at": rate_limit.get("resets_at"), "observed_at": iso_at(clock())}
    # Nothing to note and nothing to clear: leave the store untouched (and uncreated).
    if not values and not isinstance((read_store().get("claude_code") or {}).get("block"), dict):
        return
    update_store("claude_code", values, remove=("block",))


def describe_snapshot(provider, snapshot) -> str:
    """One readable line per subscription for the CLI, doctor and Studio."""
    if provider == "codex_cli":
        plan = f" ({snapshot.get('plan')})" if snapshot.get("plan") else ""
        if not snapshot.get("usable"):
            return f"Codex-Abo{plan}: nicht nutzbar · {REASON_LABELS.get(snapshot.get('reason'), snapshot.get('reason') or 'unbekannt')}"
        parts = []
        for window in snapshot.get("windows") or []:
            label = WINDOW_LABELS.get(window.get("window_minutes"), f"{window.get('window_minutes')}-Minuten-Fenster")
            used = window.get("used_percent")
            reset = local_text(window.get("resets_at"))
            usage = f"{label} {used:g} % verbraucht" if isinstance(used, (int, float)) else f"{label} ohne Angabe"
            parts.append(usage + (f" (Reset {reset})" if reset else ""))
        state = "bereit" if snapshot.get("available") else "kein Kontingent · " + REASON_LABELS.get(
            snapshot.get("reason"), snapshot.get("reason") or "Limit")
        return f"Codex-Abo{plan}: " + (" · ".join(parts) + " · " if parts else "") + state
    login = snapshot.get("login") or {}
    plan = f" ({snapshot.get('plan')})" if snapshot.get("plan") else ""
    version = f" · Claude Code {login.get('cli_version')}" if login.get("cli_version") else ""
    if not snapshot.get("usable"):
        return f"Claude-Abo{plan}: nicht nutzbar · {REASON_LABELS.get(snapshot.get('reason'), snapshot.get('reason') or 'unbekannt')}{version}"
    if snapshot.get("available"):
        return f"Claude-Abo{plan}: angemeldet über claude.ai{version} · bereit"
    until = local_text(snapshot.get("blocked_until"))
    return (f"Claude-Abo{plan}: angemeldet über claude.ai{version} · Sperre bis {until} "
            f"({REASON_LABELS.get(snapshot.get('reason'), snapshot.get('reason') or 'Limit')})")


def choose_subscription(settings, candidates: dict, *, prefer="codex_cli", exclude=(), refresh=False,
                        clock=time.time) -> dict:
    """The preferred subscription while it is available, else the other one, else pause naming the earliest reset."""
    order = [prefer] + [provider for provider in candidates if provider != prefer]
    snapshots = {}
    for provider in order:
        if provider not in candidates:
            continue
        snapshot = codex_quota(settings, refresh=refresh, clock=clock) if provider == "codex_cli" else \
            claude_quota(refresh=refresh, clock=clock)
        snapshots[provider] = snapshot
        if provider in exclude or not snapshot.get("available"):
            continue
        reasons = [f"{name.split('_')[0]}_{'exhausted_until ' + str(snapshots[name].get('resets_at')) if snapshots[name].get('usable') else 'unavailable (' + str(snapshots[name].get('reason')) + ')'}"
                   for name in order if name in snapshots and name != provider]
        reasons.append(f"{provider.split('_')[0]}_available")
        return {"provider": provider, **candidates[provider], "mode": "auto", "reason": "; ".join(reasons),
                "snapshots": snapshots, "decided_at": iso_at(clock())}
    if not any(snapshot.get("usable") for snapshot in snapshots.values()):
        raise AppError("Kein Abo-Anbieter ist nutzbar: Codex CLI und Claude Code sind nicht installiert oder nicht "
                       "per Abo angemeldet (codex login / claude auth login). " +
                       " ".join(describe_snapshot(name, snapshots[name]) + "." for name in order if name in snapshots),
                       code="subscription_required", status="blocked", details={"snapshots": snapshots})
    resets = sorted((parse_iso(snapshot.get("resets_at")), name) for name, snapshot in snapshots.items()
                    if snapshot.get("usable") and parse_iso(snapshot.get("resets_at")))
    earliest = resets[0] if resets else None
    message = "Kein Abo hat gerade Kontingent. " + " ".join(
        describe_snapshot(name, snapshots[name]) + "." for name in order if name in snapshots)
    if earliest:
        message += (f" Frühester Reset: {format_local(earliest[0])} "
                    f"({'Codex' if earliest[1] == 'codex_cli' else 'Claude'}).")
    message += " Später mit 'pla resume' fortsetzen."
    raise AppError(message, code="subscriptions_exhausted", status="waiting_for_quota",
                   details={"earliest_reset": earliest[0].isoformat() if earliest else None,
                            "earliest_provider": earliest[1] if earliest else None, "snapshots": snapshots})


def quota_overview(settings, *, refresh=True, clock=time.time) -> dict:
    """Both subscriptions at a glance, for ``pla quota``, the doctor and the Studio check."""
    codex = codex_quota(settings, refresh=refresh, clock=clock)
    claude = claude_quota(refresh=refresh, clock=clock)
    return {"checked_at": iso_at(clock()), "codex_cli": codex, "claude_code": claude,
            "lines": [describe_snapshot("codex_cli", codex), describe_snapshot("claude_code", claude)],
            "any_usable": bool(codex.get("usable") or claude.get("usable")),
            "any_available": bool(codex.get("available") or claude.get("available"))}
