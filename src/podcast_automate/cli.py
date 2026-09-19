from __future__ import annotations

import argparse
import getpass
import json
import warnings
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .doctor import inspect
from .errors import AppError
from .episode_audio import run_episode_audio
from .logs import configure_logging, logger, release_logging
from .models import RuntimeSettings, SCHEMAS, TopicBrief
from .polishing import DialoguePolishReview
from .runner import run_probe, status
from .runner import manifest_path
from .research import run_research
from .research_models import RESEARCH_SCHEMAS
from .script_models import SCRIPT_SCHEMAS
from .series_review import SeriesReview
from .teaching import TEACHING_SCHEMAS
from .scripting import run_script
from .storage import init_project, load_project, read_yaml, write_json
from .platforms import configure_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pla", description="Podcast Automate: Projektverwaltung und technische Proben.")
    parser.add_argument("--version", action="version", version=f"podcast-automate {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    studio = commands.add_parser("studio", help="Geführtes Podcast Studio lokal im Browser öffnen")
    studio.add_argument("workspace", type=Path, nargs="?", default=Path.cwd())
    studio.add_argument("--port", type=int, default=8765)
    studio.add_argument("--no-browser", action="store_true")
    studio.set_defaults(json_output=False)
    init = commands.add_parser("init", help="Persönliches Projekt anlegen")
    init.add_argument("project_dir", type=Path)
    init.add_argument("--topic", required=True)
    init.add_argument("--total-minutes", type=float, default=None)
    init.add_argument("--tts-python", help="Python der separaten Qwen-Umgebung")
    doctor = commands.add_parser("doctor", help="Installation, Abo-Anmeldungen (Codex, Claude) und Kontingent prüfen; kein Modellaufruf")
    doctor.add_argument("project_dir", type=Path, nargs="?")
    doctor.add_argument("--skip-tts", action="store_true", help="Lokales Qwen überspringen, etwa bei Gemini-Audio")
    state = commands.add_parser("status", help="Fortschritt und Fehler des letzten Laufs anzeigen")
    state.add_argument("project_dir", type=Path)
    state.add_argument("--run-id")
    text_probe = commands.add_parser("text-probe", help="Strukturierte Abo-Verbindungsprobe (Codex oder Claude Code)")
    text_probe.add_argument("project_dir", type=Path)
    text_probe.add_argument("--backend", choices=("codex_cli", "claude_code", "auto"),
                            help="Abo-Anbieter der Probe; Standard codex_cli")
    quota = commands.add_parser("quota", help="Kontingent beider Abos (Codex, Claude) ohne Modellaufruf anzeigen")
    audio_probe = commands.add_parser("audio-probe", help="Deutsche oder englische Qwen-Hörprobe montieren")
    audio_probe.add_argument("project_dir", type=Path)
    audio_probe.add_argument("--approve-audio", action="store_true")
    research = commands.add_parser("research", help="Live recherchieren, Quellen abrufen und ein belegtes Dossier erstellen")
    research.add_argument("project_dir", type=Path)
    research.add_argument("--reuse-sources", metavar="RUN_ID",
                          help="Gespeicherte Quellen für einen neuen Dossiertext wiederverwenden")
    research.add_argument("--backend", choices=("codex_cli", "claude_code", "auto"),
                          help="Abo-Anbieter der Recherche; Standard codex_cli, auto wechselt bei leerem Kontingent")
    research.add_argument("--model", help="Modell-ID des festen Abo-Anbieters")
    research.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"),
                          help="Denkaufwand für Recherche-Modellaufrufe des festen Anbieters")
    script = commands.add_parser("script", help="Geprüftes Dossier in Serienentwurf und Dialogskripte umsetzen")
    script.add_argument("project_dir", type=Path)
    script.add_argument("--episode", help="Nur die gewählte Folge schreiben, zum Beispiel ep_001")
    script.add_argument("--revise", metavar="EPISODE_ID", help="Vorhandenes Skript mit seinem bisherigen Plan überarbeiten")
    script.add_argument("--feedback", default="", help="Redaktionelle Rückmeldung für --revise")
    series = commands.add_parser("series-review",
        help="Veröffentlichte Skripte eines Laufs als Serie prüfen, ohne den Lauf zu verändern")
    series.add_argument("project_dir", type=Path)
    series.add_argument("--run", dest="run_id", help="Skriptlauf; Standard ist der zuletzt veröffentlichte")
    series.add_argument("--backend", choices=("codex_cli", "claude_code", "auto"))
    series.add_argument("--model", help="Modell-ID des festen Abo-Anbieters")
    series.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"))
    audio = commands.add_parser("audio", help="Geprüfte Folge mit Qwen vertonen und zur Hörprüfung montieren")
    audio.add_argument("project_dir", type=Path)
    audio.add_argument("--episode", required=True)
    audio.add_argument("--approve-audio", action="store_true")
    audio.add_argument("--approval-note", default="", help="Rückmeldung zur Freigabe dieses Skriptstands")
    resume = commands.add_parser("resume", help="Unterbrochene Probe ohne fertige Arbeit zu wiederholen fortsetzen")
    resume.add_argument("project_dir", type=Path)
    resume.add_argument("--run-id")
    resume.add_argument("--approve-audio", action="store_true")
    resume.add_argument("--approval-note", default="", help="Rückmeldung zur erneuten Audio-Freigabe")
    for command in (script, resume):
        command.add_argument("--backend", choices=("codex_cli", "openrouter", "claude_code", "auto"),
                             help="Textanbieter für Skripte und Reviews; Standard codex_cli, auto wählt je Aufruf Codex oder "
                                  "Claude nach Kontingent, bei resume gespeicherter Anbieter")
        command.add_argument("--model", help="Modell-ID des Textanbieters; für OpenRouter erforderlich")
        command.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"),
                             help="Denkaufwand für Textmodellaufrufe; bei resume bleibt die gespeicherte Stufe erhalten")
        command.add_argument("--api-key", nargs="?", const="", default=None, metavar="KEY",
                             help="OpenRouter-Key nur für diesen Aufruf; ohne Wert verdeckt abfragen, alternativ OPENROUTER_API_KEY")
        command.add_argument("--max-output-tokens", type=int,
                             help="OpenRouter-Ausgabelimit pro Modellaufruf; Standard 32768")
    approve = commands.add_parser("approve", help="Ausdrückliche Freigabe für einen Lauf: höheres Aufruf- oder "
                                                   "Suchrundenlimit oder eine blockierte Teilfrage als Lücke akzeptieren")
    approve.add_argument("project_dir", type=Path)
    approve.add_argument("--run-id", help="Standard: der letzte Lauf des Projekts")
    approve.add_argument("--model-calls", type=int, help="Neues Limit für Modellaufrufe dieses Laufs")
    approve.add_argument("--search-rounds", type=int, help="Neues Limit für Web-Suchrunden dieses Laufs")
    approve.add_argument("--accept-gap", metavar="TASK_ID",
                         help="Blockierte Teilfrage, die im Dossier als Lücke dokumentiert bleibt")
    approve.add_argument("--reason", default="", help="Kurze Begründung der akzeptierten Lücke")
    schemas = commands.add_parser("schemas", help="Implementierte JSON-Schemas exportieren")
    schemas.add_argument("output_dir", type=Path)
    for command in (init, doctor, state, text_probe, audio_probe, research, script, series, audio, resume, schemas, quota, approve):
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
    if "lines" in data and "codex_cli" in data:
        for line in data["lines"]:
            print(line)
        print(f"Geprüft: {data['checked_at']}")
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
                if output.startswith(("probes/", "research/", "reports/", "models/", "episodes/")):
                    print(f"  {output}")
    if data.get("project_changed"):
        print("Projektkonfiguration geändert; für neue Eingaben eine neue Probe starten.")
    if data.get("invalid_completed_stages"):
        print("Artefakte fehlen oder wurden geändert: " + ", ".join(data["invalid_completed_stages"]))
    if data.get("failure_records"):
        print("Technische Fehlerprotokolle: " + ", ".join(data["failure_records"]))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_path(Path.cwd())
    project_dir = getattr(args, "project_dir", None)
    log_path = project_dir / "logs/pla.log" if isinstance(project_dir, Path) and project_dir.is_dir() else None
    configure_logging(log_path)
    try:
        return run_command(args)
    finally:
        release_logging(log_path)


