"""What the speech engine hears, as a second field next to the reviewed script text.

A script says "DeepSeek-V3.2-Exp" and "H800" because that is what the sources say. A speech
engine reads those tokens by guessing. The operator therefore keeps a table of written forms
and their spoken forms, and every synthesis request carries both: ``text`` stays the reviewed
script, used for transcripts, hashes and approvals, and ``spoken_text`` is what the engine
gets. A spoken form never rewrites the script and never changes a script hash.

``report`` lists the tokens a reader should check before approving audio: multi-digit numbers,
all-caps abbreviations, mixed letter-and-digit version strings, and words with characters the
project's language does not use.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from .models import Contract, NonEmpty

# Boundaries, shared by apply() and report(): a hyphen, an en dash or a slash separates tokens
# ("KL-Abweichung" holds "KL"), while a dot between word characters does not ("V3.2" is one
# token, "1.000.000" is one number). Only a trailing dot ends a sentence.
TOKEN = re.compile(r"[^\s,;:!?()\[\]\"„“”»«…\-–/]+")
BOUNDARY_BEFORE = r"(?<!\w)(?<!\w\.)"
BOUNDARY_AFTER = r"(?!\w)(?!\.\w)"
CONTRACTION = re.compile(r"[^\W\d_][’'][^\W\d_]")
ALPHABETS = {"de": set("abcdefghijklmnopqrstuvwxyzäöüßABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÜ"),
             "en": set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")}


class SpokenForm(Contract):
    written: NonEmpty
    spoken: NonEmpty


class SpokenForms(Contract):
    schema_version: Literal["1.0"] = "1.0"
    entries: list[SpokenForm] = Field(default_factory=list)


def load_forms(root):
    path = root / "studio/spoken_forms.json"
    return (SpokenForms.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file()
            else SpokenForms())


def _pattern(table):
    """One alternation, longest written form first, so no replacement feeds another."""
    written = sorted({entry.written for entry in table.entries}, key=len, reverse=True)
    if not written:
        return None
    return re.compile(BOUNDARY_BEFORE + "(" + "|".join(re.escape(value) for value in written) + ")" + BOUNDARY_AFTER)


def apply(text: str, table: SpokenForms) -> str:
    """Case-sensitive replacement at token boundaries; the last entry for a form wins.

    A hyphen and an en dash are boundaries, so an entry ``KL`` reaches "KL-Abweichung"; a dot
    between word characters is not, so ``V3`` leaves "V3.2-Exp" alone and ``1.000`` leaves
    "1.000.000" alone. The single longest-first alternation never feeds one replacement into
    another.
    """
    expression = _pattern(table)
    if expression is None:
        return text
    spoken = {entry.written: entry.spoken for entry in table.entries}
    return expression.sub(lambda match: spoken[match.group(1)], text)


def applied(text: str, table: SpokenForms) -> dict[str, int]:
    expression = _pattern(table)
    if expression is None:
        return {}
    counts: dict[str, int] = {}
    for match in expression.finditer(text):
        counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


def spoken_text(segment, table, overrides=None) -> str:
    """An explicit per-segment override always wins over the table."""
    override = (overrides or {}).get(segment.segment_id)
    return override if override else apply(segment.text, table)


def report(script, table, *, language="de-DE", overrides=None) -> dict:
    """Pronunciation risks in the final spoken text, for a reader deciding about audio."""
    alphabet = ALPHABETS.get((language or "").split("-")[0].lower(), ALPHABETS["de"])
    categories = {"numbers": {}, "abbreviations": {}, "versions": {}, "foreign": {}}
    fired: dict[str, int] = {}
    for segment in script.segments:
        spoken = spoken_text(segment, table, overrides)
        for written, count in applied(segment.text, table).items():
            fired[written] = fired.get(written, 0) + count
        for raw in TOKEN.findall(spoken):
            token = raw.strip(".")
            if not token:
                continue
            letters = [c for c in token if c.isalpha()]
            digits = [c for c in token if c.isdigit()]
            if digits and letters:
                name = "versions"
            elif len(digits) >= 2:
                name = "numbers"
            elif len(token) >= 2 and token.isalpha() and token.isupper():
                # Tokens end at a hyphen, so "KL-Abweichung" lists "KL", the form an entry can address.
                name = "abbreviations"
            elif any(c.isalpha() and c not in alphabet for c in token) or CONTRACTION.search(token):
                # A letter the language does not use, or a contraction apostrophe as in "j'ai".
                name = "foreign"
            else:
                continue
            categories[name].setdefault(token, []).append(segment.segment_id)
    return {"schema_version": "1.0", "language": language,
            "entries": len(table.entries), "applied": fired,
            "overrides": sorted(overrides or {}),
            "flagged": {name: [{"token": token, "segment_ids": list(dict.fromkeys(ids)), "count": len(ids)}
                               for token, ids in sorted(rows.items())]
                        for name, rows in categories.items() if rows},
            "human_pronunciation_reviewed": False}
