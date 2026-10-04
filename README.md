# Podcast Automate

A personal research-to-podcast system. You give it a topic; the Studio researches it on the web with evidence, plans a
deep-dive series, writes and reviews dialogue scripts, and records approved episodes as MP3. Everything runs on your
own computer. Text models run through your Claude or Codex subscription or through OpenRouter; speech comes from local
Qwen or from Gemini via OpenRouter.

The explanations assume no subject or maths background and build depth through familiar images, worked examples and
named limits. Series length and episode count follow from the topic.

```text
topic ─▶ research ─▶ table of contents ─▶ teaching plan, script, polishing, reviews ─▶ you read ─▶ recording ─▶ MP3
          (dossier)    (you approve)                                                   (you approve)
```

## Docs

| File | Content |
| --- | --- |
| [docs/PRODUCT.md](docs/PRODUCT.md) | What it is for, capabilities, text and audio models, all `pla` commands, out of scope, roadmap |
| [docs/STUDIO.md](docs/STUDIO.md) | The browser Studio step by step: hold cards, stopping and resuming, progress, downloads |
| [docs/BUSINESS_LOGIC.md](docs/BUSINESS_LOGIC.md) | Cross-cutting rules: provider choice and quotas, budgets, approvals, resume, episode length |
| [docs/RESEARCH.md](docs/RESEARCH.md) | From topic to verified dossier: sub-questions, reviews, gap probe, evidence contracts |
| [docs/SCRIPTS.md](docs/SCRIPTS.md) | From dossier to reviewed dialogue: series plan, polishing, script and series review |
| [docs/TEACHING.md](docs/TEACHING.md) | Editorial standard and the teaching plan every script run must pass |
| [docs/AUDIO.md](docs/AUDIO.md) | Recording with Qwen or Gemini: take checks, spoken forms, re-rendering, exports |
| [docs/QUALITY.md](docs/QUALITY.md) | Quality gates, assessment criteria, acceptance, evals |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Pipeline stages, processes, provider adapters, data contracts, project and run folders |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Where each setting lives: project brief, Studio settings, environment variables |
| [docs/SECURITY.md](docs/SECURITY.md) | What leaves the machine, Studio access, keys, local files, source rights |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Runbooks: install on [Windows](docs/OPERATIONS.md#install-on-windows-11) or [macOS/Linux](docs/OPERATIONS.md#install-on-macos-and-linux), [local Qwen](docs/OPERATIONS.md#set-up-local-qwen-on-windows), [update](docs/OPERATIONS.md#update-the-studio), [logs](docs/OPERATIONS.md#logs-and-diagnosis) |
| [docs/DECISIONS.md](docs/DECISIONS.md) | All decisions and open questions |
| [docs/GOTCHAS.md](docs/GOTCHAS.md) | Pitfalls: symptom, cause, fix |
| [CHANGELOG.md](CHANGELOG.md) | User-visible changes |
| [AGENTS.md](AGENTS.md) | Commands, code layout, conventions and testing rules for contributors and coding agents |

Plans with open work: [MVP acceptance](docs/specs/2026-09-10-mvp-acceptance-plan.md) (including the verification
list) and [review-loop decision gates](docs/specs/2026-10-04-review-loop-decision-gates-plan.md). Finished plans and the
quality audit of 19 September 2026 are in [docs/specs/](docs/specs/).

## Quick start

| System | Setup | Start |
| --- | --- | --- |
| Windows 11 | [Install on Windows 11](docs/OPERATIONS.md#install-on-windows-11) | double-click `Podcast-Studio.cmd` |
| macOS | `brew install python@3.12 ffmpeg`, then `sh scripts/setup.sh` ([details](docs/OPERATIONS.md#install-on-macos-and-linux)) | double-click `Podcast-Studio.command` |
| Linux | Install Python 3.12 or later and FFmpeg, then `sh scripts/setup.sh` | `sh Podcast-Studio.sh` |

The Studio opens `http://127.0.0.1:8765` and is reachable only from this computer. Evidence-backed web research needs a
logged-in subscription: Claude Code with a Claude Max plan or the Codex CLI with a ChatGPT plan. `pla doctor --skip-tts`
checks the installation without local Qwen. All commands: [PRODUCT](docs/PRODUCT.md#commands).

## Layout

| Path | Content |
| --- | --- |
| `src/podcast_automate/` | The `pla` CLI, the pipeline, the Studio server and its browser UI (`web/`), all prompts (`prompts/`) |
| `tests/` | Python and browser test suites; which to run when is in [AGENTS.md](AGENTS.md) |
| `evals/` | Real-model regression checks, run by hand ([QUALITY](docs/QUALITY.md#evals)) |
| `scripts/` | Setup and maintenance scripts |
| `docs/` | The documentation above; dated plans and specs in `docs/specs/` |
| `projects/` | Your projects and their runs (git-ignored); structure in [ARCHITECTURE](docs/ARCHITECTURE.md#project-folder-layout) |

## Deploy

There is no deployment: the Studio and the CLI run locally from this checkout. To get a new version, follow
[Update the Studio](docs/OPERATIONS.md#update-the-studio).

## License

MIT, see [LICENSE](LICENSE).
