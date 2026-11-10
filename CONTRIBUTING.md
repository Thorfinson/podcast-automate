# Contributing

Thank you for helping. Podcast Automate is a personal open-source project maintained in spare time, so a review can
take a few days. This page covers setup, tests, how a change reaches `main` and how a version is released; the binding
details are in [AGENTS.md](AGENTS.md), which is also the standard pull requests are reviewed against.

## Before you start

- **Questions and ideas go to [Discussions](https://github.com/Thorfinson/podcast-automate/discussions).** Bugs and
  agreed features go to [Issues](https://github.com/Thorfinson/podcast-automate/issues/new/choose).
- **Bigger changes start with an issue.** A new provider, pipeline stage or Studio flow, a changed business rule or a
  new dependency: open a feature request first, so the approach is agreed before you write the code. Typos, doc fixes
  and small bug fixes can go straight to a pull request.
- **Looking for a first task?** Issues labelled
  [`good first issue`](https://github.com/Thorfinson/podcast-automate/labels/good%20first%20issue) are small and
  self-contained; [`help wanted`](https://github.com/Thorfinson/podcast-automate/labels/help%20wanted) marks work that
  needs an account or hardware the maintainer does not have.
- **Check the scope.** [Out of scope](docs/PRODUCT.md#out-of-scope) lists what the project does not do on purpose, for
  example quizzes or automatic publishing; [DECISIONS](docs/DECISIONS.md) records why a rule is the way it is.
- **Security problems** never go into a public issue; see [SECURITY.md](SECURITY.md).
- **Coding agents** (Claude Code, Codex and others) read [AGENTS.md](AGENTS.md); Claude Code loads it through
  `CLAUDE.md`. A change made with an agent follows the same rules as one made by hand.

## The project in five minutes

[ARCHITECTURE › In five minutes](docs/ARCHITECTURE.md#in-five-minutes) shows the three run kinds (research, script,
audio), the Studio around them and where each lives in the code. Read it before your first change; the other docs
are listed in the [README](README.md#docs).

## Set up

You need Python 3.12 or newer, FFmpeg and, for the browser suite, Node 22. The tests need no account, key, GPU or
network.

Windows (PowerShell, in the repository folder):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\setup-ffmpeg.ps1
```

macOS and Linux: install FFmpeg with your package manager, then run `sh scripts/setup.sh`, which creates `.venv` and
installs the package in editable mode; add the contributor tools with `.venv/bin/python -m pip install -e ".[dev]"`.
Details: [Install on Windows 11](docs/OPERATIONS.md#install-on-windows-11),
[Install on macOS and Linux](docs/OPERATIONS.md#install-on-macos-and-linux). Local Qwen (`.venv-tts`) is not needed
for development.

## Lint

`ruff check .` runs in a second and is the first CI job; its rules are in `pyproject.toml` (Ruff's defaults, so
pyflakes and the error subset of pycodestyle, at 120 columns). `ruff check --fix .` removes unused imports. There is no
formatter: keep the style of the file you edit.

## Run the tests

There are two suites: the Python suite (about 1,280 tests, about 20 minutes, because every script run starts a fake
story agent as a process) and the browser-logic suite (about 240 tests, about a second). Their commands, the Windows
PATH line for FFmpeg and which tests a change selects are in AGENTS.md:

- [The two suites](AGENTS.md#the-two-suites): commands and requirements.
- [When to run what](AGENTS.md#when-to-run-what) and [Choosing what to run](AGENTS.md#choosing-what-to-run): the
  focused tests during work, and both suites in full before a pull request.
- [Source module to test modules](AGENTS.md#source-module-to-test-modules): which test modules guard which module.

**The fast loop while you work:** run only the test modules mapped to the module you changed, for example
`python -m unittest tests.test_spoken_forms tests.test_audio`, and the browser suite after any change to `web/`. That
takes seconds to a few minutes. Before the pull request, run both suites in full once; CI runs them again on every push.

CI runs the lint job and both suites on Linux, macOS and Windows with Python 3.12 and 3.13; all seven checks must pass.

**Never add a test that performs a real model call,** a download, speech synthesis or any other network access. Model
answers come from patched adapters and the fixtures in `tests/*_fixtures.py`. Checks against real models live under
`evals/` and are run by hand ([Evals](docs/QUALITY.md#evals)). Never make a run green by retrying, skipping or relaxing
an assertion; [Rules for the tests themselves](AGENTS.md#rules-for-the-tests-themselves) has the full list.

## Conventions

The full list is in [AGENTS.md › Conventions](AGENTS.md#conventions). In short:

- **Errors** are raised as `errors.AppError` with a stable `code`. The Studio and the run manifest show the code, and
  the English Studio explains it by its text `error.code.<code>` in both catalogs, so renaming one is a behaviour
  change and a new one needs its text (a test fails without it).
- **Project files** are written through `storage` (`atomic_text`, `write_json`, `write_yaml`); a file another worker
  may be writing is read through `storage.read_text`.
- **Prompts** are text files in [`src/podcast_automate/prompts/`](src/podcast_automate/prompts/README.md). Every model
  call carries a `prompt_version` tag; bump it when the meaning of its prompt changes, because checkpoints are bound to
  the prompt hash.
- **Language.** Code, comments, docs and prompts are English. Text the Studio shows comes from its English and German
  UI catalogs; a new or changed text needs both. AGENTS.md names where each kind of Studio text lives.
- **Docs.** Every fact has one home doc, listed in the [README](README.md#docs). Change a documented fact in its home
  doc in the same pull request and bump its `last_reviewed`. Decisions go into [DECISIONS](docs/DECISIONS.md), traps
  into [GOTCHAS](docs/GOTCHAS.md), user-visible changes into [CHANGELOG](CHANGELOG.md) under `[Unreleased]`.

## How a change reaches main

1. Fork the repository and branch from `main`; keep one topic per pull request. Name the branch after it, for example
   `fix/resume-after-quota` or `docs/elevenlabs-voices`.
2. Run the lint, the focused tests while you work and both suites in full before you open the pull request.
3. Fill in the [pull request template](.github/PULL_REQUEST_TEMPLATE.md): what changed and why, the linked issue
   ("Closes #123"), what you ran and what you did not run. A draft pull request is welcome for early feedback.
4. When you replace a test, name the protection it keeps in the commit message; business rules keep their tests
   through refactors.

`main` is protected by a repository ruleset: a change arrives through a pull request, the seven CI checks (lint and the
six test legs) must pass, review threads must be resolved, and nobody force-pushes or deletes `main`. The maintainer
reviews every pull request ([CODEOWNERS](.github/CODEOWNERS)) and merges it by squash, with the pull request's title
as the commit message, or by rebase for a series of commits that each stand alone; merged branches are deleted.
The maintainer, as the repository's administrator, may push small fixes to `main` directly; CI checks those too.

Labels sort issues and pull requests: the kind (`bug`, `enhancement`, `documentation`, `question`), the area
(`area: research`, `area: scripts`, `area: audio`, `area: studio`, `area: providers`, `area: i18n`), and `ci`,
`dependencies`, `security`, `good first issue` and `help wanted`. Dependabot opens weekly pull requests for the
workflows' actions and the Python dependencies; they pass the same checks, and a major version waits until its
changelog has been read.

Never commit keys, project folders (`projects/`), run folders or logs; `.gitignore` covers the usual places, and the
Studio keeps keys in the operating system's credential store, never in project files.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE) of this project.

## Releases

Versions follow [Semantic Versioning](https://semver.org/); while the version is 0.x, a minor version may change
behaviour, and the CHANGELOG says how. Only the maintainer releases: move the `[Unreleased]` entries of
[CHANGELOG.md](CHANGELOG.md) under a new `## [X.Y.Z] - YYYY-MM-DD` section, set the same version in `pyproject.toml`,
and once that commit's checks passed on `main`, push the tag `vX.Y.Z`. Tags `v*` are protected: only the maintainer
creates them. The [release workflow](.github/workflows/release.yml) then builds and checks the package, runs both
suites against the built wheel, waits for the maintainer's approval in the `pypi` environment, publishes to PyPI and
creates the GitHub release with the CHANGELOG section ([Release a version](docs/OPERATIONS.md#release-a-version)).

## Reporting bugs

Use the [bug report form](https://github.com/Thorfinson/podcast-automate/issues/new/choose). The most useful facts are
the stop code the Studio shows in its hold card (or `pla status`), the output of `pla doctor --skip-tts --json` and the
steps that led there. Remove every key and personal path before you paste anything; the logs are filtered for known
keys, but check them anyway.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
