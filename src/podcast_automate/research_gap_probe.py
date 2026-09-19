"""Ask the stored corpus whether a declared gap is really missing from it.

A gap is a sentence a model wrote about what it could not find. Nothing checked that claim
against the sections already retrieved, and the September 2026 audit found one gap whose
answer stood verbatim in a stored section.

The probe is the lexical corpus search, so it costs no model call. A term-overlap search
over hundreds of sections matches almost anything, so a hit is never treated as proof that
the gap is false: it is a section somebody has to read. The blocking state is therefore
``hits_unread``. A reader either resolves the gap with a referenced finding or confirms it
after reading, and that confirmation is recorded.

The probe compares words, so a German gap sentence finds nothing in an English corpus. A
coverage row therefore carries ``gap_terms``: search words in the sources' language that a
section answering the gap would contain. They count as key terms beside the sentence's own.
"""
from __future__ import annotations

from .research_retrieval import terms
from .storage import digest

MIN_KEY_TERMS = 2
# ``hits_unowned`` exists only in the script lane: the hits sit in sources no episode uses,
# so no reader can be asked to read them there. Such a row is reported, never blocking.
STATUSES = ("no_hits", "hits_unread", "hits_unowned", "hits_read_confirmed", "resolved")


def gap_id(text: str) -> str:
    """The stable key of a gap sentence, shared by every lane that collects gaps."""
    return "gap_" + digest(text)[:12]


def key_terms(text: str, *, maximum: int = 12) -> list[str]:
    """The distinctive words of a gap sentence, longest first, in a stable order."""
    ordered = sorted(dict.fromkeys(terms(text)), key=lambda word: (-len(word), word))
    return ordered[:maximum]


def coverage_terms(dossier) -> dict[str, list[str]]:
    """``gap_terms`` of every coverage row that declares a gap, keyed like the gaps themselves."""
    return {gap_id(row.gap): list(row.gap_terms) for row in dossier.coverage if row.gap and row.gap_terms}


def hit_sources(row) -> set[str]:
    return {hit["reference"].split("#")[0] for hit in row["hits"]}


def probe_gap(reader, gap_id: str, text: str, *, gap_terms=(), limit: int = 5) -> dict:
    """One gap against the corpus; a section counts only with two distinct whole-word key terms.

    ``gap_terms`` come first in the key-term list: they are the words in the corpus's language.
    The threshold counts exact tokens of a section, not substrings, so "rule" never matches
    "overruled" and "load" never matches "download".
    """
    supplied = list(dict.fromkeys(token for term in gap_terms for token in terms(term)))
    words = list(dict.fromkeys([*supplied, *key_terms(text)]))
    query = " ".join([text, *gap_terms])
    result = reader.search(query, key_terms=words, limit=max(limit * 4, 20))
    hits = []
    for row in result["candidates"]:
        if row["reference_list"]:
            continue
        matched = reader.exact_matches(row["reference"], words)
        if matched >= MIN_KEY_TERMS:
            hits.append({"reference": row["reference"], "title": row["title"],
                         "key_term_matches": matched, "preview": row["preview"][:400]})
    hits = hits[:limit]
    return {"gap_id": gap_id, "text": text, "key_terms": words, "hits": hits,
            "status": "hits_unread" if hits else "no_hits"}


def probe(index, gaps: dict[str, str], *, gap_terms=None, limit: int = 5) -> list[dict]:
    """Probe every gap of a run. ``index`` is a ``SourceIndex``; no model call is made.

    ``gap_terms`` maps a gap id to its corpus-language search words; a gap without an entry,
    such as one from a dossier written before the field existed, is probed by its text alone.
    """
    from .research_reader import SourceReader
    reader = SourceReader(index)
    supplied = gap_terms or {}
    return [probe_gap(reader, gid, text, gap_terms=supplied.get(gid, ()), limit=limit)
            for gid, text in gaps.items()]


def settle(row, *, read_refs=(), resolved=False) -> dict:
    """Update one probe row after a reader worked on it.

    ``resolved`` means the gap is answered and no longer a gap. Otherwise the gap stands,
    and whether it stands honestly depends on whether its hits were actually read.
    """
    if resolved:
        return {**row, "status": "resolved"}
    if not row["hits"]:
        return {**row, "status": "no_hits"}
    unread = [hit["reference"] for hit in row["hits"] if hit["reference"] not in set(read_refs)]
    return {**row, "status": "hits_unread" if unread else "hits_read_confirmed",
            "unread_references": unread}


def unread(rows) -> list[dict]:
    return [row for row in rows if row["status"] == "hits_unread"]


def statuses(rows) -> list[dict]:
    """The probe without its previews, small enough for a review payload."""
    return [{"gap_id": row["gap_id"], "text": row["text"], "status": row["status"],
             "references": [hit["reference"] for hit in row["hits"]]} for row in rows]


def suffix(row) -> str:
    """The sentence appended to a gap in ``open_questions.md``."""
    if row["status"] == "no_hits":
        return "Korpusprobe: kein passender Abschnitt in den gespeicherten Quellen."
    if row["status"] == "resolved":
        return "Korpusprobe: in den gespeicherten Quellen beantwortet."
    references = ", ".join(hit["reference"] for hit in row["hits"])
    if row["status"] == "hits_read_confirmed":
        return f"Korpusprobe: gelesen und bestätigt trotz Treffern in {references}."
    if row["status"] == "hits_unowned":
        return f"Korpusprobe: Treffer in {references}, die keine Folge nutzt."
    return f"Korpusprobe: ungelesene Treffer in {references}."
