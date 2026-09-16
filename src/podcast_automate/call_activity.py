"""Small, allowlisted public activity receipts; never persist raw model streams."""
import json
import re
from urllib.parse import urlsplit

from .models import now
from .storage import write_json


def clean_status(value, limit=600):
    text = str(value or "")
    text = re.sub(r"\b(?:sk-|sess-)[A-Za-z0-9_-]+", "[Zugangsdaten entfernt]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [entfernt]", text)
    text = re.sub(r"(?i)((?:api[_ -]?key|token|password|secret)\s*[=:]\s*)[^\s,;]+", r"\1[entfernt]", text)
    return " ".join(text.split())[:limit]


class CallActivity:
    def __init__(self, directory, schema, model):
        self.path = directory / "activity.json"
        self.data = {"schema": schema, "model": model, "status": "running", "started_at": now(), "events": []}
        self.record("Aufruf gestartet")

    def record(self, message):
        self.data["updated_at"] = now()
        self.data["events"] = (self.data["events"] + [{"at": now(), "message": clean_status(message)}])[-40:]
        try:
            write_json(self.path, self.data)
        except OSError:
            pass

    def observe(self, line):
        if len(line) > 65536:
            return
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind in {"turn.started", "turn.completed", "turn.failed", "error"}:
            self.record({"turn.started": "Modell bearbeitet den Auftrag", "turn.completed": "Modellantwort empfangen; Validierung folgt",
                         "turn.failed": "Modell meldet einen fehlgeschlagenen Aufruf", "error": "Anbieter meldet ein Verbindungs- oder Aufrufproblem"}[kind])
            return
        item = event.get("item")
        if kind not in {"item.started", "item.completed"} or not isinstance(item, dict):
            return
        item_type = item.get("type")
        # Reasoning and raw tool output are deliberately outside this allowlist.
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
            self.record(item.get("text", ""))
        elif item_type in {"command_execution", "mcp_tool_call", "file_change"}:
            self.record("Werkzeugaktion gestartet" if kind == "item.started" else "Werkzeugaktion abgeschlossen")

    def finish(self, status):
        self.data["status"] = status
        self.record("Antwort gespeichert" if status == "completed" else "Aufruf beendet: " + status)
