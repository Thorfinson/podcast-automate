"""Read-only, source-index-backed tools. No arbitrary paths, shell or model tools."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from .errors import AppError
from .research_retrieval import terms


def compact(text):
    return "".join(terms(text))


def source_catalog(index):
    return [{"id": s.id, "title": s.title, "url": s.final_url, "sections": len(s.sections),
             "extraction_coverage": s.extraction_coverage.model_dump() if s.extraction_coverage else None,
             "role": "retrieved_source" if s.url and s.final_url else "user_material"} for s in index.sources]


class SourceReader:
    def __init__(self, index):
        self.index = index
        self.sources = {s.id: s for s in index.sources}
        self.entries = [(source, n, section, Counter(terms(section.text)))
                        for source in index.sources for n, section in enumerate(source.sections)]
        self.lookup = {f"{source.id}#{section.id}": (source, n, section) for source, n, section, _ in self.entries}
        self.compacted = {ref: compact(section.text) for ref, (_, _, section) in self.lookup.items()}
        self.title_words = {s.id: set(terms(s.title)) for s in index.sources}
        self.frequency = Counter(word for *_, counts in self.entries for word in counts)
        self.average = sum(sum(c.values()) for *_, c in self.entries) / max(1, len(self.entries)) or 1

    def search(self, query, *, key_terms=(), source_id="", offset=0, include_notes=False, limit=12):
        if source_id and source_id not in self.sources:
            raise AppError("Unbekannte Quelle beim Nachlesen.", code="invalid_reader_request", status="blocked")
        # Web operators and URLs are not useful content terms within a document.
        query = re.sub(r"(?:https?://|site:)\S+", " ", query)
        words = set(terms(query))
        prefixes = {w[:6] for w in words if len(w) >= 7 and w not in self.frequency}
        words.update(w for w in self.frequency if len(w) >= 6 and w[:6] in prefixes)
        phrases = [value for term in key_terms if (value := compact(term))]
        ranked = []
        for source, position, section, counts in self.entries:
            if source_id and source.id != source_id:
                continue
            if not include_notes and not (source.url and source.final_url):
                continue
            matched = words & counts.keys()
            phrase_hits = sum(term in self.compacted[f"{source.id}#{section.id}"] for term in phrases)
            if not matched and not phrase_hits:
                continue
            length = sum(counts.values())
            score = sum(math.log(1 + (len(self.entries) - self.frequency[w] + .5) / (self.frequency[w] + .5)) *
                        counts[w] * 2.2 / (counts[w] + 1.2 * (.25 + .75 * length / self.average)) for w in matched)
            # Whole-document names are selectors, not evidence that a paragraph answers the question.
            title_words = self.title_words[source.id]
            content_hits = len(matched - title_words)
            links = len(re.findall(r"https?://", section.text))
            reference_list = links >= 3 or bool(re.match(r"\s*(references|bibliography|literaturverzeichnis)\b", section.text, re.I))
            ranked.append((not reference_list, phrase_hits, content_hits, score, source, position, section))
        ranked.sort(key=lambda row: (-row[0], -row[1], -row[2], -row[3], row[4].id, row[5]))
        # Show alternatives across sources before additional passages from one source.
        # Pagination retains ALL candidates, so a reader can examine rank 27 and beyond.
        primary, overflow, counts = [], [], Counter()
        for row in ranked:
            sid = row[4].id
            (primary if counts[sid] < 3 else overflow).append(row)
            counts[sid] += 1
        ranked = primary + overflow
        selected = ranked[offset:offset + limit]
        return {"query": query, "source_id": source_id, "offset": offset, "total": len(ranked),
                "next_offset": offset + len(selected) if offset + len(selected) < len(ranked) else None,
                "candidates": [{"reference": f"{s.id}#{sec.id}", "title": s.title, "page": sec.page,
                                "role": "retrieved_source" if s.url and s.final_url else "user_material",
                                "key_term_matches": phrase_hits, "reference_list": not substantive,
                                "preview": sec.text[:1000]} for substantive, phrase_hits, _, _, s, _, sec in selected]}

    def read(self, windows, *, max_chars=36_000):
        chosen, deferred, used = {}, [], 0
        # Read the requested passage before its neighbors. A large earlier window
        # must not silently displace the actual target of a later request.
        ordered = []
        for window in windows:
            if window.reference not in self.lookup:
                raise AppError("Angeforderter Textabschnitt existiert nicht.", code="invalid_reader_request", status="blocked")
            source, position, section = self.lookup[window.reference]
            ordered.append((source, section))
        for window in windows:
            source, position, _ = self.lookup[window.reference]
            ordered.extend((source, source.sections[n]) for n in range(max(0, position-window.before),
                           min(len(source.sections), position+window.after+1)) if n != position)
        for source, section in ordered:
            ref = f"{source.id}#{section.id}"
            if ref in chosen:
                continue
            if used + len(section.text) > max_chars:
                deferred.append(ref)
                continue
            chosen[ref] = (source, section)
            used += len(section.text)
        by_source = defaultdict(list)
        for ref, (source, section) in chosen.items():
            by_source[source.id].append({"reference": ref, "text": section.text, "page": section.page})
        context = [{"source_id": sid, "title": self.sources[sid].title, "url": self.sources[sid].final_url,
                    "text_hash": self.sources[sid].text_hash,
                    "extraction_coverage": self.sources[sid].extraction_coverage.model_dump() if self.sources[sid].extraction_coverage else None,
                    "reliability_note": self.sources[sid].reliability_note, "uncertainties": self.sources[sid].uncertainties,
                    "total_sections": len(self.sources[sid].sections), "sections": sections}
                   for sid, sections in by_source.items()]
        return {"context": context, "deferred": list(dict.fromkeys(deferred)), "characters": used}
