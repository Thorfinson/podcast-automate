# Contributing

Thank you for helping. Podcast Automate is a personal open-source project maintained in spare time, so a review can
take a few days. This page covers setup, tests and how to propose a change; the binding details are in
[AGENTS.md](AGENTS.md), which is also the standard pull requests are reviewed against.

## Before you start

- **Bigger changes start with an issue.** A new provider, pipeline stage or Studio flow, a changed business rule or a
  new dependency: open a [feature request](https://github.com/Thorfinson/podcast-automate/issues/new/choose) first, so
  the approach is agreed before you write the code. Typos, doc fixes and small bug fixes can go straight to a pull
  request.
- **Check the scope.** [Out of scope](docs/PRODUCT.md#out-of-scope) lists what the project does not do on purpose, for
  example quizzes or automatic publishing; [DECISIONS](docs/DECISIONS.md) records why a rule is the way it is.
- **Security problems** never go into a public issue; see [SECURITY.md](SECURITY.md).
- **Coding agents** (Claude Code, Codex and others) read [AGENTS.md](AGENTS.md); Claude Code loads it through
  `CLAUDE.md`. A change made with an agent follows the same rules as one made by hand.

## Set up

You need Python 3.12 or newer, FFmpeg and, for the browser suite, Node 22. The tests need no account, key, GPU or
network.

Windows (PowerShell, in the repository folder):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\scripts\setup-ffmpeg.ps1
```

macOS and Linux: install FFmpeg with your package manager, then run `sh scripts/setup.sh`, which creates `.venv` and
installs the package in editable mode. Details: [Install on Windows 11](docs/OPERATIONS.md#install-on-windows-11),
[Install on macOS and Linux](docs/OPERATIONS.md#install-on-macos-and-linux). Local Qwen (`.venv-tts`) is not needed
for development.

## Run the tests

There are two suites, a Python suite that takes a few minutes and a browser-logic suite that takes about a second.
Their commands, the Windows PATH line for FFmpeg and which tests a change selects are in AGENTS.md:

- [The two suites](AGENTS.md#the-two-suites): commands and requirements.
- [When to run what](AGENTS.md#when-to-run-what) and [Choosing what to run](AGENTS.md#choosing-what-to-run): the
  focused tests during work, and both suites in full before a pull request.
- [Source module to test modules](AGENTS.md#source-module-to-test-modules): which test modules guard which module.

CI runs both suites on Linux, macOS and Windows with Python 3.12 and 3.13; all six legs must pass.

**Never add a test that performs a real model call,** a download, speech synthesis or any other network access. Model
answers come from patched adapters and the fixtures in `tests/*_fixtures.py`. Checks against real models live under
`evals/` and are run by hand ([Evals](docs/QUALITY.md#evals)). Never make a run green by retrying, skipping or relaxing
an assertion; [Rules for the tests themselves](AGENTS.md#rules-for-the-tests-themselves) has the full list.

## Conventions

The full list is in [AGENTS.md › Conventions](AGENTS.md#conventions). In short:

- **Errors** are raised as `errors.AppError` with a stable `code`. The Studio and the run manifest show the code, so
  renaming one is a behaviour change.
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

## Pull requests

1. Fork the repository and branch from `main`; keep one topic per pull request.
2. Run the focused tests while you work and both suites in full before you open the pull request.
3. Fill in the [pull request template](.github/PULL_REQUEST_TEMPLATE.md): what changed and why, the linked issue, what
   you ran and what you did not run.
4. When you replace a test, name the protection it keeps in the commit message; business rules keep their tests
   through refactors.

Never commit keys, project folders (`projects/`), run folders or logs; `.gitignore` covers the usual places, and the
Studio keeps keys in memory only.

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE) of this project.

## Reporting bugs

Use the [bug report form](https://github.com/Thorfinson/podcast-automate/issues/new/choose). The most useful facts are
the stop code the Studio shows in its hold card (or `pla status`), the output of `pla doctor --skip-tts --json` and the
steps that led there. Remove every key and personal path before you paste anything; the logs are filtered for known
keys, but check them anyway.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
