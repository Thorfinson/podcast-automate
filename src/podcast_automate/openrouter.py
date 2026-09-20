"""Structured text calls to OpenRouter; credentials stay in memory and HTTP headers."""
from __future__ import annotations

import json
import math
import os
import re
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import SecretStr, ValidationError

from .call_activity import CallActivity, contract_rejection, parsed_json
from .errors import AppError
from .models import now
from .storage import write_json
from .text_settings import validate_reasoning

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
ADAPTER_VERSION = "openrouter.v1"
DEFAULT_MAX_OUTPUT_TOKENS = 32768
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def stream_response(response, activity, secret, deadline):
    """Consume SSE incrementally; assemble only the final text needed for validation."""
    first = response.readline(MAX_RESPONSE_BYTES + 1)
    if first.lstrip().startswith((b"{", b"[")):
        raw = first + response.read(MAX_RESPONSE_BYTES + 1 - min(len(first), MAX_RESPONSE_BYTES))
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("Response too large")
        return json.loads(raw)
    envelope, content, finish, data = {}, [], None, []
    size = 0
    tails = {}

    def guard(text, lane):
        combined = tails.get(lane, "") + text
        if secret in combined:
            raise AppError("OpenRouter-Antwort enthält Zugangsdaten und wird nicht gespeichert.",
                           code="credential_in_response", status="blocked")
        tails[lane] = combined[-len(secret):]

    def consume(payload):
        nonlocal finish
        if payload == "[DONE]":
            return True
        chunk = json.loads(payload)
        if not isinstance(chunk, dict):
            raise ValueError("Expected stream object")
        if secret in json.dumps(chunk, ensure_ascii=False):
            raise AppError("OpenRouter-Antwort enthält Zugangsdaten und wird nicht gespeichert.",
                           code="credential_in_response", status="blocked")
        if chunk.get("error"):
            error = chunk["error"]
            code = error.get("code", 502) if isinstance(error, dict) else 502
            activity.diagnostic("provider_error", str(code))
            raise api_failure(int(code) if str(code).isdigit() else 502)
        for key in ("id", "model", "provider", "usage"):
            if key in chunk:
                envelope[key] = chunk[key]
        for choice in chunk.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if isinstance(text, str) and text:
                guard(text, "content")
                content.append(text)
                activity.trace.append("text", text, "content")
                activity.content_received()
            details = delta.get("reasoning_details") or []
            visible = [part for part in details if isinstance(part, dict)
                       and part.get("type") in {"reasoning.summary", "reasoning.text"}]
            if visible:
                for part in visible:
                    text = part.get("summary") if part["type"] == "reasoning.summary" else part.get("text")
                    if isinstance(text, str):
                        lane = "reasoning_" + str(part.get("index", 0))
                        guard(text, lane)
                        activity.trace.append("reasoning", text, lane)
                        activity.content_received()
            else:
                text = delta.get("reasoning") or delta.get("reasoning_content")
                if isinstance(text, str) and text:
                    guard(text, "reasoning")
                    activity.trace.append("reasoning", text, "reasoning")
                    activity.content_received()
            if choice.get("finish_reason") is not None:
                finish = choice["finish_reason"]
        activity.diagnostics["last_stream_event_at"] = now()
        activity._save_diagnostics()
        return False

    line = first
    while line:
        if time.monotonic() > deadline:
            raise TimeoutError()
        size += len(line)
        if size > MAX_RESPONSE_BYTES:
            raise ValueError("Response too large")
        text = line.decode("utf-8").rstrip("\r\n")
        if not text:
            if data and consume("\n".join(data)):
                envelope["choices"] = [{"finish_reason": finish, "message": {"content": "".join(content)}}]
                return envelope
            data = []
        elif text.startswith("data:"):
            data.append(text[5:].lstrip(" "))
        # SSE comments are keepalives, not evidence of model progress.
        line = response.readline(MAX_RESPONSE_BYTES + 1)
    if data and consume("\n".join(data)):
        envelope["choices"] = [{"finish_reason": finish, "message": {"content": "".join(content)}}]
        return envelope
    raise ValueError("Stream ended before DONE")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A completion request must never forward its Authorization header elsewhere.
        return None


