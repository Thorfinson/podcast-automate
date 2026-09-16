#!/bin/sh
# Controller only. Qwen is optional and has its own environment.
set -eu
project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
case "$(uname -s)" in
    Darwin|Linux) ;;
    *) printf '%s\n' 'Für Windows: docs/windows-quickstart.md'; exit 1 ;;
esac
if [ -z "${PYTHON:-}" ]; then
    if command -v python3.12 >/dev/null 2>&1; then PYTHON=python3.12; else PYTHON=python3; fi
fi
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python 3.12 oder neuer benötigt.")'
if [ ! -d "$project_root/.venv" ]; then
    "$PYTHON" -m venv "$project_root/.venv"
fi
if [ ! -x "$project_root/.venv/bin/python" ]; then
    printf '%s\n' 'Die vorhandene .venv passt nicht zu diesem System. Virtuelle Umgebungen nicht zwischen Rechnern kopieren.' >&2
    exit 1
fi
"$project_root/.venv/bin/python" -m pip install -e "$project_root"
chmod +x "$project_root/Podcast-Studio.sh" "$project_root/Podcast-Studio.command"
for program in ffmpeg ffprobe; do
    if ! command -v "$program" >/dev/null 2>&1; then
        printf '%s\n' "$program fehlt. macOS: brew install ffmpeg; Ubuntu/Debian: sudo apt install ffmpeg" >&2
        exit 1
    fi
done
printf '%s\n' 'Studio bereit: sh Podcast-Studio.sh' 'macOS: alternativ Podcast-Studio.command doppelklicken.' 'Lokales Qwen ist optional; Einrichtung: docs/macos-linux.md'
