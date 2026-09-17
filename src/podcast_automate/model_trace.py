"""A shared, bounded tail of provider-visible output, separate from saved results."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid

from .errors import AppError
from .models import now
from .storage import file_lock, write_json
from .structured_trace import StructuredTrace

MAX_LINES = 20
LINE_CHARS = 600

TEXT_FIELDS = {"reason": "Einordnung", "rationale": "Einordnung", "explanation": "Erklärung",
               "summary": "Zwischenstand", "statement": "Feststellung", "question": "Frage",
               "query": "Suchbegriff", "title": "Titel", "text": "Text"}
LIST_FIELDS = {"web_queries": "Websuche", "queries": "Suchbegriff", "issues": "Noch offen",
               "limits": "Einschränkung", "limitations": "Einschränkung"}
ACTIONS = {"read": "Weitere Quellenabschnitte lesen", "search_local": "Vorhandene Quellen durchsuchen",
           "search_web": "Zusätzliche Quellen im Web suchen", "answer": "Antwort aus den Belegen formulieren",
           "blocked": "Eine Beleglücke ist noch offen"}


def readable_output(text):
    """Project structured output onto human content, without inventing a summary."""
    text = text.strip()
    if not text:
        return ""
    try:
        value = json.loads(text.rstrip(","))
    except (ValueError, TypeError):
        value = None
    if isinstance(value, (dict, list)):
        result = []
        def visit(node):
            if isinstance(node, list):
                for child in node:
                    if isinstance(child, (dict, list)):
                        visit(child)
            elif isinstance(node, dict):
                for key, child in node.items():
                    if key == "action" and isinstance(child, str) and child in ACTIONS:
                        result.append(ACTIONS[child])
                    elif key in TEXT_FIELDS and isinstance(child, str) and child.strip():
                        result.append(f"{TEXT_FIELDS[key]}: {child}")
                    elif key in LIST_FIELDS and isinstance(child, list):
                        result.extend(f"{LIST_FIELDS[key]}: {item}" for item in child if isinstance(item, str) and item.strip())
                    elif isinstance(child, (dict, list)):
                        visit(child)
        visit(value)
        return "\n".join(result)
    # Pretty-printed JSON arrives one line at a time. Publish complete values only.
    if text.startswith('"') and ":" in text:
        return readable_output("{" + text.rstrip(",") + "}")
    if text.startswith(('"', "{", "}", "[", "]", "```")) or text.rstrip(",") in {"null", "true", "false"}:
        return ""
    if re.fullmatch(r"-?\d+(?:\.\d+)?[,]?", text):
        return ""
    return text


def redact(text, secrets=()):
    text = str(text or "")
    for secret in secrets:
        if not secret:
            continue
        text = text.replace(secret, "[Zugangsdaten entfernt]")
        # A streamed credential can straddle chunks. Do not publish its prefix.
        for size in range(min(len(secret) - 1, len(text)), 0, -1):
            if text.endswith(secret[:size]):
                text = text[:-size]
                break
    text = re.sub(r"\b(?:sk-|sess-)[A-Za-z0-9_-]*", "[Zugangsdaten entfernt]", text)
    text = re.sub(r"(?i)\bBearer\s+\S*", "Bearer [entfernt]", text)
    text = re.sub(r'''(?i)((?:api[_ -]?key|access[_ -]?token|token|password|secret)["']?\s*[=:]\s*["']?)[^\s,;"']*''', r"\1[entfernt]", text)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)


def trace_view(work):
    try:
        data = json.loads((work / "model_trace.json").read_text(encoding="utf-8"))
        rows = []
        for row in data.get("lines", [])[-MAX_LINES:]:
            content = redact(readable_output(row.get("text", ""))) if row.get("kind") == "text" else row.get("text", "")
            if content:
                rows.append({**{key: row.get(key) for key in ("at", "call", "model", "kind")}, "text": content})
        return {"updated_at": rows[-1].get("at") if rows else None, "lines": rows, "limit": MAX_LINES}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


class ModelTrace:
    def __init__(self, directory, model, *, secrets=(), enabled=True):
        work = directory.parent.parent if directory.parent.name == "calls" else directory
        self.path = work / "model_trace.json"
        self.call, self.model, self.secrets = directory.name, model, secrets
        self.enabled = enabled
        self._lock = threading.RLock()
        self._updates, self._streams = {}, {}
        self._structured = {}
        self._timer = None
        self._closed = False
        self._last_write = 0

    def _line(self, kind, text, identifier):
        clean = redact(text, self.secrets)
        if kind == "text":
            clean = redact(readable_output(clean), self.secrets)
        if not clean.strip():
            self._updates.pop(identifier, None)
            return
        if len(clean) > LINE_CHARS:
            clean = clean[:LINE_CHARS] + " …"
        self._updates[identifier] = {"id": identifier, "at": now(), "call": self.call,
                                    "model": self.model, "kind": kind, "text": clean}
        self._updates = dict(list(self._updates.items())[-MAX_LINES:])

    def record(self, kind, text):
        with self._lock:
            if not self.enabled or self._closed:
                return
            # Redact before splitting, including credentials spanning line breaks.
            for line in redact(text, self.secrets).splitlines():
                if line.strip():
                    self._line(kind, line, uuid.uuid4().hex)
            self._schedule()

    def append(self, kind, text, stream):
        """Update an unfinished line in place; never retain the full reasoning stream."""
        if not isinstance(text, str) or not text:
            return
        with self._lock:
            if not self.enabled or self._closed:
                return
            pending, identifier = self._streams.get(stream, ("", uuid.uuid4().hex))
            parts = (pending + text).split("\n")
            for line in parts[:-1]:
                self._line(kind, line, identifier)
                identifier = uuid.uuid4().hex
            pending = parts[-1]
            if pending:
                self._line(kind, pending, identifier)
            # Only an unfinished line is retained for cross-chunk redaction.
            cap = max(4096, max((len(s) * 2 for s in self.secrets), default=0))
            if len(pending) > cap:
                pending = redact(pending, self.secrets)[-cap:]
            self._streams[stream] = (pending, identifier)
            self._streams = dict(list(self._streams.items())[-8:])
            self._schedule()

    def append_structured(self, text, stream):
        """Show readable JSON values while the final object is still incomplete."""
        with self._lock:
            if not self.enabled or self._closed or not text:
                return
            if stream not in self._structured:
                if not text.strip():
                    return
                self._structured[stream] = (StructuredTrace(TEXT_FIELDS, LIST_FIELDS, ACTIONS)
                                            if text.lstrip().startswith(("{", "[")) else None)
                self._structured = dict(list(self._structured.items())[-8:])
            parser = self._structured[stream]
            if parser is None:
                self.append("text", text, stream)
                return
            for identifier, value in parser.feed(text).items():
                self._line("text", value, identifier)
            self._schedule()

    def _schedule(self):
        if time.monotonic() - self._last_write >= 0.3:
            self.flush()
        elif self._timer is None:
            self._timer = threading.Timer(0.3, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            if not self._updates:
                return
            try:
                # Parallel episode workers share one 20-line tail per run.
                with file_lock(self.path.with_suffix(".lock"), timeout=0.3):
                    try:
                        rows = json.loads(self.path.read_text(encoding="utf-8")).get("lines", [])
                    except (OSError, ValueError):
                        rows = []
                    rows = [row for row in rows if row.get("id") not in self._updates]
                    rows = sorted([*rows, *self._updates.values()], key=lambda row: row["at"])[-MAX_LINES:]
                    write_json(self.path, {"limit": MAX_LINES, "updated_at": rows[-1]["at"], "lines": rows})
                self._updates.clear()
                self._last_write = time.monotonic()
            except (OSError, ValueError, TypeError, AppError):
                pass  # Observability must not break generation.

    def finish(self):
        with self._lock:
            self.flush()
            self._streams.clear()
            self._structured.clear()
            self._closed = True
