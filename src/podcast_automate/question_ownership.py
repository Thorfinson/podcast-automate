"""Conservative, durable ownership of composed findings by fixed research tasks."""
from __future__ import annotations

from .errors import AppError
from .storage import digest


def finding_owners(dossier, tasks, rows, saved=None):
    identifiers = {t.id for t in tasks}
    owners = {}
    for finding in dossier.findings:
        if saved is not None and finding.id in saved:
            assigned = set(saved[finding.id])
            if not assigned or not assigned <= identifiers:
                raise AppError("Ungültige Zuordnung zwischen Befunden und Recherchefragen.",
                               code="invalid_research_checkpoint", status="blocked")
        else:
            assigned = {t.id for t in tasks if finding.id in t.finding_ids}
            if not assigned:
                content = finding.model_dump(exclude={"id"})
                refs = {e.reference for e in finding.evidence}
                exact, related = set(), set()
                for task in tasks:
                    row = rows[task.id]
                    answer = row.get("answer") or row.get("draft_answer") or {}
                    for entry in answer.get("findings", []):
                        if {k: v for k, v in entry.items() if k != "id"} == content:
                            exact.add(task.id)
                        if refs & {e["reference"] for e in entry["evidence"]}:
                            related.add(task.id)
                # IDs inside an answer are local, so ID equality alone conveys
                # no ownership. Ambiguous legacy/paraphrased findings are shared,
                # never assigned to one task based on a broad coverage question.
                assigned = exact or related or identifiers
        owners[finding.id] = sorted(assigned)
    return owners


def editable_findings(owners, batch, dirty):
    return {fid for fid, assigned in owners.items()
            if set(assigned) & set(batch) and set(assigned) <= set(dirty)}


def preserve_unrelated(before, after, targets):
    actual = {f.id: digest(f.model_dump()) for f in after.findings}
    if any(actual.get(f.id) != digest(f.model_dump()) for f in before.findings if f.id not in targets):
        raise AppError("Die Dossieränderung verändert einen Befund einer unveränderten Recherchefrage.",
                       code="invalid_research_patch", status="blocked")
