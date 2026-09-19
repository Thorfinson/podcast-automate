"""Reviewed research -> source-bound series outline -> checked dialogue scripts.

This module prepares a script run (provider settings, resume or revision state, input hashes, the
outline approval receipt) and executes the stages implemented in :mod:`script_pipeline`. The
deterministic outline and script checks live in :mod:`script_checks` and are re-exported here.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .codex import CodexAdapter  # noqa: F401  (tests patch podcast_automate.scripting.CodexAdapter.structured)
from .errors import AppError
from .execution import ExecutionChoice, selected_execution
from .models import EpisodeScript, RunManifest, StageRecord
from .polishing import HOST_ROLES, POLISH_VERSION
from .provider_pool import AdapterPool, check_adapter_versions, text_generation_settings  # noqa: F401  (re-exported)
from .research import validate_dossier
from .research_models import ResearchDossier
from .research_quality import QUALITY_VERSION, load_complete_research, requirements_for
from .runner import execute_stages, manifest_path, outputs_valid
from .script_artifacts import script_metrics  # noqa: F401  (re-exported; the Studio imports it from here)
from .script_checks import (MAX_PLAN_REPAIRS, checked_series_plan, episode_sources, load_plan_checkpoint,  # noqa: F401
                            outline_hash, plan_dependency_conflicts, plan_failure_message,
                            script_review_signature, validate_plan, validate_script)
from .script_models import SeriesPlan
from .script_pipeline import SPOKEN_DIALOGUE, ScriptRun  # noqa: F401
from .series_review import SERIES_REVIEW_VERSION
from .storage import digest, file_hash, load_project, project_lock, read_yaml, write_json, write_yaml
from .teaching import DESIGN_VERSION, TEACHING_VERSION

SCRIPT_VERSION = "script.v5-dialogue-polish"
STAGES = ("planning", "teaching", "writing", "polishing", "review", "publish")


def load_research(root: Path, config):
    try:
        run_id = json.loads((root / "research/latest.json").read_text(encoding="utf-8"))["run_id"]
    except (OSError, ValueError, KeyError) as exc:
        raise AppError("Zuerst mit pla research ein geprüftes Dossier erstellen.",
                       code="research_required", status="blocked") from exc
    active = root / "research/active.json"
    if active.exists() and json.loads(active.read_text(encoding="utf-8")).get("run_id") != run_id:
        raise AppError("Die aktuelle Recherche ist noch nicht abgeschlossen. Ihre Qualitätsprüfung abwarten oder den Recherchelauf fortsetzen.",
                       code="research_coverage_incomplete", status="blocked")
    work = manifest_path(root, run_id).parent
    manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
    if manifest.kind != "research" or manifest.status != "completed" or any(
        not outputs_valid(root, record) for record in manifest.stages.values()
    ):
        raise AppError("Der Recherchelauf ist nicht vollständig oder seine Dateien wurden geändert.",
                       code="invalid_research", status="blocked")
    snapshot = read_yaml(work / "project_snapshot.yaml")
    if any(snapshot[key] != getattr(config, key) for key in (
        "topic", "central_question", "focus_questions", "excluded_topics", "seed_people", "seed_urls", "local_sources"
    )):
        raise AppError("Rechercheauftrag geändert; zuerst erneut recherchieren.", code="inputs_changed", status="blocked")
    if "completeness" not in manifest.stages:
        raise AppError("Das bisherige Dossier wurde noch nicht gegen alle Leitfragen geprüft. Bitte die Recherche neu starten.",
                       code="research_coverage_incomplete", status="blocked")
    discovery, sources, context, dossier = load_complete_research(work)
    quality = json.loads((work / "research_quality_gate.json").read_text(encoding="utf-8"))
    if (quality.get("version") != QUALITY_VERSION or not quality.get("passed") or
            quality.get("dossier_hash") != digest(json.loads((work / "complete_research/dossier.json").read_text(encoding="utf-8"))) or
            quality.get("brief_hash") != digest(requirements_for(config))):
        raise AppError("Die Recherche deckt den ursprünglichen Auftrag noch nicht vollständig ab. Zuerst die offenen Leitfragen recherchieren.",
                       code="research_coverage_incomplete", status="blocked")
    for relative in config.local_sources:
        local = (root / relative).resolve()
        source_id = "src_" + hashlib.sha256(str(local).encode()).hexdigest()[:16]
        saved_source = next((s for s in sources.sources if s.id == source_id), None)
        if not saved_source or not local.is_file() or file_hash(local) != saved_source.raw_hash:
            raise AppError("Lokale Quelle seit der Recherche geändert oder nicht eingelesen.",
                           code="inputs_changed", status="blocked")
    if validate_dossier(dossier, discovery, context):
        raise AppError("Dossier verletzt Quellenprüfung.", code="invalid_evidence", status="blocked")
    return run_id, dossier, discovery, sources, context


def resumed_run(root, run_id):
    """Saved request, execution mode and inputs of an interrupted run; nothing is recomputed."""
    path = manifest_path(root, run_id)
    manifest = RunManifest.model_validate(read_yaml(path))
    request = json.loads((path.parent / "script_request.json").read_text(encoding="utf-8"))
    saved_inputs = json.loads((path.parent / "inputs.json").read_text(encoding="utf-8"))
    series_review_version = saved_inputs.get("series_review_version")
    if series_review_version not in {None, SERIES_REVIEW_VERSION}:
        raise AppError("Die gespeicherte Version der Serienprüfung wird nicht unterstützt.",
                       code="inputs_changed", status="blocked")
    return {"path": path, "manifest": manifest, "saved_backend": request.get("text_generation"),
            "execution": ExecutionChoice.model_validate(request.get("execution", {})), "episode": request["episode"],
            "saved_inputs": saved_inputs, "revision": saved_inputs.get("revision"),
            "series_review_version": series_review_version, "inherited_plan": None, "inherited_knowledge": None}


def revision_inputs(root, revise, feedback, research_id, dossier):
    """Reuse the approved plan of the latest published script for an editorial revision."""
    try:
        pointer = root / "episodes" / revise / "latest.json"
        if not pointer.exists():
            pointer = root / "episodes/latest.json"
        previous_id = json.loads(pointer.read_text(encoding="utf-8"))["run_id"]
    except (OSError, ValueError, KeyError) as exc:
        raise AppError("Noch kein Skript zum Überarbeiten vorhanden.", code="script_required", status="blocked") from exc
    previous_work = manifest_path(root, previous_id).parent
    previous = RunManifest.model_validate(read_yaml(previous_work / "run_manifest.yaml"))
    if previous.kind != "script" or previous.status != "completed" or not outputs_valid(root, previous.stages["planning"]):
        raise AppError("Der gespeicherte Skriptplan ist nicht vollständig oder wurde verändert.", code="invalid_revision", status="blocked")
    previous_inputs = json.loads((previous_work / "inputs.json").read_text(encoding="utf-8"))
    if previous_inputs["research_run"] != research_id or ResearchDossier.model_validate(previous_inputs["dossier"]) != dossier:
        raise AppError("Recherche seit dem Skript geändert; einen neuen script-Lauf planen.", code="inputs_changed", status="blocked")
    inherited_plan = SeriesPlan.model_validate_json((previous_work / "series_plan.json").read_text(encoding="utf-8"))
    selected_entry = next((e for e in inherited_plan.episodes if e.episode_id == revise), None)
    if selected_entry is None:
        raise AppError("Folge im vorhandenen Plan nicht gefunden.", code="unknown_episode", status="blocked")
    original = EpisodeScript.model_validate(read_yaml(root / "episodes" / selected_entry.episode_id / "script.yaml"))
    # A revision may be requested precisely because the existing text has the wrong length.
    if validate_plan(inherited_plan, dossier) or validate_script(original, selected_entry, check_duration=False):
        raise AppError("Vorhandenes Skript oder Plan verletzt die Quellenzuordnung.", code="invalid_script", status="blocked")
    inherited_knowledge = json.loads((previous_work / "knowledge_model.json").read_text(encoding="utf-8"))
    revision = {"source_run_id": previous_id, "original_script": original.model_dump(), "feedback": feedback}
    return inherited_plan, inherited_knowledge, revision


def new_run(root, research_id, dossier, *, episode, revise, feedback):
    inherited_plan = inherited_knowledge = revision = None
    if revise:
        inherited_plan, inherited_knowledge, revision = revision_inputs(root, revise, feedback, research_id, dossier)
        episode = revise
    identifier = "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    return {"path": manifest_path(root, identifier), "manifest": None, "saved_backend": None,
            "execution": selected_execution(root), "episode": episode, "saved_inputs": None, "revision": revision,
            "series_review_version": SERIES_REVIEW_VERSION, "inherited_plan": inherited_plan,
            "inherited_knowledge": inherited_knowledge}


def build_adapter(config, text_generation, api_key):
    """The adapter pool of this run: a fixed provider as before, ``auto`` chooses a subscription per call."""
    check_adapter_versions(text_generation)
    if text_generation["provider"] != "openrouter" and api_key is not None:
        raise AppError("--api-key nur mit --backend openrouter verwenden.", code="invalid_backend", status="blocked")
    return AdapterPool(config.runtime, text_generation, api_key=api_key)


def run_inputs(config, research_id, dossier, discovery, sources, context, state, text_generation):
    """The frozen inputs whose digest binds checkpoints, approvals and resumes of this run."""
    config_hash = digest(config.model_dump(mode="json"))
    inputs = {"research_run": research_id, "dossier": dossier.model_dump(), "context": context,
              "sources": sources.model_dump(), "discovery": discovery.model_dump(),
              "project": config_hash, "version": SCRIPT_VERSION, "teaching_version": TEACHING_VERSION,
              "design_version": DESIGN_VERSION,
              "polish_version": POLISH_VERSION, "host_roles": HOST_ROLES,
              "episode": state["episode"], "text_generation": text_generation}
    if state["revision"]:
        inputs["revision"] = state["revision"]
    if state["series_review_version"]:
        inputs["series_review_version"] = state["series_review_version"]
    if state["saved_inputs"] is not None:
        # Defaults added to readable historical schemas do not change the approved research.
        # Reuse the exact old serialization only after full semantic equality is established.
        for key, value in (("dossier", dossier), ("sources", sources), ("discovery", discovery)):
            previous = state["saved_inputs"].get(key)
            if previous is not None and type(value).model_validate(previous) == value:
                inputs[key] = previous
    return config_hash, inputs, digest(inputs)


def bind_manifest(root, work, state, config, config_hash, inputs, input_hash, research_id, text_generation, plan_only):
    """Validate a resumed manifest against today's inputs, or create the run folder for a new one."""
    manifest = state["manifest"]
    if manifest is not None:
        if manifest.kind != "script" or manifest.input_hash != input_hash:
            raise AppError("Skripteingaben geändert; einen neuen script-Lauf starten.",
                           code="inputs_changed", status="blocked")
        for relative, expected in manifest.stages["publish"].outputs.items():
            if relative.startswith("episodes/") and relative.endswith("/script.yaml"):
                current = root / relative
                if current.is_file() and file_hash(current) != expected:
                    raise AppError("Kanonisches Skript manuell geändert. Änderungen vor Fortsetzung neu prüfen; "
                                   "der Text wird nicht mit dem gespeicherten Entwurf überschrieben.",
                                   code="script_edited", status="blocked")
        return manifest
    manifest = RunManifest(run_id=work.name, kind="script", project_hash=config_hash,
                           input_hash=input_hash, stages={name: StageRecord() for name in STAGES})
    write_json(work / "script_request.json", {"research_run": research_id, "episode": state["episode"],
                                             "execution": state["execution"].model_dump(),
                                             "text_generation": text_generation,
                                             "require_plan_approval": plan_only})
    write_json(work / "inputs.json", inputs)
    write_yaml(work / "project_snapshot.yaml", config.model_dump(mode="json"))
    if state["inherited_plan"]:
        write_json(work / "series_plan.json", state["inherited_plan"].model_dump())
        write_json(work / "knowledge_model.json", state["inherited_knowledge"])
        manifest.stages["planning"] = StageRecord(status="completed", outputs={
            (work / name).relative_to(root).as_posix(): file_hash(work / name) for name in
            ("series_plan.json", "knowledge_model.json", "inputs.json", "script_request.json", "project_snapshot.yaml")})
    return manifest


