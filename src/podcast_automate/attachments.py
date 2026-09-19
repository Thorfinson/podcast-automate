"""Bounded, project-owned text attachments for the brief and research."""
from __future__ import annotations

import base64
import binascii
import io
import json
import re
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .prompts import fragment
from .errors import AppError
from .storage import atomic_text, digest, inside, load_project, write_json, write_yaml

MAX_FILES = 10
MAX_FILE_BYTES = 256 * 1024
MAX_DOCX_BYTES = 2 * 1024 * 1024
MAX_TRANSFER_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
MAX_BODY_BYTES = 5_700_000  # Base64 plus JSON overhead; other endpoints keep their smaller limit.
CONTEXT_CHARS = 60_000
MANIFEST = "inputs/attachments.json"
MATERIAL_RULES = fragment("material_rules")


def inventory(root: Path) -> list[dict]:
    path = inside(root, MANIFEST)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def attachment_path(root: Path, row: dict) -> Path:
    if not re.fullmatch(r"inputs/uploads/[a-f0-9]{64}\.(md|txt)", row["path"]):
        raise AppError("Ungültiger Dateianhang.", code="invalid_attachment")
    return inside(root, row["path"])


def context(root: Path) -> list[dict]:
    """Include every attachment, sharing the bounded prompt budget fairly."""
    rows = inventory(root)
    result, remaining = [], CONTEXT_CHARS
    for position, row in enumerate(sorted(rows, key=lambda r: r["characters"])):
        text = attachment_path(root, row).read_text(encoding="utf-8")
        allowance = remaining // (len(rows) - position)
        excerpt = text[:allowance]
        remaining -= len(excerpt)
        result.append({"name": row["name"], "text": excerpt, "characters": len(text),
                       "truncated": len(excerpt) < len(text), "provenance": "user_upload_unverified"})
    return result


def docx_text(raw: bytes) -> str:
    """Read document text in order, without extracting ZIP paths or loading relationships."""
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(e.file_size for e in entries) > 32 * 1024 * 1024:
                raise ValueError("Expanded archive too large")
            if sum(e.filename == "word/document.xml" for e in entries) != 1:
                raise ValueError("Missing or duplicate document part")
            info = archive.getinfo("word/document.xml")
            if info.file_size > 4 * 1024 * 1024:
                raise ValueError("Document XML too large")
            xml = archive.read(info)
        markup = xml.replace(b"\x00", b"")
        if b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
            raise ValueError("DTD not permitted")
        document = ET.fromstring(xml)
        body = document.find(namespace + "body")
        if body is None:
            raise ValueError("Missing document body")
        output = []
        stack = [(body, False)]
        while stack:
            element, closing = stack.pop()
            if not closing:
                if element.tag == namespace + "t":
                    output.append(element.text or "")
                elif element.tag in {namespace + "br", namespace + "cr"}:
                    output.append("\n")
                elif element.tag == namespace + "tab":
                    output.append("\t")
                stack.append((element, True))
                stack.extend((child, False) for child in reversed(element))
            elif element.tag in {namespace + "p", namespace + "tr"}:
                output.append("\n")
            elif element.tag == namespace + "tc":
                output.append("\t")
        return "".join(output).strip()
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError, ET.ParseError) as exc:
        raise AppError("DOCX nicht lesbar oder zu groß entpackt. Bitte als normale .docx ohne Passwort oder als .txt speichern.",
                       code="invalid_attachment") from exc


