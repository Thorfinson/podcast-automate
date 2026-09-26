"""Render an approved, reviewed episode with resumable chapter batches."""
from __future__ import annotations

import json
import uuid
import wave
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from .audio import applied_pause, assemble, run_tts, worker_path
from .errors import AppError
from .models import EpisodeScript, RunManifest, StageRecord, host_labels
from .qwen_worker import spoken_settings
from .runner import execute_stages, manifest_path, outputs_valid
from .script_models import SeriesPlan
from .series_review import load_series_review, require_passing_series, reviewed_scripts
from .scripting import load_research, validate_script
from .speech import (AudioChoice, SPEECH_VERSION, audio_generation_record, check_gemini_rows,
                     run_gemini_tts, same_audio_generation)
from .spoken_forms import SpokenForms, load_forms, report as pronunciation_report, spoken_text
from .storage import (atomic_text, digest, file_hash, file_lock, inside, load_project, project_hash,
                      project_lock, read_yaml, write_json, write_yaml)


def reviewed_episode(root, config, episode):
    pointer = root / "episodes" / episode / "latest.json"
    per_episode = pointer.exists()
    latest = json.loads((pointer if per_episode else root / "episodes/latest.json").read_text(encoding="utf-8"))
    work = manifest_path(root, latest["run_id"]).parent
    manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
    inputs = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
    required_stages = ["planning", "writing", "review"]
    if "teaching" in manifest.stages:
        required_stages.append("teaching")
    if "polishing" in manifest.stages or inputs.get("polish_version"):
        required_stages.append("polishing")
    if manifest.kind != "script" or manifest.status != "completed" or any(
        name not in manifest.stages or not outputs_valid(root, manifest.stages[name]) for name in required_stages
    ):
        raise AppError("Ein vollständig geprüftes Skript wird benötigt.", code="invalid_script", status="blocked")
    plan = SeriesPlan.model_validate_json((work / "series_plan.json").read_text(encoding="utf-8"))
    if inputs.get("series_review_version"):
        request = json.loads((work / "script_request.json").read_text(encoding="utf-8"))
        require_passing_series(load_series_review(work, plan, reviewed_scripts(work, plan, request.get("episode")),
                                                  manifest.input_hash))
    entry = next((e for e in plan.episodes if e.episode_id == episode), None)
    if entry is None or episode not in latest["episode_ids"]:
        raise AppError("Für diese Folge fehlt ein geprüftes Skript.", code="unknown_episode", status="blocked")
    folder = root / "episodes" / entry.episode_id
    required_outputs = [f"episodes/{episode}/script.yaml", f"episodes/{episode}/script.md",
                        f"episodes/{episode}/episode_plan.yaml"]
    if not per_episode:
        required_outputs.append("reports/script_quality.yaml")
    for relative in required_outputs:
        if file_hash(root / relative) != manifest.stages["publish"].outputs.get(relative):
            raise AppError("Skript oder Leseansicht geändert; zuerst den neuen Text prüfen.",
                           code="script_edited", status="blocked")
    script = EpisodeScript.model_validate(read_yaml(folder / "script.yaml"))
    reviewed = EpisodeScript.model_validate_json((work / "reviewed" / f"{episode}.json").read_text(encoding="utf-8"))
    research_id, *_ = load_research(root, config)
    if inputs["research_run"] != research_id or script != reviewed or validate_script(script, entry):
        raise AppError("Skript und geprüfte Recherche passen nicht zusammen.", code="invalid_script", status="blocked")
    return manifest, script, file_hash(folder / "script.yaml")


def migrate_review_ownership(root, script_manifest):
    """Older script runs incorrectly hashed the mutable audio decision as a script output."""
    outputs = script_manifest.stages["publish"].outputs
    if "episodes/audio_review.yaml" not in outputs:
        return
    work = manifest_path(root, script_manifest.run_id).parent
    write_json(work / "audio_review_ownership_migration.json", {
        "reason": "Audio decisions change after script review; the script itself remains immutable.",
        "previous_publish_outputs": dict(outputs)})
    outputs.pop("episodes/audio_review.yaml")
    inventory = work / "published_artifacts.json"
    data = json.loads(inventory.read_text(encoding="utf-8"))
    data.pop("episodes/audio_review.yaml", None)
    data.pop("episodes\\audio_review.yaml", None)
    write_json(inventory, data)
    outputs[inventory.relative_to(root).as_posix()] = file_hash(inventory)
    write_yaml(work / "run_manifest.yaml", script_manifest.model_dump(mode="json"))


