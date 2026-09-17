"""Retrieve public source documents and preserve deterministic, citable sections."""
from __future__ import annotations

import hashlib
import io
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from pypdf import PdfReader

from .errors import AppError
from .models import now
from .research_models import SourceCandidate, SourceDocument, SourceSection
from .storage import file_hash, write_json

MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 1_000_000
EXTRACTION_VERSION = "sources.v2-fonts"


def canonical_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise AppError("Nur öffentliche HTTP(S)-Quellen ohne Zugangsdaten werden abgerufen.", code="invalid_source_url")
    if parts.port not in {None, 80, 443}:
        raise AppError("Unzulässiger Port einer Quellenadresse.", code="invalid_source_url")
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def public_url(url: str) -> str:
    url = canonical_url(url)
    host = urllib.parse.urlsplit(url).hostname
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise AppError("Die Quellenadresse verweist nicht auf einen öffentlichen Server.", code="invalid_source_url")
    except socket.gaierror as exc:
        raise AppError("Quellenserver nicht erreichbar (DNS).", code="source_download_failed") from exc
    return url


class PublicRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url: str) -> tuple[bytes, str, str]:
    url = public_url(url)
    # No browser cookies or credentials are used for source retrieval.
    request = urllib.request.Request(url, headers={
        "User-Agent": "PodcastAutomate/0.1 (personal research; public documents)",
        "Accept": "text/html,application/pdf,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
    })
    try:
        opener = urllib.request.build_opener(PublicRedirect())
        with opener.open(request, timeout=30) as response:
            if int(response.headers.get("Content-Length", 0)) > MAX_BYTES:
                raise AppError("Quelle überschreitet 20 MiB.", code="source_too_large")
            chunks, size, started = [], 0, time.monotonic()
            while chunk := response.read(65536):
                size += len(chunk)
                if size > MAX_BYTES or time.monotonic() - started > 90:
                    raise AppError("Quelle überschreitet Abrufgrenzen.", code="source_too_large")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("Content-Type", ""), response.geturl()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise AppError(f"Quellenabruf fehlgeschlagen ({type(exc).__name__}).", code="source_download_failed") from exc


class ArticleParser(HTMLParser):
    OMIT = {"script", "style", "nav", "header", "footer", "form", "aside", "noscript", "svg", "template"}
    BLOCK = {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "li", "tr", "br"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []
        self.main_text = []
        self.title = []
        self.metadata = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta":
            key = attributes.get("name", attributes.get("property", "")).lower()
            if key and attributes.get("content"):
                self.metadata.setdefault(key, []).append(attributes["content"])
        if tag == "html" and attributes.get("lang"):
            self.metadata["language"] = [attributes["lang"]]
        if tag in self.BLOCK:
            self.add_text("\n\n")
        if tag not in {"meta", "link", "br", "hr", "img", "input", "source", "wbr"}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.add_text("\n\n")
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]

    def add_text(self, value):
        if not any(tag in self.OMIT for tag in self.stack):
            self.text.append(value)
            if "main" in self.stack or "article" in self.stack:
                self.main_text.append(value)

    def handle_data(self, data):
        if "title" in self.stack:
            self.title.append(data)
        if "head" not in self.stack:
            self.add_text(data)


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def sections_from_blocks(blocks: list[tuple[str, int | None]]) -> list[SourceSection]:
    result, seen = [], set()
    for text, page in blocks:
        text = clean(text)
        while text:
            end = len(text) if len(text) <= 1600 else text.rfind(" ", 0, 1600)
            if end <= 0:
                end = min(1600, len(text))
            part, text = text[:end], text[end:].lstrip()
            identifier = "sec_" + hashlib.sha256(f"{page}:{part}".encode()).hexdigest()[:16]
            if identifier not in seen and len(part) >= 30:
                seen.add(identifier)
                result.append(SourceSection(id=identifier, text=part, page=page))
    if sum(len(s.text) for s in result) < 200:
        raise AppError("Quelle enthält zu wenig lesbaren Text; OCR oder Anmeldung möglicherweise nötig.", code="source_unreadable")
    return result