def strict_schema(output_type):
    schema = output_type.model_json_schema()

    def visit(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


def api_failure(code):
    # Deliberately do not expose raw provider messages, which can echo request data.
    if code == 401:
        return AppError("OpenRouter-Key fehlt, ist ungültig oder abgelaufen. Bei resume erneut übergeben.",
                        code="openrouter_authentication", status="blocked")
    if code == 402:
        return AppError("OpenRouter-Guthaben oder Key-Limit erschöpft. Guthaben prüfen und mit pla resume fortsetzen.",
                        code="openrouter_credits", status="waiting_for_quota")
    if code == 429:
        return AppError("OpenRouter-Anfragelimit erreicht. Später mit pla resume fortsetzen.",
                        code="openrouter_rate_limit", status="waiting_for_quota")
    if code == 403:
        return AppError("OpenRouter hat die Anfrage abgewiesen. Key-Berechtigungen und Anbieterregeln prüfen.",
                        code="openrouter_forbidden", status="blocked")
    if code in {400, 404, 413, 422}:
        return AppError("OpenRouter-Anfrage nicht unterstützt. Modell-ID, Reasoning-Stufe, JSON-Schema-Unterstützung und "
                        "Kontext-/Ausgabelimit prüfen; geänderte Modelleinstellungen benötigen einen neuen script-Lauf.",
                        code="openrouter_request", status="blocked")
    return AppError("OpenRouter vorübergehend nicht erreichbar oder ohne passenden Anbieter. Später pla resume verwenden.",
                    code="openrouter_unavailable", status="blocked")


class OpenRouterAdapter:
    def __init__(self, settings, *, model: str, api_key: str | None = None,
                 max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS, reasoning_effort=None):
        if not model or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model) or "/" not in model or "://" in model:
            raise AppError("OpenRouter benötigt --model mit einer Modell-ID wie anbieter/modell.",
                           code="invalid_backend", status="blocked")
        if ":online" in model:
            raise AppError("Für Skripte eine Textmodell-ID ohne :online verwenden; Recherche bleibt eine eigene Stufe.",
                           code="openrouter_search_unsupported", status="blocked")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or max_output_tokens < 1:
            raise AppError("--max-output-tokens muss eine positive ganze Zahl sein.", code="invalid_backend", status="blocked")
        key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
        key = key.strip()
        if key and (any(not 33 <= ord(c) <= 126 for c in key) or key in model):
            raise AppError("OpenRouter-Key oder Modellangabe ist ungültig.", code="invalid_backend", status="blocked")
        self._key = SecretStr(key)
        self.settings = settings
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = validate_reasoning(reasoning_effort, provider="openrouter", model=model)

    def require_key(self):
        if not self._key.get_secret_value():
            raise AppError("OpenRouter benötigt einen Key: --api-key für verdeckte Eingabe oder OPENROUTER_API_KEY setzen.",
                           code="openrouter_key_required", status="blocked")

    def structured(self, prompt: str, output_type, directory: Path, *,
                   prompt_version: str, search: bool = False):
        self.require_key()
        activity = CallActivity(directory, output_type.__name__, self.model,
                                secrets=(self._key.get_secret_value(),))
        activity.diagnostic("request", prompt_chars=len(prompt), prompt_bytes=len(prompt.encode("utf-8")),
                            timeout_seconds=self.settings.text_timeout_seconds,
                            reasoning_effort=self.reasoning_effort)
        try:
            result = self._structured(prompt, output_type, directory, activity,
                                      prompt_version=prompt_version, search=search)
        except AppError as exc:
            activity.diagnostic("failure", exc.code, code=exc.code)
            activity.finish(exc.code)
            receipt = {"code": exc.code, "message": str(exc), "model": self.model, "prompt_version": prompt_version}
            if exc.code == "rejected_output":
                # The parsed answer is model output, kept as an accepted one is kept in response.json.
                receipt["validation_errors"] = exc.details["defects"]
                write_json(directory / "rejected_output.json", exc.details["payload"])
            write_json(directory / "failure.json", receipt)
            raise
        except BaseException:
            activity.finish("interrupted")
            raise
        activity.finish("completed")
        return result

    def _structured(self, prompt, output_type, directory, activity, *, prompt_version, search):
        self.require_key()
        if search:
            raise AppError("Dieser OpenRouter-Adapter erzeugt Skripte und Reviews; Live-Recherche erfolgt separat.",
                           code="openrouter_search_unsupported", status="blocked")
        secret = self._key.get_secret_value()
        if secret in prompt:
            raise AppError("Ein API-Key darf nicht Teil des Modellprompts sein.", code="credential_in_prompt", status="blocked")
        schema = strict_schema(output_type)
        payload = {
            "model": self.model, "stream": True,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {
                "name": output_type.__name__, "strict": True, "schema": schema}},
            "provider": {"require_parameters": True, "sort": "throughput"},
            "max_tokens": self.max_output_tokens,
        }
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort, "exclude": False}
        request = Request(ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json",
                     "X-OpenRouter-Title": "Podcast Automate"}, method="POST")
        start = time.monotonic()
        write_json(directory / "output_schema.json", schema)
        try:
            with build_opener(NoRedirect()).open(request, timeout=self.settings.text_timeout_seconds) as response:
                envelope = stream_response(response, activity, secret, start + self.settings.text_timeout_seconds)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise api_failure(code) from None
        except (TimeoutError, URLError, OSError, HTTPException):
            raise AppError("OpenRouter-Verbindung unterbrochen oder Zeitlimit erreicht. Mit pla resume fortsetzen.",
                           code="openrouter_connection", status="blocked") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AppError("OpenRouter hat keinen vollständigen gültigen Antwortstream geliefert; Entwurf nicht übernommen.",
                           code="invalid_model_output", status="blocked") from None
        try:
            if not isinstance(envelope, dict):
                raise ValueError("Expected an object")
            if envelope.get("error"):
                error = envelope["error"]
                raise api_failure(int(error.get("code", 502)) if isinstance(error, dict) else 502)
            # Check decoded data as well, so JSON unicode escapes cannot evade this guard.
            if secret in json.dumps(envelope, ensure_ascii=False):
                raise AppError("OpenRouter-Antwort enthält Zugangsdaten und wird nicht gespeichert.",
                               code="credential_in_response", status="blocked")
            choice = envelope["choices"][0]
            if not isinstance(choice, dict):
                raise ValueError("Expected a completion object")
            if choice.get("finish_reason") == "length":
                raise AppError("OpenRouter hat die Antwort am Tokenlimit abgeschnitten. Einen neuen script-Lauf mit "
                               "höherem --max-output-tokens oder geeignetem Modell starten.",
                               code="openrouter_truncated", status="blocked")
            if choice.get("finish_reason") != "stop":
                raise AppError("OpenRouter hat keine vollständig abgeschlossene Textantwort geliefert.",
                               code="invalid_model_output", status="blocked")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Expected text content")
        except (ValueError, KeyError, IndexError, TypeError):
            raise AppError("OpenRouter hat keine gültige strukturierte Antwort geliefert; Entwurf nicht übernommen.",
                           code="invalid_model_output", status="blocked") from None
        answer = parsed_json(content)
        if answer is None:
            raise AppError("OpenRouter hat keine gültige strukturierte Antwort geliefert; Entwurf nicht übernommen.",
                           code="invalid_model_output", status="blocked")
        try:
            output = output_type.model_validate(answer)
        except (ValueError, TypeError, ValidationError) as exc:
            # The envelope was checked for the key above, so the answer can be kept as a receipt.
            raise contract_rejection(exc, answer, provider="OpenRouter") from None
        if secret in output.model_dump_json():
            raise AppError("Modellantwort enthält Zugangsdaten und wird nicht gespeichert.",
                           code="credential_in_response", status="blocked")

        raw_usage = envelope.get("usage")
        usage = {key: value for key, value in raw_usage.items()
                 if key in {"prompt_tokens", "completion_tokens", "total_tokens", "cost"}
                 and isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and value >= 0} if isinstance(raw_usage, dict) else {}
        metadata = {
            "provider": "openrouter", "auth_mode": "api_key", "adapter_version": ADAPTER_VERSION,
            "requested_model": self.model, "actual_model": envelope.get("model") if isinstance(envelope.get("model"), str) else None,
            "requested_reasoning_effort": self.reasoning_effort,
            "generation_id": envelope.get("id") if isinstance(envelope.get("id"), str) else None,
            "upstream_provider": envelope.get("provider") if isinstance(envelope.get("provider"), str) else None,
            "prompt_version": prompt_version, "usage": usage,
            "separately_billed_cost": usage.get("cost"), "cost_currency": "USD",
            "elapsed_seconds": round(time.monotonic() - start, 3),
            "max_output_tokens": self.max_output_tokens, "provider_sort": "throughput",
            "research_performed": False, "web_search_events": 0, "web_search_requests": 0,
        }
        write_json(directory / "output_schema.json", schema)
        write_json(directory / "metadata.json", metadata)
        write_json(directory / "response.json", output.model_dump(mode="json"))
        return output, metadata
