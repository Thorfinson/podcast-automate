"""A new version of a project (D-168): the same brief and the editor's inputs in a fresh project, so the current
pipeline makes the series again from the start while the old version stays as it was.

Carried over: ``project.yaml``, ``inputs/`` (attachments and provided works), every other local source of the brief,
``style_notes.md`` and the Studio's text, audio, execution, spoken-form and Jev choices; a German project without a
Jev choice gets today's default. Not carried over: runs, research, scripts, recordings, exports, the setup chat and
approvals, which stay the editor's decision for each version. The newest completed research run's sources come along
as a starting library: a research run of the new version offers them to its search (sources.load_library) instead of
downloading them again, and still searches for newer ones.

The new folder is built under a hidden name and renamed at the end, so the Studio never lists a half-copied project.
"""
from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from .errors import AppError
from .execution import default_jev_probe
from .models import RuntimeSettings, TopicBrief, now
from .provided_works import MANIFEST as WORKS_MANIFEST, inventory as provided_inventory
from .runner import manifest_path
from .storage import init_project, inside, read_optional_json, read_yaml, write_json

VERSION_FILE = "version.json"
# The Studio's choices a new version keeps; allowances and their log stay behind with the approvals.
STUDIO_CHOICES = ("text.json", "audio.json", "execution.json", "spoken_forms.json", "jev_probe.json")


def version_info(root: Path) -> dict:
    """The project's version record, or an empty dict for a first version made before versions existed."""
    info = read_optional_json(root / VERSION_FILE, {})
    return info if isinstance(info, dict) else {}


def version_number(root: Path) -> int:
    number = version_info(root).get("version")
    return number if type(number) is int and number > 0 else 1


def lineage(root: Path) -> str:
    """The first version's folder name: every later version of the same podcast shares it."""
    value = version_info(root).get("lineage")
    return value if isinstance(value, str) and value else root.name


def imported_library(root: Path) -> str | None:
    """The run id of the starting library this version brought along, while its documents are still here."""
    library = version_info(root).get("library")
    run_id = library.get("run_id") if isinstance(library, dict) else None
    if isinstance(run_id, str) and re.fullmatch(r"run_[\w-]{1,80}", run_id) and (
            root / "sources/processed" / run_id).is_dir():
        return run_id
    return None


def view(root: Path) -> dict:
    """What the Studio shows: the version number, the version it came from and its starting library."""
    info = version_info(root)
    library = info.get("library") if imported_library(root) else None
    return {"version": version_number(root), "from": info.get("from"),
            "library": {"run_id": library["run_id"], "documents": library.get("documents", 0),
                        "from_version": library.get("from_version")} if library else None}


def next_version(projects: Path, family: str) -> int:
    numbers = [version_number(path.parent) for path in projects.glob("*/project.yaml")
               if lineage(path.parent) == family]
    return max(numbers, default=1) + 1


def folder_name(topic: str, version: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40] or "podcast"
    return f"{slug}-v{version}-{uuid.uuid4().hex[:6]}"


def library_run(root: Path) -> str | None:
    """The source project's sources a new version starts from: its newest completed research run, else the library
    it brought along itself. A run that did not complete may still be writing; it is never copied."""
    from .research import latest_research_run
    run_id = latest_research_run(root)
    if run_id is None or run_id == imported_library(root):
        return run_id
    return run_id if read_yaml(manifest_path(root, run_id)).get("status") == "completed" else imported_library(root)


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def create_version(source: Path, *, target: Path | None = None, runtime: RuntimeSettings | None = None) -> Path:
    """Make the next version of the project at ``source`` beside it (or at ``target``) and return its folder.
    ``runtime`` replaces the brief's runtime settings; the Studio passes its own, as for a new project."""
    source = source.resolve()
    brief = TopicBrief.model_validate(read_yaml(source / "project.yaml"))
    if runtime is not None:
        brief.runtime = runtime
    family = lineage(source)
    number = next_version(source.parent, family)
    target = (target or source.parent / folder_name(brief.topic, number)).resolve()
    if target.exists():
        raise AppError(f"Der Zielordner existiert bereits: {target}", code="project_exists", status="blocked")
    building = target.parent / f".building-{target.name}"
    try:
        init_project(building, brief)
        if (source / "inputs").is_dir():
            shutil.copytree(source / "inputs", building / "inputs", ignore=shutil.ignore_patterns("*.pending"))
        if (building / WORKS_MANIFEST).is_file():
            # A provided work was named for questions of the old research; the new research plans its own.
            write_json(building / WORKS_MANIFEST,
                       [{**row, "tasks": [], "run_id": None} for row in provided_inventory(building)])
        # Every local source lies inside the project (research.run_research refuses others); most are in inputs/.
        for value in brief.local_sources:
            path, copy = inside(source, value), inside(building, value)
            if path.is_file() and not copy.exists():
                copy_file(path, copy)
        if (source / "style_notes.md").is_file():
            copy_file(source / "style_notes.md", building / "style_notes.md")
        for name in STUDIO_CHOICES:
            if (source / "studio" / name).is_file():
                copy_file(source / "studio" / name, building / "studio" / name)
        if not (building / "studio/jev_probe.json").exists():
            default_jev_probe(building, brief.language)
        library = None
        run_id = library_run(source)
        if run_id is not None:
            for kind in ("processed", "raw"):
                if (source / "sources" / kind / run_id).is_dir():
                    shutil.copytree(source / "sources" / kind / run_id, building / "sources" / kind / run_id)
            # A library carried on from an earlier version still names the version whose research found it.
            carried = run_id == imported_library(source)
            library = {"run_id": run_id,
                       "from_version": version_info(source)["library"].get("from_version") if carried
                       else version_number(source),
                       "documents": len(list((building / "sources/processed" / run_id).glob("src_*.json")))}
        write_json(building / VERSION_FILE, {"version": number, "lineage": family, "from": source.name,
                                             "created_at": now(), "library": library})
        building.rename(target)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    return target
