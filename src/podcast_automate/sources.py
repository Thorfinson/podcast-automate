"""Retrieve public source documents and preserve deterministic, citable sections."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from .errors import AppError
from .models import now
from .research_models import SourceCandidate, SourceDocument, SourceSection
from .storage import file_hash, write_json

MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 1_000_000
EXTRACTION_VERSION = "sources.v2-fonts"
# Parsing untrusted PDFs happens in a child process with a wall-clock limit (see pdf_text).
PDF_TIMEOUT_SECONDS = 120
# Name resolution has no timeout of its own; a hung resolver must not hang the worker.
DNS_TIMEOUT_SECONDS = 10


def canonical_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise AppError("Nur öffentliche HTTP(S)-Quellen ohne Zugangsdaten werden abgerufen.", code="invalid_source_url")
    if parts.port not in {None, 80, 443}:
        raise AppError("Unzulässiger Port einer Quellenadresse.", code="invalid_source_url")
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def resolve(host: str):
    """Address records for ``host`` within ``DNS_TIMEOUT_SECONDS``; a lookup that never returns
    keeps only its own daemon thread, never the caller."""
    outcome = []

    def lookup():
        try:
            outcome.append(socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
        except OSError as exc:
            outcome.append(exc)

    thread = threading.Thread(target=lookup, daemon=True)
    thread.start()
    thread.join(DNS_TIMEOUT_SECONDS)
    if not outcome:
        raise AppError("Quellenserver nicht erreichbar (DNS-Zeitlimit).", code="source_download_failed")
    if isinstance(outcome[0], Exception):
        raise AppError("Quellenserver nicht erreichbar (DNS).", code="source_download_failed") from outcome[0]
    return outcome[0]


def public_url(url: str) -> str:
    url = canonical_url(url)
    host = urllib.parse.urlsplit(url).hostname
    addresses = resolve(host)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise AppError("Die Quellenadresse verweist nicht auf einen öffentlichen Server.", code="invalid_source_url")
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
    except urllib.error.HTTPError as exc:
        raise AppError(f"Quellenabruf fehlgeschlagen (HTTP {exc.code}).", code="source_download_failed",
                       details={"http_status": exc.code}) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise AppError(f"Quellenabruf fehlgeschlagen ({type(exc).__name__}).", code="source_download_failed",
                       details={"network_error": type(exc).__name__}) from exc


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


def extract_pdf_isolated(raw: bytes) -> dict:
    """Run the PDF parser in its own process; a hung or crashing parse is one unreadable source."""
    command = [sys.executable, "-m", "podcast_automate.pdf_text"]
    try:
        completed = subprocess.run(command, input=raw, capture_output=True, timeout=PDF_TIMEOUT_SECONDS,
                                   env=os.environ.copy(),
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired as exc:
        raise AppError(f"PDF konnte nicht innerhalb von {PDF_TIMEOUT_SECONDS} Sekunden als Text eingelesen werden.",
                       code="source_unreadable") from exc
    except OSError as exc:
        raise AppError("Der PDF-Leseprozess konnte nicht gestartet werden.", code="source_unreadable") from exc
    if completed.returncode != 0:
        raise pdf_failure(completed.stdout)
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
        if not isinstance(result, dict) or "blocks" not in result:
            raise ValueError("unexpected parser result")
    except ValueError as exc:
        raise AppError("PDF konnte nicht zuverlässig als Text eingelesen werden.", code="source_unreadable") from exc
    return result


PDF_LIMITS = {
    "encrypted": ("PDF ist verschlüsselt und kann nicht als Text eingelesen werden.", "source_unreadable"),
    "too_many_pages": ("PDF hat mehr als 300 Seiten und wird nicht eingelesen.", "source_too_large"),
    "text_too_large": ("PDF-Text überschreitet die Importgrenze von 1 MiB.", "source_too_large"),
}


def pdf_failure(stdout: bytes) -> AppError:
    """The child's verdict as one actionable error: a reader limit by name, otherwise the parser's error class."""
    try:
        verdict = json.loads(stdout.decode("utf-8"))
    except ValueError:
        verdict = {}
    if not isinstance(verdict, dict):
        verdict = {}
    if (message := PDF_LIMITS.get(verdict.get("reason"))):
        return AppError(message[0], code=message[1], details={"pdf_reason": verdict["reason"]})
    error = verdict.get("error")
    if isinstance(error, str) and re.fullmatch(r"\w{1,64}", error):
        return AppError(f"PDF konnte nicht zuverlässig als Text eingelesen werden ({error}).",
                        code="source_unreadable", details={"parser_error": error})
    return AppError("PDF konnte nicht zuverlässig als Text eingelesen werden.", code="source_unreadable")


def extract(raw: bytes, content_type: str, name: str) -> tuple[str, str, dict, list[SourceSection]]:
    metadata = {}
    if raw.startswith(b"%PDF-"):
        result = extract_pdf_isolated(raw)
        metadata = result.get("metadata") or {}
        try:
            sections = sections_from_blocks([(text, page) for text, page in result["blocks"]])
        except AppError as exc:
            coverage = metadata.get("extraction_coverage") or {}
            if exc.code != "source_unreadable" or "pages_total" not in coverage:
                raise
            # Pages without a text layer are the signature of a scan; say so instead of guessing at logins.
            raise AppError(f"PDF enthält zu wenig lesbaren Text: {coverage.get('pages_with_text', 0)} von "
                           f"{coverage['pages_total']} Seiten haben eine Textebene; vermutlich gescannt, OCR nötig.",
                           code="source_unreadable", details={"pages_total": coverage["pages_total"],
                           "pages_with_text": coverage.get("pages_with_text", 0)}) from exc
        return "pdf", ".pdf", metadata, sections
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


# A publisher that refuses an unattended download, or a host that cannot be reached, often has the same
# work in a free repository. OpenAlex (no key needed) lists those copies; the lookup is an ordinary download.
OPENALEX = "https://api.openalex.org/works"
ACCESS_BLOCKS = {401, 402, 403, 451}
# Repository pages that carry the full text themselves; other landing pages usually show an abstract only.
FULL_TEXT_PAGES = ("ncbi.nlm.nih.gov/pmc/", "pmc.ncbi.nlm.nih.gov/", "europepmc.org/")
DOI = re.compile(r"10\.\d{4,9}/[^\s?#]+", re.I)


def access_blocked(exc):
    details = getattr(exc, "details", None) or {}
    return getattr(exc, "code", "") == "source_download_failed" and (
        details.get("http_status") in ACCESS_BLOCKS or bool(details.get("network_error")))


def comparable_title(text):
    return re.sub(r"\W+", " ", (text or "").casefold()).strip()


def open_access_copies(candidate, limit=3):
    """Free copies of the same work that OpenAlex knows: PDF files first, then full-text repository pages.

    The work is found by the DOI in its address, otherwise by an exactly matching title. A failed or
    unreadable lookup means no copies."""
    found = DOI.search(urllib.parse.unquote(candidate.url))
    if found:
        doi = re.sub(r"(\.pdf|/full|/abstract|/epdf|/pdf)$", "", found.group(0).rstrip("/."), flags=re.I)
        query = f"{OPENALEX}/doi:{urllib.parse.quote(doi, safe='/')}"
    elif comparable_title(candidate.title) and not candidate.title.startswith(("http://", "https://")):
        query = f"{OPENALEX}?search={urllib.parse.quote(candidate.title)}&per_page=3"
    else:
        return []
    try:
        data = json.loads(download(query)[0])
        works = [data] if found else [work for work in data.get("results", [])
                                      if comparable_title(work.get("title")) == comparable_title(candidate.title)][:1]
    except (AppError, ValueError, TypeError, AttributeError):
        return []
    files, pages = [], []
    for work in works:
        for location in [work.get("best_oa_location") or {}, *(work.get("locations") or [])]:
            if not isinstance(location, dict) or not location.get("is_oa"):
                continue
            if location.get("pdf_url"):
                files.append(location["pdf_url"])
            elif any(host in (location.get("landing_page_url") or "") for host in FULL_TEXT_PAGES):
                pages.append(location["landing_page_url"])
    return [url for url in dict.fromkeys(files + pages) if url != candidate.url][:limit]


def open_access_copy(candidate, blocked):
    """The first readable free copy of a work whose own address refused the download, else that refusal."""
    for url in open_access_copies(candidate):
        try:
            raw, content_type, final_url = download(url)
            return (raw, content_type, final_url), extract(raw, content_type, url)
        except AppError:
            continue
    raise blocked


def import_failure(address, exc):
    """One failure row of the source index: the address, the reason shown to the user and a stable code
    (``source_unreadable``, ``source_download_failed``, ...) that a later step can select on."""
    return {"source": address, "reason": str(exc), "code": getattr(exc, "code", "import_failed")}


def import_source(candidate: SourceCandidate, root: Path, run_id: str, *, local: Path | None = None,
                  downloaded: tuple[bytes, str, str] | None = None) -> tuple[SourceDocument, Path]:
    address = str(local.resolve()) if local else canonical_url(candidate.url)
    source_id = "src_" + hashlib.sha256(address.encode()).hexdigest()[:16]
    extracted = None
    if downloaded is not None:
        raw, content_type, final_url = downloaded
    elif local:
        if local.stat().st_size > MAX_BYTES:
            raise AppError("Lokale Quelle überschreitet 20 MiB.", code="source_too_large")
        raw, content_type, final_url = local.read_bytes(), "", ""
    else:
        try:
            raw, content_type, final_url = download(address)
        except AppError as exc:
            if not access_blocked(exc):
                raise
            # The same work from a free repository; it keeps the address it was found under as its identity.
            (raw, content_type, final_url), extracted = open_access_copy(candidate, exc)
    kind, suffix, metadata, sections = extracted or extract(raw, content_type, address)
    copy_note = f"Open-access copy of the same work found via OpenAlex: {final_url}. " if extracted else ""
    raw_path = root / "sources/raw" / run_id / f"{source_id}{suffix}"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    pending = raw_path.with_suffix(suffix + ".pending")
    pending.write_bytes(raw)
    pending.replace(raw_path)
    document = SourceDocument(
        # A document's own title can be blank, as in a PDF whose title field holds one space; the candidate always has one.
        id=source_id, extraction_version=EXTRACTION_VERSION, type=kind, title=clean(metadata.get("title") or "") or candidate.title,
        authors=metadata.get("authors") or candidate.authors,
        published_date=metadata.get("published_date") or candidate.published_date,
        imported_at=now(), url=candidate.url if not local else "", final_url=final_url,
        language=metadata.get("language", "unknown"),
        reliability_note=("User-supplied local material; provenance and factual claims have not been independently verified. "
                          if local else copy_note + "Search selection (not independently certified): ") + candidate.rationale,
        uncertainties=["Publication metadata may come from search results; verify bibliographic details.",
                       "Automatic text extraction can omit images, tables and mathematical notation."],
        raw_path=raw_path.relative_to(root).as_posix(), raw_hash=file_hash(raw_path),
        text_hash=hashlib.sha256("\n".join(s.text for s in sections).encode()).hexdigest(), sections=sections,
        extraction_coverage=metadata.get("extraction_coverage"),
    )
    processed = root / "sources/processed" / run_id / f"{source_id}.json"
    write_json(processed, document.model_dump(mode="json"))
    return document, processed
