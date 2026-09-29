"""Jev, TypeSafe's decision model on OpenRouter, as a second finder for the gap probe.

The evaluation of 2026-09-29 (``evals/jev_decisions``) showed where it helps and where it does not. It separates
the sections a research cites from random ones (AUC 0.95 to 0.975), also for German questions against English
sources, where the term probe finds nothing. It does not judge whether a sentence stays faithful to a finding
(AUC 0.57 to 0.64, below always saying yes). So it only proposes candidates: a section it names must still be read
by the text model before a gap may stand, exactly like a term hit.

The scan asks one yes/no question per gap and section, eight gaps to a request, and keeps every answer in a
JSON-lines cache, so an interrupted scan resumes without asking again. The key stays in memory and in the
Authorization header; redirects are refused.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from pydantic import SecretStr

from .errors import AppError
from .openrouter import NoRedirect

JEV_MODEL = "typesafe/jev-1.13"
# The path that answered on 2026-09-29; the reference showed /api/v1/api/alpha/decisions, which returned 404.
DECISIONS_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
JEV_PROBE_VERSION = "jev_probe.v1"
BOOL_TYPE = "noul"  # the documented name of the yes/no question type
THRESHOLD = 0.3  # found 75 to 89 percent of the cited sections and 3 to 7 percent of random ones in the evaluation
JEV_HITS = 5  # proposals per gap on top of the term hits, the ones the reader must read
QUESTIONS_PER_REQUEST = 8
WORKERS = 8
MIN_SECTION_CHARS = 200  # headings and captions carry no answer
SECTION_CHARS = 6000
ATTEMPTS = 4


class JevClient:
    def __init__(self, api_key=None, *, timeout=120):
        self._key = SecretStr((api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")).strip())
        self.timeout = timeout

    def require_key(self):
        if not self._key.get_secret_value():
            raise AppError("Für die Jev-Lückenprobe den OpenRouter-Key im Studio hinterlegen oder OPENROUTER_API_KEY setzen.",
                           code="openrouter_key_required", status="blocked")

    def decide(self, state, questions):
        """The answers to ``questions`` about ``state`` and the reported cost in USD. A rate limit, a server error
        or a broken connection is asked again after a pause, at most four times in all; anything else stops."""
        self.require_key()
        secret = self._key.get_secret_value()
        body = json.dumps({"model": JEV_MODEL, "state": state, "questions": questions}, ensure_ascii=False).encode("utf-8")
        for attempt in range(ATTEMPTS):
            request = Request(DECISIONS_ENDPOINT, data=body, method="POST", headers={
                "Authorization": "Bearer " + secret, "Content-Type": "application/json",
                "X-OpenRouter-Title": "Podcast Automate"})
            try:
                with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                    answer = json.loads(response.read(1024 * 1024))
                return answer["answers"], float((answer.get("usage") or {}).get("cost") or 0)
            except HTTPError as exc:
                code = exc.code
                try:
                    detail = exc.read(8192).decode("utf-8", "replace").lower()
                except (OSError, ValueError):
                    detail = ""
                exc.close()
                if code == 429 or code >= 500:
                    time.sleep(2 ** attempt * 2)
                    continue
                if code == 401:
                    raise AppError("OpenRouter hat den Key für die Jev-Lückenprobe abgelehnt.",
                                   code="openrouter_key_required", status="blocked") from None
                if code == 402:
                    raise AppError("OpenRouter-Guthaben für die Jev-Lückenprobe erschöpft.",
                                   code="openrouter_credits", status="blocked") from None
                if code == 404 and ("zdr" in detail or "data policy" in detail or "privacy" in detail):
                    raise AppError("Die Datenschutz-Einstellung deines OpenRouter-Kontos schließt Jev aus. Unter "
                                   "openrouter.ai/settings/privacy freigeben oder die Jev-Lückenprobe ausschalten.",
                                   code="openrouter_privacy", status="blocked") from None
                raise AppError(f"Jev hat die Anfrage abgewiesen (HTTP {code}).", code="jev_unavailable",
                               status="blocked") from None
            except (TimeoutError, URLError, OSError, HTTPException, ValueError, KeyError):
                time.sleep(2 ** attempt * 2)
        raise AppError("Jev antwortet nicht; die Lückenprobe setzt beim Fortsetzen fort, wo sie stand.",
                       code="jev_unavailable", status="blocked")


def gap_question(text):
    return {"type": BOOL_TYPE,
            "instructions": "Does this source passage contain information that would fill this research gap: " + text,
            "criteria": {"true": "The passage states facts, results, definitions or arguments about what the gap "
                                 "says is missing.",
                         "false": "The passage is unrelated, mentions the topic only in passing, or is navigation, "
                                  "a reference list or boilerplate."}}


def candidate_sections(index):
    """Every section that can hold an answer, as (reference, title, text): long enough and not a reference list."""
    from .research_reader import is_reference_list
    rows = []
    for source in index.sources:
        for section in source.sections:
            reference = f"{source.id}#{section.id}"
            if len(section.text) >= MIN_SECTION_CHARS and not is_reference_list(section.text):
                rows.append((reference, source.title, section.text))
    return rows


def load_scores(cache):
    scores = {}
    if cache.exists():
        for line in cache.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                for gap, probability in row["gaps"].items():
                    scores[(row["reference"], gap)] = probability
    return scores


def scan(client, index, gaps, cache, *, progress=None):
    """Jev's probability for every gap in ``gaps`` (id -> text) and every candidate section of ``index``.

    Returns ``({gap_id: [(reference, title, probability, preview)]}, summary)``, sections at or above the threshold
    by falling probability. Answers already in ``cache`` are not asked again."""
    client.require_key()
    sections = candidate_sections(index)
    scores = load_scores(cache)
    pending = []
    for reference, title, text in sections:
        open_gaps = [gid for gid in gaps if (reference, gid) not in scores]
        for start in range(0, len(open_gaps), QUESTIONS_PER_REQUEST):
            pending.append((reference, title, text, open_gaps[start:start + QUESTIONS_PER_REQUEST]))
    lock, spent, done = threading.Lock(), [0.0], [0]
    cache.parent.mkdir(parents=True, exist_ok=True)

    def ask(item):
        reference, title, text, batch = item
        questions = {f"g{n}": gap_question(gaps[gid]) for n, gid in enumerate(batch)}
        answers, cost = client.decide(f"Source: {title}\n\n{text[:SECTION_CHARS]}", questions)
        row = {"reference": reference, "gaps": {gid: float((answers.get(f"g{n}") or {}).get(BOOL_TYPE) or 0)
                                                for n, gid in enumerate(batch)}}
        with lock:
            with cache.open("a", encoding="utf-8") as sink:
                sink.write(json.dumps(row) + "\n")
            scores.update({(reference, gid): p for gid, p in row["gaps"].items()})
            spent[0] += cost
            done[0] += 1
            if progress:
                progress(done[0], len(pending))

    if pending:
        with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="jev") as pool:
            for _ in pool.map(ask, pending):
                pass
    titles = {reference: (title, text) for reference, title, text in sections}
    found = {gid: sorted(((ref, titles[ref][0], p, titles[ref][1][:400]) for (ref, g), p in scores.items()
                          if g == gid and p >= THRESHOLD and ref in titles), key=lambda row: (-row[2], row[0]))
             for gid in gaps}
    summary = {"model": JEV_MODEL, "version": JEV_PROBE_VERSION, "threshold": THRESHOLD, "sections": len(sections),
               "requests": len(pending), "cost_usd": round(spent[0], 6)}
    return found, summary
