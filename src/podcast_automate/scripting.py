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
from .models import EpisodeScript, RunManifest, StageRecord, host_roles_of
from .cast import POLISH_VERSION, cast_note, host_roles
from .provider_pool import AdapterPool, check_adapter_versions, text_generation_settings  # noqa: F401  (re-exported)
from .research import reconcile_budget, refund_call, reserve_call, settle_call, unanswered, validate_dossier
from .text_settings import BILLED_TEXT_PROVIDERS
from .research_patches import re_asked
from .research_models import ResearchDossier
from .research_quality import QUALITY_VERSION, load_complete_research, requirements_for
from .run_budget import effective_limits, text_switch
from .runner import execute_stages, manifest_path, outputs_valid
from .script_artifacts import SPOKEN_WORDS_PER_MINUTE, script_metrics  # noqa: F401  (script_metrics re-exported; the Studio imports it from here)
from .script_checks import (MAX_PLAN_REPAIRS, checked_series_plan, episode_sources, load_plan_checkpoint,  # noqa: F401
                            outline_hash, plan_dependency_conflicts, plan_failure_message,
                            script_review_signature, validate_plan, validate_script)
from .script_models import SeriesPlan
from .script_pipeline import ScriptRun  # noqa: F401
from .series_review import SERIES_REVIEW_VERSION, SeriesReview, reviewed_scripts, series_issues, series_report
from .story_agent import story_entry
from .storage import (bound_brief, digest, file_hash, load_project, project_hash, project_lock, read_yaml, write_json,
                      write_yaml)
from .teaching import DESIGN_VERSION, TEACHING_VERSION

SCRIPT_VERSION = "script.v5-dialogue-polish"
# Every script run is written by the story agent (D-181, the only writer since D-184): its "story" stage plans and
# writes each episode in one Claude Code session, which also reviews its own telling.
STORY_STAGES = ("planning", "teaching", "story", "review", "publish")
# The latest series review of an outline's published episodes (run_series_review with outline_run).
SERIES_REVIEW_POINTER = "episodes/series_review.json"
# The stages of the writing prompt, which ended with D-184: a run begun with them moves onto STORY_STAGES while it has
# written nothing (retire_prompt_stages); one that has, cannot resume.
PROMPT_STAGES = ("writing", "narration", "polishing")
# The words a minute a voice engine speaks a script, before the project has a recording of its own (speaking_rate),
# measured as measured_rate does on German recordings:
# - ElevenLabs v4 spoke Asimov v2 ep_007 (2026-11-01) at 150, where every budget assumed 130, and the episode planned at 45
#   minutes ran 35:47;
# - Gemini spoke the 15 Orlagau episodes (2026-10) at 137 to 151, most near 148.
# Qwen keeps the 130 measured on the Transformer recordings (SPOKEN_WORDS_PER_MINUTE).
STARTING_RATES = {"elevenlabs_tts": 150, "google_gemini_tts": 148}
# A measured rate outside this range is no speech rate (a broken export, a script that is not the recording's).
PLAUSIBLE_RATES = (90, 220)


def local_source_paths(root: Path, config):
    """Each configured local source, resolved; one outside the project folder is refused (2026-10-02: an absolute
    path or ``..`` let a script run hash any readable file of the machine). Studio uploads live in inputs/uploads."""
    root = Path(root).resolve()
    paths = []
    for relative in config.local_sources:
        local = (root / relative).resolve()
        if not local.is_relative_to(root):
            raise AppError(f"Lokale Quelle liegt außerhalb des Projektordners: {relative}. Nur Dateien im Projektordner "
                           "sind erlaubt.", code="invalid_request", status="blocked")
        paths.append(local)
    return paths


def load_research(root: Path, config):
    local_files = local_source_paths(root, config)
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
    for local in local_files:
        source_id = "src_" + hashlib.sha256(str(local).encode()).hexdigest()[:16]
        content = file_hash(local) if local.is_file() else None
        # The id comes from the file's absolute path, so a project copied or moved elsewhere names the same file anew;
        # the content the research read still counts as that source (D-178; 2026-10-19, the copy „Orlagau (ElevenLabs)“).
        saved_source = next((s for s in sources.sources if s.id == source_id), None) or next(
            (s for s in sources.sources if content is not None and s.raw_hash == content), None)
        if not saved_source or content is None or content != saved_source.raw_hash:
            raise AppError("Lokale Quelle seit der Recherche geändert oder nicht eingelesen.",
                           code="inputs_changed", status="blocked")
    if validate_dossier(dossier, discovery, context):
        raise AppError("Dossier verletzt Quellenprüfung.", code="invalid_evidence", status="blocked")
    return run_id, dossier, discovery, sources, context


