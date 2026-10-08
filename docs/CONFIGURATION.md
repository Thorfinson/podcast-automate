---
title: Configuration
doc_type: configuration
status: current
last_reviewed: 2026-10-07
covers:
  - src/podcast_automate/studio_settings.py
  - src/podcast_automate/studio_text.py
  - src/podcast_automate/models.py
  - src/podcast_automate/storage.py
  - src/podcast_automate/trial.py
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
| `<project>/version.json` | One project | Its version number, the podcast's first version, the version it came from and its starting library ([New versions](STUDIO.md#new-versions)); absent in a first version | `pla new-version`, the Studio's „Neue Version anlegen“ |
| `projects/.studio-settings.json` | Every project of a Studio workspace | Text model, web search, audio, execution modes, pre-approvals, research limits and the time limit of one model call ([Studio settings](#studio-settings)) | The Studio's settings page only |
| `<project>/studio/text.json`, `audio.json`, `execution.json`, `allowances.json` | One project | The same choices per project; used only while the workspace file does not exist | The Studio |
| `<project>/studio/jev_probe.json` | One project | Whether new script runs also ask Jev in the gap probe; stays per project even with workspace settings ([Gap probe](RESEARCH.md#gap-probe)) | The Studio switch |
| `<project>/studio/spoken_forms.json`, `<project>/style_notes.md` | One project | Spoken forms ([Spoken forms and pronunciation](AUDIO.md#spoken-forms-and-pronunciation)) and editorial notes ([Host names and style notes](SCRIPTS.md#host-names-and-style-notes)) | The Studio |
| `~/.podcast-automate/subscriptions.json` | The user account, all workspaces | Quota store and the Claude extra-usage switch ([Files outside the project](#files-outside-the-project)) | Adapters, Studio, `pla quota` |
| `.studio/tts-runtime.json` | One workspace | The Qwen runtime (`tts_python`, model, pinned revision, device) that new Studio projects take over ([Set up local Qwen on Windows](OPERATIONS.md#set-up-local-qwen-on-windows)) | `scripts/setup-qwen.py` |
| `.studio/ui.json` | One workspace | The Studio's interface language `ui_language`: `auto`, `de` or `en` ([Interface language](#interface-language)) | The Studio |
| Environment variables | The running process | Keys, store locations and switches ([Environment variables](#environment-variables)) | You |
| CLI options | One run | Backend, model, reasoning level, OpenRouter output limit and web search of a run ([Commands](PRODUCT.md#commands)) | You |

## Project brief

`project.yaml` holds one `TopicBrief` (`models.TopicBrief`), validated strictly: unknown fields are refused
(`models.Contract`). `pla init <project> --topic "…"` creates it with these defaults; `pla init <project> --trial`
creates a [trial project](BUSINESS_LOGIC.md#trial-project), where `--topic` is optional:

| Field | Default | Meaning |
| --- | --- | --- |
| `schema_version` | `"1.0"` | Version of the contract |
| `topic`, `central_question` | required, `""` | Topic and guiding question of the series |
| `language` | `de-DE` | Language of the series; `de-DE` or `en-US` |
| `audience_level`, `prior_knowledge` | „Neugierige Erwachsene ohne spezielles Vorwissen; fachlich anspruchsvoll und auf Augenhöhe“ (curious adults without special prior knowledge; demanding and on equal footing), `""` | Level and the foundations already known |
| `depth_request` | A default text that asks for depth built from the ground up, one worked example, no formulas and no repeated definitions | Desired depth and explanatory focus; independent of listening time |
| `focus_questions`, `excluded_topics` | `[]` | Desired focus and boundaries |
| `seed_people`, `seed_urls`, `local_sources` | `[]` | Optional research entry points. People need an evidenced attribution to a source; local sources must lie inside the project folder ([Local files](SECURITY.md#local-files)) |
| `target_total_minutes` | `null` | Optional, explicit planning wish (`pla init --total-minutes`, no default); no implicit total limit ([Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length)). A trial project has at most 20 (`trial.TRIAL_MINUTES`; unset means 20) |
| `max_episode_minutes` | `30`, fixed | Unused since 2026-10-04 (one episode is one MP3); kept for the project hash of existing runs: [Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length) |
| `research_limits` | `search_rounds`, `sources`, `model_calls`; `cost_usd` unset | Limits per run, not on the series' scope; defaults and what happens at a limit: [Budgets](BUSINESS_LOGIC.md#budgets). `cost_usd` is the money limit in USD per run (above 0, at most 100 000; no default), required for a text model billed to a key ([Money limit](BUSINESS_LOGIC.md#money-limit)). Operational like `runtime`; the workspace settings replace it, and a trial project caps each limit ([Trial project](BUSINESS_LOGIC.md#trial-project)) |
| `text_backend` | `codex_cli` | CLI default text adapter: `codex_cli`, `claude_code` or `auto` (why: D-014); the Studio's choice is in the [Studio settings](#studio-settings) |
| `tts_backend` | `qwen3_local` | CLI default speech adapter; the Studio's audio choice is in the [Studio settings](#studio-settings) |
| `voice_profile` | `{"host_a": "Ryan", "host_b": "Serena"}` | The persistent Qwen voices of the two hosts; two different voices are required |
| `host_names` | `null` | Optional names the hosts use for each other ([Host names and style notes](SCRIPTS.md#host-names-and-style-notes)) |
| `style_profile_id` | `de_calm_deep` | The speaking-style profile ([Editorial standard](TEACHING.md#editorial-standard)) |
| `export_context` | `private_learning` | The only allowed value; public export stays outside the MVP ([Source rights and privacy](SECURITY.md#source-rights-and-privacy)) |
| `series_goal` | unset | Weights 0 (not wanted) to 3 (main aim) for `understand`, `evaluate` and `apply`, at least one above 0; unset means the evaluating default. Studio: **„Ziel der Serie“** (goal of the series); effect: [Series goal, task aims and source types](RESEARCH.md#series-goal-task-aims-and-source-types) |
| `recency_months` | unset | 1 to 120: prefer practice, tool and benchmark sources published within this many months; unset means no rule. Studio: **„Aktualität der Quellen“** (recency of sources) |
| `trial` | `false` | A trial project: small limits per run, one short episode ([Trial project](BUSINESS_LOGIC.md#trial-project); why: D-157). Strictly a boolean; set by `pla init --trial` or the Studio (`POST /api/projects` with `"trial": true`) |
| `runtime` | see below | Operational settings |

Unset, `series_goal`, `recency_months` and `research_limits.cost_usd` are left out of `project.yaml` and every run
snapshot (`models.LaterFields`), and so is `trial` while it is `false` (`TopicBrief.omit_unset_later_fields`), so an
unchanged brief keeps its hash.

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
OpenRouter, Google, Anthropic and Perplexity keys; they apply to all projects (why: D-108). How to use the page: [Settings page](STUDIO.md#settings-page).

**Storage.** The page writes `projects/.studio-settings.json` (`studio_settings`), with the sections
`studio_settings.SECTIONS`. While the file exists, each section replaces the project's own file or field:

| Section | Replaces in each project | Details |
| --- | --- | --- |
| `text` | `studio/text.json` | Preset, reasoning level and the OpenRouter output limit `max_output_tokens` (1 024 to 200 000, default 32 768); [Providers and models](PRODUCT.md#providers-and-models) |
| `audio` | `studio/audio.json` | Provider, speech model, both voices, the pause minimums ([Pause minimums](AUDIO.md#pause-minimums)), for Gemini whether [expression tags](AUDIO.md#expression-tags) are set, and for Gemini through Google each role's style (`styles`) and whether the roles swap from episode to episode (`alternate_roles`; [Style and roles](AUDIO.md#style-and-roles)) |
| `execution` | `studio/execution.json` | Sequential or parallel, separately for text work and recording ([Sequential or parallel](STUDIO.md#sequential-or-parallel)) |
| `allowances` | `studio/allowances.json` | Pre-approvals ([Budgets](BUSINESS_LOGIC.md#budgets)) |
| `research_limits` | `research_limits` in `project.yaml` | Model calls, sources, search rounds and the money limit `cost_usd`; a billed text model is refused without it ([Budgets](BUSINESS_LOGIC.md#budgets)) |
| `text_timeout_seconds` | `runtime.text_timeout_seconds` in `project.yaml` | Time limit of one model call, 300 to 14 400 seconds (5 to 240 minutes) |
| `web_search` | nothing; without it new runs search through the text model | How new research and script runs search the web: `model` (the text model's own tools, the default) or `perplexity` (the Perplexity Search API; `studio_settings.WEB_SEARCH`, read by `studio_settings.web_search`); `perplexity` is refused without the money limit `cost_usd` (`cost_limit_required`). Rules: [Research runs and their web search](BUSINESS_LOGIC.md#research-runs-and-their-web-search) |

The file also records `changed_at`. A save carries the hash of the values the page showed; if the settings changed in
between, it is refused with „Einstellungen inzwischen geändert. Seite neu laden.“ (settings changed in the meantime,
reload the page). Claude's extra usage is not part of this file (see below), and the OpenRouter, Google, Anthropic,
Perplexity and CORE keys are kept in the operating system's credential store
([Keys in the credential store](SECURITY.md#keys-in-the-credential-store)).

**Without the file.** As long as `projects/.studio-settings.json` does not exist, every project keeps its own files
and fields from the table. The page then shows the values of the most recently changed project, and the first save
makes them every project's; a trial project shows its own limits there, not its trial caps
(`storage.load_project(root, trial_caps=False)`), so a trial changed last does not make its small limits every
project's. A new workspace, without this file and without a project, shows the pre-approvals its first project gets
([Pre-approvals](BUSINESS_LOGIC.md#pre-approvals)). This is also what the CLI outside a Studio workspace and the tests
see.

**Who reads it.** The CLI reads the limits and the time limit through `storage.load_project` from this file too, and
`load_project` caps them for a trial project ([Trial project](BUSINESS_LOGIC.md#trial-project)); speech, execution and
pre-approvals read their sections. Running and paused jobs keep their text model, their web
search, their execution mode and approved recordings; limits and the time limit apply on the next resume
([Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)). The conversation partner does
not propose them: a proposal carries only the brief, even when the model answer contains other values
(`studio_worker`).

**Claude extra usage.** If you bought Claude usage beyond the subscription windows, switch on
**„Zusatzkontingent gekauft: gespeicherte Claude-Sperren übergehen“** (extra usage bought: pass over stored Claude
blocks). The switch is stored in the quota store `~/.podcast-automate/subscriptions.json`
(`subscriptions.claude_extra_usage`), not in the workspace file, so it holds for the whole user account. Its effect on
the provider choice is in [Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection)
(why: D-031).

## Interface language

The Studio shows its pages and its own messages in German or English (why: D-152; which texts follow it:
[Languages](ARCHITECTURE.md#languages)). The choice is stored in `.studio/ui.json` as `{"ui_language": …}`
(`studio_text.SETTING_FILE`), not in `projects/.studio-settings.json`, whose mere existence switches every project to
the workspace settings. Changing it (`POST /api/ui-language`) is no settings save: it is outside the settings page's
draft and its hash.

| Value | Language |
| --- | --- |
| `auto` | Follows the browser's `Accept-Language`, q-values included: German or English, whichever ranks higher; English when the header names neither, German for a request without the header (`studio_text.resolve`) |
| `de`, `en` | Always German or always English |

- Without `ui.json`, a workspace that already has projects stays German (`de`), so its users do not flip to their
  browser's language; an empty workspace counts as `auto`, and creating its first project writes `auto`
  (`studio_text.remember_auto`).
- The interface language changes no project file and no export: exports follow the project's content language
  (`language` in the [Project brief](#project-brief); why: D-153).

## Environment variables

Variables you can set. What the adapters set or remove for the CLIs they start is in
[Text provider adapters](ARCHITECTURE.md#text-provider-adapters).

| Variable | Effect | Read in |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | OpenRouter key for text, Gemini speech through OpenRouter, Jev and the Studio server when no key was entered; handling rules: [Secrets and keys](SECURITY.md#secrets-and-keys) | `openrouter.OpenRouterAdapter`, `speech.py`, `jev.JevClient`, `studio.py`, `logs.scrub` |
| `GEMINI_API_KEY` | Google key for Gemini speech through Google and the conversation samples, when none was entered in the Studio; handling rules: [Google key in the Studio](SECURITY.md#google-key-in-the-studio) | `google_speech.GoogleSpeech`, `studio.py`, `logs.scrub` |
| `ANTHROPIC_API_KEY` | Anthropic key for Claude on your API key (`claude_api`), when none was entered in the Studio or after `--api-key`; checked by `pla doctor` (`claude_api`). Never handed to a subscription call; handling rules: [Anthropic key in the Studio](SECURITY.md#anthropic-key-in-the-studio) | `claude_code.ClaudeCodeAdapter`, `doctor.claude_api_check`, `studio.py`, `logs.scrub` |
| `PERPLEXITY_API_KEY` | Perplexity key for the web search through Perplexity, when none was entered in the Studio; the only source of the key for `pla research` and `pla script` with `--web-search perplexity`; reported by `pla doctor` (`perplexity_search`). Handling rules: [Perplexity key in the Studio](SECURITY.md#perplexity-key-in-the-studio) | `web_search.PerplexitySearch`, `doctor.inspect`, `studio.py`, `logs.scrub` |
| `PLA_SUBSCRIPTIONS_STORE` | Path of the quota store instead of `~/.podcast-automate/subscriptions.json`; the tests set it so they never write to `~/.podcast-automate` | `subscriptions.store_path` |
| `PLA_KEEP_AWAKE` | `0` stops worker processes from keeping the computer awake ([Stopping and resuming](STUDIO.md#stopping-and-resuming)) | `studio_worker.keep_awake` |
| `PLA_UNPAYWALL_EMAIL` | Contact address that enables Unpaywall in the search for a free copy of a work whose own address refused the download | `sources.unpaywall_copies` |
| `PLA_CORE_API_KEY` | Free CORE API key; enables CORE for the same search when none was entered in the Studio ([CORE key in the Studio](SECURITY.md#core-key-in-the-studio)) | `sources.core_copies`, `studio.py` |
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
