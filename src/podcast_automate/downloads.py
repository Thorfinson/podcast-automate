"""Readable download names and a bounded-memory bundle of published recordings."""
import json
import re
import tempfile
import unicodedata
import zipfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .errors import AppError
from .models import EpisodeScript
from .runner import manifest_path
from .storage import inside, load_project, project_lock, read_yaml


def name_part(value: str, max_bytes: int = 64) -> str:
    value = unicodedata.normalize("NFC", value)
    value = "".join(c for c in value if not unicodedata.category(c).startswith("C"))
    value = re.sub(r'[<>:"/\\|?*]', " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .") or "Podcast"
    if len(value.encode("utf-8")) > max_bytes:
        shortened = value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
        # Prefer complete words. No ellipsis or trailing dots in file names.
        if " " in shortened and value[len(shortened):len(shortened) + 1] not in {"", " "}:
            shortened = shortened.rsplit(" ", 1)[0]
        value = shortened.rstrip(" .-") or "Podcast"
    return value


def episode_filename(topic, episode_id, title, index, part, parts, *, in_archive=False):
    match = re.fullmatch(r"ep_(\d+)", episode_id)
    number = int(match[1]) if match else index
    suffix = f" - Teil {part:02d} von {parts:02d}" if parts > 1 else ""
    prefix = "" if in_archive else f"{name_part(topic, 32)} - "
    title_budget = 76 - len(suffix.encode("utf-8")) if in_archive else 56
    return f"{prefix}Folge {number:02d} - {name_part(title, title_budget)}{suffix}.mp3"


def disposition(filename):
    # RFC 6266 / RFC 5987: keep an ASCII fallback and the complete UTF-8 name.
    fallback = unicodedata.normalize("NFKD", filename.replace("ß", "ss")).encode("ascii", "ignore").decode()
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename, safe="")}'


@dataclass(frozen=True)
class Recording:
    relative: str
    path: Path
    filename: str
    episode_id: str
    archive_filename: str


@dataclass
class PodcastDownload:
    topic: str
    recordings: list[Recording]
    episode_count: int

    @property
    def finished_episodes(self):
        return len({r.episode_id for r in self.recordings})

    @property
    def filename(self):
        scope = "Alle Folgen" if self.finished_episodes == self.episode_count else f"{self.finished_episodes} von {self.episode_count} Folgen"
        # Explorer extracts into a folder with the ZIP's base name. Budget for
        # that folder AND the member name, not just each name independently.
        return f"{name_part(self.topic, 44)} - {scope}.zip"


def podcast_download(root: Path, *, selected_path=None) -> PodcastDownload:
    topic = load_project(root).topic
    recordings, episodes = [], set()
    pointer = root / "studio/outline.json"
    if pointer.is_file():
        run_id = json.loads(pointer.read_text(encoding="utf-8"))["run_id"]
        plan_path = manifest_path(root, run_id).parent / "series_plan.json"
        if plan_path.is_file():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            episodes.update(e["episode_id"] for e in plan.get("episodes", []))
    for index, folder in enumerate(sorted((root / "episodes").glob("ep_*")), 1):
        if not (folder / "script.yaml").is_file():
            continue
        script = EpisodeScript.model_validate(read_yaml(folder / "script.yaml"))
        episodes.add(script.episode_id)
        report = folder / "audio_latest.json"
        if not report.is_file():
            continue
        parts = json.loads(report.read_text(encoding="utf-8")).get("parts", [])
        if selected_path is not None and all(row["audio"] != selected_path for row in parts):
            continue
        for part, row in enumerate(parts, 1):
            relative = row["audio"]
            path = inside(root, relative)
            if (path.suffix.lower() != ".mp3" or
                    not path.is_relative_to((root / "exports" / folder.name).resolve()) or not path.is_file()):
                raise AppError(f"Die Aufnahme für „{script.title}“ ist nicht vollständig verfügbar. "
                               "Audioexport dieser Folge prüfen.", code="missing_audio")
            filename = episode_filename(topic, script.episode_id, script.title, index, part, len(parts))
            archive_filename = episode_filename(topic, script.episode_id, script.title, index, part,
                                                 len(parts), in_archive=True)
            used = {r.archive_filename.casefold() for r in recordings}
            candidate, suffix = archive_filename, 2
            while candidate.casefold() in used:
                candidate = f"{archive_filename[:-4]} - Aufnahme {suffix}.mp3"
                suffix += 1
            recordings.append(Recording(relative, path, filename, script.episode_id, candidate))
    return PodcastDownload(topic, recordings, len(episodes))


@contextmanager
def podcast_zip(root: Path, *, locked: bool = True):
    # Take a consistent selection of published exports. Remote audio jobs can
    # continue while shared locking prevents deletion of the source project.
    # A caller that knows the lock holder never writes exports passes ``locked=False``.
    with project_lock(root, shared=True) if locked else nullcontext():
        download = podcast_download(root)
        if not download.recordings:
            raise AppError("Noch keine fertigen Folgen zum Herunterladen vorhanden.", code="missing_audio")
        with tempfile.TemporaryDirectory(prefix="podcast-download-") as directory:
            path = Path(directory) / "podcast.zip"
            # MP3 is already compressed. Copy it unchanged without loading the
            # series into memory, transcoding, or repeating any model calls.
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for recording in download.recordings:
                    archive.write(recording.path, recording.archive_filename)
            yield path, download.filename
