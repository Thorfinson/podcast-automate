"""Set up optional local Qwen on macOS/Linux; never synthesize audio here."""
import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from podcast_automate.platforms import venv_python
from podcast_automate.storage import atomic_text, inside, load_project, project_lock, write_json, write_yaml

MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
REVISION = "85e237c12c027371202489a0ec509ded67b5e4b5"
TORCH_VERSION = "2.9.1"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=["auto", "cpu", "mps", "cuda:0"], default="auto")
    parser.add_argument("--python", help="Python 3.12 executable for the separate TTS environment")
    parser.add_argument("--torch-index-url", help="Official PyTorch wheel index matching your Linux GPU/driver")
    parser.add_argument("--project", type=Path, help="Also configure this existing project (relative to the repository)")
    args = parser.parse_args(argv)
    workspace = Path(__file__).resolve().parents[1]
    if platform.system() not in {"Darwin", "Linux"}:
        parser.error("Windows/Radeon: scripts/setup-qwen.ps1 verwenden.")
    if args.device == "mps" and platform.system() != "Darwin":
        parser.error("MPS ist nur auf macOS verfügbar.")
    root = inside(workspace / "projects", str((workspace / args.project).resolve())) if args.project else None
    if root:
        load_project(root)  # Validate before installing/downloading anything.
    python = args.python or shutil.which("python3.12") or (sys.executable if sys.version_info[:2] == (3, 12) else None)
    if not python:
        parser.error("Python 3.12 installieren oder mit --python angeben.")
    version_check = "import sys; assert sys.version_info[:2] == (3, 12), 'Qwen setup requires Python 3.12'"
    subprocess.run([python, "-c", version_check], check=True)
    environment = workspace / ".venv-tts"
    tts = venv_python(environment)
    if not environment.exists():
        subprocess.run([python, "-m", "venv", str(environment)], check=True)
    if not tts.is_file():
        parser.error("Die vorhandene .venv-tts gehört zu einem anderen System. Lokal neu anlegen.")
    subprocess.run([str(tts), "-c", version_check], check=True)
    command = [str(tts), "-m", "pip", "install", f"torch=={TORCH_VERSION}", f"torchaudio=={TORCH_VERSION}"]
    if args.torch_index_url:
        command += ["--index-url", args.torch_index_url]
    subprocess.run(command, check=True)
    subprocess.run([str(tts), "-m", "pip", "install", "-r", str(workspace / "requirements-tts.txt")], check=True)
    subprocess.run([str(tts), "-m", "pip", "check"], check=True)
    # Reuse device selection from the standalone worker, without requiring the
    # controller package or its dependencies inside the TTS environment.
    verify = """
import importlib.util, sys, torch
from huggingface_hub import snapshot_download
from qwen_tts import Qwen3TTSModel
spec = importlib.util.spec_from_file_location('worker', sys.argv[1])
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
device = worker.select_device(torch, sys.argv[2])
x = torch.randn((32, 32), device=device, dtype=worker.model_dtype(torch, device))
assert (x @ x).isfinite().all(), 'Device computation failed'
print('Qwen device:', device)
snapshot_download(sys.argv[3], revision=sys.argv[4])
"""
    subprocess.run([str(tts), "-c", verify, str(workspace / "src/podcast_automate/qwen_worker.py"),
                    args.device, MODEL, REVISION], check=True)
    settings = {"tts_python": str(tts), "tts_model": MODEL, "tts_revision": REVISION,
                "tts_device": args.device, "tts_attention": "eager"}
    if root:
        with project_lock(root):
            config = load_project(root)
            backup = root / "reports/project-before-qwen.yaml"
            if not backup.exists():
                atomic_text(backup, (root / "project.yaml").read_text(encoding="utf-8"))
            config.runtime = type(config.runtime).model_validate({**config.runtime.model_dump(), **settings})
            write_yaml(root / "project.yaml", config.model_dump(mode="json"))
    write_json(workspace / ".studio/tts-runtime.json", settings)
    installed = subprocess.run([str(tts), "-m", "pip", "freeze"], check=True, capture_output=True, text=True).stdout
    atomic_text(workspace / ".studio/tts-requirements-installed.txt", installed)
    print("Qwen eingerichtet. Neue Studio-Projekte verwenden diese lokalen Einstellungen.")
    print("Geräteprüfung bestanden; echte Sprachausgabe erst nach deiner Audiofreigabe.")


if __name__ == "__main__":
    main()
