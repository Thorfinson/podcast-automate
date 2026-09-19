"""Deterministic, non-blocking observations about a finished script.

Every other check in the script lane blocks after a bounded repair. Advisories are the
opposite: they count what the prompts ask the model to avoid, are stored with the episode
report and shown to the reader, and never stop a run. Nothing consumes them automatically;
they exist so a pattern that survives the prompts stays visible instead of unmeasured.

The heuristics are language-keyed. German carries the patterns; any other language gets an
empty list so nothing fires silently on a project the phrasing was never written for.
"""
from __future__ import annotations

import re

WORD = re.compile(r"\b[\w’-]+\b")
SENTENCE = re.compile(r"(?<=[.!?…])\s+")
COLD_OPEN_WORDS = 100
HEDGING_LIMIT = 2
DURATION_FACTOR = 1.2

# A definition reads as "<Begriff> ist …", "ein <Begriff> ist …", "<Begriff>, also …",
# "nennen wir <Begriff>" or "heißt <Begriff>". {term} is filled with the escaped term.
DEFINITION_PATTERNS = {
    "de": (
        r"\b{term}\b\s*(?:,[^,.;:!?]{{0,40}},)?\s+(?:ist|sind|war|waren)\b",
        r"\b{term}\b\s*,\s*also\b",
        r"\b{term}\b\s+(?:bedeutet|bezeichnet|meint|beschreibt)\b",
        r"\bnenn(?:en wir|t man|e ich)\b[^.;:!?]{{0,40}}?\b{term}\b",
        r"\bheiß(?:t|en)\b[^.;:!?]{{0,40}}?\b{term}\b",
        r"\bunter\s+(?:dem|der|einem|einer)?\s*{term}\b[^.;:!?]{{0,40}}?\bversteht man\b",
    ),
}

# A back-reference names the example the prompts told the writer to name ("in unserem
# Gedankenexperiment", "dieses Gedankenbeispiel"); only a fresh introduction is a hedge.
# Python lookbehinds are fixed-width, so every determiner gets its own.
_BACK_REFERENCE = ("unser", "unsere", "unserem", "unseren", "unserer", "unseres",
                   "dies", "diese", "diesem", "diesen", "dieser", "dieses", "das", "dem", "im")
_NOT_AFTER_DETERMINER = "".join(rf"(?<!\b{word} )" for word in _BACK_REFERENCE)
_EXAMPLE_NOUN = r"(?:Beispiel|Zahlen|Satz|Werte)\w*"
_MEASUREMENT_NOUN = r"(?:Modelllauf|Messung|Daten|Ergebnis|Wert|Lauf|Experiment)\w*"

# Reminders that an invented example is not measured. One per example is the rule the
# prompts ask for; the advisory counts how often the reminder is repeated instead. The
# list was calibrated against the September 2026 sample series and then narrowed so the
# ordinary verb ("2017 erfunden"), an everyday "keine echte Alternative" and the recommended
# back-reference to a named example no longer count.
HEDGING_PATTERNS = {
    "de": (
        _NOT_AFTER_DETERMINER + r"\bGedanken(?:beispiel|experiment|modell)\w*",
        r"\berfunden\w*\s+(?:\w+\s+){0,2}?" + _EXAMPLE_NOUN,
        r"\b" + _EXAMPLE_NOUN + r"\s+(?:\w+\s+){0,2}?erfunden\w*",
        r"\bhypothetisch\w*",
        r"\bschematisch\w*",
        r"\bBeispielzahlen\b",
        r"\bkein(?:e|en|em|er|es)?\s+(?:\w+\s+){0,2}?(?:gemessen|beobachtet|ausgelesen)\w*",
        r"\bkein(?:e|en|em|er|es)?\s+(?:\w+\s+){0,2}?(?:echt|real)\w*\s+" + _MEASUREMENT_NOUN,
        r"\bkeine?\s+konkrete\w*\s+(?:\w+\s+){0,3}?eines\s+(?:\w+\s+){0,2}?Modells\b",
        r"\bnicht\s+(?:\w+\s+){0,2}?(?:gemessen|beobachtet|ausgelesen)\w*",
    ),
}


def language_key(language: str) -> str:
    """``de-DE`` and ``de`` both select the German patterns; anything else selects none."""
    return (language or "").split("-")[0].lower()


def words(text: str) -> int:
    return len(WORD.findall(text))


def sentences(text: str) -> list[str]:
    return [part for part in SENTENCE.split(text.strip()) if part]


