"""Works the editor provides: the books and articles no free source offers, from a library or a purchase.

A provided work is evidence like a downloaded primary work, unlike the editor's notes, which stay idea sources:
its citation says what it is (research_models.is_idea). The works live beside the brief in ``inputs/works.json``,
so adding one never changes the inputs of a run in progress. The research reads a new work at its next step and
retries the blocked questions it was provided for (QuestionResearch.adopt_provided_works). The list of missing
works comes from the blocked questions' primary works (2026-10-01, the user's wish: "die Bücherliste sehen und dort
hochladen").
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .errors import AppError
from .models import now
from .research_models import SourceCandidate
from .storage import inside, read_text, write_json

MANIFEST = "inputs/works.json"
FOLDER = "inputs/works"
# A scanned or whole book; the text limits of a provided work are raised in pdf_text's book mode.
MAX_WORK_BYTES = 150 * 1024 * 1024


def inventory(root: Path) -> list[dict]:
    path = inside(root, MANIFEST)
    if not path.exists():
        return []
    rows = json.loads(read_text(path))
    return rows if isinstance(rows, list) else []


def work_path(root: Path, row: dict) -> Path:
    if not re.fullmatch(r"inputs/works/[a-f0-9]{64}\.(pdf|epub|html|txt)", row.get("path", "")):
        raise AppError("Ungültiges bereitgestelltes Werk.", code="invalid_work")
    return inside(root, row["path"])


def file_kind(raw: bytes) -> str:
    """The format of an uploaded work by its content: PDF, an e-book (EPUB, D-198), a saved web page, or plain text."""
    if raw.startswith(b"%PDF-"):
        return ".pdf"
    if raw.startswith(b"PK\x03\x04") and b"mimetypeapplication/epub+zip" in raw[:200]:
        return ".epub"
    head = raw[:4000].lower()
    if b"<html" in head or b"<!doctype html" in head:
        return ".html"
    if b"\x00" in raw[:100_000]:
        raise AppError("Bitte das Werk als PDF, EPUB, gespeicherte Webseite oder Textdatei hochladen.", code="invalid_work")
    try:
        raw[:100_000].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError("Bitte das Werk als PDF, EPUB, gespeicherte Webseite oder Textdatei hochladen.",
                       code="invalid_work") from exc
    return ".txt"


def add(root: Path, raw: bytes, *, citation: str, tasks=(), run_id: str | None = None, source: str | None = None) -> dict:
    """Store one uploaded work with the citation the editor confirmed and the questions it is for. ``source`` is the
    refused address the editor fetched it from in a browser (refused_sources), so the list shows it as uploaded."""
    citation = " ".join((citation or "").split())
    if not 3 <= len(citation) <= 400:
        raise AppError("Bitte das Werk mit Autor, Titel und Jahr angeben.", code="invalid_work")
    if source is not None and not re.fullmatch(r"https?://\S{3,2000}", source):
        raise AppError("Ungültige Quellenadresse.", code="invalid_work")
    if not 0 < len(raw) <= MAX_WORK_BYTES:
        raise AppError(f"Das Werk darf höchstens {MAX_WORK_BYTES // (1024 * 1024)} MB groß sein.", code="invalid_work")
    tasks = sorted({task for task in tasks if isinstance(task, str) and re.fullmatch(r"[\w-]{1,64}", task)})
    suffix = file_kind(raw)
    sha = hashlib.sha256(raw).hexdigest()
    path = inside(root, f"{FOLDER}/{sha}{suffix}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        pending = path.with_suffix(suffix + ".pending")
        pending.write_bytes(raw)
        pending.replace(path)
    rows = inventory(root)
    row = next((r for r in rows if r["sha256"] == sha and r["citation"] == citation), None)
    if row is None:
        row = {"id": f"work_{sha[:16]}", "citation": citation, "path": path.relative_to(root).as_posix(), "sha256": sha,
               "bytes": len(raw), "added_at": now(), "tasks": tasks, "run_id": run_id, **({"source": source} if source else {})}
        rows.append(row)
    else:
        # The same file for one more question: the question joins; the run adopts the work once.
        row["tasks"] = sorted({*row["tasks"], *tasks})
        if source:
            row["source"] = source
    write_json(inside(root, MANIFEST), rows)
    return row


def candidate(row: dict) -> SourceCandidate:
    """The search-free candidate a provided work is imported as: the editor's citation is its title."""
    year = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", row["citation"])
    return SourceCandidate(url=row["path"], title=row["citation"], authors=[], published_date=year.group(1) if year else "",
                           rationale="Provided by the editor (library or purchase).", primary_source=True,
                           source_type="primary_work")


def latest_state(root: Path) -> Path | None:
    """The question state of the project's newest research run, which latest_ledger reads the ledger of."""
    for path in sorted(root.glob("runs/run_*/research_questions.json"), reverse=True):
        try:
            json.loads(read_text(path))
        except (OSError, ValueError):
            continue
        return path.parent / "question_research/state.json"
    return None


