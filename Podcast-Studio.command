#!/bin/sh
set -eu
studio_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec sh "$studio_root/Podcast-Studio.sh" "$@"