def measured_rate(root: Path, provider: str) -> int | None:
    """The words a minute of the project's newest completed recording by ``provider``: its approved script's words
    over the exported audio's minutes less the script's planned pauses, as script_metrics estimates a duration; None
    without such a recording."""
    for path in sorted((root / "runs").glob("run_*/run_manifest.yaml"), reverse=True):
        work = path.parent
        try:
            manifest = read_yaml(path)
            if not isinstance(manifest, dict) or manifest.get("kind") != "episode_audio" or                     manifest.get("status") != "completed":
                continue
            inputs = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
            if (inputs.get("audio_generation") or {}).get("provider") != provider:
                continue
            script = EpisodeScript.model_validate_json((work / "approved_script.json").read_text(encoding="utf-8"))
            report = root / "exports" / inputs["episode_id"] / work.name / "audio_report.json"
            minutes = json.loads(report.read_text(encoding="utf-8"))["duration_seconds"] / 60
        except (OSError, ValueError, KeyError, TypeError):
            continue
        pauses = sum(segment.pause_after_ms for segment in script.segments) / 60_000
        if minutes <= pauses:
            continue
        rate = round(script_metrics(script)["words"] / (minutes - pauses))
        if PLAUSIBLE_RATES[0] <= rate <= PLAUSIBLE_RATES[1]:
            return rate
    return None


def speaking_rate(root: Path, config) -> int:
    """The words a minute a new script run writes and checks its lengths at (D-184): the rate measured on the
    project's newest recording by its current voice engine, else that engine's starting rate, else 130."""
    from .speech import selected_audio
    provider = selected_audio(root, config).provider
    return measured_rate(root, provider) or STARTING_RATES.get(provider, SPOKEN_WORDS_PER_MINUTE)


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
            "series_review_version": series_review_version, "inherited_plan": None, "inherited_knowledge": None,
            "speaking_rate": saved_inputs.get("speaking_rate")}


def revision_inputs(root, revise, feedback, research_id, dossier, roles=None):
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
    # A story agent's episode has its own chapters (story/<episode>/entry.json): its text is checked against them, and
    # its revision starts from them (story_agent.revision_entry, D-184).
    written = story_entry(previous_work, selected_entry)
    # A revision may be requested precisely because the existing text has the wrong length.
    if validate_plan(inherited_plan, dossier) or validate_script(original, written, check_duration=False,
                                                                         **({'roles': roles} if roles else {})):
        raise AppError("Vorhandenes Skript oder Plan verletzt die Quellenzuordnung.", code="invalid_script", status="blocked")
    inherited_knowledge = json.loads((previous_work / "knowledge_model.json").read_text(encoding="utf-8"))
    revision = {"source_run_id": previous_id, "original_script": original.model_dump(), "feedback": feedback,
                **({"entry": written.model_dump()} if written is not selected_entry else {})}
    return inherited_plan, inherited_knowledge, revision