def run_command(args) -> int:
    try:
        if args.command == "studio":
            from .studio import serve
            serve(args.workspace, port=args.port, open_browser=not args.no_browser)
            return 0
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
            data = inspect(runtime, include_tts=not args.skip_tts)
            code = 0 if data["ready"] else 1
        elif args.command == "status":
            data = status(args.project_dir.resolve(), args.run_id)
            code = 0
        elif args.command == "quota":
            from .subscriptions import quota_overview
            overview = quota_overview(RuntimeSettings(), refresh=True)
            data = {"status": "ready" if overview["any_available"] else
                    "waiting_for_quota" if overview["any_usable"] else "blocked", **overview}
            code = 0 if overview["any_available"] else 2 if overview["any_usable"] else 1
        elif args.command == "approve":
            from .run_budget import approve_model_call_limit, approve_research_gap
            root = args.project_dir.resolve()
            run_id = manifest_path(root, args.run_id).parent.name
            if args.model_calls is None and args.search_rounds is None and not args.accept_gap:
                raise AppError("Freigabe angeben: --model-calls, --search-rounds oder --accept-gap.",
                               code="invalid_request", status="blocked")
            data = {"status": "approved", "run_id": run_id}
            if args.model_calls is not None or args.search_rounds is not None:
                approval = approve_model_call_limit(root, run_id, args.model_calls, search_rounds=args.search_rounds)
                data["budget_approval"] = approval.model_dump(mode="json")
            if args.accept_gap:
                gap = approve_research_gap(root, run_id, args.accept_gap, args.reason)
                data["gap_approval"] = gap.model_dump(mode="json")
            data["message"] = "Freigabe gespeichert. Der Lauf übernimmt sie beim nächsten Aufruf oder mit pla resume."
            code = 0
        elif args.command == "schemas":
            for name, model in (SCHEMAS | RESEARCH_SCHEMAS | SCRIPT_SCHEMAS | TEACHING_SCHEMAS |
                                {"dialogue_polish_review": DialoguePolishReview, "series_review": SeriesReview}).items():
                write_json(args.output_dir / f"{name}.schema.json", model.model_json_schema())
            data = {"status": "completed", "message": f"Schemas exportiert: {args.output_dir.resolve()}"}
            code = 0
        elif args.command == "series-review":
            from .scripting import run_series_review, series_review_target
            manifest = run_series_review(args.project_dir, run_id=args.run_id, backend=args.backend,
                                         model=args.model, reasoning_effort=args.reasoning_effort)
            target = series_review_target(args.project_dir, manifest.run_id)
            verdict = ("Serienprüfung abgeschlossen." if manifest.status == "completed"
                       else "Serienprüfung meldet Einwände; Bericht prüfen.")
            location = (" Das Urteil steht in reports/script_quality.yaml." if target["report_mirrored"] else
                        f" Der geprüfte Lauf {target['script_run_id']} ist nicht der veröffentlichte Stand; "
                        f"das Urteil steht nur unter runs/{manifest.run_id}/series_review.json.")
            data = {"status": manifest.status, "run_id": manifest.run_id, **target, "message": verdict + location}
            code = 0 if manifest.status == "completed" else 1
        else:
            episode_audio_run = args.command == "audio" or (
                args.command == "resume" and
                read_yaml(manifest_path(args.project_dir.resolve(), args.run_id)).get("kind") == "episode_audio")
            script_run = args.command == "script" or (
                args.command == "resume" and
                read_yaml(manifest_path(args.project_dir.resolve(), args.run_id)).get("kind") == "script")
            research_run = args.command == "research" or (
                args.command == "resume" and
                read_yaml(manifest_path(args.project_dir.resolve(), args.run_id)).get("kind") == "research")
            if (args.command == "resume" and
                    read_yaml(manifest_path(args.project_dir.resolve(), args.run_id)).get("kind") == "series_review"):
                # A review run holds one verdict and nothing to continue; it never becomes a probe.
                raise AppError("Ein Serienprüflauf ist nicht fortsetzbar; pla series-review erneut aufrufen.",
                               code="invalid_run", status="blocked")
            text_options = ("backend", "model", "api_key", "max_output_tokens", "reasoning_effort")
            given = {name for name in text_options if getattr(args, name, None) is not None}
            allowed = (set(text_options) if script_run else {"backend", "model", "reasoning_effort"} if research_run
                       else {"backend"} if args.command == "text-probe" else set())
            if given - allowed:
                raise AppError("Textanbieter-Optionen gelten für script, research, text-probe und die Wiederaufnahme "
                               "eines Skript- oder Rechercheaufs; --api-key und --max-output-tokens nur für script.",
                               code="invalid_backend", status="blocked")
            if episode_audio_run:
                manifest = run_episode_audio(args.project_dir, episode=getattr(args, "episode", None),
                    approve_audio=getattr(args, "approve_audio", False), approval_note=getattr(args, "approval_note", ""),
                    resume=args.command == "resume", run_id=getattr(args, "run_id", None))
            elif script_run:
                api_key = args.api_key
                if api_key == "":
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("error", getpass.GetPassWarning)
                            api_key = getpass.getpass("OpenRouter API-Key (nur für diesen Aufruf): ")
                    except (EOFError, getpass.GetPassWarning):
                        raise AppError("Keine verdeckte Key-Eingabe möglich; OPENROUTER_API_KEY verwenden.",
                                       code="openrouter_key_required", status="blocked") from None
                manifest = run_script(args.project_dir, episode=getattr(args, "episode", None),
                                      revise=getattr(args, "revise", None), feedback=getattr(args, "feedback", ""),
                                      resume=args.command == "resume", run_id=getattr(args, "run_id", None),
                                      backend=args.backend, model=args.model, api_key=api_key,
                                      max_output_tokens=args.max_output_tokens, reasoning_effort=args.reasoning_effort)
            elif research_run:
                manifest = run_research(args.project_dir, resume=args.command == "resume",
                                        run_id=getattr(args, "run_id", None),
                                        reuse_sources=getattr(args, "reuse_sources", None),
                                        backend=getattr(args, "backend", None), model=getattr(args, "model", None),
                                        reasoning_effort=getattr(args, "reasoning_effort", None))
            else:
                manifest = run_probe(
                    args.project_dir,
                    kind={"text-probe": "text_probe", "audio-probe": "audio_probe"}.get(args.command),
                    resume=args.command == "resume", run_id=getattr(args, "run_id", None),
                    approve_audio=getattr(args, "approve_audio", False), backend=getattr(args, "backend", None))
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
    except OSError as exc:
        logger("cli").error("Dateizugriff fehlgeschlagen: %s", exc, exc_info=exc)
        emit({"status": "failed", "code": "filesystem_error",
              "message": "Dateizugriff fehlgeschlagen; Pfad und Schreibrechte prüfen."}, args.json_output)
        return 1
    except KeyboardInterrupt:
        emit({"status": "pending", "code": "interrupted",
              "message": "Abgebrochen. Gespeicherten Lauf mit pla resume fortsetzen."}, args.json_output)
        return 130