def outline_revision(root, work, manifest, outline_feedback):
    """Start an outline revision from editorial feedback, or continue an interrupted one."""
    previous_outline = None
    if outline_feedback:
        if any(record.attempts for name, record in manifest.stages.items() if name != "planning"):
            raise AppError("Skripterstellung bereits begonnen; ein neues Inhaltsverzeichnis anlegen.", code="plan_in_use", status="blocked")
        if not outputs_valid(root, manifest.stages["planning"]):
            raise AppError("Ein vollständiges Inhaltsverzeichnis wird benötigt.", code="invalid_plan", status="blocked")
        previous_outline = json.loads((work / "series_plan.json").read_text(encoding="utf-8"))
        write_json(work / "outline_revision.json", {"previous": previous_outline, "feedback": outline_feedback})
        for record in manifest.stages.values():
            record.status, record.outputs, record.error = "pending", {}, None
    elif (work / "outline_revision.json").exists() and not outputs_valid(root, manifest.stages["planning"]):
        revision_data = json.loads((work / "outline_revision.json").read_text(encoding="utf-8"))
        previous_outline, outline_feedback = revision_data["previous"], revision_data["feedback"]
    return previous_outline, outline_feedback


def require_plan_approval(root, work, manifest, plan_only, approved_plan_hash):
    """Writing starts only after the user approved exactly this outline and its inputs."""
    request = json.loads((work / "script_request.json").read_text(encoding="utf-8"))
    if not request.get("require_plan_approval") or plan_only:
        return
    if not outputs_valid(root, manifest.stages["planning"]):
        raise AppError("Zuerst das Inhaltsverzeichnis erstellen und prüfen.", code="plan_approval_required", status="blocked")
    expected = outline_hash(work)
    receipt = work / "plan_approval.json"
    saved_approval = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
    if approved_plan_hash is not None:
        if approved_plan_hash != expected:
            raise AppError("Inhaltsverzeichnis geändert. Bitte den aktuellen Stand erneut lesen.", code="plan_changed", status="blocked")
        write_json(receipt, {"plan_hash": expected, "approved_at": datetime.now(timezone.utc).isoformat()})
    elif saved_approval.get("plan_hash") != expected:
        raise AppError("Das Inhaltsverzeichnis wartet auf deine Freigabe.", code="plan_approval_required", status="blocked")