def outline_inputs(root, run_id, research_id, dossier, episode):
    """The approved outline of script run ``run_id`` for a new run that writes ``episode`` of it without planning again
    (``--from-outline``): one episode first, before the series run writes them all (2026-11-01: an approved outline of
    20 episodes would have started 20 story agent sessions at once). The outline must be complete, approved as it
    stands, and made from today's research."""
    work = manifest_path(root, run_id).parent
    if not (work / "run_manifest.yaml").is_file():
        raise AppError(f"Skriptlauf {run_id} nicht gefunden.", code="unknown_run", status="blocked")
    manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
    if manifest.kind != "script" or not outputs_valid(root, manifest.stages["planning"]):
        raise AppError("Das Inhaltsverzeichnis dieses Laufs ist nicht vollständig oder wurde verändert.",
                       code="invalid_plan", status="blocked")
    approval = json.loads((work / "plan_approval.json").read_text(encoding="utf-8")) \
        if (work / "plan_approval.json").is_file() else {}
    if approval.get("plan_hash") != outline_hash(work):
        raise AppError("Das Inhaltsverzeichnis dieses Laufs ist noch nicht freigegeben.", code="plan_approval_required",
                       status="blocked")
    previous_inputs = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
    if previous_inputs["research_run"] != research_id or ResearchDossier.model_validate(previous_inputs["dossier"]) != dossier:
        raise AppError("Recherche seit dem Inhaltsverzeichnis geändert; einen neuen script-Lauf planen.",
                       code="inputs_changed", status="blocked")
    plan = SeriesPlan.model_validate_json((work / "series_plan.json").read_text(encoding="utf-8"))
    if episode not in {entry.episode_id for entry in plan.episodes}:
        raise AppError("Folge im freigegebenen Inhaltsverzeichnis nicht gefunden.", code="unknown_episode",
                       status="blocked")
    return plan, json.loads((work / "knowledge_model.json").read_text(encoding="utf-8"))


def outline_episodes(root: Path, run_id: str) -> list[dict]:
    """The episodes of the outline in run ``run_id``, in its order, each with the run that published it from this outline,
    or None while it is not written (D-184): a series is written one episode per run, in order (outline_inputs). Such
    a run and a revision copy the outline's series_plan.json byte for byte, so an episode published from another
    outline (an earlier plan of the project) counts as not written."""
    plan_file = manifest_path(root, run_id).parent / "series_plan.json"
    # Read for its episode ids and titles only, as the Studio reads an outline: a page must not fail on its schema.
    try:
        outline = file_hash(plan_file)
        episodes = json.loads(plan_file.read_text(encoding="utf-8")).get("episodes") or []
    except (OSError, ValueError, AttributeError):
        return []
    rows = []
    for entry in episodes:
        if not isinstance(entry, dict) or not isinstance(entry.get("episode_id"), str):
            continue
        folder, written = root / "episodes" / entry["episode_id"], None
        try:
            run = json.loads((folder / "latest.json").read_text(encoding="utf-8"))["run_id"]
            if (folder / "script.yaml").is_file() and \
                    file_hash(manifest_path(root, run).parent / "series_plan.json") == outline:
                written = run
        except (OSError, ValueError, KeyError, TypeError, AppError):
            pass
        rows.append({"episode_id": entry["episode_id"], "title": entry.get("title", ""), "run_id": written})
    return rows


def next_episode(root: Path, run_id: str) -> str | None:
    """The first episode of the outline in run ``run_id`` that is not written yet, or None when all are."""
    return next((row["episode_id"] for row in outline_episodes(root, run_id) if row["run_id"] is None), None)


def outline_series_review(root: Path, run_id: str) -> dict | None:
    """The latest series review of the outline in run ``run_id`` (SERIES_REVIEW_POINTER): its run, its run's status,
    its verdict and what blocks per episode, with the episodes it read; None without one."""
    try:
        pointer = json.loads((root / SERIES_REVIEW_POINTER).read_text(encoding="utf-8"))
        if pointer.get("outline_run_id") != run_id:
            return None
        work = manifest_path(root, pointer["run_id"]).parent
        manifest = read_yaml(work / "run_manifest.yaml")
        saved = json.loads((work / "series_review.json").read_text(encoding="utf-8")) \
            if (work / "series_review.json").is_file() else {}
        runs = json.loads((work / "inputs.json").read_text(encoding="utf-8")).get("episode_runs") or {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, AppError):
        return None
    report = saved.get("report") or {}
    return {"run_id": pointer["run_id"], "status": manifest.get("status"), "verdict": report.get("status"),
            "episode_issues": saved.get("episode_issues") or {}, "advisories": report.get("advisories") or [],
            "episode_runs": runs}


