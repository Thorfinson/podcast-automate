"""Small, allowlisted public activity receipts; never persist raw model streams."""
import json
import re
import threading
import time
from urllib.parse import urlsplit

from pydantic import ValidationError

from .errors import AppError
from .model_trace import ModelTrace
from .models import now
from .storage import write_json

# Defects named in a contract rejection; the receipt keeps them all.
MAX_NAMED_DEFECTS = 8


def clean_status(value, limit=600):
    text = str(value or "")
    text = re.sub(r"\b(?:sk-|sess-)[A-Za-z0-9_-]+", "[Zugangsdaten entfernt]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [entfernt]", text)
    text = re.sub(r"(?i)((?:api[_ -]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[entfernt]", text)
    return " ".join(text.split())[:limit]


class CallActivity:
    def __init__(self, directory, schema, model, *, secrets=()):
        self.path = directory / "activity.json"
        self.diagnostics_path = directory / "diagnostics.json"
        self.trace = ModelTrace(directory, model, secrets=secrets, enabled=schema != "ProgressDigest")
        self._lock = threading.RLock()
        self._snapshots = {}
        self._claude_tools = {}
        self._claude_messages = 0
        self._last_diagnostic_write = 0
        self.diagnostics = {"model": model, "schema": schema, "status": "running", "started_at": now(),
                            "stdout_lines": 0, "stderr_lines": 0, "events": []}
        self.data = {"schema": schema, "model": model, "status": "running", "started_at": now(), "events": []}
        self.record("Aufruf gestartet")

    def record(self, message):
        self.trace.record("status", message)
        self.data["updated_at"] = now()
        self.data["events"] = (self.data["events"] + [{"at": now(), "message": clean_status(message)}])[-40:]
        try:
            write_json(self.path, self.data)
        except OSError:
            pass

    def diagnostic(self, kind, message="", **fields):
        """Persist useful categories, never arbitrary stderr, prompts or tool payloads."""
        with self._lock:
            category = None
            text = str(message).lower()
            for code, terms in (
                ("quota", ("usage_limit", "quota", "insufficient_quota")),
                ("rate_limit", ("rate limit", "rate_limit", "429")),
                ("authentication", ("unauthorized", "authentication", "401")),
                ("output_schema", ("invalid_json_schema", "invalid schema")),
                ("context_limit", ("context_length", "context window")),
                ("connection", ("connection", "stream disconnected", "tls", "dns", "socket", "transport")),
                ("retry", ("retry", "reconnect")),
                ("timeout", ("timeout", "timed out")),
                ("server_error", ("server_error", "500", "502", "503", "504")),
            ):
                if any(term in text for term in terms):
                    category = code
                    break
            event = {"at": now(), "kind": kind, **fields}
            if category:
                event["category"] = category
                categories = self.diagnostics.setdefault("categories", {})
                categories[category] = categories.get(category, 0) + 1
                self.trace.record("diagnostic", {
                    "quota": "Anbieter meldet ein ausgeschöpftes Nutzungslimit.",
                    "rate_limit": "Anbieter meldet eine Begrenzung der Anfragerate.",
                    "authentication": "Anbieter meldet ein Anmeldeproblem.",
                    "output_schema": "Anbieter meldet ein Problem mit dem Antwortformat.",
                    "context_limit": "Anbieter meldet ein überschrittenes Kontextlimit.",
                    "connection": "Anbindung meldet ein Verbindungs- oder Streamproblem.",
                    "retry": "Anbindung versucht die Verbindung erneut.",
                    "timeout": "Anbindung meldet ein Zeitlimit.",
                    "server_error": "Anbieter meldet einen Serverfehler.",
                }[category])
            self.diagnostics["events"] = (self.diagnostics["events"] + [event])[-40:]
            self._save_diagnostics(force=True)

    def _save_diagnostics(self, force=False):
        with self._lock:
            if not force and time.monotonic() - self._last_diagnostic_write < 1:
                return
            try:
                write_json(self.diagnostics_path, self.diagnostics)
                self._last_diagnostic_write = time.monotonic()
            except OSError:
                pass

    def observe_stderr(self, line):
        with self._lock:
            self.diagnostics["stderr_lines"] += 1
            self.diagnostics["last_stderr_at"] = now()
            self.diagnostic("stderr", line)

    def content_received(self):
        with self._lock:
            self.diagnostics["last_content_at"] = now()
            self._save_diagnostics()

    def stream_delta(self, kind, delta, stream):
        """Only public app-server text and reasoning-summary deltas reach here."""
        with self._lock:
            self.diagnostics["stream_deltas"] = self.diagnostics.get("stream_deltas", 0) + 1
            self.diagnostics["stdout_lines"] += 1
            self.diagnostics["last_stdout_at"] = now()
            self.diagnostics["last_delta_at"] = now()
            self.diagnostics["stream_chars"] = self.diagnostics.get("stream_chars", 0) + len(delta)
            self.diagnostics["stream_whitespace_chars"] = self.diagnostics.get("stream_whitespace_chars", 0) + sum(c.isspace() for c in delta)
            if delta.strip():
                self.diagnostics["last_nonblank_delta_at"] = now()
            self.diagnostics.setdefault("first_content_at", now())
            self.content_received()
            if kind == "text":
                self.trace.append_structured(delta, stream)
            else:
                self.trace.append("reasoning", delta, stream)

    def observe(self, line):
        with self._lock:
            self.diagnostics["stdout_lines"] += 1
            self.diagnostics["last_stdout_at"] = now()
            self._save_diagnostics()
        if len(line) > 2_000_000:
            self.diagnostic("oversized_event")
            return
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind in {"turn.started", "turn.completed", "turn.failed", "error"}:
            self.diagnostic(kind, json.dumps(event.get("error") or event.get("message") or ""))
            self.record({"turn.started": "Modell bearbeitet den Auftrag", "turn.completed": "Modellantwort empfangen; Validierung folgt",
                         "turn.failed": "Modell meldet einen fehlgeschlagenen Aufruf", "error": "Anbieter meldet ein Verbindungs- oder Aufrufproblem"}[kind])
            return
        item = event.get("item")
        if kind not in {"item.started", "item.updated", "item.completed"} or not isinstance(item, dict):
            return
        item_type = item.get("type")
        # Only the public CLI text fields, never encrypted reasoning or tool output.
        if item_type in {"reasoning", "agent_message"} and isinstance(item.get("text"), str):
            text = item["text"]
            key = str(item.get("id", item_type))
            previous = self._snapshots.get(key, 0)
            if len(text) < previous:
                previous = 0
            if text[previous:].strip():
                self.content_received()
            self.trace.append("reasoning" if item_type == "reasoning" else "text", text[previous:], key)
            self._snapshots[key] = len(text)
            self._snapshots = dict(list(self._snapshots.items())[-40:])
        if item_type in {"web_search", "web_search_call"}:
            action = item.get("action") or {}
            if not isinstance(action, dict):
                return
            query = action.get("query") or item.get("query")
            queries = action.get("queries")
            if not query and isinstance(queries, list):
                query = "; ".join(str(q) for q in queries[:3])
            verb = "Websuche gestartet" if kind == "item.started" else "Websuche abgeschlossen"
            if query:
                self.record(f"{verb}: {clean_status(query, 400)}")
            else:
                try:
                    host = urlsplit(str(action.get("url", ""))).hostname
                except ValueError:
                    host = None
                self.record(f"{verb}" + (f" · {host}" if host else ""))
        elif item_type == "agent_message" and item.get("phase") == "commentary":
            self.record("Modell hat eine Zwischenmeldung ausgegeben")
        elif item_type in {"command_execution", "mcp_tool_call", "file_change"}:
            self.record("Werkzeugaktion gestartet" if kind == "item.started" else "Werkzeugaktion abgeschlossen")

    def observe_claude(self, line):
        """Public Claude Code stream-json events: output deltas, tool activity, limit notices, the result."""
        with self._lock:
            self.diagnostics["stdout_lines"] += 1
            self.diagnostics["last_stdout_at"] = now()
            self._save_diagnostics()
        if len(line) > 2_000_000:
            self.diagnostic("oversized_event")
            return
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "system":
            subtype = str(event.get("subtype") or "")
            if subtype == "init":
                self.diagnostic("stream.connected")
                self.record("Modell bearbeitet den Auftrag")
            elif subtype:
                # Retry and status notices keep only their category, never the CLI's free text.
                self.diagnostic(subtype, subtype.replace("_", " "))
        elif kind == "stream_event":
            self._observe_claude_stream(event.get("event") or {})
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                self._claude_tools = dict(list(self._claude_tools.items())[-40:] + [(str(block.get("id")), name)])
                arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
                if name == "WebSearch":
                    query = arguments.get("query")
                    self.record("Websuche gestartet" + (f": {clean_status(query, 400)}" if isinstance(query, str) and query else ""))
                elif name == "WebFetch":
                    try:
                        host = urlsplit(str(arguments.get("url", ""))).hostname
                    except ValueError:
                        host = None
                    self.record("Quelle wird abgerufen" + (f" · {host}" if host else ""))
                elif name == "StructuredOutput":
                    self.record("Strukturierte Antwort empfangen; Validierung folgt")
                else:
                    self.record("Werkzeugaktion gestartet")
        elif kind == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    name = self._claude_tools.get(str(block.get("tool_use_id")), "")
                    if name == "WebSearch":
                        self.record("Websuche abgeschlossen")
                    elif name == "WebFetch":
                        self.record("Quelle abgerufen")
        elif kind == "rate_limit_event":
            info = event.get("rate_limit_info") if isinstance(event.get("rate_limit_info"), dict) else {}
            status = str(info.get("status") or "")
            self.diagnostic("quota_window", status=status, window=info.get("rateLimitType"),
                            resets_at=info.get("resetsAt"))
            if status and status.lower() != "allowed":
                self.diagnostic("quota", "usage limit " + status)
        elif kind == "result":
            subtype = str(event.get("subtype") or "")
            if subtype == "success" and not event.get("is_error"):
                self.record("Modellantwort empfangen; Validierung folgt")
            else:
                errors = event.get("errors") if isinstance(event.get("errors"), list) else []
                self.diagnostic("result", subtype + " " + " ".join(str(e) for e in errors[:3]), subtype=subtype)
                self.record("Modell meldet einen fehlgeschlagenen Aufruf")

    def _observe_claude_stream(self, event):
        """Only the CLI's public partial-message deltas; thinking arrives empty on current models."""
        kind = event.get("type")
        if kind == "message_start":
            self._claude_messages += 1
            return
        if kind != "content_block_delta":
            return
        delta = event.get("delta") or {}
        if not isinstance(delta, dict):
            return
        stream = f"claude:{self._claude_messages}:{event.get('index', 0)}"
        if delta.get("type") == "input_json_delta":
            text, lane = delta.get("partial_json"), "text"
        elif delta.get("type") == "text_delta":
            text, lane = delta.get("text"), "text"
        elif delta.get("type") == "thinking_delta":
            text, lane = delta.get("thinking"), "reasoning"
        else:
            return
        if isinstance(text, str) and text:
            self.stream_delta(lane, text, stream)

    def finish(self, status):
        self.data["status"] = status
        self.record("Antwort gespeichert" if status == "completed" else "Aufruf beendet: " + status)
        self.diagnostics.update(status=status, ended_at=now())
        self._save_diagnostics(force=True)
        self.trace.finish()


