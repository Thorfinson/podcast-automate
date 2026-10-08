"""Topic -> live search -> retrieved documents -> evidence-linked research dossier."""
from __future__ import annotations

import json
import hashlib
import re
import shutil
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .prompts import fragment, instructions
from . import __version__
from . import attachments
from .codex import CodexAdapter  # noqa: F401  (tests patch podcast_automate.research.CodexAdapter.structured)
from .errors import AppError
from .execution import ExecutionChoice, selected_execution
from .question_budget import write_calibration
from .run_budget import (accepted_gaps, approve_research_plan, criterion_gaps, dispute_decisions, effective_limits,
                         plan_approval_for, residual_finish, retry_requests, text_switch)
from .editorial import TERMINOLOGY, TEACHING_SCOPE
from .models import RunManifest, StageRecord
from .cost_estimate import fallback_usd, spent_usd
from .provider_pool import (AdapterPool, api_selection, check_adapter_versions, read_billing, subscription_selection,
                            text_generation_settings, with_web_search)
from .research_models import ResearchDiscovery, ResearchDossier, SourceCandidate, SourceDocument, SourceIndex
from .research_dates import run_date
from .research_reader import source_facts
from .runner import execute_stages, manifest_path, outputs_valid, run_observer
from .research_gap_probe import suffix as probe_suffix
from .research_quality import load_complete_research, requirements_for
from .question_research import run_question_research
from .research_advisor import advisor_selection
from .project_versions import imported_library
from .research_ledger import read_value
from .research_evidence import verbatim
from .sources import EXTRACTION_VERSION, canonical_url, clean, import_failure, import_source, library_view, load_library
from .storage import (atomic_text, bound_brief, digest, file_hash, file_lock, inside, load_project, project_hash, project_lock,
                      read_text, read_optional_json, read_yaml, write_json, write_yaml)
from .text_settings import validate_model, validate_reasoning

RESEARCH_VERSION = "research.v3-complete-brief"
PLAIN_LANGUAGE = TERMINOLOGY + TEACHING_SCOPE + fragment("plain_language")
# Failures after which no model response exists: the reservation is returned to the run budget.
# Model work that was rejected (invalid output, unobserved search, provider failure) stays charged.
UNANSWERED_CALL_CODES = frozenset({
    "timeout", "stall", "interrupted", "missing_executable", "codex_missing", "claude_missing",
    "authentication_required", "subscription_required", "claude_version", "prompt_too_large",
    "invalid_output_schema", "unsupported_codex_launcher", "unsupported_claude_launcher",
    "anthropic_key_required", "anthropic_authentication"})


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


def reserve_call(work: Path, limits, *, search=False, billed=False) -> int:
    """Charge one call against the run budget and return its call number.

    Call numbers come from a separate sequence, so a refunded reservation never reuses a
    directory that already holds receipts of an earlier attempt. A ``billed`` call (an API key, OpenRouter, a billed
    web search) also needs the run's money limit and room under it (D-146); a billed call already in flight may
    overshoot it by its own cost, never by a further call.

    A stop names the limit that ran out in ``details["limit"]`` (``model_calls`` or ``search_rounds``, the
    ``ResearchLimits`` field); the run manifest keeps it (runner.failure_details), so the Studio need not read the
    German message.
    """
    with file_lock(work / ".budget.lock", timeout=30):
        path = work / "budget.json"
        budget = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"model_calls": 0, "search_rounds": 0}
        if budget["model_calls"] >= limits.model_calls:
            raise AppError(f"Limit von {limits.model_calls} Modellaufrufen erreicht. Der bisherige Stand bleibt gespeichert.",
                           code="research_budget_exhausted", status="blocked", details={"limit": "model_calls"})
        if search and budget["search_rounds"] >= limits.search_rounds:
            raise AppError(f"Limit von {limits.search_rounds} Rechercherunden erreicht. Der bisherige Stand bleibt gespeichert.",
                           code="research_budget_exhausted", status="blocked", details={"limit": "search_rounds"})
        if billed:
            check_money(budget, getattr(limits, "cost_usd", None))
        budget["model_calls"] += 1
        budget["search_rounds"] += int(search)
        budget["sequence"] = budget.get("sequence", budget["model_calls"] - 1) + 1
        write_json(path, budget)
        return budget["sequence"]


def refund_call(work: Path, number: int, *, search=False) -> bool:
    """Return the reservation of a call that produced no model response; never twice for one call."""
    with file_lock(work / ".budget.lock", timeout=30):
        path = work / "budget.json"
        if not path.exists():
            return False
        budget = json.loads(path.read_text(encoding="utf-8"))
        refunded = budget.setdefault("refunded", [])
        if number in refunded:
            return False
        refunded.append(number)
        budget["model_calls"] = max(0, budget.get("model_calls", 0) - 1)
        if search:
            budget["search_rounds"] = max(0, budget.get("search_rounds", 0) - 1)
        write_json(path, budget)
        return True


