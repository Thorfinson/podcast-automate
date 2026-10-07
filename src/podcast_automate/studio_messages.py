"""Stop messages as the Studio shows them: in the interface language where the Studio can say it, without CLI
commands, local paths or internal ids.

The pipeline raises one message for every reader: the command line, the log and, for a rejected
answer, the model itself, which is re-asked with that text. Only this Studio boundary rewrites a
message for a person at the browser, so no prompt, CLI text or stored record changes here.

German output is what it always was (D-152): English check sentences become their catalog text, other English
sentences one generic German sentence. In the English interface the catalog's English replaces a known check
sentence, any other English sentence stays as written, and a German pipeline message stays German; the result names
its ``message_language``, so the page shows it inline only where it matches the interface.
"""
from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

from .studio_text import Localized, text as catalog_text

REVIEW_DISAGREEMENT = "message.review_disagreement"

# English texts of pipeline checks, each to its catalog key. Most of them are also re-ask instructions for the model,
# so they stay English at their source and are translated only here.
ENGLISH = {
    "An unresolved review disagreement cannot trigger unanchored research.": REVIEW_DISAGREEMENT,
    "Unanchored review disagreement; no automatic new research.": REVIEW_DISAGREEMENT,
    "Review disagreement cannot trigger more research.": REVIEW_DISAGREEMENT,
    "Stored support review no longer passes.": "message.support_review_stale",
    "The verified prerequisites changed.": "message.prerequisites_changed",
    "Invalid research task prerequisites.": "message.prerequisites_invalid",
    "Research task prerequisites contain a cycle.": "message.prerequisites_cycle",
    "Search must record executed queries and counterevidence outcome.": "message.search_receipt",
    "Every existing objection needs an explicit closure check.": "message.objection_closure_check",
    "Failing support receipts require explicit corrective issues.": "message.failing_support_receipts",
    "Objection closure requires read source evidence.": "message.objection_closure_evidence",
    "A dossier objection requires an affected finding and closure condition.": "message.objection_anchor",
    "Every routed task needs a specific evidence or criterion anchor.": "message.routed_anchor",
    "Missing task-specific objection anchor.": "message.routed_anchor",
    "The closure condition of an existing objection changed.": "message.closure_condition_changed",
    "Synthesis edits must concern the editable findings.": "message.synthesis_edits",
    "Script review must check every segment's claim preservation exactly once.": "message.script_preservation_coverage",
    "Script preservation receipt is inconsistent with its segment.": "message.script_preservation_receipt",
    "Supplement semantic support check failed.": "message.supplement_support",
    "Codex app-server stream closed before the account data arrived.": "message.codex_stream_closed",
}
GENERIC_ENGLISH = "message.generic_english"
REJECTED = re.compile(r"^(?P<provider>[\w -]{1,40}?) answered, but the answer violates its output contract: "
                      r"(?P<defects>.*?)\.? Return the complete answer again with exactly these defects corrected\.\s*",
                      re.DOTALL)
# Command-line advice, rewritten into what the Studio offers. Applied in this order.
CLI = [
    (re.compile(r"mit ['„\"]?pla resume['“\"]? fortsetzen", re.IGNORECASE), "mit „Fortsetzen“ weitermachen"),
    (re.compile(r"pla resume( verwenden)?"), "„Fortsetzen“"),
    (re.compile(r"--api-key für verdeckte Eingabe oder OPENROUTER_API_KEY setzen"),
     "im Studio in den Einstellungen unter „OpenRouter-Key“ hinterlegen"),
    (re.compile(r"höherem --max-output-tokens oder geeignetem Modell"), "höherem Ausgabelimit oder einem geeigneten Modell"),
    (re.compile(r"Zuerst mit pla research ein"), "Zuerst auf der Seite Recherche ein"),
    (re.compile(r"mit --approve-audio"), "mit deiner Audio-Freigabe"),
    (re.compile(r"in project\.yaml unter runtime\.codex_executable den vollständigen Programmpfad eintragen"),
     "so installieren, dass der Befehl codex verfügbar ist, und das Studio neu starten"),
    (re.compile(r"script-Lauf"), "Skriptlauf"),
]
# Sentences that only make sense on the command line or name a plan that no longer exists.
DROP = re.compile(r"pla approve|--research-plan|--max-tasks|--model-calls|--search-rounds|Serien-Meilenstein")
FAILURE = re.compile(r"\s*Technische Details: (?P<path>\S+?)\.?\s*$")
WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|*?]+")
POSIX_PATH = re.compile(r"(?<![\w.:/])/(?:[\w.-]+/)+[\w.-]+")
SOURCE_ID = re.compile(r"\bsrc_[0-9a-f]{6,}#sec_[0-9a-f]{6,}\b")
SECTION_ID = re.compile(r"\bsec_[0-9a-f]{6,}\b")
DOCUMENT_ID = re.compile(r"\bsrc_[0-9a-f]{6,}\b")
EXCEPTIONS = ("OutOfMemoryError", "MemoryError")
ENGLISH_WORDS = re.compile(r"\b(the|must|cannot|requires?|needs?|was|were|is|are|does|every|missing|invalid|changed|"
                           r"failed|answer|evidence|should|contains?|unknown|only)\b", re.IGNORECASE)
