"""Checksummed question ledger and conservative import of earlier research runs."""
from __future__ import annotations

import json

from .errors import AppError
from .research_models import ResearchDiscovery, ResearchDossier, SourceIndex
from .research_retrieval import merge_context
from .storage import digest, file_hash, inside, write_json

VERSION = "question_research.v1"


def save_value(path, value):
    write_json(path, {"value": value, "sha256": digest(value)})


def read_value(path):
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("sha256") != digest(saved.get("value")):
        raise AppError("Gespeicherter Recherchestand wurde verändert.", code="invalid_research_checkpoint", status="blocked")
    return saved["value"]


def check_sources(root, index):
    for source in index.sources:
        path = inside(root, source.raw_path)
        if not path.is_file() or file_hash(path) != source.raw_hash:
            raise AppError("Ein gespeicherter Originalbeleg wurde verändert.", code="invalid_source_snapshot", status="blocked")


def validate_context(index, context):
    sections = {f"{s.id}#{part.id}": part for s in index.sources for part in s.sections}
    for source in context:
        for section in source["sections"]:
            original = sections.get(section["reference"])
            if original is None or original.text != section["text"] or original.page != section.get("page"):
                raise AppError("Gespeicherte Textauswahl passt nicht zur Quelle.", code="invalid_source_snapshot", status="blocked")


def bootstrap_legacy(root, work, discovery, index, dossier, context):
    """Import saved evidence/drafts; never treat an old model's green score as a verified answer."""
    from .research import validate_dossier
    imported, review = [], None
    for folder in sorted((work / "completeness").glob("round_*")):
        if (folder / "search.json").exists():
            extra = ResearchDiscovery.model_validate(read_value(folder / "search.json"))
            if extra.topic != discovery.topic:
                raise AppError("Thema des Recherche-Zwischenstands stimmt nicht überein.", code="invalid_research_checkpoint", status="blocked")
            known = {c.url for c in discovery.candidates}
            discovery = discovery.model_copy(update={"candidates": [*discovery.candidates,
                *(c for c in extra.candidates if c.url not in known)]})
        if (folder / "retrieval.json").exists():
            restored = SourceIndex.model_validate(read_value(folder / "retrieval.json")["index"])
            old = {s.id: s.model_dump() for s in index.sources}
            if any(sid not in {s.id for s in restored.sources} or
                   next(s.model_dump() for s in restored.sources if s.id == sid) != value for sid, value in old.items()):
                raise AppError("Quellenhistorie ist nicht konsistent.", code="invalid_source_snapshot", status="blocked")
            index = restored
        if (folder / "source_context.json").exists():
            extra_context = json.loads((folder / "source_context.json").read_text(encoding="utf-8"))
            validate_context(index, extra_context)
            context = merge_context(context, extra_context)
        # Files are ordered by the pipeline's semantic sequence, not modification time.
        for name in ("dossier.json", "dossier_references.json", "dossier_patch_applied.json",
                     "dossier_patch_references_applied.json", *[f"grounding_repair_{n}{suffix}.json"
                     for n in range(3) for suffix in ("", "_references")],
                     *[f"grounding_patch_{n}{suffix}_applied.json" for n in range(3) for suffix in ("", "_references")]):
            path = folder / name
            if not path.exists():
                continue
            if name.endswith("_applied.json"):
                value = json.loads(path.read_text(encoding="utf-8"))
                if value.get("dossier_hash") != digest(value.get("dossier")):
                    raise AppError("Dossieränderung wurde verändert.", code="invalid_research_checkpoint", status="blocked")
                value = value["dossier"]
            else:
                value = read_value(path)
            candidate = ResearchDossier.model_validate(value)
            if not validate_dossier(candidate, discovery, context):
                dossier = candidate
                imported.append(str(path.relative_to(work)))
        for path in sorted(folder.glob("grounding_*_routed.json")):
            review = read_value(path)
    check_sources(root, index)
    validate_context(index, context)
    return discovery, index, dossier, context, {"drafts": imported, "last_review": review}


def public_ledger(state, index=None):
    rows = []
    sections = {f"{source.id}#{section.id}": (source, section) for source in index.sources
                for section in source.sections} if index else {}
    for spec in state["plan"]["tasks"]:
        task = state["tasks"][spec["id"]]
        answer = task.get("answer") if task["status"] == "verified" else None
        rows.append({"id": spec["id"], "question": spec["question"], "kind": spec["kind"],
                     "requirement_ids": spec["requirement_ids"], "acceptance": spec["acceptance"],
                     "status": task["status"], "activity": task["activity"], "steps": task["step"],
                     "depends_on": spec.get("depends_on", []), "outcome": task.get("outcome"),
                     "support": task.get("verification", {}).get("support_summary") if answer else None,
                     "search_receipts": task.get("search_receipts", []),
                     "read_sections": len(task["read_refs"]), "reason": task.get("reason", ""),
                     "answer": answer["summary"] if answer else "", "limits": answer["limits"] if answer else [],
                     "findings": answer["findings"] if answer else [],
                     "sources": [{"reference": ref, "title": sections[ref][0].title,
                                  "url": sections[ref][0].final_url, "page": sections[ref][1].page}
                                 for ref in dict.fromkeys(e["reference"] for f in answer["findings"] for e in f["evidence"])
                                 if ref in sections] if answer else [],
                     "reopened": len(task["reopenings"])})
    return {"version": VERSION, "total": len(rows), "closed": sum(r["status"] == "verified" for r in rows),
            "blocked": sum(r["status"] == "blocked" for r in rows), "phase": state["phase"],
            "source_count": len(index.sources) if index else None,
            "source_failures": len(index.failures) if index else None,
            "source_attempt_count": state.get("source_attempt_count"),
            "budget_projection": state.get("budget_projection"),
            "active_task": state.get("active_task"), "questions": rows}
