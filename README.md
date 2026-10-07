# Podcast Automate

Podcast Automate turns a topic into a researched, reviewed dialogue podcast series and records it as MP3. You give it
a topic or a question; the Studio, a small web app on your own computer, searches the web for sources, builds an
evidence-backed dossier, plans a series, writes and reviews a two-host dialogue script for each episode and, once you
have read and approved a script, records it with two synthetic voices. Text models run through your Claude or Codex
subscription, through Claude Code on your own Anthropic API key, or through OpenRouter; the web search runs through the
text model's own tools or through Perplexity; speech comes from Gemini through Google (recommended), Gemini through
OpenRouter or local Qwen on a GPU.

The explanations assume no subject or maths background and build depth through familiar images, worked examples and
named limits. Series length and episode count follow from the topic.

```text
topic ─▶ research ─▶ table of contents ─▶ teaching plan, script, polishing, reviews ─▶ you read ─▶ recording ─▶ MP3
          (dossier)    (you approve)                                                   (you approve)
```

## Who it is for

People who want to understand a topic in depth by listening, for their own learning, and who are comfortable
installing Python and a command-line tool. It is a single-user tool: no accounts, no hosting, no automatic publishing.
You read every script before it is recorded. The sources are cited, but an episode does not replace an expert's review.

## What you need

