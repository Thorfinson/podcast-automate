"""Durable source-attempt accounting independent of retained, deduplicated text."""
from __future__ import annotations

from .errors import AppError
from .research_ledger import read_value, save_value
from .sources import canonical_url


def attempt_folder(folder, task_id, row):
    """Where a task's current attempt keeps its receipts: ``tasks/<id>/attempt_<n>`` after ``n`` reopenings, and
    below it ``dependency_<k>`` once the answer was sent back for revalidation ``k`` times. The one derivation of
    that layout for every reader of it (2026-10-02: the budget projection and the attempt restore had drifted)."""
    path = folder / "tasks" / task_id / f"attempt_{len(row['reopenings'])}"
    return path / f"dependency_{row['dependency_revision']}" if row.get("dependency_revision") else path


def download_receipts(folder):
    """Every download receipt of the run's task searches, wherever its step keeps it: ``attempt_*/step_*``, below
    ``dependency_*`` after a revalidation, and in ``search_<hash>`` for a second search of one step
    (question_answering.search_folder). The glob of one layout missed the other two."""
    tasks = folder / "tasks"
    return sorted(tasks.rglob("downloads.json")) if tasks.is_dir() else []


def source_identity(value):
    try:
        return canonical_url(value)
    except (AppError, ValueError):
        # Local files and invalid URLs still consumed an attempted candidate.
        return value


def restore_attempts(folder, index):
    path = folder / "source_attempts.json"
    attempts = set(read_value(path)) if path.exists() else set()
    attempts.update(source_identity(s.url or s.raw_path) for s in index.sources)
    attempts.update(source_identity(f["source"]) for f in index.failures
                    if f["reason"] != "Quellenlimit erreicht; nicht abgerufen.")
    # Older versions discarded duplicate text but retained these download
    # receipts. Recover those attempts without altering the old source snapshots.
    for receipt in download_receipts(folder):
        saved = read_value(receipt)
        attempts.update(source_identity(url) for url in saved.get("attempted", saved["processed"]))
    save_value(path, sorted(attempts))
    return attempts


def reserve_source(folder, receipt, result, attempts, address, limit):
    """A pending reservation can resume; completed/failed URLs are not retried."""
    if address in result.get("attempted", []):
        return True
    if address in attempts or len(attempts) >= limit:
        return False
    result.setdefault("attempted", []).append(address)
    # Persist the owning receipt first, so an interruption between writes can
    # reconstruct the reservation and resume that exact download.
    save_value(receipt, result)
    attempts.add(address)
    save_value(folder / "source_attempts.json", sorted(attempts))
    return True