def check_money(budget, limit):
    """Refuse a billed call without a money limit or with the limit reached (D-146)."""
    if limit is None:
        raise AppError("Dieser Lauf rechnet Aufrufe über einen API-Key ab und braucht eine Kostengrenze in USD. Die "
                       "Grenze in den Einstellungen setzen oder mit pla approve --cost-usd festlegen.",
                       code="cost_limit_required", status="blocked")
    spent = spent_usd(budget)
    if spent >= limit:
        raise AppError(f"Kostengrenze von {limit:g} USD erreicht ({spent:.2f} USD ausgegeben). Der bisherige Stand "
                       "bleibt gespeichert.", code="cost_limit_reached", status="blocked",
                       details={"cost_limit_usd": limit, "spent_usd": spent})


def add_money(budget, rows) -> None:
    """Count one call's billing rows (provider_pool.read_billing): a priced attempt with its money, an attempt without
    a reported cost (timeout, interrupted) at the run's mean so far or the table's value, a free one not at all."""
    for row in rows:
        usd = row.get("usd")
        if row.get("state") == "priced" and isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd >= 0:
            budget["billed_usd"] = round(budget.get("billed_usd", 0.0) + usd, 6)
            budget["priced_attempts"] = budget.get("priced_attempts", 0) + 1
        elif row.get("state") in {"unpriced", "started"}:
            priced = budget.get("priced_attempts", 0)
            each = (budget.get("billed_usd", 0.0) / priced if priced else
                    fallback_usd(row.get("model")) or 0.0)
            budget["estimated_usd"] = round(budget.get("estimated_usd", 0.0) + each, 6)
            budget["unpriced_attempts"] = budget.get("unpriced_attempts", 0) + 1


def settle_call(work: Path, number: int, directory: Path | None = None) -> bool:
    """Count the money of a finished call into the run budget, once per call (D-148). Writes only ``budget.json`` and
    only for a call with billing rows, so the budget of a subscription run stays as it was."""
    rows = read_billing(directory or work / "calls" / f"call_{number:03d}")
    if not rows:
        return False
    with file_lock(work / ".budget.lock", timeout=30):
        path = work / "budget.json"
        budget = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"model_calls": 0, "search_rounds": 0}
        settled = budget.setdefault("settled", [])
        if number in settled:
            return False
        add_money(budget, rows)
        settled.append(number)
        write_json(path, budget)
        return True


def settle_external(work: Path, source: str, usd) -> bool:
    """Count money spent outside a model call of this run, such as the Jev gap probe, once per ``source``."""
    if not isinstance(usd, (int, float)) or isinstance(usd, bool) or usd <= 0:
        return False
    with file_lock(work / ".budget.lock", timeout=30):
        path = work / "budget.json"
        budget = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"model_calls": 0, "search_rounds": 0}
        settled = budget.setdefault("settled_external", [])
        if source in settled:
            return False
        budget["external_usd"] = round(budget.get("external_usd", 0.0) + usd, 6)
        settled.append(source)
        write_json(path, budget)
        return True


def unanswered(error: AppError) -> bool:
    return error.status == "waiting_for_quota" or error.code in UNANSWERED_CALL_CODES


