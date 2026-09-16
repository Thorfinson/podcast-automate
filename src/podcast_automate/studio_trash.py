"""Recoverable project moves, including directories held open by Windows apps."""
from pathlib import Path

from .errors import AppError


def plain_directory(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink() and not path.is_junction()


def has_artifacts(root: Path) -> bool:
    """Empty directory shells can remain when Windows holds a directory open."""
    for path in root.iterdir():
        if path.name == ".pla.lock":
            continue
        if not plain_directory(path) or any_files(path):
            return True
    return False


def any_files(root: Path) -> bool:
    return any(not plain_directory(path) or any_files(path) for path in root.iterdir())


def move_contents(source: Path, destination: Path, *, exclude=()) -> None:
    moved, created, emptied = [], [], []

    def move(path, target):
        if not target.exists():
            try:
                path.rename(target)
                moved.append((path, target))
                return
            except PermissionError:
                # Windows directory handles (e.g. watchers) can deny the rename
                # while still allowing all contained files to be moved safely.
                if not plain_directory(path):
                    raise
        elif not (plain_directory(path) and plain_directory(target)):
            raise FileExistsError(f"Ziel bereits vorhanden: {target.name}")
        if not target.exists():
            target.mkdir()
            created.append(target)
        for child in path.iterdir():
            move(child, target / child.name)
        emptied.append(path)

    try:
        for path in source.iterdir():
            if path.name not in exclude:
                move(path, destination / path.name)
    except OSError as exc:
        try:
            for original, target in reversed(moved):
                target.rename(original)
            for path in reversed(created):
                path.rmdir()
        except OSError as rollback_error:
            raise AppError("Verschieben konnte nicht vollständig zurückgesetzt werden. "
                           "Die Daten im Projektordner und im lokalen Papierkorb bleiben erhalten.",
                           code="project_move_incomplete") from rollback_error
        name = Path(exc.filename).name if exc.filename else "eine Projektdatei"
        raise AppError(f"„{name}“ konnte nicht verschoben werden. Möglicherweise hält ein anderes "
                       "Programm die Datei geöffnet. Schließe sie dort und versuche es erneut. "
                       "Das Projekt wurde nicht verschoben.", code="project_files_locked") from exc
    # Prune empty shells only after the entire transfer has succeeded. A locked
    # empty directory may stay behind; restoration can merge into it later.
    for path in emptied:
        try:
            path.rmdir()
        except OSError:
            pass