def run_script(root: Path, *, episode: str | None = None, resume=False, run_id=None,
               revise: str | None = None, feedback="", backend=None, model=None, api_key=None,
               max_output_tokens=None, reasoning_effort=None, plan_only=False, outline_feedback="", approved_plan_hash=None):
    root = root.resolve()
    with project_lock(root):
        config = load_project(root)
        research_id, dossier, discovery, sources, context = load_research(root, config)
        if outline_feedback and not (resume and plan_only):
            raise AppError("Inhaltsverzeichnis zum Überarbeiten im Planungsmodus fortsetzen.", code="invalid_plan")
        if (revise and (resume or (episode and episode != revise))) or (feedback and not revise):
            raise AppError("--revise mit einer passenden Folge und optional --feedback verwenden.", code="invalid_revision")
        state = resumed_run(root, run_id) if resume else new_run(root, research_id, dossier,
                                                                 episode=episode, revise=revise, feedback=feedback)
        text_generation = text_generation_settings(config, backend=backend, model=model, max_output_tokens=max_output_tokens,
                                                   reasoning_effort=reasoning_effort, saved=state["saved_backend"])
        adapter = build_adapter(config, text_generation, api_key)
        config_hash, inputs, input_hash = run_inputs(config, research_id, dossier, discovery, sources, context,
                                                     state, text_generation)
        work = state["path"].parent
        manifest = bind_manifest(root, work, state, config, config_hash, inputs, input_hash, research_id,
                                 text_generation, plan_only)
        previous_outline, outline_feedback = outline_revision(root, work, manifest, outline_feedback)
        require_plan_approval(root, work, manifest, plan_only, approved_plan_hash)
        write_json(root / "runs/latest.json", {"run_id": manifest.run_id})
        pipeline = ScriptRun(root=root, work=work, config=config, manifest=manifest, adapter=adapter,
                             input_hash=input_hash, research_id=research_id, dossier=dossier, discovery=discovery,
                             sources=sources, context=context, episode=state["episode"], revision=state["revision"],
                             execution=state["execution"], plan_only=plan_only, previous_outline=previous_outline,
                             outline_feedback=outline_feedback, series_review_version=state["series_review_version"],
                             text_generation=text_generation, resume=resume)
        if not plan_only and (work / "series_plan.json").exists():
            pipeline.refresh_foundations(pipeline.selected()[1])
        return execute_stages(root, manifest, state["path"], pipeline.stages(),
                              stop_after="planning" if plan_only else None)
