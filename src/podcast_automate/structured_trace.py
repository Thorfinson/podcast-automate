"""Incremental projection of JSON string values; never display wire-format keys."""
import json
import uuid


class StructuredTrace:
    def __init__(self, text_fields, list_fields, actions):
        self.fields, self.lists, self.actions = text_fields, list_fields, actions
        self.stack = []
        self.quoted = self.escaped = False
        self.key_string = False
        self.value = self.key = self.identifier = ""
        self.label = None

    def text(self, complete=False):
        raw = self.value
        if not complete:
            # A backslash/unicode escape can be split across transport chunks.
            while raw:
                try:
                    return json.loads('"' + raw + '"')
                except ValueError:
                    raw = raw[:-1]
            return ""
        try:
            return json.loads('"' + raw + '"')
        except ValueError:
            return ""

    def feed(self, delta):
        updates = {}
        def publish(complete=False):
            if self.label is None:
                return
            text = self.text(complete)
            if self.label == "action":
                if complete and text in self.actions:
                    updates[self.identifier] = self.actions[text]
            elif text.strip():
                # JSON may temporarily contain an unpaired Unicode surrogate.
                text = text.encode("utf-8", errors="replace").decode("utf-8")
                updates[self.identifier] = f"{self.label}: {text}"
        for char in delta:
            if self.quoted:
                if char == '"' and not self.escaped:
                    self.quoted = False
                    if self.key_string:
                        self.stack[-1]["key"] = self.text(True)
                    else:
                        publish(True)
                    self.label = None
                else:
                    if len(self.value) < 8192:
                        self.value += char
                    self.escaped = char == "\\" and not self.escaped
                continue
            if char == '"':
                parent = self.stack[-1] if self.stack else {}
                self.key_string = parent.get("kind") == "object" and parent.get("expect") == "key"
                key = parent.get("key", "")
                self.label = None if self.key_string else (
                    ("action" if key == "action" else self.fields.get(key)) if parent.get("kind") == "object"
                    else self.lists.get(key))
                self.quoted, self.escaped, self.value = True, False, ""
                self.identifier = uuid.uuid4().hex
            elif char in "{[":
                key = self.stack[-1].get("key", "") if self.stack else ""
                self.stack.append({"kind": "object" if char == "{" else "array", "expect": "key", "key": key})
            elif char in "}]" and self.stack:
                self.stack.pop()
            elif char == ":" and self.stack:
                self.stack[-1]["expect"] = "value"
            elif char == "," and self.stack:
                self.stack[-1]["expect"] = "key"
        if self.quoted and not self.key_string:
            publish()
        return updates