def reconcile_budget(work: Path) -> list[int]:
    """Refund calls a stopped or killed worker never finished: no response and no charged failure.

    Runs before a resume, under the project lock, so no call is in flight. A call that ended with a
    charged failure (invalid output, failed provider turn) keeps its charge.
    """
    with file_lock(work / ".budget.lock", timeout=30):
        path = work / "budget.json"
        if not path.exists():
            return []
        budget = json.loads(path.read_text(encoding="utf-8"))
        settled = budget.get("settled", [])
        money = False
        for directory in sorted((work / "calls").glob("call_*")):
            # A billed call the worker never settled (killed after or during the call): its rows, where a killed
            # attempt left one that only says it started, count now (D-148).
            match = re.fullmatch(r"call_(\d+)", directory.name)
            rows = read_billing(directory) if match and int(match[1]) not in settled else []
            if rows:
                add_money(budget, rows)
                budget.setdefault("settled", []).append(int(match[1]))
                money = True
        refunded = budget.setdefault("refunded", [])
        found = []
        for directory in sorted((work / "calls").glob("call_*")):
            match = re.fullmatch(r"call_(\d+)", directory.name)
            if not match or int(match[1]) in refunded or (directory / "response.json").exists():
                continue
            if not (directory / "output_schema.json").exists() and not (directory / "activity.json").exists():
                continue
            failure = read_optional_json(directory / "failure.json", {}) or {}
            if failure and failure.get("code") not in UNANSWERED_CALL_CODES:
                continue
            choice = read_optional_json(directory / "provider_choice.json", {}) or {}
            number = int(match[1])
            refunded.append(number)
            found.append(number)
            budget["model_calls"] = max(0, budget.get("model_calls", 0) - 1)
            if choice.get("search"):
                budget["search_rounds"] = max(0, budget.get("search_rounds", 0) - 1)
        if found or money:
            write_json(path, budget)
        return found


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
                        **source_facts(source), "text_hash": source.text_hash,
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
            elif not verbatim(evidence.excerpt, text):
                errors.append(f"{finding.id}: excerpt is not verbatim in {evidence.reference}.")
            quotes.setdefault(evidence.reference.split("#")[0], set()).add(clean(evidence.excerpt))
    # A dossier assembled from the verified answers holds all of them; the 25-word quote rule applies to the broadcast
    # script instead (script_checks.quotation_errors; the user's choice, 2026-10-01).
    for source_id, excerpts in ({} if dossier.assembled else quotes).items():
        if sum(len(quote.split()) for quote in excerpts) > 25:
            errors.append(f"{source_id}: all distinct quoted excerpts together must be at most 25 words.")
    for source_id, count in ({} if dossier.assembled else paraphrased_words).items():
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
                   context: list[dict], run_id: str, *, language: str, local_prefix="../") -> str:
    """The readable research_briefing.md the Studio's dossier view shows, its fixed words in the podcast's content
    language (D-153); the findings and sources keep their own words."""
    from .content_text import text as wording
    say = lambda key, **values: wording(language, key, **values)
    refs = {f"{source.id}#{section.id}": (source, section) for source in index.sources for section in source.sections}
    lines = [f"# {say('dossier_title', topic=dossier.topic)}", "", say("dossier_run", run_id=run_id), "",
             dossier.scope_note, "",
             say("dossier_read", sources=len(index.sources), failures=len(index.failures))
             + say("dossier_context", sections=sum(len(s['sections']) for s in context))
             + say("dossier_checked"), "",
             f"## {say('dossier_findings')}", ""]
    for finding in dossier.findings:
        lines.extend([f"### {finding.id} — {finding.kind}", "", finding.statement, ""])
        if finding.claim_contract:
            contract = finding.claim_contract
            lines.extend([say("dossier_contract", basis=contract.basis, relation=contract.relation,
                              scope="; ".join(contract.scope)), ""])
            lines.extend(f"- {say('dossier_qualification', text=q)}" for q in contract.qualifications)
        if finding.illustration:
            lines.extend([f"**{say('dossier_illustration')}** {finding.illustration}", "",
                          f"**{say('dossier_illustration_limit')}** {finding.illustration_limit}", ""])
        for evidence in finding.evidence:
            source, section = refs[evidence.reference]
            url = source.final_url or local_prefix + source.raw_path
            page = say("dossier_page", page=section.page) if section.page else ""
            lines.append(f"- [{source.title.replace('[', '').replace(']', '')}]({url}){page} "
                         f"(`{evidence.reference}`): „{evidence.excerpt}“")
        lines.append("")
    questions = {q.id: q.question for q in discovery.questions}
    if dossier.evidence_version:
        lines.extend([say("dossier_evidence_check"), ""])
    if dossier.synthesis:
        lines.extend([f"## {say('dossier_synthesis')}", ""])
        for relation in dossier.synthesis:
            lines.extend([f"- {relation.dimension} ({relation.relation}, {relation.resolution}): {relation.explanation} "
                          + say("dossier_relation", conditions=relation.conditions,
                                findings=", ".join(relation.finding_ids)), ""])
    lines.extend([f"## {say('dossier_coverage')}", ""])
    for row in dossier.coverage:
        findings = say("dossier_coverage_findings", findings=", ".join(row.finding_ids) or say("dossier_none"))
        lines.extend([f"- **{questions[row.question_id]}** — {row.status}; {findings} {row.gap}", ""])
    lines.extend([f"## {say('dossier_open')}", "", *[f"- {q}" for q in dossier.open_questions], "",
                  f"## {say('dossier_access')}", ""])
    lines.extend([f"- {failure['source']}: {failure['reason']}" for failure in index.failures]
                 or [say("dossier_no_access_problem")])
    lines.extend(["", f"## {say('dossier_sources')}", ""])
    for source in index.sources:
        lines.append(f"- **{source.title}** — {', '.join(source.authors) or say('dossier_no_author')}, "
                     f"{source.published_date or say('dossier_no_date')}. {source.final_url or source.raw_path} "
                     f"— `{source.id}`. {say('dossier_metadata')}")
    return "\n".join(lines) + "\n"


