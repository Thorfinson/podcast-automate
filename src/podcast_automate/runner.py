from __future__ import annotations

import json
import re
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .audio import assemble, run_tts
from .codex import CodexAdapter
from .errors import AppError
from .models import EpisodeScript, Failure, RunManifest, StageRecord, now
from .storage import (digest, file_hash, inside, load_project, project_lock,
                      read_yaml, write_json, write_yaml)

# Optional observer in the isolated Studio worker; no global project state.
run_observer = ContextVar("run_observer", default=None)


def probe_script(language: str = "de-DE") -> EpisodeScript:
    filename = {"de-DE": "audio_probe.json", "en-US": "audio_probe_en.json"}[language]
    return EpisodeScript.model_validate_json(
        files("podcast_automate").joinpath(f"data/{filename}").read_text(encoding="utf-8"))


def manifest_path(root: Path, run_id: str | None = None) -> Path:
    if run_id is None:
        try:
            run_id = json.loads((root / "runs/latest.json").read_text(encoding="utf-8"))["run_id"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AppError("Noch kein Lauf vorhanden. Zuerst text-probe oder audio-probe starten.",
                           code="no_run", status="blocked") from exc
    if not isinstance(run_id, str) or not re.fullmatch(r"run_[a-z0-9_]+", run_id):
        raise AppError("Ungültige Run-ID.", code="invalid_run", status="blocked")
    return inside(root, f"runs/{run_id}/run_manifest.yaml")


def outputs_valid(root: Path, stage: StageRecord) -> bool:
    if stage.status != "completed" or not stage.outputs:
        return False
    try:
        return all(file_hash(inside(root, path)) == checksum
                   for path, checksum in stage.outputs.items())
    except (OSError, AppError):
        return False


def status(root: Path, run_id: str | None = None) -> dict:
    config = load_project(root)
    if run_id is None and not (root / "runs/latest.json").exists():
        return {"topic": config.topic, "status": "not_started", "run": None}
    manifest = RunManifest.model_validate(read_yaml(manifest_path(root, run_id)))
    return {
        "topic": config.topic, "status": manifest.status,
        "project_changed": manifest.project_hash != digest(config.model_dump(mode="json")),
        "run": manifest.model_dump(mode="json"),
        "invalid_completed_stages": [
            name for name, record in manifest.stages.items()
            if record.status == "completed" and not outputs_valid(root, record)
        ],
    }


def run_probe(root: Path, *, kind: str | None = None,
              resume: bool = False, run_id: str | None = None,
              approve_audio: bool = False) -> RunManifest:
    root = root.resolve()
    config = load_project(root)
    script = probe_script(config.language)
    with project_lock(root):
        config_hash = digest(config.model_dump(mode="json"))
        if resume:
            path = manifest_path(root, run_id)
            manifest = RunManifest.model_validate(read_yaml(path))
            kind = manifest.kind
        else:
            if kind not in {"text_probe", "audio_probe"}:
                raise AppError("Unbekannter Probentyp.", code="invalid_probe")
            if kind == "audio_probe" and not approve_audio:
                raise AppError("Audio-Probe mit --approve-audio starten.",
                               code="audio_approval_required", status="blocked")
            identifier = "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
            identifier += "_" + uuid.uuid4().hex[:8]
            path = manifest_path(root, identifier)
            stages = ["codex_probe"] if kind == "text_probe" else ["synthesis", "assembly"]
            manifest = RunManifest(
                run_id=identifier, kind=kind, project_hash=config_hash, input_hash="",
                audio_approved=approve_audio,
                stages={name: StageRecord() for name in stages})
        current_hash = digest({
            "project": config_hash, "pipeline": __version__, "kind": kind,
            "prompt": "text_probe.v1",
            "script": script.model_dump(mode="json") if kind == "audio_probe" else None,
        })
        if resume and manifest.input_hash != current_hash:
            raise AppError("Eingaben oder Programmversion wurden geändert. Bitte eine neue Probe starten.",
                           code="inputs_changed", status="blocked")
        manifest.input_hash = current_hash
        if kind == "audio_probe":
            manifest.audio_approved = manifest.audio_approved or approve_audio
            if not manifest.audio_approved:
                raise AppError("Audio-Freigabe fehlt.", code="audio_approval_required", status="blocked")
        work = path.parent
        work.mkdir(parents=True, exist_ok=True)
        if not resume:
            write_yaml(work / "project_snapshot.yaml", config.model_dump(mode="json"))
        write_json(root / "runs/latest.json", {"run_id": manifest.run_id})

        def text_stage():
            output, metadata = CodexAdapter(config.runtime).probe(config.topic, work / "codex")
            directory = root / "probes/text" / manifest.run_id
            write_json(directory / "result.json", output.model_dump())
            write_json(directory / "metadata.json", metadata)
            return [directory / "result.json", directory / "metadata.json"]

        def synthesis_stage():
            write_json(work / "script.json", script.model_dump(mode="json"))
            paths = run_tts(config, script, root, work)
            return [work / "tts_report.json", work / "script.json", *paths]

        def assembly_stage():
            report = json.loads((work / "tts_report.json").read_text(encoding="utf-8"))
            paths = [inside(root / "cache/audio", row["path"]) for row in report["segments"]]
            return assemble(script, paths, root / "probes/audio" / manifest.run_id,
                            max_seconds=config.max_episode_minutes * 60, language=config.language)

        actions = {"codex_probe": text_stage, "synthesis": synthesis_stage, "assembly": assembly_stage}
        return execute_stages(root, manifest, path, actions)


def execute_stages(root: Path, manifest: RunManifest, path: Path, actions: dict, *, stop_after=None) -> RunManifest:
    def save():
        manifest.updated_at = now()
        write_yaml(path, manifest.model_dump(mode="json"))
        observer = run_observer.get()
        if observer:
            observer(manifest)

    manifest.status = "running"
    save()
    invalidate_following = False
    for index, (name, record) in enumerate(manifest.stages.items()):
        if stop_after is not None and index > list(manifest.stages).index(stop_after):
            manifest.status = "pending"
            save()
            return manifest
        if not invalidate_following and outputs_valid(root, record):
            continue
        if not invalidate_following:
            for following in list(manifest.stages.values())[index:]:
                following.status = "pending"
                following.outputs = {}
                following.error = None
        invalidate_following = True
        record.status = "running"
        record.attempts += 1
        record.outputs = {}
        record.error = None
        save()
        try:
            outputs = actions[name]()
            if not outputs:
                raise AppError("Stufe lieferte keine Artefakte.", code="missing_outputs")
            record.outputs = {p.relative_to(root).as_posix(): file_hash(p) for p in outputs}
            record.status = "completed"
            save()
        except KeyboardInterrupt:
            record.status = manifest.status = "pending"
            record.error = Failure(code="interrupted", message="Abgebrochen; mit pla resume fortsetzen.")
            save()
            raise
        except (AppError, OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            error = exc if isinstance(exc, AppError) else AppError(
                "Lokale Verarbeitung fehlgeschlagen; Eingaben und Dateien prüfen.",
                code="invalid_local_data")
            record.status = manifest.status = error.status
            record.error = Failure(code=error.code, message=str(error))
            save()
            return manifest
    manifest.status = "completed"
    save()
    return manifest
