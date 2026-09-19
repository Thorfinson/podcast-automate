"""Topic -> live search -> retrieved documents -> evidence-linked research dossier."""
from __future__ import annotations

import json
import hashlib
import re
import shutil
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .prompts import fragment, instructions
from . import __version__
from . import attachments
from .codex import CodexAdapter
from .errors import AppError
from .run_budget import effective_limits
from .editorial import TERMINOLOGY, TEACHING_SCOPE
from .models import RunManifest, StageRecord
from .research_models import ResearchDiscovery, ResearchDossier, SourceCandidate, SourceDocument, SourceIndex
from .runner import execute_stages, manifest_path, outputs_valid, run_observer
from .research_quality import load_complete_research, requirements_for
from .question_research import run_question_research
from .research_ledger import read_value
from .sources import EXTRACTION_VERSION, canonical_url, clean, import_source
from .storage import (atomic_text, digest, file_hash, file_lock, inside, load_project, project_lock,
                      read_yaml, write_json, write_yaml)
from .text_settings import validate_model, validate_reasoning

RESEARCH_VERSION = "research.v3-complete-brief"
PLAIN_LANGUAGE = TERMINOLOGY + TEACHING_SCOPE + fragment("plain_language")


def inherit_sources(root, work, manifest, config, local_files, parent_id):
    """Start a fresh writing pass from verified, dated research snapshots."""
    parent_path = manifest_path(root, parent_id)
    parent = RunManifest.model_validate(read_yaml(parent_path))
    previous = parent_path.parent
    scope = ("topic", "central_question", "focus_questions", "excluded_topics",
             "seed_people", "seed_urls", "local_sources")
    snapshot = read_yaml(previous / "project_snapshot.yaml")
    if any(snapshot.get(key) != getattr(config, key) for key in scope):
        raise AppError("Rechercheauftrag geändert; Quellen mit einem neuen research-Lauf recherchieren.",
                       code="inputs_changed", status="blocked")
    if parent.kind != "research" or any(
        not outputs_valid(root, parent.stages[name]) for name in ("discovery", "retrieval")
    ):
        raise AppError("Gespeicherte Recherchequellen fehlen oder wurden geändert.",
                       code="invalid_source_snapshot", status="blocked")
    index = SourceIndex.model_validate_json((previous / "source_index.json").read_text(encoding="utf-8"))
    if not index.sources or len(index.sources) > config.research_limits.sources or any(
        source.extraction_version != EXTRACTION_VERSION for source in index.sources
    ):
        raise AppError("Quellenstand passt nicht zum Parser oder Quellenlimit; neue Recherche erforderlich.",
                       code="invalid_source_snapshot", status="blocked")
    local_by_id = {"src_" + hashlib.sha256(str(p).encode()).hexdigest()[:16]: p for p in local_files}
    for source_id, local in local_by_id.items():
        source = next((s for s in index.sources if s.id == source_id), None)
        if source is None or not local.is_file() or file_hash(local) != source.raw_hash:
            raise AppError("Lokale Quelle geändert oder im alten Lauf nicht eingelesen.",
                           code="inputs_changed", status="blocked")
    provenance = {"reused_from_run": parent_id, "new_web_search": False,
                  "original_discovery_metadata": json.loads(
                      (previous / "discovery_metadata.json").read_text(encoding="utf-8"))}
    events = list((previous / "calls").glob("call_*/search_events.json"))
    provenance["original_search_events"] = [json.loads(p.read_text(encoding="utf-8")) for p in events]
    write_json(work / "discovery_metadata.json", provenance)
    shutil.copyfile(previous / "discovery.json", work / "discovery.json")
    retrieval_outputs = []
    for source in index.sources:
        old_raw = inside(root, source.raw_path)
        new_raw = inside(root, f"sources/raw/{manifest.run_id}/{source.id}{old_raw.suffix}")
        new_raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(old_raw, new_raw)
        source.raw_path = new_raw.relative_to(root).as_posix()
        processed = inside(root, f"sources/processed/{manifest.run_id}/{source.id}.json")
        write_json(processed, source.model_dump(mode="json"))
        identity = str(local_by_id[source.id]) if source.id in local_by_id else canonical_url(source.url)
        write_json(work / "retrieval_cache" / (digest(identity) + ".json"),
                   {"processed": processed.relative_to(root).as_posix(), "sha256": file_hash(processed)})
        retrieval_outputs.extend([new_raw, processed])
    write_json(work / "source_index.json", index.model_dump(mode="json"))
    for name, files in {
        "discovery": [work / "discovery.json", work / "discovery_metadata.json"],
        "retrieval": [work / "source_index.json", *retrieval_outputs],
    }.items():
        manifest.stages[name] = StageRecord(status="completed", outputs={
            p.relative_to(root).as_posix(): file_hash(p) for p in files})


