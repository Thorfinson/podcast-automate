from __future__ import annotations

import json
import os
import platform
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from .prompts import instructions
from .errors import AppError
from .call_activity import CallActivity, contract_rejection, mentions, parsed_json, write_rejected_output
from .codex_stream import run_app_server
from .model_trace import redact
from .models import RuntimeSettings, TextProbeOutput
from .process import STALL_TIMEOUT_SECONDS, run_process
from .storage import write_json
from .text_settings import validate_model, validate_reasoning


def windows_codex_installation() -> Path | None:
    """Find existing user installations when Explorer did not inherit the IDE's PATH."""
    home = Path.home()
    native = home / ".local/bin/codex.exe"
    if native.is_file():
        return native
    architecture = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64"
    candidates = []
    for profile in (".vscode", ".vscode-insiders"):
        for extension in (home / profile / "extensions").glob("openai.chatgpt-*"):
            match = re.fullmatch(r"openai\.chatgpt-(\d+(?:\.\d+)*)(?:-.+)?", extension.name)
            binary = extension / "bin" / f"windows-{architecture}" / "codex.exe"
            if match and binary.is_file():
                candidates.append((tuple(int(part) for part in match[1].split(".")), str(binary)))
    return Path(max(candidates)[1]) if candidates else None


def executable_command(executable: str) -> list[str]:
    resolved = shutil.which(executable)
    if not resolved and executable.lower() in {"codex", "codex.exe"} and platform.system() == "Windows":
        installed = windows_codex_installation()
        resolved = str(installed) if installed else None
    if not resolved:
        raise AppError("Codex wurde nicht gefunden. Codex CLI installieren oder in project.yaml unter "
                       "runtime.codex_executable den vollständigen Programmpfad eintragen.",
                       code="codex_missing", status="blocked")
    path = Path(resolved)
    if path.suffix.lower() in {".cmd", ".bat", ".ps1"}:
        # Avoid cmd.exe interpolation of Windows npm shims and project paths.
        script = path.parent / "node_modules/@openai/codex/bin/codex.js"
        node = shutil.which("node")
        if node and script.is_file():
            return [node, str(script)]
        raise AppError("Codex-Shim nicht auflösbar. Native Codex-Installation oder npm-Installation verwenden.",
                       code="unsupported_codex_launcher", status="blocked")
    return [str(path)]


def subscription_environment() -> dict[str, str]:
    """A child environment that can only use the CLI's subscription login, never an API key."""
    environment = os.environ.copy()
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENROUTER_API_KEY",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        environment.pop(key, None)
    return environment


# Words of a failed turn's text, matched as whole words (call_activity.mentions) where the app-server names no
# category of its own.
QUOTA_MARKERS = ("usage limit*", "usage_limit*", "usagelimit*", "rate limit*", "rate_limit*", "ratelimit*",
                 "quota", "quotas", "insufficient_quota", "too many requests", "429")
AUTH_MARKERS = ("unauthorized", "not logged in", "authentication", "401")
# The app-server's ``codexErrorInfo`` of a failed turn, lower-cased: a category name, or an object keyed by it.
QUOTA_ERROR_INFO = frozenset({"usagelimitexceeded"})
AUTH_ERROR_INFO = frozenset({"unauthorized"})
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def error_info(error) -> tuple[str | None, int | None]:
    """The category and HTTP status a failed turn carries, if any."""
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    if isinstance(info, str):
        return info.lower(), None
    if isinstance(info, dict) and len(info) == 1:
        name, value = next(iter(info.items()))
        status = value.get("httpStatusCode") if isinstance(value, dict) else None
        return str(name).lower(), status if isinstance(status, int) and not isinstance(status, bool) else None
    return None, None


