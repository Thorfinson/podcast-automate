#!/bin/sh
set -eu
studio_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd "$studio_root"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ ! -x "$studio_root/.venv/bin/python" ]; then
    printf '%s\n' 'Die Python-Umgebung fehlt. Zuerst: sh scripts/setup.sh' >&2
    exit 1
fi
exec "$studio_root/.venv/bin/python" -m podcast_automate studio "$studio_root" "$@"
