---
title: Configuration
doc_type: configuration
status: current
last_reviewed: 2026-10-04
covers:
  - src/podcast_automate/studio_settings.py
  - src/podcast_automate/models.py
  - src/podcast_automate/storage.py
  - src/podcast_automate/subscriptions.py
  - src/podcast_automate/sources.py
  - src/podcast_automate/platforms.py
  - src/podcast_automate/studio.py
  - src/podcast_automate/studio_worker.py
  - src/podcast_automate/cli.py
  - scripts/setup-qwen.py
---

# Configuration

## Where settings live

| Place | Scope | Holds | Written by |
| --- | --- | --- | --- |
| `<project>/project.yaml` | One project | The topic brief, the CLI default adapters and the runtime settings ([Project brief](#project-brief)); never credentials | `pla init`, the Studio's brief conversation |
| `projects/.studio-settings.json` | Every project of a Studio workspace | Text model, audio, execution modes, pre-approvals, research limits and the time limit of one model call ([Studio settings](#studio-settings)) | The Studio's settings page only |
| `<project>/studio/text.json`, `audio.json`, `execution.json`, `allowances.json` | One project | The same choices per project; used only while the workspace file does not exist | The Studio |
| `<project>/studio/jev_probe.json` | One project | Whether new script runs also ask Jev in the gap probe; stays per project even with workspace settings ([Gap probe](RESEARCH.md#gap-probe)) | The Studio switch |
| `<project>/studio/spoken_forms.json`, `<project>/style_notes.md` | One project | Spoken forms ([Spoken forms and pronunciation](AUDIO.md#spoken-forms-and-pronunciation)) and editorial notes ([Host names and style notes](SCRIPTS.md#host-names-and-style-notes)) | The Studio |
| `~/.podcast-automate/subscriptions.json` | The user account, all workspaces | Quota store and the Claude extra-usage switch ([Files outside the project](#files-outside-the-project)) | Adapters, Studio, `pla quota` |
| `.studio/tts-runtime.json` | One workspace | The Qwen runtime (`tts_python`, model, pinned revision, device) that new Studio projects take over ([Set up local Qwen on Windows](OPERATIONS.md#set-up-local-qwen-on-windows)) | `scripts/setup-qwen.py` |
| Environment variables | The running process | Keys, store locations and switches ([Environment variables](#environment-variables)) | You |
| CLI options | One run | Backend, model, reasoning level and OpenRouter output limit of a run ([Commands](PRODUCT.md#commands)) | You |

## Project brief

`project.yaml` holds one `TopicBrief` (`models.TopicBrief`), validated strictly: unknown fields are refused
(`models.Contract`). `pla init <project> --topic "…"` creates it with these defaults:

| Field | Default | Meaning |
| --- | --- | --- |
| `schema_version` | `"1.0"` | Version of the contract |
| `topic`, `central_question` | required, `""` | Topic and guiding question of the series |
| `language` | `de-DE` | Language of the series; `de-DE` or `en-US` |
| `audience_level`, `prior_knowledge` | „Neugierige Erwachsene ohne spezielles Vorwissen; fachlich anspruchsvoll und auf Augenhöhe“ (curious adults without special prior knowledge; demanding and on equal footing), `""` | Level and the foundations already known |
| `depth_request` | A default text that asks for depth built from the ground up, one worked example, no formulas and no repeated definitions | Desired depth and explanatory focus; independent of listening time |
| `focus_questions`, `excluded_topics` | `[]` | Desired focus and boundaries |
| `seed_people`, `seed_urls`, `local_sources` | `[]` | Optional research entry points. People need an evidenced attribution to a source; local sources must lie inside the project folder ([Local files](SECURITY.md#local-files)) |
| `target_total_minutes` | `null` | Optional, explicit planning wish (`pla init --total-minutes`, no default); no implicit total limit ([Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length)) |
| `max_episode_minutes` | `30`, fixed | Unused since 2026-10-04 (one episode is one MP3); kept for the project hash of existing runs: [Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length) |
| `research_limits` | `search_rounds`, `sources`, `model_calls` | Limits per run, not on the series' scope; defaults and what happens at a limit: [Budgets](BUSINESS_LOGIC.md#budgets). Operational like `runtime`; the workspace settings replace it |
| `text_backend` | `codex_cli` | CLI default text adapter: `codex_cli`, `claude_code` or `auto` (why: D-014); the Studio's choice is in the [Studio settings](#studio-settings) |
| `tts_backend` | `qwen3_local` | CLI default speech adapter; the Studio's audio choice is in the [Studio settings](#studio-settings) |
| `voice_profile` | `{"host_a": "Ryan", "host_b": "Serena"}` | The persistent Qwen voices of the two hosts; two different voices are required |
| `host_names` | `null` | Optional names the hosts use for each other ([Host names and style notes](SCRIPTS.md#host-names-and-style-notes)) |
| `style_profile_id` | `de_calm_deep` | The speaking-style profile ([Editorial standard](TEACHING.md#editorial-standard)) |
| `export_context` | `private_learning` | The only allowed value; public export stays outside the MVP ([Source rights and privacy](SECURITY.md#source-rights-and-privacy)) |
| `series_goal` | unset | Weights 0 (not wanted) to 3 (main aim) for `understand`, `evaluate` and `apply`, at least one above 0; unset means the evaluating default. Studio: **„Ziel der Serie“** (goal of the series); effect: [Series goal, task aims and source types](RESEARCH.md#series-goal-task-aims-and-source-types) |
| `recency_months` | unset | 1 to 120: prefer practice, tool and benchmark sources published within this many months; unset means no rule. Studio: **„Aktualität der Quellen“** (recency of sources) |
| `runtime` | see below | Operational settings |

Unset, `series_goal` and `recency_months` are left out of `project.yaml`, so an unchanged brief keeps its hash.

`runtime` (`models.RuntimeSettings`):

| Field | Default | Meaning |
| --- | --- | --- |
| `codex_executable` | `codex` | Codex CLI to run; an explicit full path stays authoritative ([Codex CLI](ARCHITECTURE.md#codex-cli)) |
| `codex_model` | `null` | Model passed to Codex when set |
| `text_timeout_seconds` | `1800` | Time limit of one model call; the workspace settings replace it |
| `tts_python` | The Python that runs `pla` | Python of the separate Qwen environment (`pla init --tts-python`) |
| `tts_model` | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` | Qwen model |
| `tts_revision` | `main` | Model revision; `pla init` writes the commit this computer already uses ([Qwen](AUDIO.md#qwen)) |
| `tts_device` | `auto` | `auto`, `cuda:0`, `mps` or `cpu` |
| `tts_attention` | `eager` | `eager` or `sdpa` |
| `tts_timeout_seconds` | `3600` | Time limit of the Qwen worker |
| `seed` | `42` | Seed of the speech synthesis |

Setting up Qwen and changing these values on an existing project: [Set up local Qwen on Windows](OPERATIONS.md#set-up-local-qwen-on-windows).

## Studio settings

The settings page **„Einstellungen“** (settings) holds the values in the table below plus Claude's extra usage and the
OpenRouter key; they apply to all projects (why: D-108). How to use the page: [Settings page](STUDIO.md#settings-page).

**Storage.** The page writes `projects/.studio-settings.json` (`studio_settings`), with the sections
`studio_settings.SECTIONS`. While the file exists, each section replaces the project's own file or field:

| Section | Replaces in each project | Details |
| --- | --- | --- |
| `text` | `studio/text.json` | Preset, reasoning level and the OpenRouter output limit `max_output_tokens` (1 024 to 200 000, default 32 768); [Providers and models](PRODUCT.md#providers-and-models) |
| `audio` | `studio/audio.json` | Provider, speech model, both voices, the pause minimums ([Pause minimums](AUDIO.md#pause-minimums)) and, for Gemini, whether [expression tags](AUDIO.md#expression-tags) are set |
| `execution` | `studio/execution.json` | Sequential or parallel, separately for text work and recording ([Sequential or parallel](STUDIO.md#sequential-or-parallel)) |
| `allowances` | `studio/allowances.json` | Pre-approvals ([Budgets](BUSINESS_LOGIC.md#budgets)) |
| `research_limits` | `research_limits` in `project.yaml` | Model calls, sources, search rounds ([Budgets](BUSINESS_LOGIC.md#budgets)) |
| `text_timeout_seconds` | `runtime.text_timeout_seconds` in `project.yaml` | Time limit of one model call, 300 to 14 400 seconds (5 to 240 minutes) |

The file also records `changed_at`. A save carries the hash of the values the page showed; if the settings changed in
between, it is refused with „Einstellungen inzwischen geändert. Seite neu laden.“ (settings changed in the meantime,
reload the page). Claude's extra usage is not part of this file (see below), and the OpenRouter key is held in memory
only ([Secrets and keys](SECURITY.md#secrets-and-keys)).

**Without the file.** As long as `projects/.studio-settings.json` does not exist, every project keeps its own files
and fields from the table. The page then shows the values of the most recently changed project, and the first save
makes them every project's. This is also what the CLI outside a Studio workspace and the tests see.

**Who reads it.** The CLI reads the limits and the time limit through `storage.load_project` from this file too;
speech, execution and pre-approvals read their sections. Running and paused jobs keep their text model, their
execution mode and approved recordings; limits and the time limit apply on the next resume
([Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)). The conversation partner does
not propose them: a proposal carries only the brief, even when the model answer contains other values
(`studio_worker`).

**Claude extra usage.** If you bought Claude usage beyond the subscription windows, switch on
**„Zusatzkontingent gekauft: gespeicherte Claude-Sperren übergehen“** (extra usage bought: pass over stored Claude
blocks). The switch is stored in the quota store `~/.podcast-automate/subscriptions.json`
(`subscriptions.claude_extra_usage`), not in the workspace file, so it holds for the whole user account. Its effect on
the provider choice is in [Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection)
(why: D-031).

## Environment variables

Variables you can set. What the adapters set or remove for the CLIs they start is in
[Text provider adapters](ARCHITECTURE.md#text-provider-adapters).

| Variable | Effect | Read in |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | OpenRouter key for text, Gemini speech, Jev and the Studio server when no key was entered; handling rules: [Secrets and keys](SECURITY.md#secrets-and-keys) | `openrouter.OpenRouterAdapter`, `speech.py`, `jev.JevClient`, `studio.py`, `logs.scrub` |
| `PLA_SUBSCRIPTIONS_STORE` | Path of the quota store instead of `~/.podcast-automate/subscriptions.json`; the tests set it so they never write to `~/.podcast-automate` | `subscriptions.store_path` |
| `PLA_KEEP_AWAKE` | `0` stops worker processes from keeping the computer awake ([Stopping and resuming](STUDIO.md#stopping-and-resuming)) | `studio_worker.keep_awake` |
| `PLA_UNPAYWALL_EMAIL` | Contact address that enables Unpaywall in the search for a free copy of a work whose own address refused the download | `sources.unpaywall_copies` |
| `PLA_CORE_API_KEY` | Free CORE API key; enables CORE for the same search | `sources.core_copies`, `studio.py` |
| `PLA_CORE_DAILY_LIMIT` | CORE calls per UTC day, default 1000 (`sources.CORE_DAILY_LIMIT`), shared by all research workers ([Pipeline](RESEARCH.md#pipeline)) | `sources.core_usage` |
| `PLA_CORE_USAGE_STORE` | Path of the CORE call counter instead of `~/.podcast-automate/core_usage.json` | `sources.core_usage_path` |
| `HF_HUB_CACHE`, `HF_HOME` | Where `pla init` looks for the Qwen revision: `HF_HUB_CACHE`, else `HF_HOME/hub`, else `~/.cache/huggingface/hub` | `cli.pinned_revision` |
| `PATH` | How `codex`, `claude`, `node`, `ffmpeg` and `ffprobe` are found. The Studio prepends `tools/ffmpeg/bin` and, outside Windows, `~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin` where they exist | `platforms.configure_path` |

## Files outside the project

| Path | Holds | Details |
| --- | --- | --- |
| `~/.podcast-automate/subscriptions.json` | Quota store shared by workers, Studio server and status monitor (last Codex rate-limit snapshot, last Claude login facts and rate-limit event, an active Claude block, short-lived "unusable" notes per subscription) and the Claude extra-usage switch; never credentials. Lock file `.subscriptions.lock` next to it; on Windows under `%USERPROFILE%`; override `PLA_SUBSCRIPTIONS_STORE` | [Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection) |
| `~/.podcast-automate/core_usage.json` | Today's CORE API calls; override `PLA_CORE_USAGE_STORE` | [Environment variables](#environment-variables) |
| `projects/.gemini_throttle.json` | The rate-limit pause shared by all Gemini recordings of the workspace | [Parallel recording and queue](AUDIO.md#parallel-recording-and-queue) |
| `projects/voice-samples/gemini/` | Shared Gemini voice-sample library | [Voice samples](OPERATIONS.md#voice-samples) |
| `.studio/trash/` | Deleted projects, restorable from the overview | [Overview, navigation and hold cards](STUDIO.md#overview-navigation-and-hold-cards) |
| `.venv/`, `.venv-tts/`, `tools/ffmpeg/` | Controller environment, separate Qwen environment, local FFmpeg | [Install on Windows 11](OPERATIONS.md#install-on-windows-11) |

Settings files are listed under [Where settings live](#where-settings-live), the logs under `.studio/` in
[Logs and diagnosis](OPERATIONS.md#logs-and-diagnosis). `.studio/` and `projects/` are relative to the Studio
workspace, the folder `pla studio` runs in (the repository root when started with `Podcast-Studio.cmd`, `.command`
or `.sh`).
