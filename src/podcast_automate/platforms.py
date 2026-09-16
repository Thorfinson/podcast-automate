"""Host-specific paths without importing optional GPU libraries."""
import os
import platform
from pathlib import Path


def venv_python(folder: Path) -> Path:
    return folder / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def configure_path(workspace: Path) -> None:
    candidates = [workspace / "tools/ffmpeg/bin"]
    if platform.system() != "Windows":
        # Finder/desktop launchers may not inherit an interactive shell's PATH.
        candidates += [Path.home() / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    current = os.environ.get("PATH", "").split(os.pathsep)
    additions = [str(p) for p in candidates if p.is_dir() and str(p) not in current]
    os.environ["PATH"] = os.pathsep.join(additions + current)
