"""PDF text extraction as a separate process, so a malformed file cannot hang the worker.

pypdf has had crafted inputs that loop forever inside its parser. ``sources.extract`` therefore runs
this module with the raw bytes on stdin and a wall-clock limit; the result arrives as one JSON object
on stdout (``metadata`` with ``extraction_coverage`` and ``blocks`` of ``[text, page]``).
"""
from __future__ import annotations

import io
import json
import re
import sys

MAX_PAGES = 300
MAX_TEXT = 1_000_000


class UnreadablePdf(ValueError):
    """A limit of this reader, named by a fixed token so the parent can say which one applied."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def extract_pdf(raw: bytes) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(raw))
    if reader.is_encrypted and not opens_without_password(reader):
        raise UnreadablePdf("encrypted")
    if len(reader.pages) > MAX_PAGES:
        raise UnreadablePdf("too_many_pages")
    blocks, size = [], 0
    coverage = {"pages_total": len(reader.pages), "pages_with_text": 0, "empty_pages": [],
                "suspected_image_pages": [], "suspected_table_pages": [], "suspected_equation_pages": [],
                "notes": ["Heuristic coverage only; table structure and equations have not been visually verified."]}
    for number, page in enumerate(reader.pages, 1):
        # A lone surrogate from a broken font map must not poison the JSON or the files written from it.
        text = (page.extract_text() or "").encode("utf-8", "replace").decode("utf-8")
        if text.strip():
            coverage["pages_with_text"] += 1
        else:
            coverage["empty_pages"].append(number)
            coverage["suspected_image_pages"].append(number)
        if re.search(r"\b(table|tabelle)\s*\d|(?:\S+[ \t]{3,}){3}", text, re.I):
            coverage["suspected_table_pages"].append(number)
        if re.search(r"[=∑∫√]|\b(equation|gleichung)\s*\d", text, re.I):
            coverage["suspected_equation_pages"].append(number)
        size += len(text)
        if size > MAX_TEXT:
            raise UnreadablePdf("text_too_large")
        blocks.append([text, number])
    metadata = {}
    if reader.metadata:
        metadata = {"title": str(reader.metadata.title or ""),
                    "authors": [str(reader.metadata.author)] if reader.metadata.author else []}
    metadata["extraction_coverage"] = coverage
    return {"metadata": metadata, "blocks": blocks}


def opens_without_password(reader) -> bool:
    """Public PDFs often carry only an owner password (print and copy restrictions); those open with the
    empty user password. A missing cipher backend or a real user password leaves the file unreadable."""
    try:
        return bool(reader.decrypt(""))
    except Exception:
        return False


def emit(payload: dict) -> None:
    # ASCII-only JSON on the raw byte stream. The pipe's text encoding is the console's (cp1252 on
    # Windows), which cannot represent the ligatures and symbols that page text routinely contains.
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=True).encode("ascii"))


def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        result = extract_pdf(raw)
    except UnreadablePdf as exc:
        emit({"error": type(exc).__name__, "reason": exc.reason})
        return 2
    except Exception as exc:  # Any parser failure is one unreadable source, reported by class only.
        emit({"error": type(exc).__name__})
        return 2
    emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
