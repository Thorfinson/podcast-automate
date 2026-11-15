#!/bin/sh
# Controller only. Qwen is optional and has its own environment.
set -eu
project_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
case "$(uname -s)" in
    Darwin|Linux) ;;
    *) printf '%s\n' 'On Windows, follow docs/OPERATIONS.md, section "Install on Windows 11".'; exit 1 ;;
esac
# Python 3.13 and 3.12 are the versions every release is tested with; PYTHON chooses another one.
if [ -z "${PYTHON:-}" ]; then
    if command -v python3.13 >/dev/null 2>&1; then PYTHON=python3.13
    elif command -v python3.12 >/dev/null 2>&1; then PYTHON=python3.12
    else PYTHON=python3
    fi
fi
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python 3.12 or newer is required.")'
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info[:2] <= (3, 13) else 1)'; then
    printf '%s\n' "Note: $("$PYTHON" -c 'import platform; print(platform.python_version())') is newer than the tested Python 3.12 and 3.13; if the setup or the Studio fails, run again with PYTHON=python3.13." >&2
fi
if [ ! -d "$project_root/.venv" ]; then
    "$PYTHON" -m venv "$project_root/.venv"
fi
if [ ! -x "$project_root/.venv/bin/python" ]; then
    printf '%s\n' 'The existing .venv does not belong to this system. Never copy a virtual environment between computers; remove .venv and run the setup again.' >&2
    exit 1
fi
"$project_root/.venv/bin/python" -m pip install -e "$project_root"
chmod +x "$project_root/Podcast-Studio.sh" "$project_root/Podcast-Studio.command"
for program in ffmpeg ffprobe; do
    if ! command -v "$program" >/dev/null 2>&1; then
        printf '%s\n' "$program is missing. macOS: brew install ffmpeg; Ubuntu/Debian: sudo apt install ffmpeg" >&2
        exit 1
    fi
done
printf '%s\n' 'Studio ready: sh Podcast-Studio.sh' 'On macOS you can also double-click Podcast-Studio.command.' 'Local Qwen is optional; setup: docs/OPERATIONS.md'
