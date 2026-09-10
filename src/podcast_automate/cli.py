from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .doctor import inspect
from .errors import AppError
from .models import RuntimeSettings, SCHEMAS, TopicBrief
from .runner import run_probe, status
from .storage import init_project, load_project, write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pla", description="Podcast Automate: Projektverwaltung und technische Proben.")
    parser.add_argument("--version", action="version", version=f"podcast-automate {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Persönliches Projekt anlegen")
    init.add_argument("project_dir", type=Path)
    init.add_argument("--topic", required=True)
    init.add_argument("--total-minutes", type=float, default=None)
    init.add_argument("--tts-python", help="Python der separaten Qwen-Umgebung")
    doctor = commands.add_parser("doctor", help="Installation und Abo-Anmeldung prüfen; kein Modellaufruf")
    doctor.add_argument("project_dir", type=Path, nargs="?")
    state = commands.add_parser("status", help="Fortschritt und Fehler des letzten Laufs anzeigen")
    state.add_argument("project_dir", type=Path)
    state.add_argument("--run-id")
    text_probe = commands.add_parser("text-probe", help="Strukturierte Codex-Abo-Verbindungsprobe")
    text_probe.add_argument("project_dir", type=Path)
    audio_probe = commands.add_parser("audio-probe", help="Deutsche Qwen-Hörprobe automatisch montieren")
    audio_probe.add_argument("project_dir", type=Path)
    audio_probe.add_argument("--approve-audio", action="store_true")
    resume = commands.add_parser("resume", help="Unterbrochene Probe ohne fertige Arbeit zu wiederholen fortsetzen")
    resume.add_argument("project_dir", type=Path)
    resume.add_argument("--run-id")
    resume.add_argument("--approve-audio", action="store_true")
    schemas = commands.add_parser("schemas", help="Implementierte JSON-Schemas exportieren")
    schemas.add_argument("output_dir", type=Path)
    for command in (init, doctor, state, text_probe, audio_probe, resume, schemas):
        command.add_argument("--json", action="store_true", dest="json_output")
    return parser


def emit(data: dict, as_json: bool):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if "checks" in data:
        for check in data["checks"]:
            print(f"{'OK' if check['ok'] else 'FEHLT'} {check['name']}: {check['detail']}")
        print("Modell-Inferenz ist erst mit audio-probe geprüft.")
        return
    print(data.get("message", f"Status: {data.get('status', 'ok')}"))
    run = data.get("run")
    if run:
        print(f"Lauf: {run['run_id']} ({run['kind']})")
        for name, stage in run["stages"].items():
            print(f"  {name}: {stage['status']}")
            if stage["error"]:
                print(f"  {stage['error']['message']}")
            for output in stage["outputs"]:
                if output.startswith("probes/"):
                    print(f"  {output}")
    if data.get("project_changed"):
        print("Projektkonfiguration geändert; für neue Eingaben eine neue Probe starten.")
    if data.get("invalid_completed_stages"):
        print("Artefakte fehlen oder wurden geändert: " + ", ".join(data["invalid_completed_stages"]))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            runtime = RuntimeSettings()
            if args.tts_python:
                runtime.tts_python = str(Path(args.tts_python).resolve())
            config = TopicBrief(topic=args.topic, central_question=args.topic,
                                target_total_minutes=args.total_minutes, runtime=runtime)
            init_project(args.project_dir.resolve(), config)
            data = {"status": "created", "message": f"Projekt angelegt: {args.project_dir.resolve()}",
                    "project": config.model_dump(mode="json")}
            code = 0
        elif args.command == "doctor":
            runtime = load_project(args.project_dir).runtime if args.project_dir else RuntimeSettings()
            data = inspect(runtime)
            code = 0 if data["ready"] else 1
        elif args.command == "status":
            data = status(args.project_dir.resolve(), args.run_id)
            code = 0
        elif args.command == "schemas":
            for name, model in SCHEMAS.items():
                write_json(args.output_dir / f"{name}.schema.json", model.model_json_schema())
            data = {"status": "completed", "message": f"Schemas exportiert: {args.output_dir.resolve()}"}
            code = 0
        else:
            manifest = run_probe(
                args.project_dir,
                kind={"text-probe": "text_probe", "audio-probe": "audio_probe"}.get(args.command),
                resume=args.command == "resume", run_id=getattr(args, "run_id", None),
                approve_audio=getattr(args, "approve_audio", False))
            data = {"status": manifest.status, "run": manifest.model_dump(mode="json")}
            code = 0 if manifest.status == "completed" else 2 if manifest.status == "waiting_for_quota" else 1
        emit(data, args.json_output)
        return code
    except AppError as exc:
        emit({"status": exc.status, "code": exc.code, "message": str(exc)}, args.json_output)
        return 1
    except ValidationError as exc:
        errors = "; ".join(".".join(map(str, item["loc"])) + ": " + item["msg"]
                           for item in exc.errors(include_input=False, include_url=False))
        emit({"status": "blocked", "code": "invalid_configuration",
              "message": f"Ungültige Daten: {errors}"}, args.json_output)
        return 1
    except OSError:
        emit({"status": "failed", "code": "filesystem_error",
              "message": "Dateizugriff fehlgeschlagen; Pfad und Schreibrechte prüfen."}, args.json_output)
        return 1
    except KeyboardInterrupt:
        emit({"status": "pending", "code": "interrupted",
              "message": "Abgebrochen. Gespeicherten Lauf mit pla resume fortsetzen."}, args.json_output)
        return 130
