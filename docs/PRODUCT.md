---
title: Product
doc_type: product
status: current
last_reviewed: 2026-10-07
covers:
  - src/podcast_automate/text_settings.py
  - src/podcast_automate/speech.py
  - src/podcast_automate/status_summary.py
  - src/podcast_automate/cli.py
  - src/podcast_automate/claude_code.py
  - src/podcast_automate/research_advisor.py
  - src/podcast_automate/jev.py
---

# Product

## What it is for

Podcast Automate is a personal research-to-podcast system. You give it a topic; the Studio researches it on the
web with evidence, plans a deep-dive series, writes and reviews dialogue scripts and records approved episodes as
MP3. Everything runs locally. Model calls go through your Claude or Codex subscription, through OpenRouter or through
Claude Code on your own Anthropic API key, web searches through the text model's own tools or through Perplexity,
speech through local Qwen or Gemini.

The main case is a personal deep-dive podcast series on a given topic; scope and desired depth decide the number of
episodes and the total duration (why: D-001). How long a series, an episode and a recorded part may be is set in
[Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length).

The user gives a topic or a central question. People, theses, talks, papers, links and own files are optional
starting points. Source research is part of the product; a pre-filled source folder is not required.

By default no subject-matter or mathematical prior knowledge is assumed, and demanding relationships are explained in
everyday language; prior knowledge and level of detail can be adjusted in the topic brief. The full standard is in
[Editorial standard](TEACHING.md#editorial-standard).

### Platforms and version 0.1

The MVP started as a CLI with local project files on Windows 11; today the CLI and the browser Studio run on
Windows, macOS and Linux. The target machine for local speech output has an AMD Radeon RX 9070 XT. Text processing
uses the existing subscriptions (ChatGPT/Codex and Claude Max) through the official CLIs; local speech output and
automatic audio assembly are part of the MVP. Additional paid APIs and manual audio editing are not required.

Version 0.1 implements project management, the browser Studio, question-driven research up to the reviewed dossier,
series planning, teaching plans, dialogue scripts with polishing and reviews, and the recording of approved scripts
with local Qwen or with Gemini through Google or OpenRouter. The [Roadmap](#roadmap) lists what is not built yet, the
[MVP acceptance plan](specs/2026-09-10-mvp-acceptance-plan.md) the acceptance criteria.

### Main use cases

- Machine learning, for example energy-based models, based on concretely researched works by Yann LeCun and Alfredo
  Canziani: foundations, how it works, examples, comparison, state of the evidence and open questions.
- Blood values: terms, relationships and the limits of interpretation; statements of a person the user names are
  placed in context with further subject-matter sources. Who that person is must be verified through a concrete link
  or work before any source is attributed to them.

The examples fix no subject-matter results. A named person is an entry point for research; their statements are
evidenced as theirs and kept apart from other positions and from overarching evidence.

## Capabilities

The Studio leads a project through six steps. Every step can be resumed, and changed inputs force a new run instead
of a silent recomputation ([Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)). The
Studio speaks English or German ([Interface language](STUDIO.md#interface-language)); a series is written and recorded
in the language of its project, and its exports follow that language (why: D-152, D-153). A trial project runs the six
steps once with small limits ([Trial project](BUSINESS_LOGIC.md#trial-project)).

1. **„Auftrag“** (brief): topic, depth, language and the goal of the series are settled in a
   conversation; you can attach your own MD, TXT or DOCX files. Text model, audio provider and voices are set on the
   settings page for all projects ([Studio settings](CONFIGURATION.md#studio-settings)). See
   [The guided flow](STUDIO.md#the-guided-flow).
2. **„Recherche“** (research): live search, retrieval of the original sources, fixed sub-questions with completion
   criteria, independent answer review and an overall review up to an evidenced dossier ([Research](RESEARCH.md#pipeline)).
3. **„Inhaltsverzeichnis“** (table of contents): a source-bound series plan that you read and approve explicitly
   ([Series plan](SCRIPTS.md#series-plan), [Human approvals](BUSINESS_LOGIC.md#human-approvals)).
4. **„Ausarbeitung“** (drafting): per episode a teaching plan, a dialogue draft, dialogue polishing and four separate
   reviews; missing foundations are researched again automatically ([Scripts](SCRIPTS.md#writing-and-framing)).
5. **„Skripte lesen“** (read scripts): you check every episode as text before any audio is made
   ([Review notes on the reading page](STUDIO.md#review-notes-on-the-reading-page)).
6. **„Vertonung“** (recording): only after you approve the script you read; produces MP3 with chapters, transcript
   and show notes ([Recording flow](AUDIO.md#recording-flow)), and on request the companion kit of each episode and
   of the whole podcast with the transcript of every episode ([Downloads](STUDIO.md#downloads)).

Every model answer must satisfy a strict JSON schema and is checked deterministically; the checks and gates are in
[Quality gates](QUALITY.md#quality-gates).

## Providers and models

**Text and audio are chosen independently**, for example Codex writes the script and Gemini records it through
Google. **Codex** is the locally installed Codex CLI with a ChatGPT subscription login, **Claude** the Claude Code
CLI with a claude.ai login (Claude Max subscription). The text models do not run offline on the PC, and neither
subscription bills individual calls. OpenRouter serves text models with structured JSON answers, paid from your
OpenRouter credit. **Claude on the API key** (`claude_api`) is the Claude Code CLI on your own Anthropic API key, for
use without a subscription: every call, web research included, is billed to your Anthropic account, and a run needs a
money limit ([Claude on your own API key](BUSINESS_LOGIC.md#claude-on-your-own-api-key)). Codex on an OpenAI API key
is not offered (why: D-150). ElevenLabs is not connected.

**The web search** runs with the text model's own tools (the default), or through the
[Perplexity Search API](https://docs.perplexity.ai/api-reference/search-post) on your Perplexity key: the run's model
plans the queries, Perplexity runs them at about 0.005 USD per request, and the model chooses among the results. With
Perplexity every text model can research, an OpenRouter model included, so no subscription is needed at all; a run
then needs a money limit ([Research runs and their web search](BUSINESS_LOGIC.md#research-runs-and-their-web-search);
why: D-151). Whether it finds sources as good as a model's own search is not yet evaluated (V-36).

The rules for which provider serves a call are in
[Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection), the adapter mechanics in
[Text provider adapters](ARCHITECTURE.md#text-provider-adapters), the choice in the Studio in
[Choosing the text model](STUDIO.md#choosing-the-text-model).

### Text model presets

The presets the Studio offers (`text_settings.TEXT_PRESETS`):

| Preset (label in the Studio) | Provider | Model ID | Level |
| --- | --- | --- | --- |
| „Automatisch · Claude, sonst Codex“ (`auto_subscriptions`) | Claude, else Codex subscription | `claude-sonnet-5-5` and `gpt-6-astra` | `high` (Claude), `xhigh` (Astra) |
| „Automatisch · Claude, sonst Codex · high“ (`auto_subscriptions_high`) | Claude, else Codex subscription | `claude-sonnet-5-5` and `gpt-6-astra` | `high` (both) |
| „Sonnet 5.5 · Claude-Abo · high“ (`claude_sonnet_sub`) | Claude subscription | `claude-sonnet-5-5` | `high` |
| „Opus 5.5 · Claude-Abo“ (`claude_opus_sub`) | Claude subscription | `claude-opus-5-5` | `xhigh` |
| „Astra · Codex-Abo“ (`codex_astra`) | Codex subscription | `gpt-6-astra` | `xhigh` |
| „Sonnet 5.5 · high · Anthropic-API-Key“ (`claude_sonnet_api`) | Claude on your Anthropic API key, billed | `claude-sonnet-5-5` | `high` |
| „Opus 5.5 · high · Anthropic-API-Key“ (`claude_opus_api`) | Claude on your Anthropic API key, billed | `claude-opus-5-5` | `high` (`xhigh` roughly doubles a billed run) |
| [„Astra · xhigh · OpenRouter“](https://openrouter.ai/openai/gpt-6-astra) (`openrouter_astra`) | OpenRouter | `openai/gpt-6-astra` | `xhigh` |
| [„Opus 5.5 · medium · OpenRouter“](https://openrouter.ai/anthropic/claude-opus-5.5) (`openrouter_opus`) | OpenRouter | `anthropic/claude-opus-5.5` | `medium` |
| [„Sonnet 5.5 · high · OpenRouter“](https://openrouter.ai/anthropic/claude-sonnet-5.5) (`openrouter_sonnet`) | OpenRouter | `anthropic/claude-sonnet-5.5` | `high` |
| [„DeepSeek V4.1 Flash · max · OpenRouter“](https://openrouter.ai/deepseek/deepseek-v4.1-flash) (`openrouter_deepseek`) | OpenRouter | `deepseek/deepseek-v4.1-flash` | `max` |

**„Automatisch“** (automatic) is the Studio's default preset; a saved choice is kept. Other model IDs work only
through the CLI option `--model` ([Commands](#commands)).

### Models and levels per provider

| Provider | Models | Default | Reasoning levels | Source |
| --- | --- | --- | --- | --- |
| Claude subscription or Claude on the API key (Claude Code, `text_settings.CLAUDE_PROVIDERS`) | `claude-sonnet-5-5`, `claude-opus-5-5`, `claude-opus-5` (kept for runs that saved it) | `claude-sonnet-5-5` at `high` (why: D-022); `max` stays an explicit choice | `low`, `medium`, `high`, `xhigh`, `max` | `text_settings.CLAUDE_MODELS`, `DEFAULT_CLAUDE_MODEL`, `DEFAULT_CLAUDE_EFFORT`, `CLAUDE_EFFORTS` |
| Codex subscription (Codex CLI) | `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `gpt-5.5` | `gpt-6-astra` at `xhigh` | `low`, `medium`, `high`, `xhigh` | `text_settings.CODEX_MODELS`, `DEFAULT_CODEX_MODEL`, `DEFAULT_REASONING_EFFORT`, `REASONING_EFFORTS` |
| OpenRouter | `openai/gpt-6-astra`, `anthropic/claude-opus-5.5`, `anthropic/claude-sonnet-5.5`, `deepseek/deepseek-v4.1-flash` (why: D-032) | the model's own default when no level is selected | DeepSeek V4.1 Flash: `low`, `high`, `max`; the other three: `low`, `medium`, `high`, `xhigh`, `max` | `text_settings.OPENROUTER_MODELS`, `OPENROUTER_EFFORTS` |

- **Claude Code versions.** Every Claude call needs Claude Code 2.1.280 or newer (`claude_code.MINIMUM_CLI_VERSION`,
  for Opus 5.5 and `xhigh`), Sonnet 5.5 needs 2.1.284 (`claude_code.MODEL_MINIMUM_CLI`; update with `claude update`).
- **Catalog verification.** Presets and levels were matched against Codex CLI 0.157.1 (2026-09-26) and Claude Code
  2.1.284; the catalog was last verified on 2026-09-29 (`text_settings.CATALOG_VERIFIED_ON`). `pla doctor` reports its
  age and calls it stale after 90 days (`text_settings.CATALOG_STALE_DAYS`). Availability depends on the local
  installation and the account.
- **OpenRouter catalog.** The DeepSeek preset uses `reasoning.effort=max`, not an invented model suffix. The
  [public API catalog](https://openrouter.ai/api/v1/models), checked on 2026-10-03, lists these levels and
  `structured_outputs` and `response_format` for all four models; the Studio still requires matching providers and
  checks every answer locally. An unsupported level is refused, never silently replaced.

### Fixed-purpose models

| Use | Model | Source |
| --- | --- | --- |
| Status brief (**„Kurzbericht“**) during research and drafting, at `low` | `gpt-5.6-luna` for Codex jobs, `claude-haiku-4-5` for Claude subscription jobs, `deepseek/deepseek-v4.1-flash` for OpenRouter jobs; none for jobs on the Anthropic API key (why: D-149) | `status_summary.STATUS_MODELS`, [Progress and telemetry](STUDIO.md#progress-and-telemetry) |
| Advisor for blocked sub-questions | `claude-opus-5-5` at `xhigh` when the run uses the fixed Claude subscription; other selections, Claude on the API key included, keep their own | `research_advisor.ADVISOR_MODEL`, `ADVISOR_EFFORT`, [Blocked sub-questions and decisions](RESEARCH.md#blocked-sub-questions-and-decisions) |
| Gap probe with Jev | `typesafe/jev-1.13` via OpenRouter | `jev.JEV_MODEL`, [Gap probe](RESEARCH.md#gap-probe) |

### Speech

| Provider | Models | Voices | Source |
| --- | --- | --- | --- |
| Local Qwen3-TTS (tested on Windows with an AMD GPU) | `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` (default of `runtime.tts_model`) | 9 voices: Aiden, Vivian, Ryan, Serena, Uncle_Fu, Ono_Anna, Sohee, Eric, Dylan; Studio default Aiden and Vivian | `models.RuntimeSettings.tts_model`, `speech.QWEN_VOICES`, `speech.audio_catalog` |
| Gemini TTS via Google (the Gemini default since 2026-10-06, why: D-133) | `gemini-3.8-flash-tts` (Google's id of `speech.GEMINI_MODEL`); the Lite model as on OpenRouter, unverified on Google | the same 30 voices; Studio default Erinome and Sadachbia, swapping roles from episode to episode; a style per role (`speech.STYLE_PRESETS`) | `google_speech.py`, `speech.audio_catalog` |
| Gemini TTS via OpenRouter | `google/gemini-3.8-flash-tts` (default, `speech.GEMINI_MODEL`); `google/gemini-3.8-flash-lite-tts` as the alternative on the settings page (`speech.GEMINI_MODELS`) | 30 voices valid for both models, checked against OpenRouter's models API on 2026-09-26 (`speech.VOICES_VERIFIED_ON`); Studio default Sadaltager and Aoede; shared [voice-sample library](OPERATIONS.md#voice-samples) | `speech.GEMINI_VOICES`, `speech.audio_catalog` |

The Gemini speech model is chosen with the audio provider and needs no manual model ID. With Gemini the GPU
installation is not needed: the connection check then checks the stored key of the route (Google or OpenRouter) and
skips the local Qwen check. A stored key is not yet a successful API voice test. Google's voice library has further
voices (215 for English, 87 for German, read on 2026-10-06); they are not offered yet. Recording with each provider:
[Qwen](AUDIO.md#qwen), [Gemini via Google](AUDIO.md#gemini-via-google), [Gemini via OpenRouter](AUDIO.md#gemini-via-openrouter).

## Commands

The CLI `pla` runs the same pipeline as the Studio. On Windows call it as `.\.venv\Scripts\pla.exe …`, on macOS and
Linux as `.venv/bin/pla …`. Every command except `studio` accepts `--json`; `pla --version` prints the version. The CLI
is English: its help, its prints and its own messages; stop codes and `--json` keys are the same as before, and the
messages of the pipeline modules that `pla status`, `pla quota` and `pla approve` print keep their German wording
(why: D-156).

| Command | Purpose | Main options |
| --- | --- | --- |
| `pla studio [<workspace>]` | Open the guided Studio locally in the browser (`http://127.0.0.1:8765`) | `--port 8765`, `--no-browser`, `--lan` (on Windows `Podcast-Studio-WLAN.cmd`) for a phone in the home network; [Starting the Studio](STUDIO.md#starting-the-studio) |
| `pla init <project> --topic "…"` | Create a project with a validated brief; records the Qwen revision this computer already uses | `--total-minutes <minutes>` (optional planning wish, see [Project brief](CONFIGURATION.md#project-brief)), `--tts-python` (Python of the separate Qwen environment), `--trial` (a [trial project](BUSINESS_LOGIC.md#trial-project): one episode of at most 20 minutes, a research plan of at most three sub-questions and small limits per run; only with it may `--topic` be left out, for a narrow sample topic; why: D-157) |
| `pla doctor [<project>]` | Check installation, Codex and Claude login, subscription quota, Claude on the API key (`ANTHROPIC_API_KEY` set and Claude Code recent enough), the Perplexity search (`PERPLEXITY_API_KEY` set; informational), TTS environment and catalog age; no model call | `--skip-tts` checks without local Qwen |
| `pla quota` | Show the quota of both subscriptions without a model call | |
| `pla research <project>` | Run kind `research`: live search, source import, dossier and source review up to the reviewed dossier | `--backend codex_cli\|claude_code\|claude_api\|auto`, or `openrouter` with `--web-search perplexity`, `--web-search model\|perplexity` (web search of a new run; the Perplexity key from `PERPLEXITY_API_KEY`), `--api-key` (hidden prompt for the Anthropic key of `claude_api` or the OpenRouter key, else `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`), `--model`, `--reasoning-effort`, `--approve-plan`, `--reuse-sources RUN_ID`, `--seed-corpus RUN_ID`; [Starting and resuming](RESEARCH.md#starting-and-resuming) |
| `pla script <project>` | Run kind `script`: compact knowledge model, series plan, teaching plans and reviewed dialogue scripts for reading | `--episode ep_001` (bring one episode forward; without it all planned scripts are written), `--revise EPISODE_ID` with `--feedback`, `--backend codex_cli\|openrouter\|claude_code\|claude_api\|auto`, `--model`, `--reasoning-effort`, `--api-key` (hidden prompt for the OpenRouter or Anthropic key), `--max-output-tokens`, `--jev-probe`, `--web-search model\|perplexity` (supplementary research of a new run; the Perplexity key from `PERPLEXITY_API_KEY`); [Running a script job](SCRIPTS.md#running-a-script-job) |
| `pla series-review <project>` | Run kind `series_review`: review the scripts of a published script run as a series, without changing that run | `--run <run_id>` (default: the last published), `--backend codex_cli\|claude_code\|claude_api\|auto` (`claude_api` takes its key from `ANTHROPIC_API_KEY`), `--model`, `--reasoning-effort`; [Series review](SCRIPTS.md#series-review) |
| `pla audio <project> --episode ep_001 --approve-audio` | Run kind `episode_audio`: record one read and approved episode with local Qwen, assemble it and export MP3, chapters, transcript and show notes. Gemini via OpenRouter is chosen in the Studio | `--approval-note` |
| `pla publish-kit <project> --episode ep_001` | Companion kit of one published episode for uploading it to a podcast platform: a short description, an episode description of at most 4,000 characters with chapter marks (`00:00 Title`) and sources appended, and the source list. One call of the script run's text model writes the two descriptions and is reused while the script is unchanged; chapters and sources are built from the recording and the research without a model. Writes `publish/` next to the recording, or `episodes/<ep>/publish/` without one; publishes nothing | `--fresh` asks for new descriptions; an OpenRouter script run takes its key from `OPENROUTER_API_KEY`, a `claude_api` one from `ANTHROPIC_API_KEY` |
| `pla publish-kit <project> --podcast` | Companion kit of the whole podcast: a short description and a description of the podcast for Spotify and Apple Podcasts, the sources of every episode in one list, and the transcript of every published episode with a heading per chapter and its measured start. One call of the newest script run's text model writes the two descriptions from the episodes' plans and chapter titles and is reused while they are unchanged; transcript and sources are built without a model. Writes `publish/` in the project; publishes nothing | `--fresh`; the keys as for `--episode` |
| `pla status <project>` | Progress, finished episodes, concrete pause or failure reasons and failure logs | `--run-id` |
| `pla resume <project>` | Continue the last interrupted run with unchanged valid results | `--run-id`, `--approve-audio`, `--approval-note`, and the text options of `script` |
| `pla approve <project>` | Explicit approval for one run (options below) | `--run-id` (default: the project's last run) |
| `pla text-probe <project>` | Structured subscription connection probe; writes to `probes/text/<run_id>/` | `--backend codex_cli\|claude_code\|auto` (default `codex_cli`) |
| `pla audio-probe <project> --approve-audio` | Assemble a German or English Qwen voice sample; writes to `probes/audio/<run_id>/` | |
| `pla schemas <folder>` | Export all 21 implemented data contracts as JSON Schema ([Data contracts](ARCHITECTURE.md#data-contracts)) | |

`pla approve` options; each takes effect on the next call of the run or with `pla resume`:

| Option | Effect | Details |
| --- | --- | --- |
| `--research-plan [RUN_ID]`, `--max-tasks N` | Approve the waiting research plan; with `--max-tasks` the plan is cut once to at most N sub-questions and presented again | [Scope check and plan approval](RESEARCH.md#scope-check-and-plan-approval) |
| `--model-calls N`, `--search-rounds N`, `--sources N` | New limit for model calls, web search rounds or fetched sources of this run | [Budgets](BUSINESS_LOGIC.md#budgets) |
| `--cost-usd N` | Set or raise the money limit in USD of a run billed to a key (`claude_api`, OpenRouter, the Perplexity search); it can only rise | [Money limit](BUSINESS_LOGIC.md#money-limit) |
| `--accept-gap TASK_ID [--reason "…"]`, `--retry TASK_ID [--hint "…"]`, `--access-gap TASK_ID CRITERION --blocked-source URL`, `--dispute OBJECTION_ID SIDE` (`SIDE` is `reviewer` or `objection`), `--finish-with-residuals`, `--rebuild-dossier` | Decisions on blocked sub-questions and on the overall review | [Blocked sub-questions and decisions](RESEARCH.md#blocked-sub-questions-and-decisions) |
| `--fresh-attempts` | Repeat steps that used up their repair attempts with fresh attempts on the next resume; the rejected answers stay readable | [Stopping and resuming](STUDIO.md#stopping-and-resuming) |
| `--redesign-teaching EPISODE_ID --hint "…"` | Redesign the stopped teaching plan of this episode on the next resume; the hint is binding | [Overview, navigation and hold cards](STUDIO.md#overview-navigation-and-hold-cards) |
| `--text-switch [claude\|astra\|claude-only\|astra-only\|openrouter\|claude-api]`, `--switch-model` | Continue a script or research job with another text provider from the next resume; `--switch-model` names the OpenRouter model or, for `claude-api`, a Claude model ID; a billed provider needs `--cost-usd` unless the run has a money limit | [Switching a job to another provider](BUSINESS_LOGIC.md#switching-a-job-to-another-provider) |

There is no overall `run` command; the Studio runs the run kinds one after the other (why: D-002). Their order and
preconditions: [Run kinds and order](BUSINESS_LOGIC.md#run-kinds-and-order).

## Out of scope

- **Learning features.** Tutor mode, quizzes, flashcards, exam mode, adaptive learning diagnostics and spaced
  repetition are outside the MVP. They create no required fields, prompt stages, exports or acceptance criteria for
  deep-dive series. Tutor learning goals, Bloom levels and quiz data are not required parts of the knowledge model.
- **Later options.** A web app, team features and automatic publishing.
- **Audio extras.** Music, elaborate sound design and a third host are not prerequisites for the first MVP.
- **What the system does not do.** Human subject-matter and listening acceptance (your part, see
  [Acceptance procedure](QUALITY.md#acceptance-procedure)), public rights clearance of individual sources, and a
  general anonymisation of personal data. Public export stays outside the MVP
  ([Source rights and privacy](SECURITY.md#source-rights-and-privacy)).

## Roadmap

Target scope that version 0.1 does not implement yet:

- **Individual source rights and export blocks.** The full rights contract of
  [SourceDocument](ARCHITECTURE.md#sourcedocument) with adjustable states including `restricted` and `no_export`, and
  source-specific export blocks with a binding check before script, audio and show-notes export; no public export of
  sources with unclear rights and no direct quotes from sources marked `restricted`. This is the planned `rights_check`
  gate ([Quality gates](QUALITY.md#quality-gates)); the current rule is in
  [Source rights and privacy](SECURITY.md#source-rights-and-privacy).
- **Redaction of personal data.** An explicitly configured redaction stage for personal content before model calls.
  A general detection and removal of personal data is not implemented.
- **Spoken AI disclosure.** The transparency note in the exports is done: since 2026-10-07 the show notes, the
  listening sheet, the companion kit's description and the podcast kit's description and transcript end with this
  note and an AI notice, in the project's language,
  and the MP3s carry AI tags ([AI marking](AUDIO.md#ai-marking); why: D-154). A disclosure spoken in the audio itself
  stays open, because it would change approved audio. The note (German original and English version):

  > Dieser Output ist eine quellengebundene Synthese. Er ersetzt keine fachliche, rechtliche, medizinische oder
  > wissenschaftliche Begutachtung. Unsichere oder widersprüchliche Quellenlagen werden markiert.

  > This output is a source-bound synthesis. It does not replace a subject-matter, legal, medical or scientific
  > assessment. Uncertain or contradictory source situations are marked.

- **Back-transcription with a recogniser.** A supplementary local back-transcription to detect omissions and
  repetitions is prepared as a deterministic comparison (`transcription_check.py`) but connected to no recogniser
  yet; it would not guarantee error-free pronunciation. See
  [Take checks, pauses and loudness](AUDIO.md#take-checks-pauses-and-loudness).
- **Render-time and storage estimate before production.** Before production, scope and, as far as the pilot allows,
  render time and storage needs are to be shown. Today the Studio shows only the characters of the spoken text and
  the estimated minutes before a recording.
- **Full data contracts.** The parts that [Data contracts](ARCHITECTURE.md#data-contracts) marks as target scope:
  differentiated confidence and evidence ratings in the knowledge model, the series coverage matrix, and `mode`,
  style profile, time budget, speaker roles, scene target time and transition in the episode plan.
- **Argument map.** `research/argument_map.md` (arguments and their connections) is a required artefact of the target
  scope but is not produced.