def extract(raw: bytes, content_type: str, name: str) -> tuple[str, str, dict, list[SourceSection]]:
    metadata = {}
    if raw.startswith(b"%PDF-"):
        try:
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted or len(reader.pages) > 300:
                raise ValueError("Encrypted PDF or more than 300 pages")
            blocks, size = [], 0
            coverage = {"pages_total": len(reader.pages), "pages_with_text": 0, "empty_pages": [],
                        "suspected_image_pages": [], "suspected_table_pages": [], "suspected_equation_pages": [],
                        "notes": ["Heuristic coverage only; table structure and equations have not been visually verified."]}
            for number, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
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
                    raise ValueError("PDF text too large")
                blocks.append((text, number))
            if reader.metadata:
                metadata = {"title": str(reader.metadata.title or ""),
                            "authors": [str(reader.metadata.author)] if reader.metadata.author else []}
            metadata["extraction_coverage"] = coverage
            return "pdf", ".pdf", metadata, sections_from_blocks(blocks)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("PDF konnte nicht zuverlässig als Text eingelesen werden.", code="source_unreadable") from exc
    match = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, flags=re.I)
    encoding = match.group(1) if match else "utf-8"
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    if "html" in content_type or "<html" in text[:2000].lower() or "<!doctype html" in text[:2000].lower():
        parser = ArticleParser()
        parser.feed(text)
        body = "".join(parser.main_text) if len("".join(parser.main_text)) >= 200 else "".join(parser.text)
        values = parser.metadata
        metadata = {"title": (values.get("citation_title") or [clean("".join(parser.title))])[0],
                    "authors": values.get("citation_author", values.get("author", [])),
                    "published_date": (values.get("citation_publication_date") or values.get("article:published_time") or [""])[0],
                    "language": values.get("language", ["unknown"])[0]}
        if any(marker in clean(body).lower()[:700] for marker in ("just a moment...", "verify you are human", "enable javascript and cookies")):
            raise AppError("Quelle liefert eine Zugriffssperre statt Artikeltext.", code="source_unreadable")
        kind, suffix = "html", ".html"
        blocks = [(block, None) for block in re.split(r"\n\s*\n", body)]
    elif "text/" in content_type or Path(name).suffix.lower() in {".txt", ".md"}:
        if "\x00" in text:
            raise AppError("Binärdaten statt Quellentext.", code="source_unreadable")
        kind, suffix = "text", ".txt"
        blocks = [(block, None) for block in re.split(r"\n\s*\n", text)]
    else:
        raise AppError("Quellenformat wird nicht unterstützt.", code="source_unreadable")
    if sum(len(block) for block, _ in blocks) > MAX_TEXT:
        raise AppError("Extrahierter Text überschreitet die Importgrenze.", code="source_too_large")
    return kind, suffix, metadata, sections_from_blocks(blocks)


def import_source(candidate: SourceCandidate, root: Path, run_id: str, *, local: Path | None = None,
                  downloaded: tuple[bytes, str, str] | None = None) -> tuple[SourceDocument, Path]:
    address = str(local.resolve()) if local else canonical_url(candidate.url)
    source_id = "src_" + hashlib.sha256(address.encode()).hexdigest()[:16]
    if downloaded is not None:
        raw, content_type, final_url = downloaded
    elif local:
        if local.stat().st_size > MAX_BYTES:
            raise AppError("Lokale Quelle überschreitet 20 MiB.", code="source_too_large")
        raw, content_type, final_url = local.read_bytes(), "", ""
    else:
        raw, content_type, final_url = download(address)
    kind, suffix, metadata, sections = extract(raw, content_type, address)
    raw_path = root / "sources/raw" / run_id / f"{source_id}{suffix}"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    pending = raw_path.with_suffix(suffix + ".pending")
    pending.write_bytes(raw)
    pending.replace(raw_path)
    document = SourceDocument(
        id=source_id, extraction_version=EXTRACTION_VERSION, type=kind, title=metadata.get("title") or candidate.title,
        authors=metadata.get("authors") or candidate.authors,
        published_date=metadata.get("published_date") or candidate.published_date,
        imported_at=now(), url=candidate.url if not local else "", final_url=final_url,
        language=metadata.get("language", "unknown"),
        reliability_note=("User-supplied local material; provenance and factual claims have not been independently verified. "
                          if local else "Search selection (not independently certified): ") + candidate.rationale,
        uncertainties=["Publication metadata may come from search results; verify bibliographic details.",
                       "Automatic text extraction can omit images, tables and mathematical notation."],
        raw_path=raw_path.relative_to(root).as_posix(), raw_hash=file_hash(raw_path),
        text_hash=hashlib.sha256("\n".join(s.text for s in sections).encode()).hexdigest(), sections=sections,
        extraction_coverage=metadata.get("extraction_coverage"),
    )
    processed = root / "sources/processed" / run_id / f"{source_id}.json"
    write_json(processed, document.model_dump(mode="json"))
    return document, processed
