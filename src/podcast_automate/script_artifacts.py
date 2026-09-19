"""Publish reviewed script artifacts separately from generation and review orchestration."""
from __future__ import annotations

import json
import re

from .models import EpisodeScript, host_labels
from .polishing import HOST_ROLES, POLISH_VERSION
from .research_gap_probe import statuses
from .script_advisories import advisories, established_terms
from .script_models import KnowledgeModel
from .storage import atomic_text, file_hash, read_yaml, write_json, write_yaml
from .teaching import TEACHING_VERSION


def script_metrics(script: EpisodeScript) -> dict:
    words = sum(len(re.findall(r"\b[\w’-]+\b", s.text)) for s in script.segments)
    pauses = sum(s.pause_after_ms for s in script.segments) / 60_000
    return {"words": words, "segments": len(script.segments),
            "estimated_minutes": round(words / 130 + pauses, 2),
            "conservative_minutes": round(words / 100 + pauses, 2),
            "duration_basis": "130 words/minute; conservative estimate 100; planned pauses included; not measured audio"}


def episode_continuity(work, entry):
    """The prerequisite rows the teaching stage recorded; absent on very old work folders."""
    try:
        return json.loads((work / "teaching" / entry.episode_id / "continuity.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def design_dismissals(work, entry):
    """Gaps the design review dropped; absent on work folders from before they were recorded."""
    try:
        return json.loads((work / "teaching" / entry.episode_id / "dismissed_gaps.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def retained_episode_reports(root, previous, current):
    """Report entries of earlier runs whose published text is still the one they judged.

    A run that publishes one episode must not erase what the reviews said about the others:
    the caveats and advisories under ``episodes.<ep>`` belong to the text in
    ``episodes/<ep>/script.yaml`` for as long as that text is on disk. An entry is kept when
    its ``script_sha256`` still matches that file and dropped otherwise. Every kept entry names
    the run that produced it; entries written before the tag existed inherit the report's run.
    """
    if not isinstance(previous, dict) or not isinstance(previous.get("episodes"), dict):
        return {}
    kept = {}
    for episode_id, entry in previous["episodes"].items():
        if episode_id in current or not isinstance(entry, dict) or not isinstance(episode_id, str):
            continue
        if not re.fullmatch(r"ep_[a-z0-9_]+", episode_id):
            continue
        script = root / "episodes" / episode_id / "script.yaml"
        if not script.is_file() or entry.get("script_sha256") != file_hash(script):
            continue
        kept[episode_id] = {**entry, "run_id": entry.get("run_id") or previous.get("run_id")}
    return kept


def unowned_gap_probes(work):
    """Probe rows with hits only in sources no episode uses; reported, never routed or blocking."""
    path = work / "gap_probes.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, ValueError):
        return []
    return statuses([row for row in rows if isinstance(row, dict) and row.get("status") == "hits_unowned"])


def render_script(script, labels):
    """Speaker labels are host names or the role, never the voice preset that will read it."""
    lines = [f"# {script.title}", ""]
    for chapter in script.chapters:
        lines.extend([f"## {chapter.title}", ""])
        for segment in script.segments:
            if segment.chapter_id == chapter.chapter_id:
                lines.extend([f"**{labels[segment.speaker_id]}:** {segment.text}", ""])
    return "\n".join(lines)


def publish_scripts(root, work, *, plan, entries, dossier, sources, config, teaching_for,
                    research_id, manifest, input_hash, text_generation, series_report):
    knowledge = KnowledgeModel.model_validate_json((work / "knowledge_model.json").read_text(encoding="utf-8"))
    knowledge.claims = dossier.findings
    outputs, episode_reports = [], {}
    for name, data in {"models/knowledge_model.yaml": knowledge.model_dump(),
                       "models/series_plan.yaml": plan.model_dump()}.items():
        write_yaml(root / name, data)
        outputs.append(root / name)
    overview = ["# Serienentwurf", "", plan.explanation_path, "", plan.scope_note, ""]
    for entry in plan.episodes:
        overview.extend([f"## {entry.episode_id}: {entry.title}", "", entry.central_question, "",
                         f"Geplant: etwa {entry.target_minutes:g} Minuten. " +
                         ("Skript in diesem Lauf geprüft." if entry in entries else "Bisher nur geplant."), ""])
    atomic_text(root / "research/series_outline.md", "\n".join(overview))
    outputs.append(root / "research/series_outline.md")
    source_map = {s.id: s for s in sources.sources}
    for entry in entries:
        script = EpisodeScript.model_validate_json((work / "reviewed" / f"{entry.episode_id}.json").read_text(encoding="utf-8"))
        folder = root / "episodes" / entry.episode_id
        write_yaml(folder / "episode_plan.yaml", entry.model_dump())
        write_yaml(folder / "script.yaml", script.model_dump())
        write_json(folder / "latest.json", {"run_id": manifest.run_id, "episode_ids": [entry.episode_id]})
        atomic_text(folder / "script.md", render_script(script, host_labels(config)))
        design = teaching_for(entry)
        write_yaml(folder / "teaching_plan.yaml", design.model_dump())
        atomic_text(folder / "teaching_plan.md", (work / "teaching" / entry.episode_id / "plan.md").read_text(encoding="utf-8"))
        notes = [f"# Quellen und Hinweise: {script.title}", "", entry.central_question, "",
                 "## Kapitel", "", *[f"- {c.title}" for c in script.chapters], "",
                 "## Grenzen und offene Vertiefungen", "", *[f"- {q}" for q in entry.deferred_questions], "",
                 "## Quellen", ""]
        used = {e.reference.split("#")[0] for f in dossier.findings if f.id in entry.finding_ids for e in f.evidence}
        # Two URLs of the same work are one source for a listener; the assessment's work_id
        # says which those are, and a normalised title covers the sources without one.
        works = {a.source_id: a.work_id for a in dossier.source_assessments if a.work_id}
        listed = set()
        for source_id in sorted(used):
            source = source_map[source_id]
            identity = works.get(source_id) or re.sub(r"\W+", " ", source.title).strip().casefold()
            if identity in listed:
                continue
            listed.add(identity)
            url = source.final_url or "../../" + source.raw_path
            notes.append(f"- [{source.title.replace('[', '').replace(']', '')}]({url})")
        notes.extend(["", "## Nachvollziehbarkeit", "", f"Recherchelauf: `{research_id}`. Skriptlauf: `{manifest.run_id}`.",
                      "Wissensreferenzen stehen im kanonischen Skript und führen über das Wissensmodell zu den Quellenabschnitten.", ""])
        atomic_text(folder / "show_notes.md", "\n".join(notes))
        metrics = script_metrics(script)
        teaching_report = json.loads((work / "reviews" / f"{entry.episode_id}_teaching.json").read_text(encoding="utf-8"))
        # ``reviews/<ep>.json`` is the review of exactly this text: a series repair rewrites both.
        episode_reports[entry.episode_id] = {**metrics, "script_sha256": file_hash(folder / "script.yaml"),
            "run_id": manifest.run_id,
            "structure_check": "passed", "model_review": json.loads((work / "reviews" / f"{entry.episode_id}.json").read_text(encoding="utf-8")),
            "teaching_review": teaching_report,
            "dialogue_polish": json.loads((work / "polishing" / entry.episode_id / "result.json").read_text(encoding="utf-8")),
            "dismissed_gaps": [*design_dismissals(work, entry), *teaching_report.get("dismissed_gaps", [])],
            "advisories": advisories(script, entry, metrics, language=config.language,
                                     terms=established_terms(episode_continuity(work, entry)))}
        outputs.extend(folder / name for name in ("episode_plan.yaml", "teaching_plan.yaml", "teaching_plan.md", "script.yaml", "script.md", "show_notes.md", "latest.json"))
    quality_path = root / "reports/script_quality.yaml"
    previous = read_yaml(quality_path) if quality_path.is_file() else {}
    # The top-level ``run_id`` is this run; each episode entry names the run that judged its text.
    episodes = {**retained_episode_reports(root, previous, set(episode_reports)), **episode_reports}
    report = {"schema_version": "1.0", "run_id": manifest.run_id, "research_run_id": research_id,
              "status": "script_checks_passed", "input_hash": input_hash, "episodes": episodes,
              "planned_episodes": [e.episode_id for e in plan.episodes], "human_reviewed": False,
              "all_planned_scripts_checked": len(entries) == len(plan.episodes),
              "complete_series_review": bool(series_report and series_report["status"] == "passed"),
              "series_review": series_report, "audio_generated": False,
              "gap_probes_unowned": unowned_gap_probes(work),
              "budget": json.loads((work / "budget.json").read_text()),
              "model_notes": "A model review can miss errors; durations are estimates until audio is measured."}
    report["teaching_version"] = TEACHING_VERSION
    report["text_generation"] = text_generation
    report["host_roles"] = HOST_ROLES
    report["polish_version"] = POLISH_VERSION
    report["foundation_research"] = [p.relative_to(root).as_posix()
        for p in sorted((work / "teaching").glob("ep_*/supplement*/receipt.json"))]
    write_yaml(quality_path, report)
    write_json(root / "episodes/latest.json", {"run_id": manifest.run_id, "episode_ids": list(episode_reports)})
    write_yaml(root / "episodes/audio_review.yaml", {
        "script_run_id": manifest.run_id, "status": "awaiting_user_script_review", "audio_approved": False,
        "scripts": {key: value["script_sha256"] for key, value in episode_reports.items()},
        "instruction": "Nutzer liest zuerst den aktuellen Skriptstand. Audio erst nach ausdrücklichem Auftrag erzeugen."})
    # Audio review is a mutable decision about this script hash, not an immutable script output.
    outputs.extend([root / "reports/script_quality.yaml", root / "episodes/latest.json"])
    write_json(work / "published_artifacts.json", {str(p.relative_to(root)): file_hash(p) for p in outputs})
    return [*outputs, work / "published_artifacts.json"]

