"""Local browser workspace. Reuses the production pipelines in isolated workers."""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.request import urlopen

from pydantic import Field

from .errors import AppError
from .models import Contract, EpisodeScript, RuntimeSettings, TopicBrief, now
from .runner import manifest_path
from .scripting import outline_hash, script_metrics
from .speech import AudioChoice, GEMINI_VOICES, QWEN_VOICES, audio_catalog, selected_audio
from .storage import digest, file_hash, init_project, inside, load_project, project_lock, read_yaml, write_json, write_yaml
from .voice_samples import ready_sample, sample_inventory

VOICES = QWEN_VOICES


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value):
        raise AppError("Ungültige Projektauswahl.", code="invalid_project")
    return value


class BriefProposal(Contract):
    message: str = Field(min_length=1, max_length=12000)
    topic: str = Field(min_length=1, max_length=500)
    central_question: str = Field(min_length=1, max_length=2000)
    prior_knowledge: str = Field(max_length=3000)
    depth_request: str = Field(min_length=1, max_length=6000)
    focus_questions: list[str] = Field(max_length=20)
    excluded_topics: list[str] = Field(max_length=20)


class TextChoice(Contract):
    provider: str = "codex_cli"
    model: str | None = Field(default=None, max_length=200)
    max_output_tokens: int = Field(default=32768, ge=1024, le=200000)

    def kwargs(self):
        if self.provider not in {"codex_cli", "openrouter"}:
            raise AppError("Textanbieter auswählen.", code="invalid_backend")
        return {"backend": self.provider, "model": self.model or None,
                "max_output_tokens": self.max_output_tokens if self.provider == "openrouter" else None}


