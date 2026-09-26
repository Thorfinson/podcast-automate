"""Stop messages as the Studio shows them: German, without CLI commands, local paths or internal ids.

The pipeline raises one message for every reader: the command line, the log and, for a rejected
answer, the model itself, which is re-asked with that text. Only this Studio boundary rewrites a
message for a person at the browser, so no prompt, CLI text or stored record changes here.
"""
from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

REVIEW_DISAGREEMENT = ("Die Gesamtprüfung erhebt einen Einwand, den sie nicht mit gelesenen Belegen verankern kann. "
                       "Dafür startet keine automatische Nachrecherche.")

# English texts of pipeline checks. Most of them are also re-ask instructions for the model, so
# they stay English at their source and are translated only here.
ENGLISH = {
    "An unresolved review disagreement cannot trigger unanchored research.": REVIEW_DISAGREEMENT,
    "Unanchored review disagreement; no automatic new research.": REVIEW_DISAGREEMENT,
    "Review disagreement cannot trigger more research.": REVIEW_DISAGREEMENT,
    "Stored support review no longer passes.": "Eine gespeicherte Belegprüfung besteht mit dem heutigen Stand nicht mehr.",
    "The verified prerequisites changed.": "Die geprüften Voraussetzungen einer Teilfrage haben sich seit ihrer Prüfung geändert.",
    "Invalid research task prerequisites.": "Der Rechercheplan nennt ungültige Voraussetzungen zwischen Teilfragen.",
    "Research task prerequisites contain a cycle.": "Die Teilfragen des Rechercheplans setzen sich gegenseitig voraus.",
    "Search must record executed queries and counterevidence outcome.":
        "Eine Websuche hat ihre Suchbegriffe oder das Ergebnis der Gegenrecherche nicht festgehalten.",
    "Every existing objection needs an explicit closure check.":
        "Die Gesamtprüfung hat nicht jeden bestehenden Einwand ausdrücklich geprüft.",
    "Failing support receipts require explicit corrective issues.":
        "Die Gesamtprüfung nennt für eine fehlgeschlagene Belegprüfung keine Korrektur.",
    "Objection closure requires read source evidence.": "Ein Einwand wurde ohne gelesene Quellenbelege als erledigt markiert.",
    "A dossier objection requires an affected finding and closure condition.":
        "Ein Einwand der Gesamtprüfung nennt keinen betroffenen Befund oder keine Abschlussbedingung.",
    "Every routed task needs a specific evidence or criterion anchor.":
        "Ein Einwand wurde einer Teilfrage ohne konkreten Beleg- oder Kriterienbezug zugeordnet.",
    "Missing task-specific objection anchor.":
        "Ein Einwand wurde einer Teilfrage ohne konkreten Beleg- oder Kriterienbezug zugeordnet.",
    "The closure condition of an existing objection changed.":
        "Die Abschlussbedingung eines bestehenden Einwands hat sich geändert.",
    "Synthesis edits must concern the editable findings.":
        "Eine Dossieränderung betrifft Befunde, die in diesem Schritt nicht geändert werden dürfen.",
    "Script review must check every segment's claim preservation exactly once.":
        "Die Skriptprüfung hat nicht jeden Abschnitt genau einmal auf erhaltene Aussagen geprüft.",
    "Script preservation receipt is inconsistent with its segment.":
        "Ein Prüfbeleg der Skriptprüfung passt nicht zu seinem Abschnitt.",
    "Supplement semantic support check failed.": "Die inhaltliche Prüfung der Zusatzbelege ist fehlgeschlagen.",
    "Codex app-server stream closed before the account data arrived.":
        "Die Verbindung zu Codex endete, bevor die Kontodaten ankamen.",
}
GENERIC_ENGLISH = "Eine automatische Prüfung hat ein Ergebnis abgewiesen."
REJECTED = re.compile(r"^(?P<provider>[\w -]{1,40}?) answered, but the answer violates its output contract: "
                      r"(?P<defects>.*?)\.? Return the complete answer again with exactly these defects corrected\.\s*",
                      re.DOTALL)