def parsed_json(text):
    """The JSON value in ``text``, or None when there is none to keep."""
    if not isinstance(text, str):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def validation_details(error):
    """Field paths and messages of a schema rejection; never the rejected values or provider text."""
    if isinstance(error, ValidationError):
        return [{"loc": [str(part) for part in item.get("loc", ())], "msg": str(item.get("msg", "")),
                 "type": str(item.get("type", ""))}
                for item in error.errors(include_url=False, include_context=False, include_input=False)]
    return [{"loc": [], "msg": str(error), "type": type(error).__name__}]


def contract_rejection(error, payload, *, provider):
    """The correctable error for a parsed answer that violates its output contract.

    Such an answer is real model work: the call stays charged and the adapter keeps its receipts.
    A caller with a rejection loop (``research_patches.cached_call``) repeats the task with the
    defects named; every other caller stops as ``blocked`` and a resume repeats the call. Only a
    missing or unparseable answer remains ``invalid_model_output``. A cross-field rule of a contract
    cannot be expressed in the JSON schema handed to the model, so its violation must never end a run.
    """
    defects = validation_details(error)
    named = "; ".join(f"{'.'.join(item['loc']) or 'answer'}: {re.sub(r'^Value error, ', '', item['msg'])}"
                      for item in defects[:MAX_NAMED_DEFECTS])
    if len(defects) > MAX_NAMED_DEFECTS:
        named += f"; and {len(defects) - MAX_NAMED_DEFECTS} more"
    message = (f"{provider} answered, but the answer violates its output contract: {clean_status(named, 1500).rstrip('.')}. "
               "Return the complete answer again with exactly these defects corrected.")
    return AppError(message, code="rejected_output", status="blocked", details={"payload": payload, "defects": defects})


def write_rejected_output(directory, error, receipt, *, payload=None, text=None):
    """The receipts of a schema-rejected model answer, so the defect can be diagnosed after the run.

    ``failure.json`` carries the receipt plus the offending fields; the answer itself goes to
    ``rejected_output.json`` (parsed) or ``rejected_output.txt`` (unparseable). It is the model's
    output, kept exactly as an accepted answer is kept in ``response.json``; provider text stays out.
    """
    write_json(directory / "failure.json", {**receipt, "validation_errors": validation_details(error)})
    if payload is not None:
        write_json(directory / "rejected_output.json", payload)
    elif text:
        (directory / "rejected_output.txt").write_text(text, encoding="utf-8")