def codex_reset(text, *, now=None) -> datetime | None:
    """The time a Codex usage-limit message names: "try again in 2 days 3 hours", or "try again at 3:04 PM" and
    "at Oct 4th, 2026 3:04 PM" in local time. None when it names none."""
    now = now or datetime.now(timezone.utc)
    lower = str(text or "").lower()
    relative = re.search(r"try again in ((?:\d+\s*(?:days?|hours?|hrs?|minutes?|mins?|seconds?|secs?)\b[\s,]*(?:and\s+)?)+)",
                         lower)
    if relative:
        seconds = sum(int(count) * UNITS[unit[0]] for count, unit in
                      re.findall(r"(\d+)\s*(days?|hours?|hrs?|minutes?|mins?|seconds?|secs?)", relative.group(1)))
        return now + timedelta(seconds=seconds) if seconds else None
    absolute = re.search(r"try again at (?:([a-z]{3})[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4}),? )?"
                         r"(\d{1,2}):(\d{2})\s*([ap]m)?", lower)
    if not absolute:
        return None
    month, day, year, hour, minute, meridian = absolute.groups()
    hour, minute = int(hour), int(minute)
    if meridian == "pm" and hour < 12:
        hour += 12
    if meridian == "am" and hour == 12:
        hour = 0
    local = now.astimezone()
    try:
        if month:
            if month not in MONTHS:
                return None
            moment = local.replace(year=int(year), month=MONTHS.index(month) + 1, day=int(day), hour=hour,
                                   minute=minute, second=0, microsecond=0)
        else:
            moment = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if moment <= local:
                moment += timedelta(days=1)
    except ValueError:
        return None
    return moment.astimezone(timezone.utc)


def classify_failure(message: str, *, error=None, now=None) -> AppError:
    """Map a failed Codex turn to an actionable error. ``error`` is the turn's own failure object: its category
    (``codexErrorInfo``) decides before its text, which is read word by word (2026-10-02: substrings and earlier
    retry notices of the same turn turned unrelated failures into a quota pause)."""
    lower = str(message or "").lower()
    if "invalid_json_schema" in lower or "invalid schema for response_format" in lower:
        return AppError("Das Studio hat ein nicht unterstütztes Antwortformat an Codex gesendet. "
                        "Das ist ein Fehler der Studio-Anbindung; eine neue Anmeldung behebt ihn nicht.",
                        code="invalid_output_schema")
    category, http_status = error_info(error)
    if category is not None and category != "other":
        quota, login = category in QUOTA_ERROR_INFO or http_status == 429, category in AUTH_ERROR_INFO or http_status == 401
    else:
        quota, login = mentions(lower, *QUOTA_MARKERS), mentions(lower, *AUTH_MARKERS)
    if quota:
        until = codex_reset(message, now=now)
        when = f" Voraussichtlich wieder verfügbar ab {until.astimezone().strftime('%d.%m.%Y %H:%M')}." if until else ""
        return AppError("Abo-Kontingent oder Anfragelimit erreicht." + when + " Später mit 'pla resume' fortsetzen.",
                        code="quota_exhausted", status="waiting_for_quota",
                        details={"provider": "codex_cli", **({"blocked_until": until.isoformat()} if until else {})})
    if login:
        return AppError("Codex-Anmeldung muss erneuert werden: codex login",
                        code="authentication_required", status="blocked")
    # Codex's own reason, since no class above names one (2026-10-04: after the account fell to the free plan, every
    # Transformer call failed two seconds after the start, and the stop said only "Verbindung prüfen").
    reason = provider_message(error, message)
    return AppError("Codex-Aufruf fehlgeschlagen" + (f": {reason}" if reason else "") +
                    ". Verbindung, Abo und CLI-Konfiguration prüfen.", code="codex_failed",
                    details={"provider_message": reason} if reason else {})


# The longest provider message a failure keeps (provider_message).
PROVIDER_MESSAGE_CHARS = 300


def provider_message(error=None, text="") -> str:
    """Codex's own short reason for a failed call the adapter cannot classify: the turn's error message, else the last
    line of the CLI's error output, without credentials (model_trace.redact) and at most PROVIDER_MESSAGE_CHARS long.
    Raw provider output stays unstored otherwise (SECURITY, D-129)."""
    if isinstance(error, dict) and error.get("message"):
        raw = str(error["message"])
    else:
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        raw = lines[-1] if lines else ""
    return " ".join(redact(raw).split())[:PROVIDER_MESSAGE_CHARS]