class Studio:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.projects = self.workspace / "projects"
        self.token = secrets.token_urlsafe(32)
        self.key = ""
        self.mutex = threading.RLock()
        self.process = None
        self.process_root = None
        ffmpeg = self.workspace / "tools/ffmpeg/bin"
        if ffmpeg.is_dir():
            os.environ["PATH"] = str(ffmpeg) + os.pathsep + os.environ.get("PATH", "")

    def root(self, project):
        root = inside(self.projects, identifier(project))
        if not (root / "project.yaml").is_file():
            raise AppError("Projekt nicht gefunden.", code="unknown_project")
        return root

    def runtime(self):
        settings = RuntimeSettings()
        python = self.workspace / ".venv-tts/Scripts/python.exe"
        if not python.exists():
            python = self.workspace / ".venv-tts/bin/python"
        if python.exists():
            settings.tts_python = str(python)
        # Use the installed, pinned model revision, without copying a pilot's topic.
        for path in sorted(self.projects.glob("*/project.yaml")):
            try:
                runtime = load_project(path.parent).runtime
                if runtime.tts_model == settings.tts_model and re.fullmatch(r"[a-f0-9]{40}", runtime.tts_revision):
                    settings.tts_revision = runtime.tts_revision
                    break
            except (AppError, ValueError):
                continue
        return settings

    def bootstrap(self):
        projects = []
        for path in sorted(self.projects.glob("*/project.yaml")):
            try:
                identifier(path.parent.name)
                config = load_project(path.parent)
                projects.append({"id": path.parent.name, "topic": config.topic})
            except (AppError, ValueError):
                continue
        return {"app": "podcast-studio", "workspace": str(self.workspace),
                "token": self.token, "projects": projects, "voices": VOICES,
                "audio_catalog": audio_catalog(),
                "voice_samples": sample_inventory(self.projects),
                "key_available": bool(self.key or os.environ.get("OPENROUTER_API_KEY")),
                "defaults": TopicBrief(topic="Neues Podcast-Projekt", runtime=self.runtime(),
                                      voice_profile={"host_a": "Aiden", "host_b": "Vivian"}).model_dump(mode="json")}

    def job(self, root):
        data = read_json(root / "studio/job.json")
        if data and data["status"] == "running":
            owned = self.process_root == root and self.process is not None and self.process.poll() is None
            if not owned:
                data["status"] = "interrupted"
                data["message"] = "Auftrag unterbrochen. Gespeicherten Stand fortsetzen."
                write_json(root / "studio/job.json", data)
            run = data.get("run")
            if run:
                work = manifest_path(root, run["run_id"]).parent
                progress = read_json(work / "progress.json")
                if progress and "total_segments" in progress:
                    if progress.get("chapter_progress"):
                        nested = read_json(inside(root, progress["chapter_progress"]), {})
                        progress["completed_segments"] = min(progress["total_segments"],
                            progress["completed_segments"] + nested.get("completed_segments", nested.get("completed", 0)))
                    data["progress"] = progress
        if data and (data.get("run") or {}).get("kind") == "script":
            from .studio_progress import script_progress
            data["progress"] = script_progress(root, data["run"])
        return data

    def detail(self, project):
        root = self.root(project)
        config = load_project(root)
        audio = selected_audio(root, config)
        data = {"id": project, "config": config.model_dump(mode="json"),
                "config_hash": digest(config.model_dump(mode="json")),
                "text": read_json(root / "studio/text.json", TextChoice().model_dump()),
                "audio_settings": audio.model_dump(), "audio_hash": digest(audio.model_dump()),
                "voice_samples": sample_inventory(self.projects),
                "chat": read_json(root / "studio/chat.json", []), "job": self.job(root),
                "outline": None, "episodes": [], "research": None, "run": None}
        pointer = read_json(root / "studio/outline.json")
        if pointer:
            work = manifest_path(root, pointer["run_id"]).parent
            if (work / "series_plan.json").exists():
                data["outline"] = {"run_id": pointer["run_id"], "plan": read_json(work / "series_plan.json"),
                                   "hash": outline_hash(work), "approval": read_json(work / "plan_approval.json")}
        if (root / "research/research_briefing.md").is_file():
            data["research"] = (root / "research/research_briefing.md").read_text(encoding="utf-8")
        elif (root / "research/dossier.yaml").exists():
            data["research"] = (root / "research/dossier.yaml").read_text(encoding="utf-8")
        if (root / "runs/latest.json").exists():
            data["run"] = read_yaml(manifest_path(root))
        for folder in sorted((root / "episodes").glob("ep_*")):
            if not (folder / "script.yaml").exists():
                continue
            script = EpisodeScript.model_validate(read_yaml(folder / "script.yaml"))
            report = read_json(folder / "audio_latest.json", {})
            audio_paths = [part["audio"] for part in report.get("parts", [])
                           if inside(root, part["audio"]).is_file()]
            data["episodes"].append({"script": script.model_dump(), "hash": file_hash(folder / "script.yaml"),
                "readable_hash": file_hash(folder / "script.md"), "metrics": script_metrics(script),
                "audio": audio_paths, "audio_current": report.get("script_sha256") == file_hash(folder / "script.yaml")
                and report.get("voices") == audio.voices
                and report.get("audio_generation", {"provider": "qwen3_local", "voices": report.get("voices")}) == audio.model_dump()})
        return data

    def idle(self, root=None):
        if self.process is not None and self.process.poll() is None:
            raise AppError("Ein Auftrag läuft bereits. Erst fertigstellen oder anhalten.", code="project_busy")
        if root:
            with project_lock(root):
                pass

    def create(self, data):
        config = TopicBrief.model_validate(data["config"])
        choice = TextChoice.model_validate(data.get("text", {}))
        choice.kwargs()
        if choice.provider == "openrouter":
            from .openrouter import OpenRouterAdapter
            OpenRouterAdapter(config.runtime, model=choice.model, api_key=self.key or None,
                              max_output_tokens=choice.max_output_tokens)
        slug = re.sub(r"[^a-z0-9]+", "-", config.topic.lower()).strip("-")[:40] or "podcast"
        slug += "-" + uuid.uuid4().hex[:6]
        root = inside(self.projects, slug)
        # Runtime/executable paths come only from the local server, never a web form or LLM.
        config.runtime = self.runtime()
        self.validate_voices(config)
        audio = AudioChoice.model_validate(data.get("audio_settings", {"voices": config.voice_profile}))
        init_project(root, config)
        write_json(root / "studio/text.json", choice.model_dump())
        write_json(root / "studio/audio.json", audio.model_dump())
        return {"id": slug}

    @staticmethod
    def validate_voices(config):
        if any(v not in VOICES for v in config.voice_profile.values()):
            raise AppError("Bitte eine verfügbare Qwen-Stimme wählen.", code="invalid_voice")

    def save_text(self, root, data):
        choice = TextChoice.model_validate(data)
        choice.kwargs()
        if choice.provider == "openrouter":
            from .openrouter import OpenRouterAdapter
            OpenRouterAdapter(load_project(root).runtime, model=choice.model, api_key=self.key or None,
                              max_output_tokens=choice.max_output_tokens)
        write_json(root / "studio/text.json", choice.model_dump())

    def save(self, project, data):
        root = self.root(project)
        self.idle(root)
        with project_lock(root):
            old = load_project(root)
            if data.get("config_hash") != digest(old.model_dump(mode="json")):
                raise AppError("Projekt wurde inzwischen geändert. Ansicht neu laden.", code="inputs_changed")
            config = TopicBrief.model_validate(data["config"])
            config.runtime = old.runtime
            self.validate_voices(config)
            current_audio = selected_audio(root, old)
            if "audio_settings" in data and data.get("audio_hash") != digest(current_audio.model_dump()):
                raise AppError("Audioauswahl inzwischen geändert. Ansicht neu laden.", code="inputs_changed")
            audio = AudioChoice.model_validate(data.get("audio_settings", current_audio.model_dump()))
            self.save_text(root, data["text"])
            write_yaml(root / "project.yaml", config.model_dump(mode="json"))
            write_json(root / "studio/audio.json", audio.model_dump())
        return {"saved": True}

    def start(self, project, data):
        root = self.root(project)
        self.idle(root)
        action = data.get("action")
        if action not in {"assistant", "research", "plan", "replan", "script", "revise", "audio", "audio_sample", "audio_samples", "resume", "check"}:
            raise AppError("Unbekannter Arbeitsschritt.", code="invalid_action")
        payload = {"action": action, "message": str(data.get("message", ""))[:12000]}
        if action == "audio_sample":
            if data.get("voice") not in GEMINI_VOICES or data.get("language") not in {"de-DE", "en-US"}:
                raise AppError("Gemini-Stimme und Sprache für die Hörprobe auswählen.", code="invalid_voice")
            if data.get("approve_sample") is not True:
                raise AppError("Gemini-Hörprobe ausdrücklich erzeugen lassen.", code="audio_approval_required")
            payload.update(voice=data["voice"], language=data["language"])
        if action == "audio_samples":
            if data.get("language") not in {"de-DE", "en-US"}:
                raise AppError("Sprache für die Hörproben auswählen.", code="invalid_voice")
            if data.get("approve_samples") is not True:
                raise AppError("Fehlende Gemini-Hörproben ausdrücklich erzeugen lassen.", code="audio_approval_required")
            payload["language"] = data["language"]
        if action in {"assistant", "replan", "revise"} and not payload["message"].strip():
            raise AppError("Bitte deinen Änderungswunsch eingeben.", code="missing_feedback")
        if action in {"replan", "script"}:
            pointer = read_json(root / "studio/outline.json")
            if not pointer:
                raise AppError("Zuerst das Inhaltsverzeichnis erstellen.", code="plan_required")
            payload["run_id"] = pointer["run_id"]
            payload["plan_hash"] = data.get("plan_hash")
            if action == "script" and payload["plan_hash"] != outline_hash(manifest_path(root, pointer["run_id"]).parent):
                raise AppError("Bitte das aktuelle Inhaltsverzeichnis lesen und freigeben.", code="plan_changed")
        if action in {"audio", "revise"}:
            episode = data.get("episode")
            if not isinstance(episode, str) or not re.fullmatch(r"ep_[a-z0-9_]+", episode):
                raise AppError("Folge auswählen.", code="unknown_episode")
            payload["episode"] = episode
            if action == "audio":
                if data.get("approve_audio") is not True:
                    raise AppError("Das gelesene Skript ausdrücklich für Audio freigeben.", code="audio_approval_required")
                for key, name in (("script_hash", "script.yaml"), ("readable_hash", "script.md")):
                    if data.get(key) != file_hash(root / "episodes" / episode / name):
                        raise AppError("Skript inzwischen geändert. Bitte erneut lesen.", code="script_edited")
                    payload[key] = data[key]
                payload["config_hash"] = data.get("config_hash")
                if payload["config_hash"] != digest(load_project(root).model_dump(mode="json")):
                    raise AppError("Stimmen oder Auftrag geändert. Bitte die aktuelle Ansicht erneut prüfen.", code="inputs_changed")
                audio = selected_audio(root, load_project(root))
                if data.get("audio_hash") != digest(audio.model_dump()):
                    raise AppError("Audioanbieter oder Stimmen geändert. Bitte erneut freigeben.", code="inputs_changed")
                payload["audio_settings"] = audio.model_dump()
                payload["audio_hash"] = data["audio_hash"]
        if action == "resume":
            job = self.job(root)
            run_id = data.get("run_id") or ((job or {}).get("run") or {}).get("run_id")
            if not run_id:
                raise AppError("Kein fortsetzbarer Lauf vorhanden.", code="no_run")
            manifest_path(root, run_id)
            payload["run_id"] = run_id
        payload["text"] = read_json(root / "studio/text.json", TextChoice().model_dump())
        payload["api_key"] = self.key or None
        job = {"id": uuid.uuid4().hex, "action": action, "status": "running", "started_at": now(), "run": None}
        write_json(root / "studio/job.json", job)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            process = subprocess.Popen([sys.executable, "-m", "podcast_automate.studio_worker", str(root)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", env=env, start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.process, self.process_root = process, root
            process.stdin.write(json.dumps(payload, ensure_ascii=False))
            process.stdin.close()
        except OSError:
            job.update(status="failed", message="Auftrag konnte nicht gestartet werden.")
            write_json(root / "studio/job.json", job)
            raise AppError(job["message"], code="worker_start") from None
        return job

    def stop(self, project):
        root = self.root(project)
        process = self.process
        if root != self.process_root or process is None or process.poll() is not None:
            raise AppError("Kein aktiver Studio-Auftrag für dieses Projekt.", code="no_active_job")
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15, check=True)
        else:
            process.send_signal(signal.SIGINT)
        process.wait(timeout=20)
        job = read_json(root / "studio/job.json")
        job.update(status="interrupted", message="Angehalten. Fertige Arbeit bleibt gespeichert.")
        write_json(root / "studio/job.json", job)
        return job

    def media(self, project, relative):
        root = self.root(project)
        path = inside(root, relative)
        if path.suffix.lower() != ".mp3" or not relative.startswith(("exports/", "studio/samples/")) or not path.is_file():
            raise AppError("Audiodatei nicht gefunden.", code="missing_audio")
        return path


class StudioHandler(BaseHTTPRequestHandler):
    server_version = "PodcastStudio/1.0"

    def log_message(self, *_):
        pass  # No credentials, chat text or local paths in request logs.

    def send_data(self, status, body, kind="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def guard(self, mutation=False):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            raise AppError("Zugriff nur über die lokale Studio-Adresse.", code="forbidden")
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://{h}" for h in allowed}:
            raise AppError("Fremde Webseiten dürfen das Studio nicht steuern.", code="forbidden")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise AppError("Zugriff von einer fremden Webseite abgewiesen.", code="forbidden")
        if mutation and not secrets.compare_digest(self.headers.get("X-Studio-Token", ""), self.server.studio.token):
            raise AppError("Studio-Sitzung neu laden.", code="forbidden")

    def dispatch(self, mutation=False):
        app = self.server.studio
        try:
            self.guard(mutation)
            path = unquote(urlsplit(self.path).path)
            if mutation:
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise AppError("JSON-Anfrage erwartet.", code="invalid_request")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 128000:
                    raise AppError("Anfrage zu groß oder leer.", code="invalid_request")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise AppError("Ungültige Anfrage.", code="invalid_request")
                with app.mutex:
                    if path == "/api/quit":
                        if app.process is not None and app.process.poll() is None:
                            app.stop(app.process_root.name)
                        self.send_data(200, b'{"stopped":true}')
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                        return
                    elif path == "/api/key":
                        value = data.get("key", "")
                        if not isinstance(value, str) or len(value) > 512 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                            raise AppError("Ungültiger API-Key.", code="invalid_key")
                        app.key = value
                        result = {"key_available": bool(value or os.environ.get("OPENROUTER_API_KEY"))}
                    elif path == "/api/projects":
                        result = app.create(data)
                    else:
                        match = re.fullmatch(r"/api/projects/([^/]+)/(save|start|stop)", path)
                        if not match:
                            raise AppError("Seite nicht gefunden.", code="not_found")
                        project, action = match.groups()
                        result = app.stop(project) if action == "stop" else getattr(app, action)(project, data)
            elif path in {"/", "/app.js", "/style.css"}:
                name = "index.html" if path == "/" else path[1:]
                body = files("podcast_automate").joinpath("web", name).read_bytes()
                self.send_data(200, body, {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[name] + "; charset=utf-8")
                return
            elif path == "/api/bootstrap":
                result = app.bootstrap()
            elif (match := re.fullmatch(r"/api/projects/([^/]+)", path)):
                with app.mutex:
                    result = app.detail(match[1])
            elif (match := re.fullmatch(r"/media/([^/]+)/(.*)", path)):
                self.send_audio(app.media(match[1], match[2]))
                return
            elif (match := re.fullmatch(r"/samples/(de-DE|en-US)/([a-z_]+)", path)):
                if match[2] not in {v.lower() for v in VOICES}:
                    raise AppError("Stimme nicht gefunden.", code="not_found")
                audio = inside(app.projects, f"voice-samples/{match[1]}/{match[2]}/audio.mp3")
                self.send_audio(audio)
                return
            elif (match := re.fullmatch(r"/samples/gemini/(de-DE|en-US)/([A-Za-z]+)", path)):
                audio = ready_sample(app.projects, match[2], match[1])
                if audio is None:
                    raise AppError("Diese Hörprobe wurde noch nicht erstellt.", code="not_found")
                self.send_audio(audio)
                return
            else:
                raise AppError("Seite nicht gefunden.", code="not_found")
            self.send_data(200, json.dumps(result, ensure_ascii=False).encode("utf-8"))
        except (AppError, ValueError, KeyError, TypeError, OSError) as exc:
            code = exc.code if isinstance(exc, AppError) else "invalid_request"
            message = str(exc) if isinstance(exc, AppError) else "Daten konnten nicht verarbeitet werden. Eingaben prüfen und Ansicht neu laden."
            if app.key:
                message = message.replace(app.key, "[Key verborgen]")
            self.send_data(403 if code == "forbidden" else 404 if code == "not_found" else 400,
                           json.dumps({"error": message, "code": code}, ensure_ascii=False).encode("utf-8"))

    def send_audio(self, path):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        headers = {"Accept-Ranges": "bytes"}
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
            if not match or int(match[1]) >= size:
                self.send_data(416, b"", extra={"Content-Range": f"bytes */{size}"})
                return
            start, end = int(match[1]), min(int(match[2]) if match[2] else end, end)
            if end < start:
                self.send_data(416, b"")
                return
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        self.send_response(status)
        for key, value in {"Content-Type": "audio/mpeg", "Content-Length": str(end - start + 1),
                           "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", **headers}.items():
            self.send_header(key, value)
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(True)


def make_server(workspace, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), StudioHandler)
    server.studio = Studio(Path(workspace))
    server.daemon_threads = True
    return server


def serve(workspace, port=8765, open_browser=True):
    url = f"http://127.0.0.1:{port}"
    # A second double-click reopens this workspace instead of starting another server.
    try:
        with urlopen(url + "/api/bootstrap", timeout=2) as response:
            existing = json.load(response)
        if isinstance(existing, dict) and existing.get("app") == "podcast-studio" and existing.get("workspace") == str(Path(workspace).resolve()):
            if open_browser:
                webbrowser.open(url)
            print(f"Podcast Studio läuft bereits: {url}", flush=True)
            return
    except (OSError, ValueError):
        pass
    with project_lock(Path(workspace) / ".studio"):
        server = make_server(workspace, port)
        url = f"http://127.0.0.1:{server.server_port}"
        print(f"Podcast Studio: {url}\nDieses Fenster geöffnet lassen. Beenden mit Strg+C.", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        finally:
            with server.studio.mutex:
                app = server.studio
                if app.process is not None and app.process.poll() is None:
                    app.stop(app.process_root.name)
            server.server_close()