def approve_outline(root: Path, run_id: str, plan_hash: str) -> None:
    """Approve the outline of run ``run_id`` exactly as the user read it (plan_approval.json), without writing an
    episode: its episodes are written one run at a time from it (outline_inputs, D-184). Until then the approval
    resumed the outline's run, which wrote every episode at once and none of them knew what another said."""
    root = root.resolve()
    with project_lock(root):
        work = manifest_path(root, run_id).parent
        manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
        if manifest.kind != "script" or not outputs_valid(root, manifest.stages["planning"]):
            raise AppError("Zuerst das Inhaltsverzeichnis erstellen und prüfen.", code="plan_approval_required",
                           status="blocked")
        if plan_hash != outline_hash(work):
            raise AppError("Inhaltsverzeichnis geändert. Bitte den aktuellen Stand erneut lesen.", code="plan_changed",
                           status="blocked")
        write_json(work / "plan_approval.json", {"plan_hash": plan_hash,
                                                 "approved_at": datetime.now(timezone.utc).isoformat()})


def new_run(root, research_id, dossier, *, episode, revise, feedback, roles=None, outline_run=None):
    inherited_plan = inherited_knowledge = revision = None
    if revise:
        inherited_plan, inherited_knowledge, revision = revision_inputs(root, revise, feedback, research_id, dossier, roles)
        episode = revise
    elif outline_run:
        inherited_plan, inherited_knowledge = outline_inputs(root, outline_run, research_id, dossier, episode)
    identifier = "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
    return {"path": manifest_path(root, identifier), "manifest": None, "saved_backend": None,
            "execution": selected_execution(root), "episode": episode, "saved_inputs": None, "revision": revision,
            "series_review_version": SERIES_REVIEW_VERSION, "inherited_plan": inherited_plan,
            "inherited_knowledge": inherited_knowledge}


def build_adapter(config, text_generation, api_key, perplexity_key=None):
    """The adapter pool of this run: a fixed provider as before, ``auto`` chooses a subscription per call. ``api_key``
    is the key of a billed provider: OpenRouter's, or the Anthropic key of ``claude_api`` (D-145)."""
    check_adapter_versions(text_generation)
    if text_generation["provider"] not in BILLED_TEXT_PROVIDERS and api_key is not None:
        raise AppError("--api-key nur mit --backend openrouter oder claude_api verwenden.", code="invalid_backend",
                       status="blocked")
    claude_api = text_generation["provider"] == "claude_api"
    return AdapterPool(config.runtime, text_generation, api_key=None if claude_api else api_key,
                       anthropic_key=api_key if claude_api else None, perplexity_key=perplexity_key)


def style_notes(root: Path) -> str:
    """The operator's standing editorial corrections; part of the run inputs, so a change re-runs."""
    path = root / "style_notes.md"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def run_inputs(config, research_id, dossier, discovery, sources, context, state, text_generation, notes=""):
    """The frozen inputs whose digest binds checkpoints, approvals and resumes of this run."""
    config_hash = project_hash(config)
    inputs = {"research_run": research_id, "dossier": dossier.model_dump(), "context": context,
              "sources": sources.model_dump(), "discovery": discovery.model_dump(),
              "project": config_hash, "version": SCRIPT_VERSION, "teaching_version": TEACHING_VERSION,
              "design_version": DESIGN_VERSION,
              "polish_version": POLISH_VERSION, "host_roles": host_roles(config),
              "episode": state["episode"], "text_generation": text_generation}
    if notes:
        # Only a project that uses style notes binds them, so existing runs keep their hash.
        inputs["style_notes"] = notes
    if state["revision"]:
        inputs["revision"] = state["revision"]
    if state["series_review_version"]:
        inputs["series_review_version"] = state["series_review_version"]
    if state.get("speaking_rate") and state["speaking_rate"] != SPOKEN_WORDS_PER_MINUTE:
        # Only a rate other than the old constant is bound, so a run at 130 keeps the inputs it always had (D-184).
        inputs["speaking_rate"] = state["speaking_rate"]
    if state["saved_inputs"] is not None:
        # Defaults added to readable historical schemas do not change the approved research.
        # Reuse the exact old serialization only after full semantic equality is established.
        for key, value in (("dossier", dossier), ("sources", sources), ("discovery", discovery)):
            previous = state["saved_inputs"].get(key)
            if previous is not None and type(value).model_validate(previous) == value:
                inputs[key] = previous
    return config_hash, inputs, digest(inputs)