def decode_file(item: dict, secrets: tuple[str, ...]) -> tuple[dict, str]:
    if not isinstance(item, dict):
        raise AppError("Ungültiger Dateianhang.", code="invalid_attachment")
    name, encoded = item.get("name"), item.get("base64")
    if (not isinstance(name, str) or not 1 <= len(name) <= 240 or
            any(c in name for c in '/\\:<>"|?*') or
            any(unicodedata.category(c).startswith("C") for c in name) or
            Path(name).suffix.lower() not in {".md", ".txt", ".docx"}):
        raise AppError("Bitte .md-, .txt- oder .docx-Dateien mit einem gültigen Dateinamen wählen.",
                       code="invalid_attachment")
    suffix = Path(name).suffix.lower()
    maximum = MAX_DOCX_BYTES if suffix == ".docx" else MAX_FILE_BYTES
    if not isinstance(encoded, str) or len(encoded) > (maximum + 2) // 3 * 4:
        raise AppError("Höchstens 256 KiB je Textdatei oder 2 MiB je DOCX-Datei.", code="attachment_limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AppError("Dateiübertragung unvollständig. Bitte erneut anhängen.", code="invalid_attachment") from exc
    if not raw or len(raw) > maximum:
        raise AppError("Bitte eine nicht leere Datei wählen: Text bis 256 KiB, DOCX bis 2 MiB.", code="attachment_limit")
    try:
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = docx_text(raw) if suffix == ".docx" else raw.decode(encoding)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as exc:
        raise AppError("Textcodierung nicht lesbar. Bitte die Datei als UTF-8 speichern und erneut anhängen.",
                       code="invalid_attachment") from exc
    if not text.strip() or any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text):
        raise AppError("Der Anhang enthält keinen lesbaren Text oder enthält Binärdaten.", code="invalid_attachment")
    if any(key and key in name + text for key in secrets) or re.search(r"sk-or-[A-Za-z0-9_-]{12,}", name + text):
        raise AppError("Der Anhang enthält einen API-Key. Bitte Zugangsdaten vor dem Hochladen entfernen.",
                       code="credential_in_prompt")
    identity = digest([name, text])
    stored_suffix = ".txt" if suffix == ".docx" else suffix
    return {"id": identity, "name": name, "path": f"inputs/uploads/{identity}{stored_suffix}",
            "bytes": len(text.encode("utf-8")), "characters": len(text)}, text


def save_manifest(root: Path, old: list[dict], new: list[dict]) -> None:
    config = load_project(root)
    previous = config.model_dump(mode="json")
    old_paths = {row["path"] for row in old}
    config.local_sources = list(dict.fromkeys(
        [value for value in config.local_sources if value not in old_paths] + [row["path"] for row in new]))
    try:
        write_json(inside(root, MANIFEST), new)
        write_yaml(root / "project.yaml", config.model_dump(mode="json"))
    except OSError:
        write_json(inside(root, MANIFEST), old)
        write_yaml(root / "project.yaml", previous)
        raise


def add(root: Path, files: list[dict], *, secrets: tuple[str, ...] = ()) -> list[dict]:
    """Caller holds the exclusive project lock. Validate the whole batch before writing."""
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise AppError("Bitte 1 bis 10 Markdown-, Text- oder DOCX-Dateien auswählen.", code="attachment_limit")
    if sum(len(item.get("base64", "")) for item in files if isinstance(item, dict)
           and isinstance(item.get("base64"), str)) > (MAX_TRANSFER_BYTES + 2) // 3 * 4:
        raise AppError("Bitte höchstens 4 MiB auf einmal hochladen.", code="attachment_limit")
    decoded = [decode_file(item, secrets) for item in files]
    old = inventory(root)
    combined = {row["id"]: row for row in old}
    combined.update({row["id"]: row for row, _ in decoded})
    new = list(combined.values())
    if len(new) > MAX_FILES or sum(row["bytes"] for row in new) > MAX_TOTAL_BYTES:
        raise AppError("Pro Projekt sind höchstens 10 Anhänge mit insgesamt 1 MiB Text möglich.", code="attachment_limit")
    created = []
    try:
        for row, text in decoded:
            if row["id"] not in {r["id"] for r in old}:
                path = attachment_path(root, row)
                atomic_text(path, text)
                created.append(path)
        save_manifest(root, old, new)
    except OSError:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return new


def remove(root: Path, attachment_id: str) -> list[dict]:
    old = inventory(root)
    new = [row for row in old if row["id"] != attachment_id]
    if len(old) == len(new):
        raise AppError("Anhang nicht gefunden. Ansicht neu laden.", code="invalid_attachment")
    # Keep the local copy for provenance of past calls; only active inputs change.
    save_manifest(root, old, new)
    return new


def proposal_current(root: Path, proposal: dict | None) -> bool:
    rows = inventory(root)
    path = root / "studio/proposal_inputs.json"
    if not rows and not path.exists():
        return True  # Projects created before attachments existed.
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return saved == {"proposal_hash": digest(proposal), "attachments_hash": digest(rows)}
