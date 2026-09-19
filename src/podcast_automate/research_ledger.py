"""Checksummed question ledger and conservative import of earlier research runs."""
from __future__ import annotations

import hashlib
import json

from .errors import AppError
from .research_models import ResearchDiscovery, ResearchDossier, SourceDocument, SourceIndex
from .research_retrieval import merge_context
from .storage import digest, file_hash, inside, write_json

VERSION = "question_research.v1"
# The workflow and receipt bindings above stay; the call tag names the loop wording that produced a result.
CALL_VERSION = "question_research.v2-loop"
INDEX_MANIFEST = "index_manifest.v1"


def save_value(path, value):
    write_json(path, {"value": value, "sha256": digest(value)})


def read_value(path):
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("sha256") != digest(saved.get("value")):
        raise AppError("Gespeicherter Recherchestand wurde verändert.", code="invalid_research_checkpoint", status="blocked")
    return saved["value"]


def active_tasks(state):
    """Ids of the tasks being answered right now, in plan order.

    Ledgers written before tasks ran side by side named one ``active_task``; the public ledger
    keeps that field as the first element for readers that still expect it.
    """
    rows = state.get("active_tasks")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, str)]
    single = state.get("active_task")
    return [single] if isinstance(single, str) else []


def check_sources(root, index):
    for source in index.sources:
        path = inside(root, source.raw_path)
        if not path.is_file() or file_hash(path) != source.raw_hash:
            raise AppError("Ein gespeicherter Originalbeleg wurde verändert.", code="invalid_source_snapshot", status="blocked")


def section_id(page, text):
    return "sec_" + hashlib.sha256(f"{page}:{text}".encode()).hexdigest()[:16]


def save_index(root, run_id, path, index):
    """Store an index as document metadata plus a reference to each processed text.

    Sections are the bulk of an index; they are reloaded from the processed document and verified
    through the saved text hash, so every copy of a large index costs a few lines, not a book. A
    source without a processed file of the same text is embedded whole.
    """
    if path.exists():
        return
    rows = []
    for source in index.sources:
        processed = root / "sources/processed" / run_id / f"{source.id}.json"
        row = {"id": source.id, "document": source.model_dump(mode="json", exclude={"sections"})}
        if processed.is_file():
            row["processed"] = processed.relative_to(root).as_posix()
            row["sections_hash"] = digest([[s.id, s.page] for s in source.sections])
        else:
            row["sections"] = [s.model_dump(mode="json") for s in source.sections]
        rows.append(row)
    save_value(path, {"format": INDEX_MANIFEST, "sources": rows, "failures": index.failures})


def load_index(root, path):
    """The index a signature file describes, whether stored whole (older runs) or as a manifest."""
    saved = read_value(path)
    if not isinstance(saved, dict) or saved.get("format") != INDEX_MANIFEST:
        return SourceIndex.model_validate(saved)
    sources = []
    for row in saved["sources"]:
        document = dict(row["document"])
        if "sections" in row:
            document["sections"] = row["sections"]
        else:
            processed = inside(root, row["processed"])
            try:
                stored = SourceDocument.model_validate_json(processed.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise AppError("Ein gespeicherter Quellentext fehlt oder ist unlesbar.",
                               code="invalid_source_snapshot", status="blocked") from exc
            # The processed file may have been rewritten by a later import of the same text; the
            # sections count as intact only when texts, pages and section ids are exactly the saved ones.
            texts = "\n".join(s.text for s in stored.sections)
            if (hashlib.sha256(texts.encode()).hexdigest() != document["text_hash"] or
                    digest([[s.id, s.page] for s in stored.sections]) != row["sections_hash"] or
                    any(s.id != section_id(s.page, s.text) for s in stored.sections)):
                raise AppError("Ein gespeicherter Quellentext wurde verändert.", code="invalid_source_snapshot", status="blocked")
            document["sections"] = [s.model_dump(mode="json") for s in stored.sections]
        sources.append(SourceDocument.model_validate(document))
    return SourceIndex(sources=sources, failures=saved["failures"])


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
        accepted = task.get("accepted_gap") or None
        rows.append({"id": spec["id"], "question": spec["question"], "kind": spec["kind"],
                     "requirement_ids": spec["requirement_ids"], "acceptance": spec["acceptance"],
                     "status": task["status"], "activity": task["activity"], "steps": task["step"],
                     "depends_on": spec.get("depends_on", []), "outcome": task.get("outcome"),
                     "accepted_gap": bool(accepted), "accepted_reason": (accepted or {}).get("reason", ""),
                     "support": task.get("verification", {}).get("support_summary") if answer else None,
                     "review_limitations": (task.get("verification") or {}).get("limitations", []) if answer else [],
                     "search_count": len(task.get("search_receipts", [])),
                     "read_sections": len(task["read_refs"]), "reason": task.get("reason", ""),
                     "answer": answer["summary"] if answer else "", "limits": answer["limits"] if answer else [],
                     "findings": answer["findings"] if answer else [],
                     "sources": [{"reference": ref, "title": sections[ref][0].title,
                                  "url": sections[ref][0].final_url, "page": sections[ref][1].page}
                                 for ref in dict.fromkeys(e["reference"] for f in answer["findings"] for e in f["evidence"])
                                 if ref in sections] if answer else [],
                     "reopened": len(task["reopenings"])})
    blocked = [r for r in rows if r["status"] == "blocked" and not r["accepted_gap"]]
    phase = state["phase"]
    if phase == "blocked" and not blocked:
        phase = "questions"
    active = active_tasks(state)
    return {"version": VERSION, "total": len(rows), "closed": sum(r["status"] == "verified" for r in rows),
            "blocked": len(blocked), "accepted": sum(r["accepted_gap"] for r in rows), "phase": phase,
            "source_count": len(index.sources) if index else None,
            "source_failures": len(index.failures) if index else None,
            "source_attempt_count": state.get("source_attempt_count"),
            "budget_projection": state.get("budget_projection"),
            "active_task": active[0] if active else None, "active_tasks": active, "questions": rows}
