"""Whether a change needs the test suites: CI's `changes` job (D-205).

    python scripts/ci_changes.py <base> <head>     prints "code=true" or "code=false" for $GITHUB_OUTPUT

A change that touches only files no test reads skips the test legs; any other change runs them, and so does a change
whose files cannot be listed (a first push, an unknown base, a failing git).
"""
from __future__ import annotations

import fnmatch
import subprocess
import sys

# Files no test reads (checked 2026-11-10): the tests name docs/ and AGENTS.md only in comments, while examples/ is read
# by test_third_host and the launchers by test_platforms, so those count as code, like every workflow.
NO_TEST_PATTERNS = (
    "docs/*",
    "site/*",
    "evals/*",
    "LICENSE",
    ".gitignore",
    ".github/ISSUE_TEMPLATE/*",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
)


def reads_no_test(path: str) -> bool:
    """True for a file no test reads: a Markdown file at the top level or a path under NO_TEST_PATTERNS."""
    if "/" not in path and path.endswith(".md"):
        return True
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in NO_TEST_PATTERNS)


def needs_tests(paths: list[str] | None) -> bool:
    """Whether the changed files call for the test suites; None (not listable) and an empty list do."""
    if not paths:
        return True
    return not all(reads_no_test(path) for path in paths)


def changed_files(base: str, head: str) -> list[str] | None:
    """The files that differ between the merge base of `base` and `head`, and `head`; None when git cannot tell."""
    if not base or not head or set(base) == {"0"}:
        return None
    result = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], capture_output=True, text=True,
                            encoding="utf-8")
    if result.returncode:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str]) -> int:
    base, head = (argv + ["", ""])[:2]
    paths = changed_files(base, head)
    code = needs_tests(paths)
    shown = "unknown" if paths is None else f"{len(paths)} files"
    print(f"changed: {shown}; tests {'run' if code else 'skipped'}", file=sys.stderr)
    print(f"code={'true' if code else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