- **Python 3.12 or newer and FFmpeg.** On Windows a script installs FFmpeg into the checkout.
- **One text access**, any of:
  - a Claude Max subscription, logged in through the official Claude Code CLI;
  - a ChatGPT subscription, logged in through the official Codex CLI;
  - your own Anthropic API key, used through Claude Code (`claude_api`, [experimental](#experimental-parts));
  - an OpenRouter key together with a Perplexity key for the web search ([experimental](#experimental-parts)).

  Subscriptions are used only through their official CLI logins, never through extracted tokens; check that your
  plan's terms cover this use.
- **One speech route**, any of:
  - Gemini through Google's API on your Google key (recommended, no GPU needed);
  - Gemini through OpenRouter on your OpenRouter key;
  - local Qwen3-TTS on a GPU (tested on Windows with an AMD Radeon RX 9070 XT; the macOS and Linux setup is built but
    not yet listening-tested).

## What it costs

A full series is a big job. Measured on three real series in October 2026, one series (research and scripts) took
1,400 to 1,800 model calls, worth about 600 to 800 USD at API prices, and 33 to 35 hours of research plus 45 to 54
hours of script work ([Money limit](docs/BUSINESS_LOGIC.md#money-limit)).

- **On a subscription** there is no bill per call, but a series uses a lot of your plan's quota. A run pauses when the
  quota is used up and resumes after the reset.
- **On an API key** (Anthropic, OpenRouter, and Perplexity at about 0.005 USD per search request) every call is billed,
  and a billed run does not start without a money limit in USD per run.
- **Speech** through Google or OpenRouter runs on that key at the provider's prices; local Qwen costs nothing per
  episode.
- **Start with a trial project:** `pla init <project> --trial` (with `--topic "…"`, or without it for a narrow sample
  topic) keeps research and the series small: at most three sub-questions, one episode of at most 20 minutes, at most
  110 model calls per run and, where you set a money limit, at most 45 USD
  ([Trial project](docs/BUSINESS_LOGIC.md#trial-project)). A first run then costs a fraction of a full series.

## Quick start

| System | Setup | Start |
| --- | --- | --- |
| Windows 11 | [Install on Windows 11](docs/OPERATIONS.md#install-on-windows-11) | double-click `Podcast-Studio.cmd` |
| macOS | `brew install python@3.12 ffmpeg`, then `sh scripts/setup.sh` ([details](docs/OPERATIONS.md#install-on-macos-and-linux)) | double-click `Podcast-Studio.command` |
| Linux | Install Python 3.12 or later and FFmpeg, then `sh scripts/setup.sh` | `sh Podcast-Studio.sh` |

The Studio opens `http://127.0.0.1:8765` and is reachable only from this computer
([Studio access](docs/SECURITY.md#studio-access)). Keys go into the settings page; the Studio keeps them in memory
only, never in project files. Set up your text access: [Codex](docs/OPERATIONS.md#codex),
[Claude Code](docs/OPERATIONS.md#claude-code), [Claude on your Anthropic API key](docs/OPERATIONS.md#claude-on-your-anthropic-api-key)
or [OpenRouter with Perplexity](docs/OPERATIONS.md#research-with-openrouter-and-perplexity); speech:
[Gemini via Google](docs/AUDIO.md#gemini-via-google). `pla doctor --skip-tts` checks the installation without local
Qwen. All commands: [PRODUCT](docs/PRODUCT.md#commands).

## Experimental parts

> **Experimental:** Claude on your own Anthropic API key (`claude_api`) and the web search through Perplexity are
> implemented and tested against simulated APIs only; they are not yet verified against the real APIs. Open are, among
> others, whether every call really runs on the key, whether the counted cost matches the provider's bill, the error
> codes and rate limits, and whether Perplexity finds sources as good as a model's own search (V-29 to V-36 in the
> [verification list](docs/specs/2026-09-10-mvp-acceptance-plan.md#verification-list)). Use a trial project and a low
> money limit, and check the provider's own bill.

## Good to know

- **Languages.** The Studio's interface is in English or German: it follows your browser's language, or you fix one.
  A series is written and recorded in English or German, set per project, and everything its listeners get (file
  names, show notes, the companion kit) is in that language ([Languages](docs/ARCHITECTURE.md#languages)). The `pla`
  command line is English.
- **AI-generated content.** Scripts and voices are produced by AI models, and the exports are marked as AI-generated.
  Before you share an episode, check its facts, the rights of the quoted sources and your platform's rules for AI
  content ([Source rights and privacy](docs/SECURITY.md#source-rights-and-privacy)).
- **What leaves your computer.** Prompts go to the text provider you chose, search queries to Perplexity if you use
  it, the spoken text to the speech provider ([Trust boundaries](docs/SECURITY.md#trust-boundaries)).

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
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to set up, test and propose a change |
| [SECURITY.md](SECURITY.md) | Supported versions and how to report a vulnerability privately |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | How we treat each other in issues and pull requests |

Plans with open work: [MVP acceptance](docs/specs/2026-09-10-mvp-acceptance-plan.md) (including the verification
list) and [review-loop decision gates](docs/specs/2026-10-04-review-loop-decision-gates-plan.md). Finished plans and the
quality audit of 19 September 2026 are in [docs/specs/](docs/specs/).

## Layout

| Path | Content |
| --- | --- |
| `src/podcast_automate/` | The `pla` CLI, the pipeline, the Studio server and its browser UI (`web/`), all prompts (`prompts/`) |
| `tests/` | Python and browser test suites; which to run when is in [AGENTS.md](AGENTS.md) |
| `evals/` | Real-model regression checks, run by hand ([QUALITY](docs/QUALITY.md#evals)) |
| `scripts/` | Setup and maintenance scripts |
| `docs/` | The documentation above; dated plans and specs in `docs/specs/` |
| `.github/` | CI and release workflows, issue and pull request templates |
| `projects/` | Your projects and their runs (git-ignored); structure in [ARCHITECTURE](docs/ARCHITECTURE.md#project-folder-layout) |

## Releases and updates

There is no hosted service: the Studio and the CLI run on your computer. A version tag `vX.Y.Z` builds the package,
runs both test suites and publishes it to PyPI as `podcast-automate`, with a GitHub release that carries the version's
[CHANGELOG](CHANGELOG.md) section. The package provides the same `pla` command; the launchers and the FFmpeg and Qwen
setup scripts come with a checkout, which the quick start above uses. To update a checkout, follow
[Update the Studio](docs/OPERATIONS.md#update-the-studio).

## Contributing and security

Contributions are welcome: [CONTRIBUTING.md](CONTRIBUTING.md) explains setup, tests and pull requests, and coding
agents read [AGENTS.md](AGENTS.md). Please report vulnerabilities privately as described in [SECURITY.md](SECURITY.md),
not in a public issue. Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

MIT, see [LICENSE](LICENSE).