GERMAN_WORDS = re.compile(r"[äöüÄÖÜß]|\b(der|die|das|und|nicht|ist|wird|wurde|ein|eine|einen|bitte|für|mit|auf|"
                          r"keine|kein|oder|bei|zu|im|den|dem|des)\b")


def english(sentence: str) -> bool:
    return len(ENGLISH_WORDS.findall(sentence)) >= 2 and not GERMAN_WORDS.search(sentence)


def language_of(text) -> str:
    """The language of a message the Studio did not write itself: "en" for English, else "de" (D-152)."""
    if isinstance(text, Localized):
        return text.language
    return "en" if english(str(text or "")) else "de"


def relative(path_text: str, root: Path | None) -> str:
    """A path under the project as the project-relative path, any other one as its file name."""
    cleaned = path_text.rstrip(".,;:)")
    suffix = path_text[len(cleaned):]
    windows = bool(re.match(r"^[A-Za-z]:[\\/]", cleaned))
    if not windows and not cleaned.startswith("/"):
        return path_text
    if root is not None:
        try:
            return Path(cleaned).resolve().relative_to(Path(root).resolve()).as_posix() + suffix
        except (ValueError, OSError):
            pass
    # A Windows path named on another system still loses its folders.
    return (PureWindowsPath(cleaned).name if windows else Path(cleaned).name) + suffix


def sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ„\"'(])", text) if part.strip()]


def user_text(message, root: Path | None = None, language: str = "de") -> dict:
    """The reader's version of a stop message in the interface ``language`` ("de" or "en").

    ``message`` is the pipeline's or the Studio's text; ``detail`` keeps the original wording when it
    had to be rewritten; ``file`` is a project-relative diagnostics file named by the message;
    ``message_language`` is the language the result is in: exact for the Studio's own text, else "de" as soon as one
    sentence it kept is not clearly English.
    """
    original = str(message or "").strip()
    text, file = original, None
    found = FAILURE.search(text)
    if found:
        file = relative(found["path"], root)
        text = text[:found.start()].rstrip()
    # Words the rewriting inserts are in the message's own language, so a German message stays German throughout.
    words = language if language == "de" or english(text) else "de"
    rejected = REJECTED.match(text)
    if rejected:
        fields = re.findall(r"(?:^|; )([\w.]+):", rejected["defects"])
        named = ", ".join(dict.fromkeys(fields[:3]))
        provider = rejected["provider"].strip()
        lead = (catalog_text(language, "message.rejected_fields", provider=provider, fields=named) if named
                else catalog_text(language, "message.rejected", provider=provider))
        text = (lead + " " + text[rejected.end():]).strip()
    for pattern, replacement in CLI:
        text = pattern.sub(replacement, text)
    text = WINDOWS_PATH.sub(lambda m: relative(m[0], root), text)
    text = POSIX_PATH.sub(lambda m: relative(m[0], root), text)
    text = SOURCE_ID.sub(str(catalog_text(words, "message.source_passage")), text)
    text = SECTION_ID.sub(str(catalog_text(words, "message.source_section")), text)
    text = DOCUMENT_ID.sub(str(catalog_text(words, "message.source")), text)
    for name in EXCEPTIONS:
        text = text.replace(f"({name})", f"({catalog_text(words, 'message.exception.' + name)})")
    rows, translated_any, german = [], bool(rejected), False
    for sentence in sentences(text):
        if DROP.search(sentence):
            continue
        key = ENGLISH.get(sentence.strip())
        if key is None and english(sentence) and language == "de":
            key = GENERIC_ENGLISH
        translated = str(catalog_text(language, key)) if key else None
        translated_any = translated_any or bool(translated)
        if translated and translated not in rows:
            rows.append(translated)
        elif not translated:
            rows.append(sentence.strip())
            # The pipeline writes German; a sentence counts as English only when it clearly is (english).
            german = german or (language == "en" and not english(sentence))
    text = " ".join(rows).strip()
    # The original wording stays available as technical detail wherever a translation replaced it.
    detail = None
    if translated_any:
        detail = WINDOWS_PATH.sub(lambda m: relative(m[0], root), FAILURE.sub("", original))
    if isinstance(message, Localized):
        shown = message.language
    else:
        shown = "de" if language == "de" or german else "en"
    return {"message": text, "detail": detail, "file": file, "message_language": shown}


def clean(message, root: Path | None = None, language: str = "de") -> str:
    return user_text(message, root, language)["message"]


def paths_only(text, root: Path | None = None):
    """Technical output such as a traceback keeps its wording; only local paths become project-relative."""
    if not text:
        return None
    return POSIX_PATH.sub(lambda m: relative(m[0], root), WINDOWS_PATH.sub(lambda m: relative(m[0], root), str(text)))