def latest_ledger(root: Path) -> dict | None:
    """The question ledger of the project's newest research run, or None. A ledger a worker wrote before rows named
    their primary works gets them from the run's plan."""
    for path in sorted(root.glob("runs/run_*/research_questions.json"), reverse=True):
        try:
            ledger = json.loads(read_text(path))
        except (OSError, ValueError):
            continue
        rows = ledger.get("questions") or []
        if rows and not all("primary_works" in row for row in rows):
            works = planned_works(path.parent / "question_research/state.json")
            ledger["questions"] = [{**row, "primary_works": works.get(row.get("id"), [])} for row in rows]
        return ledger
    return None


_PLANNED = {}


def planned_works(state_path: Path) -> dict:
    """Each task's primary works from a run's ledger state, read again only when the state changed."""
    try:
        key = (str(state_path), state_path.stat().st_mtime_ns)
    except OSError:
        return {}
    if key not in _PLANNED:
        from .research_ledger import read_value
        try:
            tasks = read_value(state_path)["plan"]["tasks"]
        except (OSError, ValueError, KeyError, TypeError):
            return {}
        _PLANNED.clear()
        _PLANNED[key] = {task["id"]: task.get("primary_works", []) for task in tasks}
    return _PLANNED[key]


# Addresses a publisher or a bot check refused (HTTP 401, 402, 403, 451, or a check page instead of the document): the
# editor opens each in a browser, saves the document and uploads it for this very address (D-199, 2026-11-10, the
# user's wish: „eine Liste an geblockten Inhalten … damit er es durchklickt, runterlädt und gleich in die richtige
# Schublade … zurück schiebt“). The archive and the free copies were asked before (sources.import_source).
REFUSED = re.compile(r"\(HTTP (401|402|403|451)\)")
MAX_REFUSED = 40
_REFUSED = {}


def refused_sources(state_path: Path) -> dict:
    """Each address a run's searches could not read because it was refused, by address: the title its search gave, the
    refusal (an HTTP status, or None for a check page) and the questions that met it; read again when the state changed."""
    try:
        key = (str(state_path), state_path.stat().st_mtime_ns)
    except OSError:
        return {}
    if key not in _REFUSED:
        from .research_ledger import read_value
        try:
            tasks = read_value(state_path)["tasks"]
        except (OSError, ValueError, KeyError, TypeError):
            return {}
        found = {}
        for task_id, task in tasks.items():
            for receipt in task.get("search_receipts") or []:
                titles = {row.get("url"): row.get("title") for row in [*(receipt.get("candidates") or []),
                                                                     *(receipt.get("excluded_candidates") or [])]
                          if isinstance(row, dict)}
                for failure in receipt.get("retrieval_failures") or []:
                    url, reason = failure.get("source") or "", failure.get("reason") or ""
                    status = REFUSED.search(reason)
                    if not url.startswith(("http://", "https://")) or not (status or failure.get("code") == "source_access_blocked"):
                        continue
                    entry = found.setdefault(url, {"source": url, "title": titles.get(url) or "",
                                                   "status": int(status.group(1)) if status else None, "tasks": []})
                    if task_id not in entry["tasks"]:
                        entry["tasks"].append(task_id)
        _REFUSED.clear()
        _REFUSED[key] = found
    return _REFUSED[key]


def overview(root: Path, ledger: dict | None, state_path: Path | None = None) -> dict:
    """What the research page shows: the works blocked questions lack, the refused addresses they met (``refused``,
    from the run's question state at ``state_path``), and the works provided so far.

    A blocked question names its missing works in ``primary_works``; one in a new attempt after a block (it has
    advice) is listed too, as ``retrying``, so the editor can look for the work before it blocks again. A question
    that only waits for a prerequisite, or was accepted as a gap, needs none."""
    provided = inventory(root)
    cited = {row["citation"]: row["id"] for row in provided}
    fetched = {row["source"]: row["id"] for row in provided if row.get("source")}
    missing, open_questions = {}, {}
    for question in (ledger or {}).get("questions") or []:
        status = question.get("status")
        blocked = status == "blocked" and question.get("outcome") != "prerequisite_block"
        retrying = status in {"pending", "researching", "reviewing"} and bool(question.get("advice"))
        if question.get("accepted_gap") or not (blocked or retrying):
            continue
        open_questions[question["id"]] = (question.get("question", ""), blocked)
        for work in question.get("primary_works") or []:
            entry = missing.setdefault(work, {"work": work, "tasks": [], "provided": cited.get(work), "state": "retrying"})
            entry["tasks"].append({"id": question["id"], "question": question.get("question", "")})
            if blocked:
                entry["state"] = "blocked"
    refused = []
    for entry in (refused_sources(state_path).values() if state_path else []):
        tasks = [task for task in entry["tasks"] if task in open_questions]
        if not tasks:
            continue
        refused.append({"source": entry["source"], "title": entry["title"], "status": entry["status"],
                        "tasks": [{"id": task, "question": open_questions[task][0]} for task in tasks],
                        "state": "blocked" if any(open_questions[task][1] for task in tasks) else "retrying",
                        "provided": fetched.get(entry["source"])})
        if len(refused) >= MAX_REFUSED:
            break
    return {"missing": list(missing.values()), "refused": refused,
            "provided": [{**{key: row[key] for key in ("id", "citation", "tasks", "added_at", "bytes")},
                          **({"source": row["source"]} if row.get("source") else {})} for row in provided]}