def render_open_questions(dossier: ResearchDossier, accepted_rows, gap_probes, *, language: str) -> str:
    """research/open_questions.md: the dossier's open questions and unanswered coverage gaps, then the accepted gaps,
    its fixed words in the podcast's content language (D-153). Each open question carries what the corpus probe found
    for it, so a reader sees whether the gap was checked against the stored sections or only asserted."""
    from .content_text import text as wording
    accepted_lines = [f"- {wording(language, 'open_accepted', gap=row.get('question') or row['task_id'])}"
                      + (f" ({row['reason']})" if row.get("reason") else "") for row in accepted_rows]
    probes = {row["text"]: probe_suffix(row, language=language) for row in gap_probes}
    open_line = lambda text: f"- {text}" + (f" {probes[text]}" if text in probes else "")
    return (f"# {wording(language, 'open_title')}\n\n" +
            "\n".join(open_line(q) for q in dossier.open_questions) + "\n\n" +
            "\n".join(open_line(c.gap) for c in dossier.coverage if c.status != "answered") + "\n" +
            ("\n" + "\n".join(accepted_lines) + "\n" if accepted_lines else ""))


PLAN_REVIEW_MODES = {None, "required", "auto"}


DISCOVERY_VERSION = "research_discovery.v6-archives"


def saved_discovery(work, topic):
    """A first search this run already paid for and a later check stopped: adopted on resume instead of searching
    the web again, as long as it answered the same prompt generation and keeps the topic (Asimov, 2026-09-30)."""
    for folder in sorted((work / "calls").glob("call_*"), reverse=True):
        metadata = read_optional_json(folder / "metadata.json", {}) or {}
        if metadata.get("prompt_version") != DISCOVERY_VERSION:
            continue
        try:
            found = ResearchDiscovery.model_validate_json((folder / "response.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if found.topic == topic:
            return found, {**metadata, "adopted_from": folder.name}
    return None


def unanswered_questions(work: Path) -> list[str]:
    """Rows of the run's question ledger that hold neither a verified answer nor a block, in plan order: the row rule
    of ``question_research.require_answers``. A run without a ledger (the composed workflow before it) has none."""
    state_path = work / "question_research/state.json"
    if state_path.exists():
        state = read_value(state_path)
        tasks = state.get("tasks") or {}
        order = [spec["id"] for spec in (state.get("plan") or {}).get("tasks", []) if spec.get("id") in tasks]
        return [task_id for task_id in order or list(tasks) if tasks[task_id].get("status") not in {"verified", "blocked"}]
    ledger = read_optional_json(work / "research_questions.json", {}) or {}
    return [str(row.get("id")) for row in ledger.get("questions") or []
            if isinstance(row, dict) and row.get("status") not in {"verified", "blocked"}]


def latest_research_run(root: Path):
    """The research run whose stored sources seed a new one: the newest completed, else the starting library a new
    version brought along (project_versions, D-168), else the newest at all; None without one. A run stopped right
    after its start holds almost no sources (Transformer, 2026-09-30)."""
    newest = None
    for folder in sorted((root / "runs").glob("run_*"), reverse=True):
        try:
            manifest = read_yaml(folder / "run_manifest.yaml")
        except (OSError, ValueError):
            continue
        if manifest.get("kind") != "research":
            continue
        if manifest.get("status") == "completed":
            return folder.name
        newest = newest or folder.name
    return imported_library(root) or newest


def run_research(root: Path, *, resume=False, run_id: str | None = None,
                 reuse_sources: str | None = None, model=None, reasoning_effort=None, backend=None,
                 plan_review: str | None = None, api_key=None, seed_corpus: str | None = None,
                 web_search: str | None = None, perplexity_key=None) -> RunManifest:
    # ``api_key`` is the key of a billed text provider (OpenRouter, or Claude on the Anthropic key). ``web_search``
    # "perplexity" binds a new run to the Perplexity search (D-151); a resume keeps what the run started with.
    """Run or resume the research lane.

    ``plan_review`` decides the plan gate before the first task call: ``"required"`` (the CLI and
    Studio) stops the run with the projection until ``plan_approval.json`` approves the plan,
    ``"auto"`` (``pla research --approve-plan``) records an automatic approval and is remembered in
    ``research_request.json`` for the run's resumes, ``None`` leaves the decision to the caller and
    proceeds after scoping.
    """
    if plan_review not in PLAN_REVIEW_MODES:
        raise AppError("Die Planfreigabe kennt nur required, auto oder keine Angabe.", code="invalid_request", status="blocked")
    root = root.resolve()
    config = load_project(root)
    local_files = [(root / value).resolve() for value in config.local_sources]
    # A local source is read into the run and its text goes to the model: only files of this project qualify, never
    # an absolute path or one that leaves the folder (review 2026-10-02).
    outside = [value for value, path in zip(config.local_sources, local_files) if not path.is_relative_to(root)]
    if outside:
        raise AppError("Lokale Quellen müssen im Projektordner liegen; außerhalb liegt: " + ", ".join(outside) +
                       ". Die Datei in den Projektordner kopieren oder im Studio als Material hochladen und "
                       "local_sources in project.yaml anpassen.", code="local_source_outside", status="blocked")
    local_hashes = {str(path): file_hash(path) if path.is_file() else None for path in local_files}
    with project_lock(root):
        validate_model(model)
        if backend in {"claude_code", "auto"}:
            selection = subscription_selection(config, backend, model=model, reasoning_effort=reasoning_effort)
        elif backend == "claude_api":
            selection = api_selection(config, model=model, reasoning_effort=reasoning_effort)
        elif backend == "openrouter":
            # OpenRouter has no web tools; its research searches through Perplexity (D-151).
            if web_search != "perplexity" and not resume:
                raise AppError("Recherche mit einem OpenRouter-Modell braucht die Websuche über Perplexity.",
                               code="invalid_backend", status="blocked")
            selection = text_generation_settings(config, backend="openrouter", model=model,
                                                 reasoning_effort=reasoning_effort)
        elif backend in {None, "codex_cli"}:
            validate_reasoning(reasoning_effort)
            selection = {"provider": "codex_cli", "model": model or config.runtime.codex_model,
                         "reasoning_effort": reasoning_effort} if model is not None or reasoning_effort is not None else None
        else:
            raise AppError("Für die Recherche stehen codex_cli, claude_code, claude_api, auto und mit Perplexity "
                           "openrouter zur Verfügung.", code="invalid_backend", status="blocked")
        if web_search not in (None, "model") and not resume:
            selection = with_web_search(selection or {"provider": "codex_cli", "model": config.runtime.codex_model,
                                                      "reasoning_effort": None}, web_search)
        if resume:
            path = manifest_path(root, run_id)
            request_path = path.parent / "research_request.json"
            saved = json.loads(request_path.read_text(encoding="utf-8")).get("text_generation") if request_path.exists() else None
            if ((backend is not None and backend != ((saved or {}).get("provider") or "codex_cli")) or
                    (model is not None and model != (saved or {}).get("model")) or
                    (reasoning_effort is not None and reasoning_effort != (saved or {}).get("reasoning_effort"))):
                raise AppError("Modellauswahl geändert. Fortsetzen verwendet die gespeicherte Rechercheauswahl.",
                               code="inputs_changed", status="blocked")
            selection = saved
            if selection:
                check_adapter_versions(selection)
        bound_config = config
        if resume:
            # Operational settings (deadlines, limits, voices, host names) do not alter the research inputs: they are
            # hashed as the run started (storage.bound_brief), and every content change is still rejected.
            bound_config = bound_brief(config, read_yaml(path.parent / "project_snapshot.yaml"), "research")
        # One rule for the brief's identity, shared with every other lane: ``project_hash`` drops
        # a field that is unset, so existing research manifests keep their pre-field hash.
        config_hash = project_hash(bound_config)
        inputs = {"project": config_hash, "pipeline": __version__,
                  "research": RESEARCH_VERSION, "local_files": local_hashes}
        if selection is not None:
            inputs["text_generation"] = selection
        if resume:
            if seed_corpus:
                raise AppError("Eine Startbibliothek gilt nur für einen neuen Lauf.", code="invalid_run")
            seed_corpus = (read_optional_json(manifest_path(root, run_id).parent / "research_request.json", {})
                           or {}).get("seed_corpus")
        elif seed_corpus and seed_corpus != imported_library(root):
            # A new version's library has no run here (project_versions); load_library checks its documents as any.
            parent = read_yaml(manifest_path(root, seed_corpus))
            if parent.get("kind") != "research":
                raise AppError("Die Startbibliothek muss aus einem Recherchelauf stammen.", code="invalid_run")
        if seed_corpus:
            # Only when used, so the inputs of every other run hash as before.
            inputs["seed_corpus"] = seed_corpus
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
            # The project's execution choice is fixed at the start, as for scripts; a resume keeps it.
            write_json(path.parent / "research_request.json", {"text_generation": selection,
                       "requirements": requirements_for(config), "quality_policy": "research_quality.v1",
                       "plan_review": plan_review, "execution": selected_execution(root).model_dump(),
                       **({"seed_corpus": seed_corpus} if seed_corpus else {})})
        work = path.parent
        request = read_optional_json(work / "research_request.json", {}) or {}
        # An automatic plan approval asked for at the start stays with the run; a required review does too.
        saved_review = request.get("plan_review")
        review_mode = "auto" if "auto" in (plan_review, saved_review) else plan_review
        # Runs started before the field existed answered one task at a time and keep doing so.
        execution = ExecutionChoice.model_validate(request.get("execution") or {})
        # The earlier run's stored sources: offered to the discovery, copied instead of downloaded when chosen.
        library = load_library(root, seed_corpus)
        if reuse_sources:
            if resume:
                raise AppError("Quellenübernahme nur für einen neuen Lauf verwenden.", code="invalid_run")
            inherit_sources(root, work, manifest, config, local_files, reuse_sources)
        if resume:
            # A stopped worker leaves its reservation behind; the call it never finished is not charged.
            reconcile_budget(work)
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
        # The inputs keep the selection the run started with; an approved switch only changes who answers.
        selection = text_switch(work, manifest.input_hash, selection)
        if selection:
            check_adapter_versions(selection)
        # The key goes only to the provider that bills it: OpenRouter, or Claude on the Anthropic key (D-145).
        provider = (selection or {}).get("provider")
        keys = {"api_key": api_key if provider == "openrouter" else None,
                "anthropic_key": api_key if provider == "claude_api" else None}
        pool = AdapterPool(config.runtime, selection or {"provider": "codex_cli", "model": config.runtime.codex_model,
                                                        "reasoning_effort": None}, **keys, perplexity_key=perplexity_key)
        # The advice on a blocked question asks the deepest setting of the run's subscription (research_advisor); a
        # billed run keeps its own model and key.
        advisor_pool = AdapterPool(config.runtime, advisor_selection(pool.text_generation),
                                   anthropic_key=keys["anthropic_key"], perplexity_key=perplexity_key)

        def limits():
            return effective_limits(work, config.research_limits, input_hash)

        def accepted():
            return accepted_gaps(work, input_hash)

        def retries():
            return retry_requests(work, input_hash)

        def access_gaps():
            return criterion_gaps(work, input_hash)

        def disputes():
            return dispute_decisions(work, input_hash)

        def residual():
            return residual_finish(work, input_hash)

        # Research tasks may report from several threads; the read-modify-write of the activity
        # envelope and the observer's job file happen one at a time.
        progress_lock = threading.Lock()
        # The digest of what this process last reported. Both envelopes carry the whole question ledger (2 to 2.6 MB
        # each in the Asimov and Ontologies runs), so an unchanged report is not written again (2026-10-02); the
        # envelope's updated_at stays the time of its last real change, as the Studio reads it.
        reported = {}

        def progress(activity, quality=None, round_number=None):
            with progress_lock:
                current = limits()
                question_path = work / "research_questions.json"
                # A search call reports from outside the ledger lock while another worker may be saving
                # the ledger; the read outlasts that rename.
                ledger = read_text(question_path) if question_path.exists() else None
                fingerprint = digest({"activity": activity, "quality": quality, "round": round_number, "ledger": ledger,
                                      "limits": [current.model_calls, current.search_rounds]})
                if reported.get("digest") == fingerprint:
                    return
                activity_path = work / "research_activity.json"
                try:
                    # The Studio's progress watcher and the status monitor add fields to this envelope; they stay.
                    previous = json.loads(read_text(activity_path)) if activity_path.exists() else {}
                except (OSError, ValueError):
                    previous = {}
                data = {**previous, "phase": "research", "activity": activity,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "model_call_limit": current.model_calls,
                        "search_round_limit": current.search_rounds}
                if quality is not None:
                    data["research_quality"] = quality
                if ledger is not None:
                    # Existing Studio processes also read this envelope. Keep the new
                    # ledger visible without restarting a server holding session keys.
                    data["research_questions"] = json.loads(ledger)
                if round_number is not None:
                    data["research_round"] = round_number
                write_json(activity_path, data)
                write_json(work / "progress.json", data)
                reported["digest"] = fingerprint
                observer = run_observer.get()
                if observer:
                    observer(manifest)

        def invoke(prompt, output_type, version, *, search=False, advisor=False):
            caller = advisor_pool if advisor else pool
            number = reserve_call(work, limits(), search=search, billed=caller.billed(search))
            from .research_status import record_request
            directory = work / "calls" / f"call_{number:03d}"
            record_request(directory, output_type.__name__, prompt)
            if search:
                progress("Quellen zu offenen Leitfragen werden gesucht")
            started_at, started = datetime.now(timezone.utc).isoformat(), time.monotonic()
            try:
                result = caller.structured(prompt, output_type, directory, prompt_version=version, search=search)
            except AppError as exc:
                if unanswered(exc):
                    refund_call(work, number, search=search)
                raise
            except BaseException:
                refund_call(work, number, search=search)
                raise
            finally:
                settle_call(work, number, directory)
            # Wall-clock of an answered call, the basis of the hours a plan projection names.
            write_json(directory / "timing.json", {"schema": output_type.__name__, "prompt_version": version,
                       "search": search, "seconds": round(time.monotonic() - started, 3),
                       "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat()})
            return result

        def plan_gate(projection):
            """The valid approval of the projected plan; ``auto`` writes one, everything else waits for the user."""
            approval = plan_approval_for(work, input_hash, projection["plan_hash"])
            if approval is None and review_mode == "auto":
                approval = approve_research_plan(root, manifest.run_id, source="auto: pla research --approve-plan")
            return approval.model_dump(mode="json") if approval else None

        def discovery_stage():
            brief = {key: value for key, value in config.model_dump(mode="json").items()
                     if key in {"topic", "central_question", "language", "audience_level", "prior_knowledge",
                                "depth_request", "focus_questions", "excluded_topics", "seed_people", "seed_urls",
                                "series_goal", "recency_months"}}
            # The run's own date, the same on every resume: "the last N months" count back from it.
            brief["research_date"] = run_date(work)
            if library:
                brief["library"] = library_view(library)
            # Three per focus question: explain and evaluate tasks both need sources, and the library offers more.
            maximum = min(config.research_limits.sources, max(12, len(config.focus_questions) * 3))
            brief["attachments"] = attachments.context(root)
            brief["requirements"] = requirements_for(config)
            prompt = (
                instructions("research_discovery", maximum=maximum) + " " + TERMINOLOGY + fragment("open_archives") +
                attachments.MATERIAL_RULES +
                instructions("research_discovery_attachments") + "\n" + json.dumps(brief, ensure_ascii=False))
            saved = saved_discovery(work, config.topic)
            discovery, metadata = saved or invoke(prompt, ResearchDiscovery, DISCOVERY_VERSION, search=True)
            if discovery.topic != config.topic:
                raise AppError("Suchantwort verletzt Thema oder Quellenlimit.", code="invalid_model_output")
            if len(discovery.candidates) > maximum:
                # A few candidates over the limit cost the whole paid search before (Asimov, 2026-09-30: 27 of 26).
                # The first ones stay; the rest are named, and the searches of the single questions can still find them.
                dropped = discovery.candidates[maximum:]
                discovery = discovery.model_copy(update={"candidates": discovery.candidates[:maximum], "limitations": [
                    *discovery.limitations, "Über dem Quellenlimit der ersten Suche, nicht eingelesen: "
                    + "; ".join(c.title for c in dropped)]})
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
                                           primary_source=False, source_type="idea"), p) for p in local_files]
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
                    failures.append(import_failure(address, exc))
                    continue
                if identity in seen:
                    continue
                seen.add(identity)
                if attempted >= limits().sources:
                    failures.append({"source": address, "reason": "Quellenlimit erreicht; nicht abgerufen.", "code": "source_limit"})
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
                        document, processed = import_source(candidate, root, manifest.run_id, local=local,
                                                            downloaded=downloaded,
                                                            **({"library": library} if library else {}))
                        write_json(checkpoint, {"processed": processed.relative_to(root).as_posix(), "sha256": file_hash(processed)})
                    # Keep independently retrieved provenance even when an upload
                    # contains an identical copy; the local file must also remain
                    # available for the project's input-integrity check.
                    independent_copy = bool(document.url) and any(
                        s.text_hash == document.text_hash and not s.url for s in sources)
                    if document.text_hash in hashes and not independent_copy:
                        failures.append({"source": address, "reason": "Identischer Quellentext bereits eingelesen.", "code": "duplicate_source"})
                        continue
                    hashes.add(document.text_hash)
                    sources.append(document)
                    outputs.extend([processed, inside(root, document.raw_path)])
                except (AppError, OSError, ValueError) as exc:
                    failures.append(import_failure(address, exc))
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

        def question_research(*, dossier=None, with_context=False):
            discovery, index, context = synthesis_inputs()
            return run_question_research(root, work, config, discovery, index, invoke, progress, dossier=dossier,
                                         context=context if with_context else (), limits=limits, accepted=accepted,
                                         retries=retries, access_gaps=access_gaps, disputes=disputes, residual=residual,
                                         plan_gate=plan_gate if review_mode else None,
                                         workers=execution.text_workers, advisor=True)

        def dossier_stage():
            progress("Belege werden zu Grundlagen und Erklärungen verbunden")
            outputs = question_research()
            _, _, checked_context, checked_dossier = load_complete_research(work)
            write_json(work / "dossier.json", checked_dossier.model_dump())
            write_json(work / "source_context.json", checked_context)
            write_json(work / "reference_check.json", {"errors": []})
            return outputs + [work / "dossier.json", work / "source_context.json", work / "reference_check.json"]

        def review_stage():
            outputs = []
            if not (work / "question_research/state.json").exists():
                dossier = ResearchDossier.model_validate_json((work / "dossier.json").read_text(encoding="utf-8"))
                outputs = question_research(dossier=dossier, with_context=True)
            elif not question_result_ready():
                outputs = question_research(with_context=True)
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
            dossier = ResearchDossier.model_validate_json((work / "reviewed_dossier.json").read_text(encoding="utf-8"))
            return question_research(dossier=dossier, with_context=True)

        def publish_stage():
            discovery, index, context, dossier = load_complete_research(work)
            quality = json.loads((work / "research_quality_gate.json").read_text(encoding="utf-8"))
            final_review_path = work / "complete_research/source_review.json"
            if not quality["passed"]:
                raise AppError("Die Recherche ist noch nicht vollständig geprüft.", code="research_coverage_incomplete", status="blocked")
            # Never published while a question is still open, whatever the gate recorded (Transformer, 2026-10-02:
            # published as completed with two rows still researching).
            unanswered = unanswered_questions(work)
            if unanswered:
                raise AppError("Teilfragen ohne geprüfte Antwort: " + ", ".join(unanswered) + ". Die Recherche wird nicht "
                               "veröffentlicht; Antworten und Stand bleiben gespeichert, ein Fortsetzen bearbeitet sie weiter.",
                               code="research_questions_open", status="blocked")
            accepted_rows = quality.get("accepted_gaps", [])
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
            for name in ("evidence_report.json", "search_receipts.json", "objections.json", "accepted_gaps.json"):
                source = work / "complete_research" / name
                if source.exists():
                    destination = root / "research" / name
                    atomic_text(destination, source.read_text(encoding="utf-8"))
                    outputs.append(destination)
            for relative, data in files.items():
                destination = root / relative
                write_yaml(destination, data)
                outputs.append(destination)
            briefing = render_dossier(dossier, discovery, index, context, manifest.run_id, language=config.language)
            atomic_text(work / "research_briefing.md", render_dossier(dossier, discovery, index, context,
                        manifest.run_id, language=config.language, local_prefix="../../"))
            atomic_text(root / "research/research_briefing.md", briefing)
            atomic_text(root / "research/quality.md", (work / "research_quality.md").read_text(encoding="utf-8"))
            if (work / "research_questions.md").exists():
                for source_name, destination_name in (("research_questions.md", "questions.md"), ("research_questions.json", "questions.json")):
                    destination = root / "research" / destination_name
                    atomic_text(destination, (work / source_name).read_text(encoding="utf-8"))
                    outputs.append(destination)
            atomic_text(root / "research/open_questions.md",
                        render_open_questions(dossier, accepted_rows, quality.get("gap_probes", []),
                                              language=config.language))
            write_json(root / "research/latest.json", {"run_id": manifest.run_id})
            # What this run measured per task and per call sizes the next run's plan projection.
            calibration = write_calibration(root, work, manifest.run_id)
            if calibration is not None:
                outputs.append(calibration)
            write_json(root / "reports/research_quality.json", {
                "run_id": manifest.run_id, "reference_check": "passed",
                # Noted limits are their own outcome: both completed runs of 2026-10-01 had no accepted gap and still
                # said accepted_gaps_remaining, and claimed complete coverage with requirements failing.
                "model_review": ("residual_objections_remaining" if quality.get("passed_with_residual_objections") else
                                 "accepted_gaps_remaining" if quality.get("passed_with_accepted_gaps") else
                                 "noted_limits_remaining" if quality.get("passed_with_noted_limits") else "no_remaining_issues"),
                "human_reviewed": False,
                # Remaining objections, disputed ones included, leave the coverage open as well (2026-10-02 review).
                "complete_topic_coverage": (not accepted_rows and quality.get("closed") == quality.get("total")
                                            and not quality.get("residual_objections")),
                "coverage_scope": "agreed_brief",
                "accepted_gaps": accepted_rows, "residual_objections": quality.get("residual_objections", []),
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
