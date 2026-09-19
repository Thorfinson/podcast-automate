"""Compare what the engine was given with what a recogniser hears in the produced audio.

The pipeline can prove that a segment file exists, that its hash matches and that the text sent
matches the approved script. It cannot prove that the words are in the audio: a synthesis can drop
a clause, swallow a number or mangle a name, and every hash still agrees.

This module does the deterministic half of that check. It takes per-segment transcripts from some
recogniser, aligns them word by word against the spoken text with ``difflib``, and reports a word
error rate with the missing and inserted words. It never blocks: a recogniser makes its own
mistakes, especially on the technical names this check exists for, so its findings are a reason to
listen to a segment, not a verdict about it. ``SPEC.md`` requires that separation.

The recogniser itself is not part of this module or of the test suites. ``transcribe`` is any
callable that maps a WAV path to text; ``asr_worker.py`` would supply one from a local model.
"""
from __future__ import annotations

import difflib
import re
import unicodedata

VERSION = "transcription_check.v1"
WORD = re.compile(r"[^\W_]+", re.UNICODE)
FLAG_WORD_ERROR_RATE = 0.2


def normalise(text: str) -> list[str]:
    """Words without case, accents or punctuation; a recogniser writes none of those reliably."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", (text or "").casefold())
                     if not unicodedata.combining(c))
    return WORD.findall(folded)


def compare(spoken: str, heard: str) -> dict:
    """Word error rate plus the words each side has that the other does not, in order."""
    expected, actual = normalise(spoken), normalise(heard)
    matcher = difflib.SequenceMatcher(a=expected, b=actual, autojunk=False)
    missing, inserted, edits = [], [], 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        missing.extend(expected[i1:i2])
        inserted.extend(actual[j1:j2])
        edits += max(i2 - i1, j2 - j1)
    return {"expected_words": len(expected), "heard_words": len(actual),
            "word_error_rate": round(edits / len(expected), 4) if expected else (1.0 if actual else 0.0),
            "missing": missing, "inserted": inserted}


def check_segments(script, spoken_by_id: dict[str, str], transcripts: dict[str, str]) -> dict:
    """One row per segment, flagged above the word error rate the operator should listen to."""
    rows = []
    for segment in script.segments:
        heard = transcripts.get(segment.segment_id)
        spoken = spoken_by_id.get(segment.segment_id, segment.text)
        if heard is None:
            rows.append({"segment_id": segment.segment_id, "status": "not_transcribed",
                         "flagged": False, "word_error_rate": None, "missing": [], "inserted": []})
            continue
        result = compare(spoken, heard)
        rows.append({"segment_id": segment.segment_id, "status": "compared",
                     "flagged": result["word_error_rate"] > FLAG_WORD_ERROR_RATE, **result})
    flagged = [row for row in rows if row["flagged"]]
    return {"version": VERSION, "episode_id": script.episode_id,
            "threshold": FLAG_WORD_ERROR_RATE, "segments": rows,
            "flagged_segment_ids": [row["segment_id"] for row in flagged],
            "blocking": False,
            "note": ("Eine Spracherkennung macht eigene Fehler, gerade bei Fachnamen. "
                     "Markierte Abschnitte sind ein Grund zum Anhören, kein Urteil."),
            "human_listening_reviewed": False}


def transcribe_segments(paths: dict[str, object], transcribe) -> dict[str, str]:
    """Run a supplied recogniser over the cached segment audio; no model lives in this module."""
    return {segment_id: transcribe(path) for segment_id, path in paths.items()}
