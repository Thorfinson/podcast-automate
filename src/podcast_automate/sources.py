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
from datetime import datetime, timezone
from pathlib import Path

from .errors import AppError
from .models import now
from .research_models import SourceCandidate, SourceDocument, SourceSection
from .storage import file_hash, file_lock, read_text, write_json
from .provided_works import MAX_WORK_BYTES

MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 1_000_000
MAX_BOOK_TEXT = 6_000_000
EXTRACTION_VERSION = "sources.v2-fonts"
# Parsing untrusted PDFs happens in a child process with a wall-clock limit (see pdf_text).
PDF_TIMEOUT_SECONDS = 120
# A whole book the editor provided (provided_works) is read with pdf_text's book limits and more time.
BOOK_TIMEOUT_SECONDS = 900
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
        # urllib copies every header onto the redirected request, a service key included (CORE's Authorization):
        # a request that carries one follows a redirect only on its own host and never from https to http (2026-10-02).
        if request.get_header("Authorization") or request.get_header("Proxy-authorization"):
            old, new = urllib.parse.urlsplit(request.full_url), urllib.parse.urlsplit(newurl)
            if (new.hostname or "").lower() != (old.hostname or "").lower() or (old.scheme == "https" and new.scheme != "https"):
                raise AppError("Weiterleitung eines Abrufs mit Zugangsschlüssel zu einem anderen Server abgelehnt.",
                               code="source_download_failed", details={"redirect_refused": True})
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url: str, headers: dict | None = None) -> tuple[bytes, str, str]:
    """``headers`` only for a free service that identifies the caller by a key (CORE)."""
    url = public_url(url)
    # No browser cookies or credentials are used for source retrieval.
    request = urllib.request.Request(url, headers={
        "User-Agent": "PodcastAutomate/0.1 (personal research; public documents)",
        "Accept": "text/html,application/pdf,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Encoding": "identity",
        **(headers or {}),
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


def extract_pdf_isolated(raw: bytes, *, book: bool = False) -> dict:
    """Run the PDF parser in its own process; a hung or crashing parse is one unreadable source."""
    command = [sys.executable, "-m", "podcast_automate.pdf_text", *(["--book"] if book else [])]
    timeout = BOOK_TIMEOUT_SECONDS if book else PDF_TIMEOUT_SECONDS
    try:
        completed = subprocess.run(command, input=raw, capture_output=True, timeout=timeout,
                                   env=os.environ.copy(),
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired as exc:
        raise AppError(f"PDF konnte nicht innerhalb von {timeout} Sekunden als Text eingelesen werden.",
                       code="source_unreadable") from exc
    except OSError as exc:
        raise AppError("Der PDF-Leseprozess konnte nicht gestartet werden.", code="source_unreadable") from exc
    if completed.returncode != 0:
        raise pdf_failure(completed.stdout, **({"book": True} if book else {}))
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
# The same limits of pdf_text's book mode (a provided work, a primary work, an open-access book).
BOOK_PDF_LIMITS = {**PDF_LIMITS,
                   "too_many_pages": ("PDF hat mehr als 2000 Seiten und wird nicht eingelesen.", "source_too_large"),
                   "text_too_large": ("PDF-Text überschreitet die Importgrenze von 6 Millionen Zeichen.", "source_too_large")}


def pdf_failure(stdout: bytes, *, book: bool = False) -> AppError:
    """The child's verdict as one actionable error: a reader limit by name, otherwise the parser's error class."""
    try:
        verdict = json.loads(stdout.decode("utf-8"))
    except ValueError:
        verdict = {}
    if not isinstance(verdict, dict):
        verdict = {}
    if (message := (BOOK_PDF_LIMITS if book else PDF_LIMITS).get(verdict.get("reason"))):
        return AppError(message[0], code=message[1], details={"pdf_reason": verdict["reason"]})
    error = verdict.get("error")
    if isinstance(error, str) and re.fullmatch(r"\w{1,64}", error):
        return AppError(f"PDF konnte nicht zuverlässig als Text eingelesen werden ({error}).",
                        code="source_unreadable", details={"parser_error": error})
    return AppError("PDF konnte nicht zuverlässig als Text eingelesen werden.", code="source_unreadable")


# Bot checks and sign-in walls answer with a page of their own instead of the document. The first
# markers name a check outright; the others and a check's title also occur as a banner above a real
# article, so they count only on a page too short to be one (Springer's "Client Challenge" has 300 characters).
CHALLENGE_MARKERS = ("just a moment...", "verify you are human", "enable javascript and cookies")
SHORT_PAGE_MARKERS = ("enable javascript to proceed", "javascript is disabled in your browser", "checking your browser")
CHALLENGE_TITLES = ("client challenge", "just a moment", "attention required", "access denied", "security check")
SHORT_PAGE_CHARS = 3000


def interstitial(title, text):
    """True for a bot check or sign-in wall that stands in for the requested document."""
    text = clean(text or "").lower()
    return (any(marker in text[:700] for marker in CHALLENGE_MARKERS)
            or len(text) < SHORT_PAGE_CHARS and (any(marker in text for marker in SHORT_PAGE_MARKERS)
                                                 or clean(title or "").lower().rstrip(". ") in CHALLENGE_TITLES))


def extract(raw: bytes, content_type: str, name: str, *, book: bool = False) -> tuple[str, str, dict, list[SourceSection]]:
    metadata = {}
    if raw.startswith(b"%PDF-"):
        result = extract_pdf_isolated(raw, book=book)
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
    # Europe PMC's full text is JATS XML: paragraphs in <p>, read like a page (europe_pmc_copies).
    jats = "xml" in content_type and "<article" in text[:5000]
    if jats or "html" in content_type or "<html" in text[:2000].lower() or "<!doctype html" in text[:2000].lower():
        parser = ArticleParser()
        parser.feed(text)
        body = "".join(parser.main_text) if len("".join(parser.main_text)) >= 200 else "".join(parser.text)
        values = parser.metadata
        metadata = {"title": (values.get("citation_title") or [clean("".join(parser.title))])[0],
                    "authors": values.get("citation_author", values.get("author", [])),
                    "published_date": (values.get("citation_publication_date") or values.get("article:published_time") or [""])[0],
                    "language": values.get("language", ["unknown"])[0]}
        if interstitial(clean("".join(parser.title)), body):
            raise AppError("Quelle liefert eine Zugriffssperre statt Artikeltext.", code="source_access_blocked",
                           details={"interstitial": True})
        kind, suffix = "html", ".html"
        blocks = [(block, None) for block in re.split(r"\n\s*\n", body)]
    elif "text/" in content_type or Path(name).suffix.lower() in {".txt", ".md"}:
        if "\x00" in text:
            raise AppError("Binärdaten statt Quellentext.", code="source_unreadable")
        kind, suffix = "text", ".txt"
        blocks = [(block, None) for block in re.split(r"\n\s*\n", text)]
    else:
        raise AppError("Quellenformat wird nicht unterstützt.", code="source_unreadable")
    if sum(len(block) for block, _ in blocks) > (MAX_BOOK_TEXT if book else MAX_TEXT):
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
    return getattr(exc, "code", "") == "source_access_blocked" or getattr(exc, "code", "") == "source_download_failed" and (
        details.get("http_status") in ACCESS_BLOCKS or bool(details.get("network_error")))


def blocked_sources(index):
    """The addresses this run could not read because the publisher refused them: an HTTP refusal or a
    bot check in place of the document. Only these can justify accepting a criterion as an access gap."""
    rows = {}
    for failure in index.failures:
        # The reason is this module's own message, "Quellenabruf fehlgeschlagen (HTTP 403)."
        status = re.search(r"\(HTTP (\d{3})\)", failure.get("reason", ""))
        if failure.get("code") == "source_access_blocked" or (
                failure.get("code") == "source_download_failed" and status and int(status.group(1)) in ACCESS_BLOCKS):
            rows.setdefault(failure["source"], {"url": failure["source"], "evidence": failure["reason"]})
    for source in index.sources:
        # Imported before such pages were recognised: the check stands in the index as if it were the document.
        if (source.type == "html" and sum(len(s.text) for s in source.sections) < SHORT_PAGE_CHARS
                and interstitial(source.title, " ".join(s.text for s in source.sections))):
            address = source.url or source.final_url
            rows.setdefault(address, {"url": address, "evidence": f"Bot-Abwehrseite statt Inhalt („{source.title}“)"})
    return list(rows.values())


def comparable_title(text):
    return re.sub(r"\W+", " ", (text or "").casefold()).strip()


def work_doi(candidate):
    """The DOI in a candidate's address, without a trailing format suffix, or None."""
    found = DOI.search(urllib.parse.unquote(candidate.url))
    return re.sub(r"(\.pdf|/full|/abstract|/epdf|/pdf)$", "", found.group(0).rstrip("/."), flags=re.I) if found else None


def searchable_title(candidate):
    return comparable_title(candidate.title) and not candidate.title.startswith(("http://", "https://"))


def lookup(url, headers=None):
    """A free service's JSON answer, or None when the service fails or answers with something else."""
    try:
        data = json.loads((download(url, headers) if headers else download(url))[0])
    except (AppError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def open_access_copies(candidate, limit=3):
    """Free copies of the same work that OpenAlex knows: PDF files first, then full-text repository pages.

    The work is found by the DOI in its address, otherwise by an exactly matching title. A failed or
    unreadable lookup means no copies."""
    doi = work_doi(candidate)
    if doi:
        query = f"{OPENALEX}/doi:{urllib.parse.quote(doi, safe='/')}"
    elif searchable_title(candidate):
        query = f"{OPENALEX}?search={urllib.parse.quote(candidate.title)}&per_page=3"
    else:
        return []
    try:
        data = json.loads(download(query)[0])
        works = [data] if doi else [work for work in data.get("results", [])
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


# After OpenAlex, further free services are asked one after another until a copy reads (2026-10-01: for 26 of the
# 34 addresses Asimov's run could not read, OpenAlex alone had no copy). Unpaywall wants a contact address and CORE
# a free key; each is asked only when the user has set it (PLA_UNPAYWALL_EMAIL; CORE's key in the Studio's settings
# or PLA_CORE_API_KEY).
UNPAYWALL = "https://api.unpaywall.org/v2/"
SEMANTIC_SCHOLAR = "https://api.semanticscholar.org/graph/v1/paper/"
EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
CORE = "https://api.core.ac.uk/v3/search/works"
PMCID = re.compile(r"PMC\d{4,10}", re.I)


def unpaywall_copies(candidate, doi):
    email = os.environ.get("PLA_UNPAYWALL_EMAIL", "").strip()
    if not doi or not email:
        return []
    data = lookup(f"{UNPAYWALL}{urllib.parse.quote(doi, safe='/')}?email={urllib.parse.quote(email)}") or {}
    locations = [data.get("best_oa_location") or {}, *(data.get("oa_locations") or [])]
    return [location["url_for_pdf"] for location in locations if isinstance(location, dict) and location.get("url_for_pdf")]


def semantic_scholar_copies(candidate, doi):
    if doi:
        work = lookup(f"{SEMANTIC_SCHOLAR}DOI:{urllib.parse.quote(doi, safe='/')}?fields=openAccessPdf") or {}
    elif searchable_title(candidate):
        found = lookup(f"{SEMANTIC_SCHOLAR}search/match?query={urllib.parse.quote(candidate.title)}&fields=title,openAccessPdf") or {}
        work = next((row for row in found.get("data") or [] if isinstance(row, dict)
                     and comparable_title(row.get("title")) == comparable_title(candidate.title)), {})
    else:
        return []
    pdf = work.get("openAccessPdf") if isinstance(work.get("openAccessPdf"), dict) else {}
    return [pdf["url"]] if pdf.get("url") else []


def europe_pmc_copies(candidate, doi):
    """The JATS full text of an open-access article: read without the captcha PubMed Central now shows."""
    pmcid = PMCID.search(candidate.url)
    if pmcid:
        return [f"{EUROPE_PMC}{pmcid.group(0).upper()}/fullTextXML"]
    if doi:
        query, exact = f'DOI:"{doi}"', False
    elif searchable_title(candidate):
        query, exact = f'TITLE:"{candidate.title}"', True
    else:
        return []
    found = lookup(f"{EUROPE_PMC}search?query={urllib.parse.quote(query)}&resultType=lite&format=json") or {}
    for row in ((found.get("resultList") or {}).get("result") or []):
        if (isinstance(row, dict) and row.get("pmcid") and row.get("isOpenAccess") == "Y"
                and (not exact or comparable_title(row.get("title")) == comparable_title(candidate.title))):
            return [f"{EUROPE_PMC}{row['pmcid']}/fullTextXML"]
    return []


# CORE's free key allows about 1000 API calls a day (the user's account, 2026-10-01). Every research worker counts
# its CORE searches in one shared file under a lock, so projects running side by side stay within the day together;
# with the day's calls used up CORE is skipped until the next UTC day, and the other services still answer.
CORE_DAILY_LIMIT = 1000


def core_usage_path() -> Path:
    return Path(os.environ.get("PLA_CORE_USAGE_STORE") or Path.home() / ".podcast-automate" / "core_usage.json")


def core_usage() -> dict:
    """Today's CORE API calls (UTC day) and the daily limit (PLA_CORE_DAILY_LIMIT, default 1000)."""
    path, today = core_usage_path(), datetime.now(timezone.utc).date().isoformat()
    try:
        data = json.loads(read_text(path)) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    try:
        limit = int(os.environ.get("PLA_CORE_DAILY_LIMIT") or CORE_DAILY_LIMIT)
    except ValueError:
        limit = CORE_DAILY_LIMIT
    return {"date": today, "calls": data.get("calls", 0) if data.get("date") == today else 0, "limit": limit}


def reserve_core_call() -> bool:
    """Count one CORE API call against today's allowance; False when the day's calls are used up."""
    path = core_usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path.with_name(path.name + ".lock"), timeout=30):
        usage = core_usage()
        if usage["calls"] >= usage["limit"]:
            return False
        write_json(path, {**usage, "calls": usage["calls"] + 1})
        return True


_core_key = ""


def use_core_key(key) -> None:
    """The CORE key a Studio worker was handed from the Studio's settings (D-167); empty falls back to
    PLA_CORE_API_KEY."""
    global _core_key
    _core_key = key or ""


def core_copies(candidate, doi):
    key = (_core_key or os.environ.get("PLA_CORE_API_KEY", "")).strip()
    if not key or not (doi or searchable_title(candidate)) or not reserve_core_call():
        return []
    query = f'doi:"{doi}"' if doi else f'title:"{candidate.title}"'
    found = lookup(f"{CORE}?q={urllib.parse.quote(query)}&limit=3", headers={"Authorization": f"Bearer {key}"}) or {}
    return [row["downloadUrl"] for row in found.get("results") or [] if isinstance(row, dict) and row.get("downloadUrl")
            and (doi or comparable_title(row.get("title")) == comparable_title(candidate.title))]


COPY_SERVICES = (("OpenAlex", lambda candidate, doi: open_access_copies(candidate)), ("Unpaywall", unpaywall_copies),
                 ("Semantic Scholar", semantic_scholar_copies), ("Europe PMC", europe_pmc_copies), ("CORE", core_copies))


def open_access_copy(candidate, blocked, *, book=False):
    """The first readable free copy of a work whose own address refused the download or held only a scan, with the
    service that named it; else that failure. Each service is asked only when the ones before had no readable copy."""
    doi, tried = work_doi(candidate), {candidate.url}
    for service, copies in COPY_SERVICES:
        for url in copies(candidate, doi)[:3]:
            if url in tried:
                continue
            tried.add(url)
            try:
                raw, content_type, final_url = download(url)
                return (raw, content_type, final_url), extract(raw, content_type, url, **({"book": True} if book else {})), service
            except AppError:
                continue
    raise blocked


# Open-access book archives (prompts/open_archives.txt sends explain tasks to OAPEN and DOAB) and book paths.
BOOK_HOSTS = ("oapen.org", "doabooks.org", "gutenberg.org", "archive.org", "openedition.org", "openbookpublishers.com")
BOOK_PATH = re.compile(r"/(?:books?|monographs?)/", re.I)


def book_candidate(candidate) -> bool:
    """A candidate read with the book limits (pdf_text ``--book``): a task's primary work, which is often a whole
    book, or an address in a book archive. A 300-page article limit refused the very OAPEN and DOAB books the search
    was sent to, while the same file uploaded as a provided work passed (2026-10-02)."""
    if candidate.source_type == "primary_work":
        return True
    try:
        parts = urllib.parse.urlsplit(candidate.url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in BOOK_HOSTS) or bool(BOOK_PATH.search(parts.path))


def scanned(exc):
    """A PDF without a text layer: another copy of the same work may have one."""
    return getattr(exc, "code", "") == "source_unreadable" and "pages_total" in (getattr(exc, "details", None) or {})


def import_failure(address, exc):
    """One failure row of the source index: the address, the reason shown to the user and a stable code
    (``source_unreadable``, ``source_download_failed``, ...) that a later step can select on."""
    return {"source": address, "reason": str(exc), "code": getattr(exc, "code", "import_failed")}


def load_library(root: Path, run_id: str | None) -> dict:
    """The documents an earlier research run stored, by canonical address: a new run's starting library
    (2026-09-30, the user's choice for the rebuilt series). A document whose raw file changed, or whose text was
    extracted by an older parser, is left out and fetched again if chosen; user material never joins."""
    library = {}
    if not run_id:
        return library
    folder = root / "sources/processed" / run_id
    for path in sorted(folder.glob("src_*.json")):
        try:
            document = SourceDocument.model_validate_json(path.read_text(encoding="utf-8"))
            raw = root / document.raw_path
            if (not document.url or document.extraction_version != EXTRACTION_VERSION or not raw.is_file()
                    or file_hash(raw) != document.raw_hash):
                continue
        except (OSError, ValueError):
            continue
        for url in (document.url, document.final_url):
            if url:
                library[canonical_url(url)] = document
    return library


def library_view(library, limit=200):
    """What the discovery sees of the library: one line per document."""
    rows, seen = [], set()
    for document in library.values():
        if document.id in seen:
            continue
        seen.add(document.id)
        rows.append({"title": document.title, "url": document.final_url or document.url,
                     **({"type": document.source_type} if document.source_type != "unknown" else {}),
                     **({"published": document.published_date} if document.published_date else {})})
    return rows[:limit]


def adopt_from_library(document, candidate, root: Path, run_id: str):
    """Copy a library document into this run instead of downloading it again; a type the new search gave wins."""
    old_raw = root / document.raw_path
    raw_path = root / "sources/raw" / run_id / f"{document.id}{old_raw.suffix}"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if not raw_path.exists():
        raw_path.write_bytes(old_raw.read_bytes())
    adopted = document.model_copy(update={
        "raw_path": raw_path.relative_to(root).as_posix(), "imported_at": now(),
        "source_type": candidate.source_type if candidate.source_type != "unknown" else document.source_type})
    processed = root / "sources/processed" / run_id / f"{document.id}.json"
    write_json(processed, adopted.model_dump(mode="json"))
    return adopted, processed


def import_source(candidate: SourceCandidate, root: Path, run_id: str, *, local: Path | None = None,
                  downloaded: tuple[bytes, str, str] | None = None, library: dict | None = None,
                  citation: str = "") -> tuple[SourceDocument, Path]:
    """``citation``: a local copy of a published work the editor provided (provided_works); it is read with the
    book limits and counts as that work, not as the editor's notes. A primary work or a book from an open archive
    found on the web is read with the book limits too (book_candidate)."""
    if library and local is None and downloaded is None:
        stored = library.get(canonical_url(candidate.url))
        if stored is not None:
            return adopt_from_library(stored, candidate, root, run_id)
    address = str(local.resolve()) if local else canonical_url(candidate.url)
    source_id = "src_" + hashlib.sha256(address.encode()).hexdigest()[:16]
    book = bool(citation) or (not local and book_candidate(candidate))
    extracted = None
    if downloaded is not None:
        raw, content_type, final_url = downloaded
    elif local:
        if local.stat().st_size > (MAX_WORK_BYTES if citation else MAX_BYTES):
            raise AppError("Lokale Quelle überschreitet die Größengrenze.", code="source_too_large")
        raw, content_type, final_url = local.read_bytes(), "", ""
    else:
        try:
            raw, content_type, final_url = download(address)
        except AppError as exc:
            if not access_blocked(exc):
                raise
            # The same work from a free repository; it keeps the address it was found under as its identity.
            (raw, content_type, final_url), extracted, service = open_access_copy(candidate, exc, book=book)
    copied = extracted is not None
    if not copied:
        try:
            extracted = extract(raw, content_type, address, **({"book": True} if book else {}))
        except AppError as exc:
            # A bot check that answered with a page of its own refused the download just the same, and a scan
            # without a text layer may exist elsewhere with one.
            if local or not (access_blocked(exc) or scanned(exc)):
                raise
            (raw, content_type, final_url), extracted, service = open_access_copy(candidate, exc, book=book)
            copied = True
    kind, suffix, metadata, sections = extracted
    copy_note = f"Open-access copy of the same work found via {service}: {final_url}. " if copied else ""
    raw_path = root / "sources/raw" / run_id / f"{source_id}{suffix}"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    pending = raw_path.with_suffix(suffix + ".pending")
    pending.write_bytes(raw)
    pending.replace(raw_path)
    document = SourceDocument(
        # A document's own title can be blank, as in a PDF whose title field holds one space; the candidate always has one.
        # A provided work is named by the editor's citation: a scan's own title and creation date are the scanner's.
        id=source_id, extraction_version=EXTRACTION_VERSION, type=kind,
        title=citation or clean(metadata.get("title") or "") or candidate.title,
        authors=[] if citation else metadata.get("authors") or candidate.authors,
        published_date=candidate.published_date if citation else metadata.get("published_date") or candidate.published_date,
        date_basis=("citation" if citation and candidate.published_date else "unknown" if citation
                    else "document" if metadata.get("published_date") else "search_result" if candidate.published_date else "unknown"),
        source_type=candidate.source_type, citation=citation,
        imported_at=now(), url=candidate.url if not local else "", final_url=final_url,
        language=metadata.get("language", "unknown"),
        reliability_note=(f"Copy of a published work provided by the editor (library or purchase): {citation}. "
                          if citation else "User-supplied local material; provenance and factual claims have not been "
                          "independently verified. " if local else copy_note + "Search selection (not independently certified): ")
                         + candidate.rationale,
        uncertainties=["Publication metadata may come from search results; verify bibliographic details.",
                       "Automatic text extraction can omit images, tables and mathematical notation."],
        raw_path=raw_path.relative_to(root).as_posix(), raw_hash=file_hash(raw_path),
        text_hash=hashlib.sha256("\n".join(s.text for s in sections).encode()).hexdigest(), sections=sections,
        extraction_coverage=metadata.get("extraction_coverage"),
    )
    processed = root / "sources/processed" / run_id / f"{source_id}.json"
    write_json(processed, document.model_dump(mode="json"))
    return document, processed
