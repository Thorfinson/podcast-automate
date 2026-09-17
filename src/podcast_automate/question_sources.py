"""Durable source-attempt accounting independent of retained, deduplicated text."""
from __future__ import annotations

from .errors import AppError
from .research_ledger import read_value, save_value
from .sources import canonical_url


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
    for receipt in folder.glob("tasks/*/attempt_*/step_*/downloads.json"):
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