# Command-line advice, rewritten into what the Studio offers. Applied in this order.
CLI = [
    (re.compile(r"mit ['„\"]?pla resume['“\"]? fortsetzen", re.IGNORECASE), "mit „Fortsetzen“ weitermachen"),
    (re.compile(r"pla resume( verwenden)?"), "„Fortsetzen“"),
    (re.compile(r"--api-key für verdeckte Eingabe oder OPENROUTER_API_KEY setzen"),
     "im Studio unter „Geschützter OpenRouter-Key-Eingang“ hinterlegen"),
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
EXCEPTIONS = {"OutOfMemoryError": "Grafikspeicher reicht nicht aus", "MemoryError": "Arbeitsspeicher reicht nicht aus"}
ENGLISH_WORDS = re.compile(r"\b(the|must|cannot|requires?|needs?|was|were|is|are|does|every|missing|invalid|changed|"
                           r"failed|answer|evidence|should|contains?|unknown|only)\b", re.IGNORECASE)
GERMAN_WORDS = re.compile(r"[äöüÄÖÜß]|\b(der|die|das|und|nicht|ist|wird|wurde|ein|eine|einen|bitte|für|mit|auf|"
                          r"keine|kein|oder|bei|zu|im|den|dem|des)\b")


def english(sentence: str) -> bool:
    return len(ENGLISH_WORDS.findall(sentence)) >= 2 and not GERMAN_WORDS.search(sentence)


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


def user_text(message, root: Path | None = None) -> dict:
    """The reader's version of a stop message.

    ``message`` is the German or rewritten text; ``detail`` keeps the original wording when it
    had to be rewritten; ``file`` is a project-relative diagnostics file named by the message.
    """
    original = str(message or "").strip()
    text, file = original, None
    found = FAILURE.search(text)
    if found:
        file = relative(found["path"], root)
        text = text[:found.start()].rstrip()
    rejected = REJECTED.match(text)
    if rejected:
        fields = re.findall(r"(?:^|; )([\w.]+):", rejected["defects"])
        named = ", ".join(dict.fromkeys(fields[:3]))
        text = (f"Die Antwort von {rejected['provider'].strip()} passte nicht zum erwarteten Format"
                f"{f' ({named})' if named else ''}. " + text[rejected.end():]).strip()
    for pattern, replacement in CLI:
        text = pattern.sub(replacement, text)
    text = WINDOWS_PATH.sub(lambda m: relative(m[0], root), text)
    text = POSIX_PATH.sub(lambda m: relative(m[0], root), text)
    text = SOURCE_ID.sub("Quellenstelle", text)
    text = SECTION_ID.sub("Quellenabschnitt", text)
    text = DOCUMENT_ID.sub("Quelle", text)
    for name, meaning in EXCEPTIONS.items():
        text = text.replace(f"({name})", f"({meaning})")
    rows, translated_any = [], bool(rejected)
    for sentence in sentences(text):
        if DROP.search(sentence):
            continue
        translated = ENGLISH.get(sentence.strip())
        if translated is None and english(sentence):
            translated = GENERIC_ENGLISH
        translated_any = translated_any or bool(translated)
        if translated and translated not in rows:
            rows.append(translated)
        elif not translated:
            rows.append(sentence.strip())
    text = " ".join(rows).strip()
    # The original wording stays available as technical detail wherever a translation replaced it.
    detail = None
    if translated_any:
        detail = WINDOWS_PATH.sub(lambda m: relative(m[0], root), FAILURE.sub("", original))
    return {"message": text, "detail": detail, "file": file}


def clean(message, root: Path | None = None) -> str:
    return user_text(message, root)["message"]


def paths_only(text, root: Path | None = None):
    """Technical output such as a traceback keeps its wording; only local paths become project-relative."""
    if not text:
        return None
    return POSIX_PATH.sub(lambda m: relative(m[0], root), WINDOWS_PATH.sub(lambda m: relative(m[0], root), str(text)))