def retire_prompt_stages(root: Path, run_id: str) -> bool:
    """Move the script run ``run_id``, begun with the writing prompt's stages, onto the story agent's while it has
    written nothing (D-184): its outline and the outline's approval stay (outline_hash binds the outline and its inputs,
    not the stages), a teaching design it began stays in teaching/ unread, and the agent writes. Returns whether the
    run moved; one that has written an episode keeps its stages and cannot resume, since the stages are gone.

    2026-11-01: Orlagau v2's series run stopped in the teaching design (teaching_design_failed) after its outline was
    approved; it continues with the agent instead of being planned again."""
    path = manifest_path(root, run_id)
    manifest = RunManifest.model_validate(read_yaml(path))
    if manifest.kind != "script" or tuple(manifest.stages) == STORY_STAGES:
        return False
    if any(record.status == "completed" or record.outputs for name, record in manifest.stages.items()
           if name not in ("planning", "teaching")):
        raise AppError("Dieser Skriptlauf wurde mit dem früheren Schreib-Prompt geschrieben, den es seit D-184 nicht mehr "
                       "gibt. Seine veröffentlichten Folgen bleiben lesbar und vertonbar; für neue Folgen einen neuen "
                       "Lauf starten.", code="writer_retired", status="blocked")
    manifest.stages = {name: manifest.stages["planning"] if name == "planning" else StageRecord()
                       for name in STORY_STAGES}
    write_yaml(path, manifest.model_dump(mode="json"))
    write_json(path.parent / "writer_switch.json", {"writer": "agent", "reason": "D-184",
                                                    "switched_at": datetime.now(timezone.utc).isoformat()})
    return True


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
                           input_hash=input_hash, stages={name: StageRecord() for name in STORY_STAGES})
    write_json(work / "script_request.json", {"research_run": research_id, "episode": state["episode"],
                                             "execution": state["execution"].model_dump(),
                                             "text_generation": text_generation,
                                             "require_plan_approval": plan_only, "writer": "agent"})
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
               max_output_tokens=None, reasoning_effort=None, plan_only=False, outline_feedback="", approved_plan_hash=None,
               jev_probe=False, probe_key=None, web_search=None, perplexity_key=None, outline_run=None):
    """``jev_probe`` asks Jev in the gap probe of a new run (jev.py); ``probe_key`` is the OpenRouter key for it,
    otherwise OPENROUTER_API_KEY. ``web_search`` "perplexity" sends a new run's supplementary searches through
    Perplexity (D-151), with ``perplexity_key`` or PERPLEXITY_API_KEY. The story agent writes every episode
    (story_agent.py, D-181, D-184), a revision too. ``outline_run`` with ``episode`` writes that episode of another
    run's approved outline without planning again (outline_inputs). A resumed run keeps the choices it started
    with."""
    root = root.resolve()
    with project_lock(root):
        config = load_project(root)
        research_id, dossier, discovery, sources, context = load_research(root, config)
        if outline_feedback and not (resume and plan_only):
            raise AppError("Inhaltsverzeichnis zum Überarbeiten im Planungsmodus fortsetzen.", code="invalid_plan")
        if (revise and (resume or (episode and episode != revise))) or (feedback and not revise):
            raise AppError("--revise mit einer passenden Folge und optional --feedback verwenden.", code="invalid_revision")
        if outline_run and (resume or revise or plan_only or not episode):
            raise AppError("--from-outline schreibt eine Folge (--episode) eines freigegebenen Inhaltsverzeichnisses in "
                           "einem neuen Lauf.", code="invalid_request", status="blocked")
        if resume:
            # Begun with the writing prompt and nothing written: it continues on the agent's stages (D-184).
            retire_prompt_stages(root, run_id)
        state = resumed_run(root, run_id) if resume else new_run(root, research_id, dossier,
                                                                 episode=episode, revise=revise, feedback=feedback,
                                                                 roles=host_roles_of(config), outline_run=outline_run)
        if not resume:
            state["speaking_rate"] = speaking_rate(root, config)
        if jev_probe and resume and not state["execution"].jev_probe:
            raise AppError("Die Jev-Lückenprobe gilt nur für neue Skriptläufe; ein laufender behält seine Lückenproben.",
                           code="invalid_request", status="blocked")
        if jev_probe:
            state["execution"] = state["execution"].model_copy(update={"jev_probe": True})
        text_generation = text_generation_settings(config, backend=backend, model=model, max_output_tokens=max_output_tokens,
                                                   reasoning_effort=reasoning_effort, saved=state["saved_backend"],
                                                   web_search=web_search)
        check_adapter_versions(text_generation)
        notes = style_notes(root)
        # A resumed run binds its operational fields (runtime, research_limits) as it started (storage.bound_brief):
        # a raised limit or a longer deadline saved while it waited no longer ends it; a content edit still does.
        hash_config = config
        if resume:
            snapshot = state["path"].parent / "project_snapshot.yaml"
            hash_config = bound_brief(config, read_yaml(snapshot) if snapshot.is_file() else None, "script")
        config_hash, inputs, input_hash = run_inputs(hash_config, research_id, dossier, discovery, sources, context,
                                                     state, text_generation, notes)
        work = state["path"].parent
        manifest = bind_manifest(root, work, state, config, config_hash, inputs, input_hash, research_id,
                                 text_generation, plan_only)
        if resume:
            # A Studio stop ends the worker hard (taskkill /F), so the calls it had out keep their reservation; the
            # research lane returns them on resume, and script runs do too since 2026-10-02 (up to five parallel calls).
            reconcile_budget(work)
        # The inputs keep the selection the run started with; an approved switch only changes who answers.
        text_generation = text_switch(work, manifest.input_hash, text_generation)
        adapter = build_adapter(config, text_generation, api_key, perplexity_key)
        previous_outline, outline_feedback = outline_revision(root, work, manifest, outline_feedback)
        require_plan_approval(root, work, manifest, plan_only, approved_plan_hash)
        write_json(root / "runs/latest.json", {"run_id": manifest.run_id})
        pipeline = ScriptRun(root=root, work=work, config=config, manifest=manifest, adapter=adapter,
                             input_hash=input_hash, research_id=research_id, dossier=dossier, discovery=discovery,
                             sources=sources, context=context, episode=state["episode"], revision=state["revision"],
                             execution=state["execution"], plan_only=plan_only, previous_outline=previous_outline,
                             outline_feedback=outline_feedback, series_review_version=state["series_review_version"],
                             text_generation=text_generation, resume=resume, style_notes=notes, probe_key=probe_key,
                             speaking_rate=inputs.get("speaking_rate", SPOKEN_WORDS_PER_MINUTE))
        if not plan_only and (work / "series_plan.json").exists():
            pipeline.refresh_foundations(pipeline.selected()[1])
        return execute_stages(root, manifest, state["path"], pipeline.stages(),
                              stop_after="planning" if plan_only else None)