class CodexAdapter:
    def __init__(self, settings: RuntimeSettings, *, reasoning_effort=None, cancel_check=None, transport="app_server"):
        self.settings = settings
        validate_model(settings.codex_model)
        self.reasoning_effort = validate_reasoning(reasoning_effort)
        self.cancel_check = cancel_check
        if transport not in {"app_server", "exec"}:
            raise ValueError("Unknown Codex transport")
        self.transport = transport

    def command(self) -> list[str]:
        return executable_command(self.settings.codex_executable)

    def check_login(self) -> str:
        result = run_process(self.command() + ["login", "status"], timeout=20,
                             env=subscription_environment())
        if result.returncode:
            raise AppError("Bitte zuerst mit dem ChatGPT-Abo anmelden: codex login",
                           code="authentication_required", status="blocked")
        mode = (result.stdout + result.stderr).lower()
        if "chatgpt" not in mode or "api key" in mode:
            raise AppError("Keine bestätigte ChatGPT-Abo-Anmeldung. Der Aufruf wurde nicht gestartet.",
                           code="subscription_required", status="blocked")
        return "chatgpt"

    def structured(self, prompt: str, output_type, directory: Path, *,
                   prompt_version: str, search: bool = False) -> tuple[object, dict]:
        self.check_login()
        directory.mkdir(parents=True, exist_ok=True)
        schema_file = directory / "output_schema.json"
        response_file = directory / "response.pending.json"
        response_file.unlink(missing_ok=True)
        schema = output_type.model_json_schema()

        def strict(node):
            if isinstance(node, dict):
                node.pop("default", None)
                if node.get("type") == "object" and "properties" in node:
                    node["required"] = list(node["properties"])
                    node["additionalProperties"] = False
                for value in node.values():
                    strict(value)
            elif isinstance(node, list):
                for value in node:
                    strict(value)

        strict(schema)
        write_json(schema_file, schema)
        args = self.command() + [
            "-c", 'model_provider="openai"', "--ask-for-approval", "never",
            "-c", 'web_search="live"' if search else 'web_search="disabled"',
            "-c", "features.shell_tool=false",
            "exec", "--sandbox", "read-only", "--skip-git-repo-check",
            "--ignore-user-config", "--ephemeral", "--json", "--output-schema", str(schema_file),
            "--output-last-message", str(response_file),
        ]
        if self.settings.codex_model:
            args.extend(["--model", self.settings.codex_model])
        if self.reasoning_effort is not None:
            args.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
        args.append("-")
        activity = CallActivity(directory, output_type.__name__, self.settings.codex_model)
        # The exec transport reports items only when they complete, so silence is not a stall there.
        stall_timeout = STALL_TIMEOUT_SECONDS if self.transport == "app_server" else None
        activity.diagnostic("request", prompt_chars=len(prompt), prompt_bytes=len(prompt.encode("utf-8")),
                            timeout_seconds=self.settings.text_timeout_seconds, stall_timeout_seconds=stall_timeout,
                            reasoning_effort=self.reasoning_effort, transport=self.transport)
        try:
            if self.transport == "app_server":
                result = run_app_server(self.command(), prompt=prompt, schema=schema, response_file=response_file,
                    cwd=directory.resolve(), timeout=self.settings.text_timeout_seconds,
                    env=subscription_environment(), model=self.settings.codex_model, effort=self.reasoning_effort,
                    search=search, activity=activity, cancel_check=self.cancel_check, stall_timeout=stall_timeout)
            else:
                result = run_process(args, input_text=prompt, cwd=directory,
                                     timeout=self.settings.text_timeout_seconds, env=subscription_environment(),
                                     on_stdout_line=activity.observe, on_stderr_line=activity.observe_stderr,
                                     cancel_check=self.cancel_check)
        except AppError as exc:
            activity.finish(exc.code)
            response_file.unlink(missing_ok=True)
            if exc.code not in {"timeout", "stall"}:
                raise
            seconds = self.settings.text_timeout_seconds if exc.code == "timeout" else stall_timeout
            duration = f"{seconds // 60} Minuten" if seconds % 60 == 0 else f"{seconds} Sekunden"
            if exc.code == "timeout":
                message = (f"Der einzelne Codex-Aufruf wurde nach {duration} durch das lokale Zeitlimit beendet. "
                           "Fertige Schritte sind gespeichert. Fortsetzen wiederholt den unterbrochenen Aufruf; "
                           "bereinigte technische Hinweise stehen in diagnostics.json und im Studio.")
            else:
                message = (f"Der Codex-Aufruf hat {duration} lang keine Ausgabe geliefert und wurde als hängend beendet. "
                           "Der Aufruf wird nicht angerechnet und einmal automatisch wiederholt; danach mit Fortsetzen.")
            failure = AppError(message, code=exc.code)
            write_json(directory / "failure.json", {"code": failure.code, "message": str(failure),
                       "timeout_seconds": self.settings.text_timeout_seconds, "stall_timeout_seconds": stall_timeout,
                       "model": self.settings.codex_model,
                       "reasoning_effort": self.reasoning_effort, "prompt_version": prompt_version})
            raise failure from exc
        except BaseException:
            activity.finish("interrupted")
            raise
        events = []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        terminal = [e for e in events if e.get("type") in {"turn.completed", "turn.failed"}]
        # Only the turn's own failure names its cause; earlier ``error`` events are retry notices it may have outlived.
        failed = next((e for e in reversed(events) if e.get("type") == "turn.failed"), None)
        search_items = [e["item"] for e in events
                        if e.get("type") == "item.completed"
                        and isinstance(e.get("item"), dict)
                        and e["item"].get("type") in {"web_search", "web_search_call"}]
        search_requests = [item for item in search_items if
                           item.get("action", {}).get("type") == "search" or
                           (not item.get("action") and item.get("query"))]
        # Tool events, not an assertion in generated JSON, establish that browsing happened.
        write_json(directory / "search_events.json", search_items)
        if result.returncode or not terminal or terminal[-1]["type"] != "turn.completed":
            activity.finish("failed")
            response_file.unlink(missing_ok=True)
            failure = (classify_failure(json.dumps(failed.get("error")), error=failed.get("error")) if failed
                       else classify_failure(result.stderr))
            # Keep a useful failure receipt without persisting raw provider output or prompts.
            write_json(directory / "failure.json", {"code": failure.code, "message": str(failure),
                       "exit_code": result.returncode, "model": self.settings.codex_model,
                       "reasoning_effort": self.reasoning_effort, "prompt_version": prompt_version,
                       **({"provider_message": failure.details["provider_message"]}
                          if failure.details.get("provider_message") else {})})
            raise failure
        try:
            text = response_file.read_text(encoding="utf-8")
        except OSError:
            text = None
        finally:
            response_file.unlink(missing_ok=True)
        receipt = {"exit_code": result.returncode, "model": self.settings.codex_model,
                   "reasoning_effort": self.reasoning_effort, "prompt_version": prompt_version}
        answer = parsed_json(text)
        if answer is None:
            activity.finish("invalid_model_output")
            failure = AppError("Codex hat keine gültige strukturierte Antwort geliefert.", code="invalid_model_output")
            write_rejected_output(directory, ValueError("no JSON answer"),
                                  {"code": failure.code, "message": str(failure), **receipt}, text=text)
            raise failure
        try:
            output = output_type.model_validate(answer)
        except (ValueError, TypeError, ValidationError) as exc:
            # A parsed answer the contract rejects is charged model work that a caller may re-ask.
            activity.finish("rejected_output")
            failure = contract_rejection(exc, answer, provider="Codex")
            write_rejected_output(directory, exc, {"code": failure.code, "message": str(failure), **receipt}, payload=answer)
            raise failure from exc
        if search and not search_requests:
            activity.finish("search_not_observed")
            raise AppError("Kein Websuch-Ereignis im Codex-Lauf nachgewiesen. Recherche nicht übernommen.",
                           code="search_not_observed", status="blocked")
        try:
            version = run_process(self.command() + ["--version"], timeout=20,
                                  env=subscription_environment())
            cli_version = version.stdout.strip() if version.returncode == 0 else None
        except AppError:
            cli_version = None
        metadata = {
            "provider": "codex_cli", "auth_mode": "chatgpt",
            "transport": self.transport,
            "requested_model": self.settings.codex_model,
            "requested_reasoning_effort": self.reasoning_effort,
            "timeout_seconds": self.settings.text_timeout_seconds,
            "cli_version": cli_version,
            "prompt_version": prompt_version, "usage": terminal[-1].get("usage"),
            "separately_billed_cost": None, "research_performed": bool(search_requests),
            "web_search_events": len(search_items),
            "web_search_requests": len(search_requests),
            "observed_search_queries": list(dict.fromkeys(q for item in search_requests
                for q in ([item["query"]] if isinstance(item.get("query"), str) else
                          [item["action"]["query"]] if isinstance(item.get("action", {}).get("query"), str) else
                          item.get("action", {}).get("queries", [])) if isinstance(q, str))),
        }
        write_json(directory / "metadata.json", metadata)
        write_json(directory / "response.json", output.model_dump(mode="json"))
        activity.finish("completed")
        return output, metadata

    def probe(self, topic: str, directory: Path) -> tuple[TextProbeOutput, dict]:
        prompt = (
            instructions("text_probe") + "\n" + json.dumps({"topic": topic}, ensure_ascii=False)
        )
        output, metadata = self.structured(prompt, TextProbeOutput, directory, prompt_version="text_probe.v1")
        if output.topic != topic:
            raise AppError("Codex hat das Thema verändert.", code="invalid_model_output")
        return output, metadata
