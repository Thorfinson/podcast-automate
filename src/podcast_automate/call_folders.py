"""A run's call folders in the order they were made. Folders are named ``call_NNN`` (at least three digits), and a long
research run goes past ``call_999``: sorted by name, ``call_1000`` came before ``call_101``, and every view of the
newest call stayed on ``call_999`` (review 2026-11-14). Folders are never renamed, so the order comes from the number.

No project dependencies: the Studio's views, the status brief and the research status all read it."""
from __future__ import annotations

import re
from pathlib import Path

NAME = re.compile(r"call_(\d+)")


def call_order(path: Path) -> tuple:
    """The sort key of a call folder, or of a file directly in one: its number, then its name. A folder whose name
    holds no number sorts first."""
    folder = path if NAME.fullmatch(path.name) else path.parent
    found = NAME.fullmatch(folder.name)
    return (int(found[1]) if found else -1, folder.name, path.name)


def numbered(paths) -> list[Path]:
    """``paths`` (call folders or files in them) oldest call first."""
    return sorted(paths, key=call_order)