def series_review_target(root: Path, run_id: str) -> dict:
    """Which script run a series-review run judged, or which outline's published episodes, and whether its verdict
    reached the report."""
    inputs = json.loads((manifest_path(root.resolve(), run_id).parent / "inputs.json").read_text(encoding="utf-8"))
    return {"script_run_id": inputs.get("script_run_id"), "outline_run_id": inputs.get("outline_run_id"),
            "published_run_id": inputs.get("published_run_id"), "report_mirrored": bool(inputs.get("mirror_report"))}


def run_series_review(root: Path, *, run_id=None, outline_run=None, backend=None, model=None, reasoning_effort=None):
    """Review a published run's scripts on their own, without touching that run's folder; with ``outline_run``, the
    published episodes of that outline, each written by a run of its own (outline_episodes, D-184), once all are.

    The verdict lands in a new run of kind ``series_review``. It is mirrored into
    ``reports/script_quality.yaml`` only when the reviewed run is the one that report describes
    (its own ``run_id``); a verdict on an older run stays in its run folder, because the report
    speaks for the published text. The reviewed run stays byte-identical, so the resume
    invariant holds: a later report never rewrites the evidence it judged. ``runs/latest.json``
    keeps pointing at the last pipeline run: ``pla resume`` and ``pla status`` without a run id
    dispatch on that pointer, and a review run has nothing to resume.
    """
    root = root.resolve()
    with project_lock(root):
        config = load_project(root)
        if outline_run:
            rows = outline_episodes(root, outline_run)
            missing = [row["episode_id"] for row in rows if row["run_id"] is None]
            if missing:
                raise AppError("Die Serienprüfung liest alle Folgen des Inhaltsverzeichnisses; noch nicht geschrieben: "
                               + ", ".join(missing) + ".", code="series_incomplete", status="blocked")
            plan = SeriesPlan.model_validate_json(
                (manifest_path(root, outline_run).parent / "series_plan.json").read_text(encoding="utf-8"))
            scripts = [EpisodeScript.model_validate(read_yaml(root / "episodes" / row["episode_id"] / "script.yaml"))
                       for row in rows]
            source_id, episode_runs = None, {row["episode_id"]: row["run_id"] for row in rows}
        else:
            published = json.loads((root / "episodes/latest.json").read_text(encoding="utf-8")) if (
                root / "episodes/latest.json").is_file() else {}
            source_id, episode_runs = run_id or published.get("run_id"), None
            if not source_id:
                raise AppError("Kein veröffentlichter Skriptlauf gefunden.", code="unknown_run", status="blocked")
            source = manifest_path(root, source_id).parent
            reviewed = RunManifest.model_validate(read_yaml(source / "run_manifest.yaml"))
            if reviewed.kind != "script" or reviewed.status != "completed":
                raise AppError("Die Serienprüfung braucht einen abgeschlossenen Skriptlauf.",
                               code="invalid_run", status="blocked")
            plan = SeriesPlan.model_validate_json((source / "series_plan.json").read_text(encoding="utf-8"))
            scripts = reviewed_scripts(source, plan)
        text_generation = text_generation_settings(config, backend=backend, model=model,
                                                   reasoning_effort=reasoning_effort)
        adapter = build_adapter(config, text_generation, None)
        quality_path = root / "reports/script_quality.yaml"
        quality = read_yaml(quality_path) if quality_path.is_file() else {}
        published_run_id = quality.get("run_id") if isinstance(quality, dict) else None
        identifier = "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
        path = manifest_path(root, identifier)
        work = path.parent
        # The published episodes of an outline are what the report describes: their verdict is mirrored into it.
        inputs = {"version": SERIES_REVIEW_VERSION,
                  **({"outline_run_id": outline_run, "episode_runs": episode_runs} if outline_run else
                     {"script_run_id": source_id}),
                  "published_run_id": published_run_id,
                  "mirror_report": bool(outline_run) or published_run_id == source_id,
                  "scripts": [s.model_dump() for s in scripts], "plan": plan.model_dump()}
        write_json(work / "inputs.json", inputs)
        manifest = RunManifest(run_id=identifier, kind="series_review",
                               project_hash=project_hash(config), input_hash=digest(inputs),
                               stages={"review": StageRecord()})
        limits = effective_limits(work, config.research_limits, manifest.input_hash)

        def review():
            def invoke(prompt, output_type, version, **kwargs):
                # The one call is charged like every other: reserved against the run budget,
                # refunded only when no model answered; a contract rejection is re-asked once named.
                adapter.require_key()

                def once(attempt):
                    number = reserve_call(work, limits, billed=adapter.billed())
                    try:
                        return adapter.structured(attempt, output_type, work / "calls" / f"call_{number:03d}",
                                                  prompt_version=version, search=False)[0]
                    except AppError as exc:
                        if unanswered(exc):
                            refund_call(work, number)
                        raise
                    except BaseException:
                        refund_call(work, number)
                        raise
                    finally:
                        settle_call(work, number)
                return re_asked(once, cast_note(config) + prompt)

            report = series_report(config, plan, scripts, manifest.input_hash, invoke)
            # What blocks, per episode, so the Studio can offer each episode's revision with it (D-184).
            issues = series_issues(SeriesReview.model_validate(report["review"])) if report.get("review") else {}
            write_json(work / "series_review.json", {
                "report": report, "sha256": digest(report),
                "episode_issues": {key: [issue.model_dump() for issue in rows] for key, rows in issues.items()}})
            outputs = [work / "series_review.json", work / "inputs.json"]
            if outline_run:
                # Where the Studio finds the latest review of an outline's published episodes.
                write_json(root / SERIES_REVIEW_POINTER, {"outline_run_id": outline_run, "run_id": identifier})
                outputs.append(root / SERIES_REVIEW_POINTER)
            if inputs["mirror_report"]:
                quality["series_review"] = report
                quality["complete_series_review"] = report["status"] == "passed"
                quality["series_review_run_id"] = identifier
                write_yaml(quality_path, quality)
                outputs.append(quality_path)
            return outputs

        return execute_stages(root, manifest, path, {"review": review})
