"""Web search through the Perplexity Search API, a tool the pipeline calls itself (D-151).

A text model that has no web tools of its own (OpenRouter), or any model when the user chooses it, plans the queries;
this module runs them and returns ranked results (title, address, snippet, dates); the model then picks its
candidates from those results only (provider_pool.AdapterPool.searched). Only the queries and filters leave the
computer here, never the task's prompt. The key travels in the ``Authorization`` header only, and no redirect is
followed, so it never reaches another server.

Request and response follow https://docs.perplexity.ai/api-reference/search-post, read on 2026-10-07: up to five
queries per request, a flat ``results`` list of ``title``, ``url``, ``snippet``, ``date`` and ``last_updated``.
"""
from __future__ import annotations

import json
import os
import re
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from pydantic import Field, SecretStr

from .errors import AppError
from .model_trace import redact
from .models import Contract, NonEmpty
from .openrouter import NoRedirect

ENDPOINT = "https://api.perplexity.ai/search"
ADAPTER_VERSION = "perplexity_search.v1"
MAX_QUERIES_PER_REQUEST = 5
MAX_RESULTS_PER_QUERY = 10
# Enough of a page to judge it, not to read it: the pipeline reads the chosen sources itself.
TOKENS_PER_PAGE = 300
TIMEOUT_SECONDS = 60
# 5 USD per 1,000 requests of the standard search, from Perplexity's pricing as third-party pages stated it on
# 2026-10-07; a multi-query request is billed once. Unverified against a bill.
USD_PER_REQUEST = 0.005
PRICE_READ_ON = "2026-10-07"
# Perplexity API keys start with "pplx-".
KEY_PATTERN = re.compile(r"pplx-[A-Za-z0-9_-]{12,}")


class SearchPlan(Contract):
    """The queries a text model plans for one search call (prompts/search_queries.txt)."""
    queries: list[NonEmpty] = Field(min_length=1, max_length=10)
    # ISO 639-1 codes of the languages the sources should be in; empty for any language.
    languages: list[str] = Field(max_length=5)


def failure(status: int) -> AppError:
    if status == 401 or status == 403:
        return AppError("Perplexity hat den Key abgelehnt. Den Perplexity-Key prüfen und neu eingeben.",
                        code="perplexity_authentication", status="blocked")
    if status == 402:
        return AppError("Das Perplexity-Konto hat kein Guthaben mehr. Guthaben aufladen, dann fortsetzen.",
                        code="perplexity_credits", status="waiting_for_quota", details={"provider": "perplexity"})
    if status == 429:
        return AppError("Perplexity hat die Suche wegen ihres Ratenlimits abgelehnt. Der Lauf wartet und setzt fort.",
                        code="perplexity_rate_limit", status="waiting_for_quota", details={"provider": "perplexity"})
    if 400 <= status < 500:
        return AppError("Perplexity hat die Suchanfrage als ungültig abgelehnt. Das ist ein Fehler der Studio-Anbindung.",
                        code="perplexity_request", status="blocked")
    return AppError("Perplexity war nicht erreichbar oder hat keine Treffer geliefert. Fortsetzen versucht es erneut.",
                    code="perplexity_failed")


def clean_result(item) -> dict | None:
    """One result as the pipeline keeps it, or None for one without a usable https address."""
    if not isinstance(item, dict) or not isinstance(item.get("url"), str):
        return None
    url = item["url"].strip()
    if not re.match(r"https?://", url) or len(url) > 2000:
        return None
    text = lambda key, limit: " ".join(str(item.get(key) or "").split())[:limit]  # noqa: E731
    return {"title": text("title", 300), "url": url, "snippet": text("snippet", 2000),
            "date": text("date", 40) or None, "last_updated": text("last_updated", 40) or None}


class PerplexitySearch:
    def __init__(self, api_key: str | None = None, *, opener=None):
        key = (api_key if api_key is not None else os.environ.get("PERPLEXITY_API_KEY", "")).strip()
        if key and any(not 33 <= ord(c) <= 126 for c in key):
            raise AppError("Der Perplexity-Key ist ungültig.", code="invalid_backend", status="blocked")
        self._key = SecretStr(key)
        self.opener = opener or build_opener(NoRedirect())

    def require_key(self):
        if not self._key.get_secret_value():
            raise AppError("Die Websuche über Perplexity braucht einen Perplexity-Key: in den Einstellungen eingeben oder "
                           "PERPLEXITY_API_KEY setzen.", code="perplexity_key_required", status="blocked")

    def search(self, queries, *, languages=(), domains=()) -> dict:
        """Run up to five queries in one request; returns ``{"results", "request_id", "usd", "elapsed_seconds"}``."""
        self.require_key()
        secret = self._key.get_secret_value()
        queries = [" ".join(str(q).split())[:400] for q in queries if str(q).strip()][:MAX_QUERIES_PER_REQUEST]
        if not queries:
            raise AppError("Keine Suchanfrage angegeben.", code="perplexity_request", status="blocked")
        if any(secret in query for query in queries):
            raise AppError("Ein API-Key darf nicht Teil einer Suchanfrage sein.", code="credential_in_prompt",
                           status="blocked")
        body = {"query": queries if len(queries) > 1 else queries[0], "max_results": MAX_RESULTS_PER_QUERY,
                "max_tokens_per_page": TOKENS_PER_PAGE}
        if languages:
            body["search_language_filter"] = list(languages)[:20]
        if domains:
            body["search_domain_filter"] = list(domains)[:20]
        request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                          headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
        start = time.monotonic()
        try:
            with self.opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(8 * 1024 * 1024)
        except HTTPError as exc:
            exc.close()
            raise failure(exc.code) from None
        except (TimeoutError, URLError, OSError, HTTPException):
            raise failure(0) from None
        if secret.encode() in raw:
            raise AppError("Die Perplexity-Antwort enthält den Key und wird nicht verwendet.",
                           code="credential_in_response", status="blocked")
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise failure(0) from None
        if not isinstance(envelope, dict) or not isinstance(envelope.get("results"), list):
            raise failure(0)
        seen, results = set(), []
        for item in envelope["results"]:
            row = clean_result(item)
            if row and row["url"] not in seen:
                seen.add(row["url"])
                results.append(row)
        request_id = envelope.get("id") if isinstance(envelope.get("id"), str) else None
        return {"queries": queries, "results": results, "request_id": redact(request_id) if request_id else None,
                "usd": USD_PER_REQUEST, "elapsed_seconds": round(time.monotonic() - start, 3)}
