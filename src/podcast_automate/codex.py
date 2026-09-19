from __future__ import annotations

import json
import os
import platform
import re
import shutil
from pathlib import Path

from pydantic import ValidationError

from .prompts import instructions
from .errors import AppError
from .call_activity import CallActivity
from .codex_stream import run_app_server
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


def classify_failure(message: str) -> AppError:
    lower = message.lower()
    if "invalid_json_schema" in lower or "invalid schema for response_format" in lower:
        return AppError("Das Studio hat ein nicht unterstütztes Antwortformat an Codex gesendet. "
                        "Das ist ein Fehler der Studio-Anbindung; eine neue Anmeldung behebt ihn nicht.",
                        code="invalid_output_schema")
    if any(marker in lower for marker in (
            "usage limit", "usage_limit", "usagelimit", "rate limit", "rate_limit", "ratelimit", "quota", "429")):
        return AppError("Abo-Kontingent oder Anfragelimit erreicht. Später mit 'pla resume' fortsetzen.",
                        code="quota_exhausted", status="waiting_for_quota")
    if any(marker in lower for marker in ("unauthorized", "not logged in", "authentication", "401")):
        return AppError("Codex-Anmeldung muss erneuert werden: codex login",
                        code="authentication_required", status="blocked")
    return AppError("Codex-Aufruf fehlgeschlagen. Verbindung und CLI-Konfiguration prüfen.",
                    code="codex_failed")


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
        failures = [e for e in events if e.get("type") in {"turn.failed", "error"}]
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
            failure = classify_failure(json.dumps(failures) + result.stderr)
            # Keep a useful failure receipt without persisting raw provider output or prompts.
            write_json(directory / "failure.json", {"code": failure.code, "message": str(failure),
                       "exit_code": result.returncode, "model": self.settings.codex_model,
                       "reasoning_effort": self.reasoning_effort, "prompt_version": prompt_version})
            raise failure
        try:
            output = output_type.model_validate_json(response_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, ValidationError) as exc:
            activity.finish("invalid_model_output")
            raise AppError("Codex hat keine gültige strukturierte Antwort geliefert.",
                           code="invalid_model_output") from exc
        finally:
            response_file.unlink(missing_ok=True)
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