def reserve_call(work: Path, limits, *, search=False) -> int:
    with file_lock(work / ".budget.lock", timeout=30):
        path = work / "budget.json"
        budget = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"model_calls": 0, "search_rounds": 0}
        if budget["model_calls"] >= limits.model_calls:
            raise AppError(f"Limit von {limits.model_calls} Modellaufrufen erreicht. Der bisherige Stand bleibt gespeichert.",
                           code="research_budget_exhausted", status="blocked")
        if search and budget["search_rounds"] >= limits.search_rounds:
            raise AppError(f"Limit von {limits.search_rounds} Rechercherunden erreicht. Der bisherige Stand bleibt gespeichert.",
                           code="research_budget_exhausted", status="blocked")
        budget["model_calls"] += 1
        budget["search_rounds"] += int(search)
        write_json(path, budget)
        return budget["model_calls"]


def source_context(index: SourceIndex, discovery: ResearchDiscovery, *, retained_dossier=None, extra_queries=(),
                   prioritize_queries=False) -> list[dict]:
    """Bound model context, and explicitly record which saved sections were presented."""
    words = set(re.findall(r"\w{5,}", " ".join([*(q.search_query for q in discovery.questions), *extra_queries]).lower()))
    priority_words = set(re.findall(r"\w{5,}", " ".join(extra_queries).lower())) if prioritize_queries else set()
    anchors = {e.reference for f in retained_dossier.findings for e in f.evidence} if retained_dossier else set()
    context = []
    allowance = max(1600, 150_000 // max(len(index.sources), 1))
    for source in index.sources:
        ranked = sorted(enumerate(source.sections), key=lambda item: (
            0 if f"{source.id}#{item[1].id}" in anchors else 1,
            -sum(word in item[1].text.lower() for word in priority_words),
            -sum(word in item[1].text.lower() for word in words), item[0]))
        chosen, used = [], 0
        for position, section in ranked:
            if f"{source.id}#{section.id}" in anchors or used + len(section.text) <= allowance:
                chosen.append((position, section))
                used += len(section.text)
        chosen.sort(key=lambda item: item[0])
        context.append({"source_id": source.id, "title": source.title, "url": source.final_url,
                        "text_hash": source.text_hash,
                        "extraction_coverage": source.extraction_coverage.model_dump() if source.extraction_coverage else None,
                        "reliability_note": source.reliability_note, "uncertainties": source.uncertainties,
                        "total_sections": len(source.sections),
                        "sections": [{"reference": f"{source.id}#{section.id}", "text": section.text,
                                      "page": section.page} for _, section in chosen]})
    return context


def validate_dossier(dossier: ResearchDossier, discovery: ResearchDiscovery, context: list[dict]) -> list[str]:
    errors = []
    sections = {s["reference"]: s["text"] for source in context for s in source["sections"]}
    ids = [finding.id for finding in dossier.findings]
    if dossier.topic != discovery.topic:
        errors.append("Topic must match the research topic exactly.")
    if len(set(ids)) != len(ids):
        errors.append("Finding IDs must be unique.")
    quotes, paraphrased_words = {}, Counter()
    for finding in dossier.findings:
        for source_id in sorted({e.reference.split("#")[0] for e in finding.evidence}):
            paraphrased_words[source_id] += len(finding.statement.split())
        for evidence in finding.evidence:
            text = sections.get(evidence.reference)
            if text is None:
                errors.append(f"{finding.id}: unknown or unseen reference {evidence.reference}.")
            elif clean(evidence.excerpt) not in clean(text):
                errors.append(f"{finding.id}: excerpt is not verbatim in {evidence.reference}.")
            quotes.setdefault(evidence.reference.split("#")[0], set()).add(clean(evidence.excerpt))
    for source_id, excerpts in quotes.items():
        if sum(len(quote.split()) for quote in excerpts) > 25:
            errors.append(f"{source_id}: all distinct quoted excerpts together must be at most 25 words.")
    for source_id, count in paraphrased_words.items():
        if count > 150:
            errors.append(f"{source_id}: paraphrased findings together must stay within 150 words.")
    expected = {question.id for question in discovery.questions}
    if Counter(c.question_id for c in dossier.coverage) != Counter(expected):
        errors.append("Coverage must contain each research question exactly once.")
    for coverage in dossier.coverage:
        if not set(coverage.finding_ids) <= set(ids):
            errors.append(f"{coverage.question_id}: unknown finding ID.")
        if coverage.status != "unanswered" and not coverage.finding_ids:
            errors.append(f"{coverage.question_id}: answered/partial coverage needs supported findings.")
        if coverage.status != "answered" and not coverage.gap:
            errors.append(f"{coverage.question_id}: record the unresolved gap.")
    return errors


def render_dossier(dossier: ResearchDossier, discovery: ResearchDiscovery, index: SourceIndex,
                   context: list[dict], run_id: str, *, local_prefix="../") -> str:
    refs = {f"{source.id}#{section.id}": (source, section) for source in index.sources for section in source.sections}
    lines = [f"# Recherchedossier: {dossier.topic}", "", f"Recherchelauf: `{run_id}`", "",
             dossier.scope_note, "",
             f"{len(index.sources)} Quellen eingelesen; {len(index.failures)} Abrufe/Importe fehlgeschlagen. "
             f"Dem Modell wurden {sum(len(s['sections']) for s in context)} ausgewählte Textabschnitte vorgelegt. "
             "Die Recherche wird gegen die vereinbarten Leitfragen geprüft; wissenschaftliche Unsicherheiten bleiben ausdrücklich erkennbar.", "",
             "## Befunde mit Quellenbezug", ""]
    for finding in dossier.findings:
        lines.extend([f"### {finding.id} — {finding.kind}", "", finding.statement, ""])
        if finding.claim_contract:
            contract = finding.claim_contract
            lines.extend([f"Aussagetyp: {contract.basis} / {contract.relation}. "
                          f"Geltungsbereich: {'; '.join(contract.scope)}.", ""])
            lines.extend(f"- Einschränkung: {q}" for q in contract.qualifications)
        if finding.illustration:
            lines.extend([f"**Bild zum Mitdenken:** {finding.illustration}", "",
                          f"**Grenze des Bildes:** {finding.illustration_limit}", ""])
        for evidence in finding.evidence:
            source, section = refs[evidence.reference]
            url = source.final_url or local_prefix + source.raw_path
            page = f", Seite {section.page}" if section.page else ""
            lines.append(f"- [{source.title.replace('[', '').replace(']', '')}]({url}){page} "
                         f"(`{evidence.reference}`): „{evidence.excerpt}“")
        lines.append("")
    questions = {q.id: q.question for q in discovery.questions}
    if dossier.evidence_version:
        lines.extend(["Automatisierte inhaltliche Belegprüfung dokumentiert. Dies ist keine unabhängige empirische "
                      "Bestätigung; deren Status steht je Befund im evidence_report.json.", ""])
    if dossier.synthesis:
        lines.extend(["## Quellenübergreifende Vergleiche", ""])
        for relation in dossier.synthesis:
            lines.extend([f"- {relation.dimension} ({relation.relation}, {relation.resolution}): {relation.explanation} "
                          f"Bedingungen: {relation.conditions}. Befunde: {', '.join(relation.finding_ids)}.", ""])
    lines.extend(["## Abdeckung und Lücken", ""])
    for row in dossier.coverage:
        lines.extend([f"- **{questions[row.question_id]}** — {row.status}; "
                      f"Befunde: {', '.join(row.finding_ids) or 'keine'}. {row.gap}", ""])
    lines.extend(["## Offene Fragen", "", *[f"- {q}" for q in dossier.open_questions], "",
                  "## Zugriffsprobleme", ""])
    lines.extend([f"- {failure['source']}: {failure['reason']}" for failure in index.failures] or ["Keine."])
    lines.extend(["", "## Quellenverzeichnis", ""])
    for source in index.sources:
        lines.append(f"- **{source.title}** — {', '.join(source.authors) or 'Autor nicht verifiziert'}, "
                     f"{source.published_date or 'Datum unbekannt'}. {source.final_url or source.raw_path} "
                     f"— `{source.id}`. Metadaten und Extraktionsgrenzen: siehe `models/source_index.yaml`.")
    return "\n".join(lines) + "\n"


def run_research(root: Path, *, resume=False, run_id: str | None = None,
                 reuse_sources: str | None = None, model=None, reasoning_effort=None) -> RunManifest:
    root = root.resolve()
    config = load_project(root)
    local_files = [(root / value).resolve() for value in config.local_sources]
    local_hashes = {str(path): file_hash(path) if path.is_file() else None for path in local_files}
    with project_lock(root):
        validate_model(model)
        validate_reasoning(reasoning_effort)
        selection = {"provider": "codex_cli", "model": model or config.runtime.codex_model,
                     "reasoning_effort": reasoning_effort} if model is not None or reasoning_effort is not None else None
        if resume:
            path = manifest_path(root, run_id)
            request_path = path.parent / "research_request.json"
            saved = json.loads(request_path.read_text(encoding="utf-8")).get("text_generation") if request_path.exists() else None
            if ((model is not None and model != (saved or {}).get("model")) or
                    (reasoning_effort is not None and reasoning_effort != (saved or {}).get("reasoning_effort"))):
                raise AppError("Modellauswahl geändert. Fortsetzen verwendet die gespeicherte Rechercheauswahl.",
                               code="inputs_changed", status="blocked")
            selection = saved
        bound_config = config.model_dump(mode="json")
        if resume:
            # A longer execution deadline does not alter the research inputs.
            # Keep the original fingerprint and still reject every content change.
            snapshot = read_yaml(path.parent / "project_snapshot.yaml")
            bound_config["runtime"]["text_timeout_seconds"] = snapshot["runtime"]["text_timeout_seconds"]
        config_hash = digest(bound_config)
        inputs = {"project": config_hash, "pipeline": __version__,
                  "research": RESEARCH_VERSION, "local_files": local_hashes}
        if selection is not None:
            inputs["text_generation"] = selection
        input_hash = digest(inputs)
        if resume:
            path = manifest_path(root, run_id)
            manifest = RunManifest.model_validate(read_yaml(path))
            if manifest.kind != "research" or manifest.input_hash != input_hash:
                raise AppError("Rechercheeingaben geändert. Bitte einen neuen Recherchelauf starten.",
                               code="inputs_changed", status="blocked")
        else:
            identifier = "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
            path = manifest_path(root, identifier)
            manifest = RunManifest(run_id=identifier, kind="research", project_hash=config_hash,
                                   input_hash=input_hash, stages={name: StageRecord() for name in
                                   ("discovery", "retrieval", "dossier", "review", "completeness", "publish")})
            write_yaml(path.parent / "project_snapshot.yaml", config.model_dump(mode="json"))
            write_json(path.parent / "research_request.json", {"text_generation": selection,
                       "requirements": requirements_for(config), "quality_policy": "research_quality.v1"})
        work = path.parent
        if reuse_sources:
            if resume:
                raise AppError("Quellenübernahme nur für einen neuen Lauf verwenden.", code="invalid_run")
            inherit_sources(root, work, manifest, config, local_files, reuse_sources)
        if resume and manifest.stages["retrieval"].status == "completed":
            try:
                previous_index = SourceIndex.model_validate_json((work / "source_index.json").read_text(encoding="utf-8"))
                if any(source.extraction_version != EXTRACTION_VERSION for source in previous_index.sources):
                    manifest.stages["retrieval"].status = "pending"
            except (OSError, ValueError):
                manifest.stages["retrieval"].status = "pending"
        if resume and manifest.stages["review"].status == "completed":
            try:
                review_context = json.loads((work / "review_context.json").read_text(encoding="utf-8"))
                if review_context.get("prompt_version") not in {"research_review.v5", "research_review.v6", "question_research.v1"}:
                    manifest.stages["review"].status = "pending"
            except (OSError, ValueError):
                manifest.stages["review"].status = "pending"
        write_json(root / "runs/latest.json", {"run_id": manifest.run_id})
        write_json(root / "research/active.json", {"run_id": manifest.run_id})
        if not resume and (root / "studio/outline.json").exists():
            # The previous plan and approvals remain in their run for reference,
            # but a fresh research request must not keep offering that old plan.
            previous_outline = json.loads((root / "studio/outline.json").read_text(encoding="utf-8"))
            write_json(root / "studio/previous_outline.json", previous_outline)
            (root / "studio/outline.json").unlink()
        adapter = CodexAdapter(config.runtime.model_copy(update={"codex_model": selection["model"]}) if selection else config.runtime,
                               reasoning_effort=selection.get("reasoning_effort") if selection else None)

        def progress(activity, quality=None, round_number=None):
            previous = json.loads((work / "research_activity.json").read_text(encoding="utf-8")) if (work / "research_activity.json").exists() else {}
            data = {**previous, "phase": "research", "activity": activity,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "model_call_limit": effective_limits(work, config.research_limits, input_hash).model_calls,
                    "search_round_limit": config.research_limits.search_rounds}
            if quality is not None:
                data["research_quality"] = quality
            question_path = work / "research_questions.json"
            if question_path.exists():
                # Existing Studio processes also read this envelope. Keep the new
                # ledger visible without restarting a server holding session keys.
                data["research_questions"] = json.loads(question_path.read_text(encoding="utf-8"))
            if round_number is not None:
                data["research_round"] = round_number
            write_json(work / "research_activity.json", data)
            write_json(work / "progress.json", data)
            observer = run_observer.get()
            if observer:
                observer(manifest)

        def invoke(prompt, output_type, version, *, search=False):
            number = reserve_call(work, effective_limits(work, config.research_limits, input_hash), search=search)
            from .research_status import record_request
            record_request(work / "calls" / f"call_{number:03d}", output_type.__name__, prompt)
            if search:
                progress("Quellen zu offenen Leitfragen werden gesucht")
            return adapter.structured(prompt, output_type, work / "calls" / f"call_{number:03d}",
                                      prompt_version=version, search=search)

        def discovery_stage():
            brief = {key: value for key, value in config.model_dump(mode="json").items()
                     if key in {"topic", "central_question", "language", "audience_level", "prior_knowledge",
                                "depth_request", "focus_questions", "excluded_topics", "seed_people", "seed_urls"}}
            maximum = min(config.research_limits.sources, max(8, len(config.focus_questions) * 2))
            brief["attachments"] = attachments.context(root)
            brief["requirements"] = requirements_for(config)
            prompt = (
                instructions("research_discovery", maximum=maximum) + " " + TERMINOLOGY +
                attachments.MATERIAL_RULES +
                instructions("research_discovery_attachments") + "\n" + json.dumps(brief, ensure_ascii=False))
            discovery, metadata = invoke(prompt, ResearchDiscovery, "research_discovery.v3-attachments", search=True)
            if discovery.topic != config.topic or len(discovery.candidates) > maximum:
                raise AppError("Suchantwort verletzt Thema oder Quellenlimit.", code="invalid_model_output")
            write_json(work / "discovery.json", discovery.model_dump(mode="json"))
            write_json(work / "discovery_metadata.json", metadata)
            evidence_files = list((work / "calls").glob("call_*/search_events.json"))
            return [work / "discovery.json", work / "discovery_metadata.json", *evidence_files]

        def retrieval_stage():
            progress("Gefundene Originaltexte werden eingelesen")
            discovery = ResearchDiscovery.model_validate_json((work / "discovery.json").read_text(encoding="utf-8"))
            uploaded = {attachments.attachment_path(root, row): row for row in attachments.inventory(root)}
            candidates = [(SourceCandidate(url=str(p), title=uploaded.get(p, {}).get("name", p.name), authors=[], published_date="",
                                           rationale="User-supplied local material; claims and provenance are unverified.",
                                           primary_source=False), p) for p in local_files]
            candidates += [(SourceCandidate(url=url, title=url, authors=[], published_date="",
                                             rationale="Explicit seed URL in project.yaml", primary_source=False), None) for url in config.seed_urls]
            candidates += [(candidate, None) for candidate in discovery.candidates]
            sources, failures, outputs, seen, hashes = [], [], [], set(), set()
            cache_dir = work / "retrieval_cache"
            attempted = 0
            for candidate, local in candidates:
                address = str(local) if local else candidate.url
                try:
                    identity = str(local) if local else canonical_url(candidate.url)
                except (AppError, ValueError) as exc:
                    failures.append({"source": address, "reason": str(exc)})
                    continue
                if identity in seen:
                    continue
                seen.add(identity)
                if attempted >= config.research_limits.sources:
                    failures.append({"source": address, "reason": "Quellenlimit erreicht; nicht abgerufen."})
                    continue
                attempted += 1
                checkpoint = cache_dir / (digest(identity) + ".json")
                try:
                    document, downloaded = None, None
                    if checkpoint.exists():
                        try:
                            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
                            cached = inside(root, saved["processed"])
                            if cached.is_file() and file_hash(cached) == saved["sha256"]:
                                candidate_doc = SourceDocument.model_validate_json(cached.read_text(encoding="utf-8"))
                                raw = inside(root, candidate_doc.raw_path)
                                if raw.is_file() and file_hash(raw) == candidate_doc.raw_hash:
                                    if candidate_doc.extraction_version == EXTRACTION_VERSION:
                                        document, processed = candidate_doc, cached
                                    else:
                                        mime = {"pdf": "application/pdf", "html": "text/html", "text": "text/plain"}[candidate_doc.type]
                                        downloaded = (raw.read_bytes(), mime, candidate_doc.final_url)
                        except (OSError, ValueError, KeyError, AppError):
                            pass
                    if document is None:
                        document, processed = import_source(candidate, root, manifest.run_id, local=local, downloaded=downloaded)
                        write_json(checkpoint, {"processed": processed.relative_to(root).as_posix(), "sha256": file_hash(processed)})
                    # Keep independently retrieved provenance even when an upload
                    # contains an identical copy; the local file must also remain
                    # available for the project's input-integrity check.
                    independent_copy = bool(document.url) and any(
                        s.text_hash == document.text_hash and not s.url for s in sources)
                    if document.text_hash in hashes and not independent_copy:
                        failures.append({"source": address, "reason": "Identischer Quellentext bereits eingelesen."})
                        continue
                    hashes.add(document.text_hash)
                    sources.append(document)
                    outputs.extend([processed, inside(root, document.raw_path)])
                except (AppError, OSError, ValueError) as exc:
                    failures.append({"source": address, "reason": str(exc)})
                write_json(work / "retrieval_progress.json", {"imported": len(sources), "attempted": attempted, "failures": failures})
            index = SourceIndex(sources=sources, failures=failures)
            write_json(work / "source_index.json", index.model_dump(mode="json"))
            if not sources:
                raise AppError("Keine gefundene Quelle konnte als Text eingelesen werden. Abrufbericht prüfen.",
                               code="no_readable_sources", status="blocked")
            return [work / "source_index.json", *outputs]

        def synthesis_inputs():
            discovery = ResearchDiscovery.model_validate_json((work / "discovery.json").read_text(encoding="utf-8"))
            index = SourceIndex.model_validate_json((work / "source_index.json").read_text(encoding="utf-8"))
            context = source_context(index, discovery)
            return discovery, index, context

        def question_result_ready():
            state_path = work / "question_research/state.json"
            destination = work / "complete_research"
            if not state_path.exists() or not all((destination / name).exists() for name in (
                    "dossier.json", "discovery.json", "source_index.json", "source_context.json", "source_review.json")):
                return False
            state = read_value(state_path)
            quality_path = work / "research_quality_gate.json"
            if state["phase"] != "completed" or not quality_path.exists():
                return False
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            dossier = json.loads((destination / "dossier.json").read_text(encoding="utf-8"))
            return (quality.get("passed") and quality.get("question_workflow") == "question_research.v1"
                    and quality.get("dossier_hash") == digest(dossier))


        def dossier_stage():
            progress("Belege werden zu Grundlagen und Erklärungen verbunden")
            discovery, index, context = synthesis_inputs()
            outputs = run_question_research(root, work, config, discovery, index, invoke, progress,
                                           limits=lambda: effective_limits(work, config.research_limits, input_hash))
            _, _, checked_context, checked_dossier = load_complete_research(work)
            write_json(work / "dossier.json", checked_dossier.model_dump())
            write_json(work / "source_context.json", checked_context)
            write_json(work / "reference_check.json", {"errors": []})
            return outputs + [work / "dossier.json", work / "source_context.json", work / "reference_check.json"]

        def review_stage():
            outputs = []
            if not (work / "question_research/state.json").exists():
                discovery, index, context = synthesis_inputs()
                dossier = ResearchDossier.model_validate_json((work / "dossier.json").read_text(encoding="utf-8"))
                outputs = run_question_research(root, work, config, discovery, index, invoke, progress,
                                               dossier=dossier, context=context,
                                               limits=lambda: effective_limits(work, config.research_limits, input_hash))
            elif not question_result_ready():
                discovery, index, context = synthesis_inputs()
                outputs = run_question_research(root, work, config, discovery, index, invoke, progress, context=context,
                                               limits=lambda: effective_limits(work, config.research_limits, input_hash))
            _, _, _, checked = load_complete_research(work)
            reviewed = json.loads((work / "complete_research/source_review.json").read_text(encoding="utf-8"))
            write_json(work / "reviewed_dossier.json", checked.model_dump())
            write_json(work / "review.json", reviewed)
            write_json(work / "review_context.json", {"prompt_version": "question_research.v1"})
            return outputs + [work / "reviewed_dossier.json", work / "review.json", work / "review_context.json"]

        def completeness_stage():
            if question_result_ready():
                # The question workflow already ran both independent gates.
                return [*sorted((work / "complete_research").glob("*.json")),
                        work / "research_quality_gate.json", work / "research_quality.md",
                        work / "research_questions.json", work / "research_questions.md"]
            discovery, index, context = synthesis_inputs()
            dossier = ResearchDossier.model_validate_json((work / "reviewed_dossier.json").read_text(encoding="utf-8"))
            return run_question_research(root, work, config, discovery, index, invoke, progress,
                                         dossier=dossier, context=context,
                                         limits=lambda: effective_limits(work, config.research_limits, input_hash))

        def publish_stage():
            discovery, index, context, dossier = load_complete_research(work)
            quality = json.loads((work / "research_quality_gate.json").read_text(encoding="utf-8"))
            final_review_path = work / "complete_research/source_review.json"
            if not quality["passed"]:
                raise AppError("Die Recherche ist noch nicht vollständig geprüft.", code="research_coverage_incomplete", status="blocked")
            files = {
                "research/research_plan.yaml": {"schema_version": "1.0", "run_id": manifest.run_id,
                    "topic": config.topic, "questions": [q.model_dump() for q in discovery.questions],
                    "limits": config.research_limits.model_dump(), "limitations": discovery.limitations},
                "research/source_candidates.yaml": {"schema_version": "1.0", "run_id": manifest.run_id,
                    "candidates": [c.model_dump() for c in discovery.candidates], "failures": index.failures},
                "models/source_index.yaml": index.model_dump(mode="json"),
                "research/dossier.yaml": dossier.model_dump(mode="json"),
            }
            outputs = []
            initial_receipt = {"lane": "initial_discovery", "observed_queries":
                json.loads((work / "discovery_metadata.json").read_text(encoding="utf-8")).get("observed_search_queries", []),
                "selected_candidates": json.loads((work / "discovery.json").read_text(encoding="utf-8"))["candidates"],
                "selection_limitations": discovery.limitations,
                "failures": index.failures, "query_provenance": "Tool-observed queries; absent for historical receipts."}
            write_json(root / "research/discovery_receipt.json", initial_receipt)
            outputs.append(root / "research/discovery_receipt.json")
            for name in ("evidence_report.json", "search_receipts.json", "objections.json"):
                source = work / "complete_research" / name
                if source.exists():
                    destination = root / "research" / name
                    atomic_text(destination, source.read_text(encoding="utf-8"))
                    outputs.append(destination)
            for relative, data in files.items():
                destination = root / relative
                write_yaml(destination, data)
                outputs.append(destination)
            briefing = render_dossier(dossier, discovery, index, context, manifest.run_id)
            atomic_text(work / "research_briefing.md", render_dossier(dossier, discovery, index, context,
                        manifest.run_id, local_prefix="../../"))
            atomic_text(root / "research/research_briefing.md", briefing)
            atomic_text(root / "research/quality.md", (work / "research_quality.md").read_text(encoding="utf-8"))
            if (work / "research_questions.md").exists():
                for source_name, destination_name in (("research_questions.md", "questions.md"), ("research_questions.json", "questions.json")):
                    destination = root / "research" / destination_name
                    atomic_text(destination, (work / source_name).read_text(encoding="utf-8"))
                    outputs.append(destination)
            atomic_text(root / "research/open_questions.md", "# Offene Recherchefragen\n\n" +
                        "\n".join(f"- {q}" for q in dossier.open_questions) + "\n\n" +
                        "\n".join(f"- {c.gap}" for c in dossier.coverage if c.status != "answered") + "\n")
            write_json(root / "research/latest.json", {"run_id": manifest.run_id})
            write_json(root / "reports/research_quality.json", {
                "run_id": manifest.run_id, "reference_check": "passed", "model_review": "no_remaining_issues",
                "human_reviewed": False, "complete_topic_coverage": True, "coverage_scope": "agreed_brief",
                "quality_gate": quality, "sources": len(index.sources),
                "findings": len(dossier.findings), "access_failures": index.failures,
                "budget": json.loads((work / "budget.json").read_text(encoding="utf-8")),
                "source_provenance": json.loads((work / "discovery_metadata.json").read_text(encoding="utf-8")),
                "initial_review": json.loads((work / "review.json").read_text(encoding="utf-8")),
                "review": json.loads((final_review_path if final_review_path.exists() else work / "review.json").read_text(encoding="utf-8")),
            })
            return outputs + [work / "research_briefing.md", root / "research/research_briefing.md",
                              root / "research/open_questions.md", root / "research/latest.json",
                              root / "reports/research_quality.json", root / "research/quality.md"]

        return execute_stages(root, manifest, path, {"discovery": discovery_stage, "retrieval": retrieval_stage,
            "dossier": dossier_stage, "review": review_stage, "completeness": completeness_stage, "publish": publish_stage})