def humanised(concept_id: str) -> str:
    """Fallback spoken form for plans written before ``Concept.terms`` existed."""
    return concept_id.replace("_", " ").strip()


def established_terms(context) -> list[str]:
    """Spoken terms from reviewed prerequisite rows; outline-only rows carry none.

    ``prerequisite_context`` writes the list; rows from before that field existed are
    reconstructed from their concepts so an old work folder still reports honestly.
    """
    found = []
    for row in context or []:
        if not isinstance(row, dict):
            continue
        if row.get("established_terms") is not None:
            found.extend(row["established_terms"])
            continue
        for concept in (row.get("teaching_design") or {}).get("concepts", []):
            found.extend(concept.get("terms") or [humanised(concept["concept_id"])])
    return list(dict.fromkeys(term for term in (t.strip() for t in found) if term))


def _row(code, script, segment_ids, count, detail):
    return {"code": code, "episode_id": script.episode_id, "segment_ids": list(segment_ids),
            "count": count, "detail": detail}


def definition_sentences(script, terms, language) -> dict[str, list[str]]:
    """Per established term, the segments whose sentences define it again."""
    patterns = DEFINITION_PATTERNS.get(language_key(language), ())
    found = {}
    for term in terms:
        expressions = [re.compile(p.format(term=re.escape(term)), re.IGNORECASE) for p in patterns]
        hits = [segment.segment_id for segment in script.segments
                for sentence in sentences(segment.text) if any(e.search(sentence) for e in expressions)]
        if hits:
            found[term] = hits
    return found


def redefined_terms(script, terms, language) -> list[dict]:
    """A term an earlier episode established, defined again more than once here.

    One recall clause per term is what ``continuity.txt`` allows, so a single definition
    is not reported. ``definition_sentences`` exposes the untruncated counts.
    """
    return [_row("redefined_term", script, dict.fromkeys(hits), len(hits),
                 f"«{term}» wird in dieser Folge {len(hits)}-mal neu definiert, "
                 "obwohl der Begriff aus einer früheren Folge bekannt ist.")
            for term, hits in definition_sentences(script, terms, language).items() if len(hits) > 1]


def hedging_hits(script, language) -> list[tuple[str, str]]:
    """Every (segment_id, matched text) reminder, untruncated; the counting script reports these."""
    expressions = [re.compile(p, re.IGNORECASE) for p in HEDGING_PATTERNS.get(language_key(language), ())]
    return [(segment.segment_id, match.group(0)) for segment in script.segments
            for expression in expressions for match in expression.finditer(segment.text)]


def repeated_hedging(script, language) -> list[dict]:
    """Repeated reminders that an invented example is not a measurement."""
    hits = hedging_hits(script, language)
    if len(hits) <= HEDGING_LIMIT:
        return []
    return [_row("repeated_hedging", script, dict.fromkeys(sid for sid, _ in hits), len(hits),
                 f"{len(hits)} Hinweise darauf, dass ein Beispiel erfunden oder nicht gemessen ist; "
                 f"höchstens {HEDGING_LIMIT} sind vorgesehen.")]


def long_cold_open(script) -> list[dict]:
    first = script.segments[0]
    count = words(first.text)
    if count <= COLD_OPEN_WORDS:
        return []
    return [_row("long_cold_open", script, [first.segment_id], count,
                 f"Der erste gesprochene Abschnitt hat {count} Wörter; "
                 f"über {COLD_OPEN_WORDS} beginnt die Folge ohne Atempause.")]


def over_target_duration(script, entry, metrics) -> list[dict]:
    estimated = metrics["estimated_minutes"]
    limit = entry.target_minutes * DURATION_FACTOR
    if estimated <= limit:
        return []
    # Every advisory row counts in whole numbers; here the count is the estimate as a
    # percentage of the plan, so 132 reads as "132 percent of the target".
    percent = round(estimated / entry.target_minutes * 100)
    return [_row("over_target_duration", script, [], percent,
                 f"Geschätzte {estimated:g} Minuten gegenüber geplanten {entry.target_minutes:g}, "
                 f"also {percent} Prozent des Ziels; über {round(DURATION_FACTOR * 100)} Prozent "
                 "gilt die Folge als zu lang.")]


def advisories(script, entry, metrics, *, language, terms=()) -> list[dict]:
    """All advisory rows for one episode, in a stable order."""
    return [*redefined_terms(script, terms, language), *repeated_hedging(script, language),
            *long_cold_open(script), *over_target_duration(script, entry, metrics)]
