"""Split the Python suite into shards of about equal time, so CI runs them side by side (D-205).

    python scripts/test_shards.py <k> <n>            print shard k of n (1-based): its test modules
    python scripts/test_shards.py --run <k> <n>      run them: python -m unittest -v tests.<module> ...
    python scripts/test_shards.py --weights <log>    rewrite scripts/test_shards.json from `unittest --durations 0`

Every tests/test_*.py module lands in exactly one shard, whatever the weights say: a module without a measured time
counts as the median of the measured ones. The shards are a split of the suite, never a selection from it.
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEIGHTS = Path(__file__).with_suffix(".json")
DURATION_LINE = re.compile(r"^\s*([0-9.]+)s\s+\S+\s+\((test_\w+)\.")


def modules(root: Path = ROOT) -> list[str]:
    return sorted(path.stem for path in (root / "tests").glob("test_*.py"))


def load_weights(path: Path = WEIGHTS) -> dict[str, float]:
    try:
        return {str(name): float(seconds) for name, seconds in json.loads(path.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def shards(names: list[str], count: int, weights: dict[str, float]) -> list[list[str]]:
    """Longest first into the lightest shard; ties by name and shard index, so every run splits alike."""
    if count < 1:
        raise ValueError("at least one shard")
    measured = [weights[name] for name in names if name in weights]
    default = statistics.median(measured) if measured else 1.0
    weight = {name: weights.get(name, default) for name in names}
    groups: list[list[str]] = [[] for _ in range(count)]
    totals = [0.0] * count
    for name in sorted(names, key=lambda n: (-weight[n], n)):
        lightest = min(range(count), key=lambda i: (totals[i], i))
        groups[lightest].append(name)
        totals[lightest] += weight[name]
    return [sorted(group) for group in groups]


def weights_from_log(text: str) -> dict[str, float]:
    """Seconds per test module from the "Slowest test durations" list of `python -m unittest --durations 0`. A console
    that wrapped long lines (PowerShell's redirection does, at its width) is undone first: a line that does not start
    with a duration continues the one before it."""
    lines: list[str] = []
    for line in text.splitlines():
        if lines and line.strip() and not re.match(r"^\s*[0-9.]+s\s", line) and not line.startswith(("-", "Ran ")):
            lines[-1] += line
        else:
            lines.append(line)
    totals: dict[str, float] = {}
    for line in lines:
        if match := DURATION_LINE.match(line):
            totals[match.group(2)] = totals.get(match.group(2), 0.0) + float(match.group(1))
    return {name: round(seconds, 1) for name, seconds in sorted(totals.items())}


def main(argv: list[str]) -> int:
    if argv[:1] == ["--weights"]:
        measured = weights_from_log(Path(argv[1]).read_text(encoding="utf-8", errors="replace"))
        WEIGHTS.write_text(json.dumps(measured, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"{len(measured)} modules, {sum(measured.values()):.0f} s in all", file=sys.stderr)
        return 0
    run = argv[:1] == ["--run"]
    index, count = (int(value) for value in argv[1 if run else 0:][:2])
    if not 1 <= index <= count:
        raise SystemExit(f"shard {index} of {count} does not exist")
    chosen = shards(modules(), count, load_weights())[index - 1]
    if not run:
        print("\n".join(chosen))
        return 0
    print(f"shard {index} of {count}: {len(chosen)} modules", flush=True)
    return subprocess.call([sys.executable, "-m", "unittest", "-v", *(f"tests.{name}" for name in chosen)], cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
