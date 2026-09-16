"""Question-specific retrieval from saved sections, without network or model calls."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter


STOP_WORDS = set("""the and for with from that this these those into what which how why are was were
has have does can its not all any also only full text original paper source sources research evidence
der die das den dem des ein eine einer eines einem einen und oder mit von zur zum auf aus bei für
wie was welche welcher welches warum sind ist wird werden wurde wurden sich noch auch als durch
nicht einer diese dieser dieses diesen einem anhand bitte belege quellen abschnitt abschnitte""".split())


def terms(text):
    text = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return [word.rstrip("s") if len(word) > 4 else word
            for word in re.findall(r"[^\W\d_]{3,}", text) if word not in STOP_WORDS]


def gap_queries(report):
    queries = list(report.get("search_queries", []))
    issues = report.get("source_review", {}).get("issues", [])
    for issue in issues:
        queries.extend(issue.get("search_queries", []))
    if issues and report.get("assessment_status") == "pending_after_source_review":
        # Finish current objections before rechecking the original brief. Stale
        # scores must not become a second, accumulating research task list.
        queries.extend(issue["reason"] for issue in issues)
    else:
        for row in report.get("requirements", []):
            if not row["passed"]:
                queries.extend(row.get("search_queries", []))
                queries.extend(row.get("missing", []))
        queries.extend(report.get("blocking_gaps", []))
    return list(dict.fromkeys(q for q in queries if terms(q)))


def references(context):
    return {section["reference"] for source in context for section in source["sections"]}


def select_context(context, refs):
    return [{**source, "sections": sections} for source in context
            if (sections := [s for s in source["sections"] if s["reference"] in refs])]


def merge_context(*contexts):
    """Keep every previously presented section, including anchors of untouched findings."""
    combined = {}
    for context in contexts:
        for source in context:
            if source["source_id"] not in combined:
                combined[source["source_id"]] = {**source, "sections": []}
            row = combined[source["source_id"]]
            seen = {s["reference"] for s in row["sections"]}
            row["sections"].extend(s for s in source["sections"] if s["reference"] not in seen)
    return list(combined.values())


def retrieve_saved(index, queries, seen_context, *, max_chars=40_000, hits_per_query=2):
    """BM25 per question, with adjacent sections; a hit is a lead, never proof of closure.

    Select the best hits BEFORE removing seen passages. Repeating the same gap must
    not walk down an ever less relevant ranking and postpone necessary web search.
    """
    entries = [(source, position, section, Counter(terms(section.text)))
               for source in index.sources for position, section in enumerate(source.sections)]
    if not entries:
        return [], []
    frequencies = Counter(word for _, _, _, counts in entries for word in counts)
    prefixes = {}
    for word in frequencies:
        if len(word) >= 6:
            prefixes.setdefault(word[:6], set()).add(word)
    average = sum(sum(counts.values()) for *_, counts in entries) / len(entries) or 1
    seen = references(seen_context)
    chosen, used, decisions = set(), 0, []
    # Reserve direct hits before adding neighbors, so one large document cannot
    # consume the entire allowance on context around the first query.
    neighbors = []
    for query in dict.fromkeys(queries):
        words = set(terms(query))
        # German inflections of English technical terms (e.g. "singulären",
        # "synergischen") must still find the original English terminology.
        # This is lexical expansion, not a claim of general cross-language retrieval.
        words.update(candidate for word in list(words) if word not in frequencies and len(word) >= 7
                     for candidate in prefixes.get(word[:6], ()) if abs(len(candidate) - len(word)) <= 6)
        ranked = []
        for source, position, section, counts in entries:
            matched = words & counts.keys()
            if len(matched) < min(2, len(words)) or not matched:
                continue
            length = sum(counts.values())
            score = sum(math.log(1 + (len(entries) - frequencies[w] + .5) / (frequencies[w] + .5)) *
                        counts[w] * 2.2 / (counts[w] + 1.2 * (.25 + .75 * length / average))
                        for w in matched)
            ranked.append((score, source, position, section))
        ranked.sort(key=lambda item: (-item[0], item[1].id, item[2]))
        hits, added = [], []
        for _, source, position, section in ranked[:hits_per_query]:
            ref = f"{source.id}#{section.id}"
            hits.append(ref)
            if ref not in seen and ref not in chosen and used + len(section.text) <= max_chars:
                chosen.add(ref)
                added.append(ref)
                used += len(section.text)
            # Neighbors are useful even when the best matching section was seen.
            for offset in (-1, 1):
                if 0 <= position + offset < len(source.sections):
                    neighbors.append((source, source.sections[position + offset]))
        decisions.append({"query": query, "best_matches": hits, "new_direct_matches": added})
    for source, section in neighbors:
        ref = f"{source.id}#{section.id}"
        if ref not in seen and ref not in chosen and used + len(section.text) <= max_chars:
            chosen.add(ref)
            used += len(section.text)
    context = []
    for source in index.sources:
        sections = [{"reference": f"{source.id}#{s.id}", "text": s.text, "page": s.page}
                    for s in source.sections if f"{source.id}#{s.id}" in chosen]
        if sections:
            context.append({"source_id": source.id, "title": source.title, "url": source.final_url,
                            "reliability_note": source.reliability_note, "uncertainties": source.uncertainties,
                            "total_sections": len(source.sections), "sections": sections})
    return context, decisions