def select_script(script, segments, *, title=None, episode_id=None):
    chapters = {s.chapter_id for s in segments}
    return EpisodeScript(episode_id=episode_id or script.episode_id, title=title or script.title,
        purpose=script.purpose, chapters=[c for c in script.chapters if c.chapter_id in chapters],
        segments=segments)


def segment_durations(script, paths, pauses):
    """Seconds each segment occupies in the montage: its audio plus the pause assembly will apply.

    The same ``applied_pause`` decides both numbers, so a part that fits here also fits in
    ``assemble``. Sizing parts with the planned pause alone let a near-cap episode pass
    partitioning and fail with ``duration_exceeded`` after synthesis had been paid for.
    """
    durations = []
    for index, wav in enumerate(paths):
        with wave.open(str(wav), "rb") as stream:
            seconds = stream.getnframes() / stream.getframerate()
        durations.append(seconds + applied_pause(script, index, pauses)[0] / 1000)
    return durations


def partition_audio(script, durations, max_seconds):
    """Keep chapters together; choose the fewest, most evenly sized consecutive parts."""
    limit = max_seconds - 0.2  # MP3 encoder delay can add a few milliseconds.
    chunks = []
    for chapter in script.chapters:
        indices = [i for i, s in enumerate(script.segments) if s.chapter_id == chapter.chapter_id]
        if sum(durations[i] for i in indices) <= limit:
            chunks.append(indices)
        else:
            chunks.extend([[i] for i in indices])
    if any(sum(durations[i] for i in chunk) > limit for chunk in chunks):
        raise AppError("Ein einzelnes Segment überschreitet die Dateilänge.", code="duration_exceeded", status="blocked")
    best = [(0, 0.0, [])] + [None] * len(chunks)
    for end in range(1, len(chunks) + 1):
        duration = 0.0
        for start in range(end - 1, -1, -1):
            duration += sum(durations[i] for i in chunks[start])
            if duration > limit:
                break
            count, cost, groups = best[start]
            candidate = (count + 1, cost + duration * duration,
                         groups + [[i for chunk in chunks[start:end] for i in chunk]])
            if best[end] is None or candidate[:2] < best[end][:2]:
                best[end] = candidate
    return best[-1][2]


def timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:d}:{total % 3600 // 60:02d}:{total % 60:02d}" if total >= 3600 \
        else f"{total // 60:d}:{total % 60:02d}"


def episode_chapters(root, destination, parts):
    """Chapter titles with their timestamp inside their own part, as measured at assembly."""
    rows = []
    for part in parts["parts"]:
        folder = (root / part["audio"]).parent
        try:
            measured = json.loads((folder / "chapters.json").read_text(encoding="utf-8"))["chapters"]
        except (OSError, ValueError, KeyError):
            continue
        for chapter in measured:
            rows.append({"part": part["part"], "chapter_id": chapter["chapter_id"], "title": chapter["title"],
                         "start_seconds": chapter["start_seconds"], "timestamp": timestamp(chapter["start_seconds"])})
    return rows


def render_export_notes(script, chapters, choice, overrides, labels=None):
    """Show notes the script stage cannot write: they need measured audio to carry timestamps."""
    labels = labels or host_labels(None)
    lines = [f"# {script.title}", "",
             f"Hörfassung mit {labels['host_a']} und {labels['host_b']}.",
             f"Stimmen: {choice.voices['host_a']} ({labels['host_a']}) und {choice.voices['host_b']} ({labels['host_b']}).", "",
             "## Kapitel", ""]
    if chapters:
        lines.extend(f"- {row['timestamp']} {row['title']}"
                     + (f" (Teil {row['part']})" if row["part"] > 1 else "") for row in chapters)
    else:
        lines.append("- Zeitmarken liegen für diese Fassung nicht vor.")
    if overrides:
        lines.extend(["", "## Aussprache-Hinweise", "",
                      "Für diese Abschnitte wurde eine abweichende Sprechform vertont; der Text bleibt unverändert.", ""])
        lines.extend(f"- `{sid}`: {spoken}" for sid, spoken in sorted(overrides.items()))
    lines.extend(["", "Zeitmarken stammen aus der gemessenen Montage, nicht aus einer Schätzung.", ""])
    return "\n".join(lines)


def render_listening_sheet(script, chapters):
    """A sheet to fill in while listening; nothing here is filled in automatically."""
    lines = [f"# Hörprüfung: {script.title}", "",
             "Beim Hören ausfüllen. Diese Spalten kann keine Prüfung im Programm ersetzen.", "",
             "| Zeit | Kapitel | Unklar | Aufmerksamkeit verloren | Aussprache |",
             "| --- | --- | --- | --- | --- |"]
    rows = chapters or [{"timestamp": "0:00", "title": chapter.title} for chapter in script.chapters]
    lines.extend(f"| {row['timestamp']} | {row['title']} | | | |" for row in rows)
    lines.extend(["", "Nach dem Hören im Studio ankreuzen, dass die Hörprüfung durchgeführt wurde.", ""])
    return "\n".join(lines)


def check_rows(root, script, report, config, *, table=None, overrides=None):
    rows = report.get("segments", [])
    table = table if table is not None else SpokenForms()
    if [row.get("segment_id") for row in rows] != [s.segment_id for s in script.segments]:
        raise AppError("TTS-Bericht passt nicht zum Skript.", code="invalid_audio")
    for row, segment in zip(rows, script.segments, strict=True):
        path = inside(root / "cache/audio", row["path"])
        settings = row.get("settings", {})
        # A row without a spoken form carries no spoken_text, exactly as rows did before spoken
        # forms existed; a row with one must carry the form the table produces today.
        expected = {"voice": config.voice_profile[segment.speaker_id], "text": segment.text,
                    "language": {"de-DE": "German", "en-US": "English"}[config.language],
                    "revision": config.runtime.tts_revision, "seed": config.runtime.seed,
                    "attention": config.runtime.tts_attention, "device": config.runtime.tts_device,
                    "model": config.runtime.tts_model, "spoken_text": None,
                    **spoken_settings(segment.text, spoken_text(segment, table, overrides))}
        if not path.is_file() or file_hash(path) != row.get("sha256") or any(
                settings.get(key) != value for key, value in expected.items()):
            raise AppError("Audio, Stimme oder Text stimmt nicht mit dem Auftrag überein.", code="invalid_audio")
    return [inside(root / "cache/audio", row["path"]) for row in rows]


def saved_approval(root, episode, script_hash, audio_generation):
    """Whether the stored audio decision already approves this script with this audio choice.

    The one rule for the pipeline and the Studio: the decision says ``audio_approved``, names
    this episode's current script hash, and was made for the same provider and voices
    (``same_audio_generation`` reads a decision without a pause policy as today's defaults).
    A spoken form is not an editorial decision, so a re-render with a saved approval starts
    without asking again.
    """
    folder = root / "episodes" / episode
    decision_path = folder / "audio_review.yaml"
    if not decision_path.is_file():
        decision_path = root / "episodes/audio_review.yaml"
    if not decision_path.is_file():
        return False
    decision = read_yaml(decision_path)
    return (decision.get("audio_approved") is True
            and (decision.get("scripts") or {}).get(episode) == script_hash
            and same_audio_generation(decision.get("audio_generation"), audio_generation))


def run_episode_audio(root: Path, *, episode=None, approve_audio=False, approval_note="",
                      resume=False, run_id=None, expected_script_hash=None, expected_readable_hash=None,
                      expected_config_hash=None, audio_choice=None, api_key=None, expected_audio_hash=None,
                      parallel_remote=False):
    root = root.resolve()
    with project_lock(root, shared=parallel_remote), ExitStack() as locks:
        config = load_project(root)
        if resume:
            path = manifest_path(root, run_id)
            manifest = RunManifest.model_validate(read_yaml(path))
            request = json.loads((path.parent / "inputs.json").read_text(encoding="utf-8"))
            episode = request["episode_id"]
            saved_choice = request.get("audio_generation")
            if audio_choice is not None and not same_audio_generation(
                    saved_choice, AudioChoice.model_validate(audio_choice).model_dump()):
                raise AppError("Audioanbieter oder Stimmen geändert. Fortsetzen nutzt die gespeicherte Auswahl.", code="inputs_changed", status="blocked")
            audio_choice = saved_choice
        choice = AudioChoice.model_validate(audio_choice) if audio_choice is not None else AudioChoice(voices=config.voice_profile)
        if parallel_remote and not choice.remote:
            raise AppError("Lokales Qwen wird einzeln ausgeführt.", code="invalid_parallel_audio", status="blocked")
        episode_folder = inside(root / "episodes", episode)
        locks.enter_context(file_lock(episode_folder / ".audio.lock"))

        def save_decision(decision):
            # Each episode owns its decision. The old series-level file is only
            # a compatibility pointer to the most recently updated decision.
            # Per-segment spoken forms are the operator's, not this run's, so they survive.
            previous = read_yaml(episode_folder / "audio_review.yaml") if (episode_folder / "audio_review.yaml").is_file() else {}
            # A new rendering invalidates an earlier listening review, so its note goes with it.
            carry = ("spoken_overrides",) if "human_listening_reviewed" in decision else (
                "spoken_overrides", "human_listening_reviewed", "listening_note")
            kept = {key: previous[key] for key in carry if key in previous and key not in decision}
            write_yaml(episode_folder / "audio_review.yaml", {**decision, **kept})
            write_yaml(root / "episodes/audio_review.yaml", {**decision, **kept})

        if expected_audio_hash is not None and expected_audio_hash != digest(choice.model_dump()):
            raise AppError("Audioauswahl seit der Freigabe geändert.", code="inputs_changed", status="blocked")
        script_manifest, script, script_hash = reviewed_episode(root, config, episode)
        config_hash = project_hash(config)
        if ((expected_script_hash is not None and expected_script_hash != script_hash) or
                (expected_readable_hash is not None and expected_readable_hash != file_hash(root / "episodes" / episode / "script.md")) or
                (expected_config_hash is not None and expected_config_hash != config_hash)):
            raise AppError("Skript oder Stimmen seit der Freigabe geändert. Bitte erneut prüfen.", code="script_edited", status="blocked")
        table = load_forms(root)
        decision_now = read_yaml(episode_folder / "audio_review.yaml") if (episode_folder / "audio_review.yaml").is_file() else {}
        overrides = {k: v for k, v in (decision_now.get("spoken_overrides") or {}).items()
                     if isinstance(k, str) and isinstance(v, str) and v.strip()}
        inputs = {"version": "episode_audio.v1", "project": config_hash, "episode_id": episode,
                  "script_run_id": script_manifest.run_id, "script_sha256": script_hash,
                  "script": script.model_dump(), "worker_sha256": file_hash(worker_path())}
        if table.entries or overrides:
            # Only a project that uses spoken forms binds them, so existing runs keep their hash.
            inputs["spoken_forms"] = {"table": table.model_dump(), "overrides": overrides}
        if audio_choice is not None:
            inputs["audio_generation"] = audio_generation_record(choice)
        if choice.remote:
            inputs["speech_model"] = choice.model
            inputs["speech_version"] = SPEECH_VERSION
            inputs["worker_sha256"] = file_hash(Path(__file__).with_name("speech.py"))
        audio_config = config.model_copy(update={"voice_profile": choice.voices})

        def validate_audio(batch, report):
            return (check_gemini_rows(root, batch, report, choice, config.language, table=table, overrides=overrides)
                    if choice.remote
                    else check_rows(root, batch, report, audio_config, table=table, overrides=overrides))
        if resume:
            if manifest.kind != "episode_audio" or manifest.input_hash != digest(inputs):
                raise AppError("Audioeingaben geändert; einen neuen Audiolauf starten.", code="inputs_changed", status="blocked")
            if approve_audio and not manifest.audio_approved:
                # Keep the original receipt and any completed synthesis hashes intact.
                renewed_at = datetime.now(timezone.utc)
                write_json(path.parent / "resume_approvals" / f"{renewed_at.strftime('%Y%m%d_%H%M%S_%f')}.json", {
                    "approved_at": renewed_at.isoformat(), "audio_approved": True,
                    "script_sha256": script_hash, "script_run_id": script_manifest.run_id,
                    "input_hash": manifest.input_hash, "voices": choice.voices,
                    "authorization": approval_note or "Explicit --approve-audio on resume."})
                save_decision({
                    "script_run_id": script_manifest.run_id, "audio_run_id": manifest.run_id,
                    "status": "audio_generation_approved", "audio_approved": True,
                    "scripts": {episode: script_hash},
                    "instruction": "Vertonung auf ausdrücklichen Nutzerwunsch fortsetzen; anschließend Hörprüfung."})
                manifest.audio_approved = True
        else:
            identifier = "run_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
            path = manifest_path(root, identifier)
            approved = approve_audio or saved_approval(root, episode, script_hash, inputs.get("audio_generation"))
            if not approved:
                raise AppError("Audio benötigt die Freigabe des aktuellen Skripts mit --approve-audio.",
                               code="audio_approval_required", status="blocked")
            manifest = RunManifest(run_id=identifier, kind="episode_audio", project_hash=config_hash,
                input_hash=digest(inputs), audio_approved=True,
                stages={name: StageRecord() for name in ("synthesis", "assembly", "publish")})
        if not manifest.audio_approved:
            raise AppError("Audio-Freigabe fehlt.", code="audio_approval_required", status="blocked")
        work = path.parent
        if not resume:
            with file_lock(root / ".pla.audio-metadata.lock", timeout=30):
                # Re-read under the short metadata lock: another episode may
                # already have migrated their shared script manifest.
                owner = RunManifest.model_validate(read_yaml(manifest_path(root, script_manifest.run_id)))
                migrate_review_ownership(root, owner)
            write_json(work / "inputs.json", inputs)
            write_json(work / "approved_script.json", script.model_dump())
            write_yaml(work / "project_snapshot.yaml", config.model_dump(mode="json"))
            write_json(work / "approval.json", {"script_sha256": script_hash,
                "script_run_id": script_manifest.run_id, "audio_approved": True,
                "authorization": approval_note or "Explicit --approve-audio or matching saved approval.",
                "voices": choice.voices, "audio_generation": inputs.get("audio_generation"),
                "editorial_quality_accepted": False})
            save_decision({
                "script_run_id": script_manifest.run_id, "audio_run_id": manifest.run_id,
                "status": "audio_generation_approved", "audio_approved": True,
                "audio_generation": inputs.get("audio_generation"),
                "scripts": {episode: script_hash}, "instruction": "Aktuellen Text vertonen; anschließend Hörprüfung."})
        write_json(root / "runs/latest.json", {"run_id": manifest.run_id})

        def synthesis():
            outputs, rows = [], []
            for number, chapter in enumerate(script.chapters, 1):
                batch = select_script(script, [s for s in script.segments if s.chapter_id == chapter.chapter_id])
                folder = work / "synthesis" / chapter.chapter_id
                report_file, stamp_file = folder / "tts_report.json", folder / "checkpoint.json"
                signature = digest({"inputs": manifest.input_hash, "batch": batch.model_dump()})
                write_json(work / "progress.json", {"status": "synthesis", "chapter": number,
                    "chapters": len(script.chapters), "chapter_title": chapter.title,
                    "completed_segments": len(rows), "total_segments": len(script.segments),
                    "chapter_progress": (folder / "tts_progress.json").relative_to(root).as_posix()})
                report = None
                if stamp_file.exists() and report_file.exists():
                    try:
                        stamp = json.loads(stamp_file.read_text(encoding="utf-8"))
                        if stamp == {"input_hash": signature, "sha256": file_hash(report_file)}:
                            report = json.loads(report_file.read_text(encoding="utf-8"))
                            validate_audio(batch, report)
                    except (OSError, ValueError, KeyError, AppError):
                        report = None
                if report is None:
                    if choice.remote:
                        if parallel_remote:
                            from .parallel_speech import run_parallel_gemini_tts
                            run_parallel_gemini_tts(config, batch, root, folder, choice, api_key,
                                                    table=table, overrides=overrides)
                        else:
                            run_gemini_tts(config, batch, root, folder, choice, api_key,
                                           table=table, overrides=overrides)
                    else:
                        run_tts(audio_config, batch, root, folder, table=table, overrides=overrides)
                    report = json.loads(report_file.read_text(encoding="utf-8"))
                    validate_audio(batch, report)
                    write_json(stamp_file, {"input_hash": signature, "sha256": file_hash(report_file)})
                rows.extend(report["segments"])
                outputs.extend([report_file, stamp_file])
            report = {"segments": rows, "script_sha256": script_hash, "voices": choice.voices}
            paths = validate_audio(script, report)
            write_json(work / "tts_report.json", report)
            return [*outputs, *paths, work / "tts_report.json", work / "inputs.json",
                    work / "approved_script.json", work / "approval.json", work / "project_snapshot.yaml"]

        def assembly():
            if file_hash(root / "episodes" / episode / "script.yaml") != script_hash:
                raise AppError("Skript während der Vertonung geändert; Montage wartet auf Textprüfung.", code="script_edited", status="blocked")
            report = json.loads((work / "tts_report.json").read_text(encoding="utf-8"))
            paths = validate_audio(script, report)
            groups = partition_audio(script, segment_durations(script, paths, choice.pauses),
                                     config.max_episode_minutes * 60)
            destination = root / "exports" / episode / manifest.run_id
            outputs, parts = [], []
            for number, indices in enumerate(groups, 1):
                part = select_script(script, [script.segments[i] for i in indices],
                    title=script.title if len(groups) == 1 else f"{script.title} – Teil {number} von {len(groups)}")
                folder = destination if len(groups) == 1 else destination / f"part_{number:02d}"

                def assembly_progress(step, done=0, total=0, number=number):
                    # The Studio shows the montage step instead of a finished synthesis counter.
                    write_json(work / "progress.json", {"status": "assembly", "step": step, "part": number,
                        "parts": len(groups), "completed_segments": done, "total_segments": total})
                outputs.extend(assemble(part, [paths[i] for i in indices], folder,
                    max_seconds=config.max_episode_minutes * 60, language=config.language,
                    labels=host_labels(config), pauses=choice.pauses, progress=assembly_progress))
                audio_report = json.loads((folder / "audio_report.json").read_text(encoding="utf-8"))
                parts.append({"part": number, "audio": (folder / "audio.mp3").relative_to(root).as_posix(),
                              "duration_seconds": audio_report["duration_seconds"],
                              "segment_ids": [s.segment_id for s in part.segments]})
            write_json(work / "parts.json", {"parts": parts, "total_seconds": sum(p["duration_seconds"] for p in parts)})
            return [*outputs, work / "parts.json"]

        def publish():
            parts = json.loads((work / "parts.json").read_text(encoding="utf-8"))
            destination = root / "exports" / episode / manifest.run_id
            labels = host_labels(config)
            lines = [f"# {script.title}", "",
                     "Erste Audiofassung zur Hörprüfung.",
                     f"Stimmen: {choice.voices['host_a']} ({labels['host_a']}) und {choice.voices['host_b']} ({labels['host_b']}).", ""]
            playlist = ["#EXTM3U"]
            for part in parts["parts"]:
                audio = root / part["audio"]
                relative = audio.relative_to(destination).as_posix()
                lines.append(f"- [Teil {part['part']} anhören]({relative}) – {part['duration_seconds']/60:.2f} Minuten")
                playlist.append(relative)
            if overrides:
                lines.extend(["", "## Abweichende Sprechformen", ""])
                lines.extend(f"- `{sid}`: {spoken}" for sid, spoken in sorted(overrides.items()))
            lines.extend(["", "Der gesamte freigegebene Text ist in Skriptreihenfolge enthalten.",
                          "Die technische Montage ersetzt keine Hörprüfung von Aussprache und Natürlichkeit.", ""])
            atomic_text(destination / "README.md", "\n".join(lines))
            atomic_text(destination / "playlist.m3u", "\n".join(playlist) + "\n")
            chapters = episode_chapters(root, destination, parts)
            atomic_text(destination / "show_notes.md", render_export_notes(script, chapters, choice, overrides, labels))
            atomic_text(destination / "listening_sheet.md", render_listening_sheet(script, chapters))
            pronunciation = pronunciation_report(script, table, language=config.language, overrides=overrides)
            write_json(work / "pronunciation.json", pronunciation)
            report = {"run_id": manifest.run_id, "script_run_id": script_manifest.run_id,
                "script_sha256": script_hash, "voices": choice.voices, "audio_generation": choice.model_dump(),
                "audio_generated": True, "audio_approved": True, "human_listening_reviewed": False,
                "pronunciation": pronunciation, "spoken_overrides": overrides, "chapters": chapters,
                "status": "awaiting_listening_review", **parts}
            write_json(root / "reports" / f"{episode}_audio.json", report)
            write_json(root / "episodes" / episode / "audio_latest.json", report)
            save_decision({"script_run_id": script_manifest.run_id,
                "audio_run_id": manifest.run_id, "status": "awaiting_listening_review", "audio_approved": True,
                "audio_generation": inputs.get("audio_generation"),
                "scripts": {episode: script_hash}, "human_listening_reviewed": False})
            write_json(work / "progress.json", {"status": "completed", "segments": len(script.segments)})
            return [destination / "README.md", destination / "playlist.m3u",
                    destination / "show_notes.md", destination / "listening_sheet.md",
                    work / "pronunciation.json",
                    root / "reports" / f"{episode}_audio.json", root / "episodes" / episode / "audio_latest.json"]

        return execute_stages(root, manifest, path, {"synthesis": synthesis, "assembly": assembly, "publish": publish})
