---
title: Architecture
doc_type: architecture
status: current
last_reviewed: 2026-10-06
covers:
  - src/podcast_automate/claude_code.py
  - src/podcast_automate/codex.py
  - src/podcast_automate/codex_stream.py
  - src/podcast_automate/openrouter.py
  - src/podcast_automate/provider_pool.py
  - src/podcast_automate/call_activity.py
  - src/podcast_automate/process.py
  - src/podcast_automate/subscriptions.py
  - src/podcast_automate/models.py
  - src/podcast_automate/research_models.py
  - src/podcast_automate/script_models.py
  - src/podcast_automate/storage.py
  - src/podcast_automate/runner.py
  - src/podcast_automate/prompts.py
  - src/podcast_automate/prompts/
---

# Architecture

## Overview

```
Browser (web/app.js)
   │  HTTP, local only
   ▼
Studio server (pla studio · studio.py) ──── scheduler thread (resume, pre-approvals, audio queue, restart)
   │  one process per job
   ▼
Worker processes (studio_worker.py)            CLI commands (pla … · cli.py) run the same code in-process
   │
   ▼
Pipeline stages per run kind (runner.execute_stages)
   research → script → [series_review] → episode_audio
   │                                         │
   ▼                                         ▼
Text adapters (provider_pool.AdapterPool)    Speech: Qwen worker (.venv-tts), Gemini via Google
   claude_code · codex · openrouter            (google_speech) or via OpenRouter (speech),
                                               then FFmpeg/ffprobe assembly
   │                                         │
   ▼                                         ▼
Project folder projects/<project>/  (project.yaml, studio/, research/, models/, episodes/, runs/, exports/, …)
```

The CLI and the Studio run one pipeline. Each piece of work is a run of one run kind with explicit, versioned stages,
recorded in a run manifest and resumable stage by stage. All state lives in files in the project folder or next to it;
there is no database and no external host. The `KnowledgeModel` is the shared content basis of the whole series.

## Pipeline stages and run kinds

| Stage | Task | Main output |
| --- | --- | --- |
| Topic planning | Narrow down the guiding question, record prior knowledge and desired depth | Topic brief and research plan |
| Research | Find sources, attribute people and works, detect gaps | Source candidates |
| Ingestion | Normalise, segment and de-duplicate sources and make them referenceable | Source index |
| Analysis and synthesis | Connect statements, evidence, prerequisites and counter-positions | Knowledge model and briefing |
| Series planning | Derive episodes from the path of understanding and topic coverage and estimate their duration | Series plan |
| Episode planning and script | Work out each episode thoroughly with the matching context | Episode plan, script, show notes |
| Quality review | Review single episodes and how they connect | Quality report |
| Audio and export | Speak, check and package approved scripts | MP3 episodes and companion files |

These stages are grouped into run kinds (`models.RunManifest.kind`). Three build on each other per project; a series
review and two probes complement them:

| Run kind | Command | Stages in the manifest | Covers |
| --- | --- | --- | --- |
| `research` | `pla research` | `discovery`, `retrieval`, `dossier`, `review`, `completeness`, `publish` | Topic planning, research, ingestion, synthesis up to the reviewed dossier |
| `script` | `pla script` | `planning`, `teaching`, `writing`, `polishing`, `review`, `publish` | Compact knowledge model, series plan, teaching plans, scripts and their reviews |
| `series_review` | `pla series-review` | `review` | Review of a published script run as a series |
| `episode_audio` | `pla audio` | `expression`, `synthesis`, `assembly`, `publish` | Recording, assembly, checks and export of one episode |
| `text_probe` | `pla text-probe` | `codex_probe` | Technical text probe |
| `audio_probe` | `pla audio-probe` | `synthesis`, `assembly` | Technical Qwen probe |

The original sketch with separate commands `ingest`, `model`, `plan`, `check`, `render`, `export` and `run` was
replaced by these run kinds: source import and knowledge model belong to `research` and `script`, the checks run
inside the stages, and rendering and export together form `episode_audio` (why: D-002). See
[Commands](PRODUCT.md#commands) for the commands and [Run kinds and order](BUSINESS_LOGIC.md#run-kinds-and-order) for
when each run kind may start.

**Context per episode.** A long series is never requested in one complete script call. Each episode receives its
relevant source sections, the series plan, the knowledge-model entries it needs and an overview of what has already
been explained and which questions are still open.

**Supplementary research** is possible when planning or writing reveals a concrete gap. New subject-matter content is
first anchored in the knowledge model; affected plans, scripts and quality reports are then reviewed again. Failed
stages stay resumable.

**Determinism.** The flow, the schema checks and the reuse of saved results are deterministic. New model calls are
not promised to be reproducible word for word. A saved run must stay traceable from its frozen inputs and results and
must be exportable again.

## Processes

| Process | Started by | What it does |
| --- | --- | --- |
| Studio server | `pla studio` (`Podcast-Studio.cmd`, `.command`, `.sh`) | Serves the browser page and the local HTTP API (`studio.py`). One server per workspace: it holds the lock in `.studio/`. |
| Scheduler | A thread of the Studio server | Every `studio.SCHEDULER_INTERVAL_SECONDS` (30 s): resumes due runs, applies pre-approvals, starts queued recordings, restarts the server once nothing runs ([Stopping and resuming](STUDIO.md#stopping-and-resuming)). |
| Worker process | The Studio server, one per job: `python -m podcast_automate.studio_worker <project>` | Runs one job (conversation, research, script, recording, voice samples) from a request on stdin and keeps the computer awake while it runs. |
| Status monitor | A worker of a research or script job: `python -m podcast_automate.status_summary` | Writes the status briefs under `runs/<run_id>/status_reports/` ([Progress and telemetry](STUDIO.md#progress-and-telemetry)). |
| Model CLI | A text adapter, per call | `claude -p …` or an ephemeral `codex app-server` ([Text provider adapters](#text-provider-adapters)); stopped with its whole process tree when the job is stopped. |
| PDF parser | Source import: `python -m podcast_automate.pdf_text` | Parses one PDF in its own process, so a hung or crashing parse is one unreadable source. |
| Qwen worker | Audio stage: `qwen_worker.py` run with `runtime.tts_python` | Synthesises the segments in the separate PyTorch environment, usually `.venv-tts` ([Qwen](AUDIO.md#qwen)). |
| FFmpeg / ffprobe | Assembly | Mixing, measuring and encoding; found under `tools/ffmpeg/bin` or on `PATH`. |

Where each process logs is in [Logs and diagnosis](OPERATIONS.md#logs-and-diagnosis); how many jobs, episodes and
sub-questions run at once is in [Sequential or parallel](STUDIO.md#sequential-or-parallel).

## Text provider adapters

Codex CLI and Claude Code use their existing subscription logins (ChatGPT; claude.ai with Claude Max); OpenRouter is
the API alternative. Per provider a small adapter with the same contract wraps requests, structured outputs,
validation, available usage metadata and errors:

```python
structured(prompt, output_type, directory, *, prompt_version, search=False) -> (output, metadata)
```

- **Strict schema.** The output contract becomes a strict JSON schema (`openrouter.strict_schema`: every property
  required, `additionalProperties: false`, no defaults), and the answer is validated against the Pydantic contract. A
  contract violation ends as `rejected_output`, a missing or unreadable answer as `invalid_model_output`; the files
  are listed under [Per-call records](#per-call-records).
- **Official logins only.** The application uses the official CLI logins and implements no access of its own with
  extracted session tokens; `codex.subscription_environment` removes the API-key variables from the CLIs' environment
  ([Secrets and keys](SECURITY.md#secrets-and-keys)). Model and CLI version are recorded per call.
- **Search needs real tool events.** Search, retrieval and text extraction need real tool connections: the search tools
  of the chosen CLI backend, whose availability is checked by a real fetch. A model may not present sources it did not
  fetch as read evidence; inaccessible texts stay source candidates or documented gaps. Each subscription adapter
  writes the observed tool events to `search_events.json`; a research call without them is refused with
  `search_not_observed`. The OpenRouter adapter does not search.
- **Time limits.** Each call has the absolute limit `runtime.text_timeout_seconds` (default 1800 s, see
  [Project brief](CONFIGURATION.md#project-brief)) and is stopped as hung after `process.STALL_TIMEOUT_SECONDS` (600 s)
  without output (`stall`); a hung call is not charged.

### Adapter pool

`provider_pool.AdapterPool` builds the adapter for every call of a run from the run's saved text choice. It applies
the provider rule ([Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection)), lowers
the level of capped stages (`text_settings.stage_effort`) and repeats a call once on the same provider after a stalled
stream (`stall`) or a Claude answer in the wrong format (`claude_structured_output`; `provider_pool.REPEATED_ONCE`).
Each decision leaves a receipt in the call folder ([Per-call records](#per-call-records)). A saved run binds the
adapter contract it started with (`openrouter.ADAPTER_VERSION` `openrouter.v1`, `claude_code.ADAPTER_VERSION`
`claude_code.v1`); a newer adapter needs a new run (`inputs_changed`).

### Claude Code

`claude_code.ClaudeCodeAdapter`, verified against Claude Code 2.1.92 on 2026-09-19, 2.1.283 with Opus 5.5 on
2026-09-26 and 2.1.284 with Sonnet 5.5 on 2026-09-29; the measurements are in the
[Claude backend plan](specs/2026-09-19-claude-backend-plan.md).

- **Finding the CLI.** The adapter looks on `PATH`, then for `~/.local/bin/claude` (`claude.exe` on Windows). An
  npm shim (`.cmd`, `.bat`, `.ps1`) is never run through `cmd.exe`: the adapter calls `node` with the
  `node_modules/@anthropic-ai/claude-code/cli.js` next to it, else stops with `unsupported_claude_launcher`; a missing
  CLI stops with `claude_missing`. There is no project setting for the Claude path (why: D-013).
- **Login and version.** `claude auth status --json` must report `loggedIn` and `authMethod: "claude.ai"`, else the
  call stops with `authentication_required` or `subscription_required`. `claude --version` must reach
  `MINIMUM_CLI_VERSION` (2.1.280) and the model's entry in `MODEL_MINIMUM_CLI` (`claude-sonnet-5-5`: 2.1.284), else
  `claude_version` with the hint `claude update`.
- **Invocation.** Per call:

  ```
  claude -p --output-format stream-json --verbose --include-partial-messages
         --json-schema <compact strict schema> --model <id>
         --permission-mode dontAsk --no-session-persistence --disable-slash-commands
         --strict-mcp-config --setting-sources "" --system-prompt <SYSTEM_PROMPT>
         --max-budget-usd 12 [--effort <level>] --tools ""
  ```

  Research calls use `--tools "WebSearch,WebFetch" --allowedTools "WebSearch,WebFetch"` instead of `--tools ""`. The
  prompt goes through stdin; the working directory is the empty call folder, so no project `CLAUDE.md` is read.
- **Isolation.** `--setting-sources ""` keeps out skills, MCP servers and settings files, and the short fixed
  `SYSTEM_PROMPT` replaces the CLI's coding-assistant prompt (why: D-011). `--bare` is not used: it switches off the
  subscription login and accepts only an API key. Measured with 2.1.92 on 2026-09-19: a `CLAUDE.md` with a marker
  line in the working directory was not read, and a personal `~/.claude/settings.json` with another model did not
  apply.
- **Environment.** It starts from `codex.subscription_environment` and sets `DISABLE_TELEMETRY=1`,
  `DISABLE_ERROR_REPORTING=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and `CLAUDE_CODE_MAX_OUTPUT_TOKENS`: 128 000
  for `claude-opus-5-5` and `claude-sonnet-5-5` (`MODEL_OUTPUT_TOKENS`), else `MAX_OUTPUT_TOKENS` (64 000)
  (why: D-028). `CLAUDE_CONFIG_DIR` stays untouched because it holds the subscription login.
- **Checks before the start.** A prompt longer than `prompt_limit(model)` is refused with `prompt_too_large`, neither
  started nor charged: `PROMPT_LIMIT_CHARS` (300 000 characters) for a `BASE_WINDOW_TOKENS` (200 000-token) window,
  scaled by `CONTEXT_WINDOW_TOKENS` (1 000 000 for Opus 5.5 and Sonnet 5.5, so 1 500 000 characters). The schema
  travels as one command-line argument, and Windows accepts 32 767 characters per command line. Counting the line as
  Windows receives it (every `"` of the schema as `\"`), the adapter refuses a schema over `MAX_SCHEMA_CHARS` (30 000)
  or a line over `MAX_COMMAND_LINE_CHARS` (32 766) with `invalid_output_schema` (why: D-029).
- **Answer.** The stream ends with a `result` line carrying `structured_output`, which the CLI produces through a tool
  call `StructuredOutput` (`num_turns` 2). The call succeeds only with exit code 0, `subtype: "success"` and no
  `is_error`; the answer is then checked locally. Partial messages (`input_json_delta`) feed the live output;
  the CLI delivers no visible reasoning summaries.
- **Failures.** `classify_claude_failure` yields `claude_budget_cap`, `claude_quota_exhausted` (`waiting_for_quota`),
  `claude_output_limit` (`blocked`; a cut answer is never accepted), `authentication_required`,
  `claude_structured_output` or `claude_failed`. The order of its checks and what counts as a reached limit are in
  [Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection).
- **Rate-limit events.** The stream carries `rate_limit_event` entries with `status`, `resetsAt` and `rateLimitType`
  (for example `five_hour`). The last one is stored as `rate_limit` in `metadata.json` and in the quota store; a
  refused event gives the exact reset time of a block. `isUsingOverage` and `overageStatus` are kept as
  `using_overage` and `overage_status` where the CLI names them; nothing acts on them yet (see V-17).
- **Cost cap.** `--max-budget-usd` (`MAX_BUDGET_USD`, 12.0 per call) limits outliers; the reported amount is an
  equivalent value, not a bill. Measured with 2.1.92 on 2026-09-19: the cap ends a call with subtype
  `error_max_budget_usd`, but only after the first model turn has run.
- **Search proof.** `search_events.json` is built from the `tool_use` blocks (`WebSearch` with a query is a search,
  `WebFetch` with a URL an opened page). A call counts as research when it has any such event or
  `usage.server_tool_use.web_search_requests` above 0; an opened page without a query counts too (why: D-047).

### Codex CLI

`codex.CodexAdapter` with the app-server transport (`codex_stream.run_app_server`); an `exec` transport remains for the
tests.

- **Finding the CLI.** `runtime.codex_executable` (default `codex`) is looked up on `PATH`, on Windows also as
  `~/.local/bin/codex.exe` and in the newest installed OpenAI extension of VS Code or VS Code
  Insiders. An explicit path stays authoritative. An npm shim is run with `node` and
  `node_modules/@openai/codex/bin/codex.js`, else `unsupported_codex_launcher`; a missing CLI stops with
  `codex_missing`.
- **Login.** `codex login status` must name a ChatGPT login and no API key; otherwise `authentication_required` or
  `subscription_required`.
- **One ephemeral app server per call.** `codex app-server --listen stdio://`, then JSON-RPC `initialize`,
  `config/read`, `thread/start` and `turn/start`. Model and reasoning level are passed explicitly to `thread/start` and
  `turn/start` (with `outputSchema`); the call uses the ChatGPT subscription, an ephemeral thread and the read-only
  sandbox without model fallback. The server's answer to `thread/start` is checked for provider `openai` and the
  requested model, so there is no silent model switch.
- **Overrides.** Unlike `exec --ignore-user-config`, the app server loads its base configuration, so the adapter
  overrides it per call: `web_search` `live` only for research, no project or developer instructions
  (`project_doc_max_bytes: 0`), shell, app, hook and agent features off, every configured MCP server disabled, and
  `model_reasoning_effort` when a level is set. Research calls pass a model set explicitly in `runtime.codex_model`;
  this was tested with Codex CLI `0.154.0-alpha.6.2`
  ([configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference),
  [CLI command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli),
  [app-server docs](https://learn.chatgpt.com/docs/app-server)). Interactive requests from the server (approvals,
  external tools, credential refresh) are refused and fail the call. Raw protocol payloads (prompts, account data, raw
  reasoning) are never kept.
- **Stream.** `item/agentMessage/delta` and `item/reasoning/summaryTextDelta` feed the live output (public reasoning
  summaries only), `webSearch` items become search events, and on `turn/completed` the final message is authoritative;
  deltas are display only. The server process is stopped after each call.
- **Failures.** `codex.classify_failure` maps the turn's `codexErrorInfo` (`usageLimitExceeded` or HTTP 429 →
  `quota_exhausted`, `waiting_for_quota`, with the reset parsed from "try again in …" or "try again at …";
  `unauthorized` or 401 → `authentication_required`), an unsupported schema to `invalid_output_schema` and anything else
  to `codex_failed`.
- **Quota without a model call.** `codex_stream.read_rate_limits` starts an ephemeral app server and sends
  `account/read` and `account/rateLimits/read` without a thread (time limit `subscriptions.QUERY_TIMEOUT`, 20 s).
  Verified with Codex CLI 0.154.0-alpha.6.2 on 2026-09-19: `account/read` returns `type: "chatgpt"` and `planType`,
  `account/rateLimits/read` the windows `rateLimits.primary` and `.secondary` (`usedPercent`, `windowDurationMins`,
  `resetsAt` in Unix seconds), `rateLimitReachedType`, `credits` (`hasCredits`, `balance`), `planType`,
  `spendControlReached` and `rateLimitsByLimitId`. Only public fields are kept, without IDs or e-mail; the read interval
  is in [Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection).

### OpenRouter

`openrouter.OpenRouterAdapter`; calls are billed to the OpenRouter credit.

- **Request.** A streaming POST to `openrouter.ENDPOINT` (`https://openrouter.ai/api/v1/chat/completions`) with
  [structured JSON-schema outputs](https://openrouter.ai/docs/guides/features/structured-outputs) (`response_format`
  of type `json_schema`, `strict: true`) and `provider: {"require_parameters": true, "sort": "throughput"}`: only
  providers that support every parameter, preferring high
  [token throughput](https://openrouter.ai/docs/guides/routing/provider-selection#provider-sorting) for longer texts.
  Actual speed depends on model, provider and load; the quality reviews still run in full. A selected level is sent
  as `reasoning: {"effort": <level>, "exclude": false}`
  ([reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens)). Redirects are never
  followed (`NoRedirect`). Live output uses [streaming](https://openrouter.ai/docs/api/reference/streaming).
- **Output limit.** `max_tokens` is `--max-output-tokens`, default `DEFAULT_MAX_OUTPUT_TOKENS` (32768) per call; a
  model with a smaller output limit needs a matching value, and too tight a limit can cut a complete episode text. A
  cut answer (`finish_reason: "length"`) ends as `openrouter_truncated`, any other unfinished answer as
  `invalid_model_output`; truncated, refused or invalid answers are never taken as finished text. Provider, model,
  token limit and adapter version are bound inputs of the run
  ([Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)).
- **Errors.** Raw provider messages are never exposed (`api_failure`); a refusal (403) keeps only OpenRouter's short
  reason, redacted and at most 300 characters (`refusal_reason`, why: D-132). Missing credit (402 `openrouter_credits`) and
  rate limits (429 `openrouter_rate_limit`) pause the run with `waiting_for_quota`; 401 `openrouter_authentication`,
  403 `openrouter_forbidden`, 400/404/413/422 `openrouter_request`, other statuses `openrouter_unavailable` and a
  broken connection or timeout `openrouter_connection` are saved as handleable errors, and `resume` reuses finished
  work. A dropped connection triggers no automatic repeated paid request. A key found in the prompt or the answer
  stops the call (`credential_in_prompt`, `credential_in_response`; see [Secrets and keys](SECURITY.md#secrets-and-keys)).
- **No search.** The adapter refuses `search=True` and model IDs with `:online` (`openrouter_search_unsupported`).

## Prompts and versioning

Prompts are separate text files under `src/podcast_automate/prompts/` with defined inputs (a JSON payload as the
last line), outputs (Pydantic contracts as strict JSON schema) and deterministic validation, composed by
`prompts.py`. Shared rule blocks (terminology, teaching standard, continuity, episode framing, evidence rules) are
placed before the task-specific instructions.

Every call carries a version tag in code, for example `write_episode.v12-listenability`
(`script_pipeline.WRITE_EPISODE_VERSION`). Checkpoints are bound to the hash of the complete composed prompt, so a
text change repeats only the affected calls ([Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)).
When the meaning of a prompt changes, the tag at the call site is bumped so receipts show which wording produced a
result.

The task families are: research search; question plan and scope check; reading decision and answer review per
sub-question; dossier composition with overall review and objection routing; series plan; teaching plan with its
review; script draft; dialogue polishing with comparison; source, reading, editorial and teaching reviews; and the
series review. Details and the composition rules: [prompts/README.md](../src/podcast_automate/prompts/README.md).

## Data contracts

The full contracts are being implemented step by step as validatable schemas. `pla schemas <folder>` exports the
implemented state as JSON schemas and is the authoritative list; currently there are 21 contracts: TopicBrief,
EpisodeScript, TextProbeOutput, RunManifest, ResearchDiscovery, SourceDocument, SourceIndex, ResearchDossier,
DossierReview, KnowledgeModel, SeriesPlan, EpisodePlan, ScriptReview, TeachingPlan, TeachingPlanReview,
TeachingPlanRepair, ListenerReadback, TeachingReview, EditorialReview, DialoguePolishReview and SeriesReview. Every
contract forbids unknown fields (`models.Contract`). The compact knowledge model takes over the evidenced findings
unchanged and references terms, mechanisms, examples and limits by their IDs. Series plan and scenes are
validatable. The sections below also describe the missing target scope, such as differentiated confidence and
evidence ratings and full series coverage ([Roadmap](PRODUCT.md#roadmap)). TopicBrief is described in
[Project brief](CONFIGURATION.md#project-brief).

### SourceDocument

Every source contains, in the target contract:

- `id`, `type`, `title`, `author`, `published_date`, `imported_at`, `language`, `url`,
- `license_status`: `owned`, `public_domain`, `open_license`, `permission_granted`, `unknown` or `restricted`,
- `allowed_usage`: `private_learning`, `internal_review`, `publishable_summary`, `publishable_quotes` or `no_export`,
- `private`: explicit exclusion from export,
- `reliability` with reasoning, relation to primary sources, conflicts of interest and access status,
- `text_hash` and `sections` with stable IDs, text and existing page or time marks,
- `quotes` with section reference and `max_export_words`,
- `uncertainties` about the source itself.

Implemented today (`research_models.SourceDocument`): `id`, `type` (`html`, `pdf`, `text`), `title`, `authors`,
`published_date`, `imported_at`, `url`, `final_url`, `language`, `reliability_note`, `uncertainties`, `raw_path`,
`raw_hash`, `text_hash`, `sections`, `extraction_coverage`, `source_type`, `date_basis` and `citation`. The rights
fields are fixed at `license_status: unknown`, `allowed_usage: private_learning` and `private: true`; they mark the
private workspace and are no per-source export block ([Source rights and privacy](SECURITY.md#source-rights-and-privacy)).

A source that was found but not imported stays a source candidate. Titles and search snippets are no evidence for
detailed subject-matter statements.

### KnowledgeModel

| Part | Required content (target) |
| --- | --- |
| `key_terms` | ID, definition and source references |
| `claims` | ID, statement, type, confidence and evidence entries with source section, strength and reasoning |
| `counterpoints` | ID, counter-argument or limit, source references and link to the affected claim |
| `dependencies` | Which terms or claims must be explained before others |
| `mechanisms` | Explanatory steps for a relationship with the related claim and term IDs |
| `examples` | Examples with references; invented illustrations are explicitly marked as hypothetical |
| `uncertainties` | ID, open question and reason, such as missing data, contradictory sources or unclear terms |
| `editorial_priorities` | Relevance for the guiding question, necessary prerequisites and content left out with reasons |

Source references use `source_id#section_id`. Every subject-matter claim has at least one evidence entry. Editorial
conclusions are marked as such and point to evidenced starting claims. Illustrative examples must not suggest
unevidenced facts.

Implemented today (`script_models.KnowledgeModel`): `research_run_id`, `topic`, `claims` (the dossier findings),
`key_terms`, `mechanisms`, `examples` and `counterpoints` as lists of IDs, `dependencies`, `uncertainties`,
`editorial_priorities`, `synthesis` and `source_assessments`.

### SeriesPlan

The series plan contains the guiding question, the desired depth, the content-justified number of episodes, the total
duration estimated from it, the overarching explanatory arc and an ordered list of episodes. A duration the user
explicitly asked for is recorded separately. Per episode at least:

- `episode_id`, number, title, its own central question and purpose,
- `target_minutes`,
- prerequisite episodes, terms and claims,
- terms, claims, mechanisms and examples to be newly explained,
- counter-positions and uncertainties covered,
- deliberately deferred questions with a later target episode or a justified exclusion,
- a question leading to the next episode; for the last episode a concluding synthesis.

A coverage matrix assigns every prioritised sub-question and every central claim to one or more episodes. Reuse is
justified as necessary deepening or a short recap (target scope). The implemented episode entries with core and
supporting findings are described in [Series plan](SCRIPTS.md#series-plan).

### EpisodePlan and script

An episode plan contains `episode_id`, `mode: deep_dive`, style profile, time budget, speaker roles and scenes. Every
scene has a function, a question, a target time, relevant knowledge-model IDs, explanatory steps and a transition.
Implemented today (`script_models.EpisodePlan`, `ScenePlan`): per scene `scene_id`, `title`, `question`, `purpose`
(`orientation`, `explanation`, `worked_example`, `limitation`, `synthesis`), `finding_ids` and `explanation_steps`;
mode, style profile, speaker roles, scene target time and transition are target scope.

The canonical `script.yaml` (`models.EpisodeScript`) contains `schema_version`, `episode_id` and ordered segments
with `segment_id`, `scene_id`, `chapter_id`, `speaker_id`, `text`, `knowledge_refs` and `pause_after_ms`.
`knowledge_refs` points to stable IDs of the knowledge model, which keeps the source references in the form
`source_id#section_id`. Pure transitions may do without a subject-matter reference. Source binding and the content
review check that the assignment is complete.

The spoken version consists of complete explanations. Directions and references are not spoken. `script.md`, the
transcript and the render jobs are generated from the same canonical state. Model-driven splits of long speaker
passages happen at sentence boundaries and stay traceable to the original segment. The audio timeline holds the
actually measured times; editorial chapter positions are only planned before rendering.

## Project folder layout

```
projects/<project>/
  project.yaml            brief and runtime settings (no credentials)
  style_notes.md          editorial notes
  studio/                 Studio state of the project: job, chat, outline, queue, logs, fallback choices
  inputs/                 attachments and provided works
  sources/raw/, sources/processed/
  research/, models/      dossier, source index, series plan, knowledge model
  episodes/<ep>/          script.yaml, script.md, teaching plan, show notes, audio approval, publish descriptions
  reports/                quality reports
  runs/<run_id>/          manifest, checkpoints, model calls, budget, failures/
  exports/<ep>/<run_id>/  MP3, chapters, transcript, show notes, listening sheet, publish/ (companion kit)
  cache/audio/            reusable audio segments
  probes/                 results of text-probe and audio-probe
  logs/                   log of single CLI commands
src/podcast_automate/
  prompts/                all model instructions as text files (see prompts/README.md)
  research.py, question_*.py            research: flow, sub-questions, synthesis
  scripting.py, script_pipeline.py      script run: preparation and stages
  studio.py, studio_worker.py           local server and job processes
```

Log files are described in [Logs and diagnosis](OPERATIONS.md#logs-and-diagnosis), the files the Studio keeps next to
the projects in [Files outside the project](CONFIGURATION.md#files-outside-the-project).

The complete product runs from the topic brief to the audible series. Audio export is part of it; every run can end
with reviewable scripts before audio is generated. All paths are relative to the project folder:

| Required artefact | Purpose |
| --- | --- |
| `project.yaml` | Topic brief, prior knowledge, desired depth, focus and language |
| `research/research_plan.yaml` | Sub-questions, search strategy, coverage and research limits |
| `research/source_candidates.yaml` | Sources found, reasons for selection and access problems |
| `models/source_index.yaml` | Imported sources, sections, metadata, rights and hashes |
| `research/research_briefing.md` | Cross-source synthesis |
| `research/open_questions.md` | Open questions, contradictions and research gaps |
| `models/knowledge_model.yaml` | Terms, claims, evidence, dependencies, examples and uncertainties |
| `models/series_plan.yaml` | Content-justified number of episodes, order, topic coverage, overall arc and duration estimates |
| `episodes/<episode_id>/episode_plan.yaml` | Scenes, explanatory goals, required claims and transitions |
| `episodes/<episode_id>/script.yaml` | Canonical script with spoken text, directions and knowledge-model references |
| `episodes/<episode_id>/script.md` | Reading version generated from the canonical script |
| `episodes/<episode_id>/show_notes.md` | Sources, chapter overview and supplementary notes |
| `reports/research_quality.json`, `reports/script_quality.yaml` | Findings of the reviews per episode and for the whole series |
| `runs/<run_id>/run_manifest.yaml` | Versions, inputs, outputs, progress, approval state and available usage data |

Each stage's full outputs are listed in [Research outputs](RESEARCH.md#outputs), [Script outputs](SCRIPTS.md#outputs)
and [Exports and listening sheet](AUDIO.md#exports-and-listening-sheet).

After audio approval, every selected episode additionally gets an export folder `exports/<episode_id>/<run_id>/` with
`audio.mp3`, `chapters.json`, `transcript.md` and `show_notes.md` among other files; one MP3 per episode since
2026-10-04, and an episode recorded in parts before that has one `part_NN/` folder per part. During audio generation, `timeline.json` in the same folder records the
actually measured segment times and their mapping to script and chapters. Reusable audio segments are in
`cache/audio/`.

Raw sources are in `sources/raw/<run_id>/`, cleaned sections in `sources/processed/<run_id>/`, frozen per run. Run
folders hold input and output states and logs. The claim graph is part of the `KnowledgeModel`; an extra file with a
competing data state is not needed.

Run data, model weights and credentials do not belong in the Git repository: `pla init` writes a `.gitignore` with `*`
into every new project, and the repository's `.gitignore` excludes `projects/`, `runs/`, `cache/audio/`, `.studio/`,
`sources/raw/`, `.venv-tts/` and audio files.

## Run folder and manifest

Every run has a folder `runs/<run_id>/`; `runs/latest.json` names the project's last run.

**What a run records.** The manifest records run ID, creation time, pipeline and schema version, topic brief,
research limits, source and artefact hashes, prompt versions, models and settings used, and the output paths per
episode. It also records status and resume points per stage, episode and segment, warnings and errors, available
usage data, render times, cache hits, quality status, reviewed input hashes, and origin and scope of the audio
approval for concrete script states and episodes. Cost fields distinguish estimates from separately billed amounts;
they must not derive invented invoice amounts from subscription calls. Source texts and model answers are frozen in
the run as far as permitted, so later changes to online sources do not silently change the original run.

**Where it lives.** `run_manifest.yaml` (`models.RunManifest`) holds `schema_version`, `pipeline_version`, `run_id`,
`kind`, `created_at`, `updated_at`, `project_hash`, `input_hash`, `status` (statuses:
[Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)), `audio_approved` and `stages` with
`status`, `attempts`, `outputs` and `error` (`code`, `message`, and for a quota pause the reset facts in `details`,
`runner.RETRY_DETAIL_KEYS`). The rest sits next to it:

| File | Content |
| --- | --- |
| `project_snapshot.yaml` | The brief as it was when the run started |
| `inputs.json` | The bound inputs of a script or audio run |
| `script_request.json` | Script run: research run, episode, execution mode and the bound text choice (`text_generation`) |
| `research_request.json` | Research run: the bound text choice (when chosen explicitly), requirements, quality policy and seed corpus |
| `probe_request.json` | Text probe with a backend other than `codex_cli` |
| `budget.json`, `budget_projection.json` | Budget use and projection ([Budgets](BUSINESS_LOGIC.md#budgets)) |
| `calls/call_NNN/` | One folder per model call (below) |
| `model_trace.json` | Shared ring buffer of the live output ([Progress and telemetry](STUDIO.md#progress-and-telemetry)) |
| `status_reports/` | Status briefs and their call metadata |
| `failures/` | Cleaned tracebacks of failed stages ([Logs and diagnosis](OPERATIONS.md#logs-and-diagnosis)) |
| `question_research/` | Work plan, reading steps and answer reviews of a research run ([Outputs](RESEARCH.md#outputs)) |

### Per-call records

Every model call has its folder `runs/<run_id>/calls/call_NNN/`:

| File | Written by | Content |
| --- | --- | --- |
| `provider_choice.json` | Adapter pool | Provider, model, reasoning level, mode, reason and quota snapshots of the decision; `run_effort` when a stage cap lowered the level; `search`, `prompt_version`, `prompt_chars` |
| `provider_switch.json` | Adapter pool | A switch inside the call: `from`, `to`, `error_code`, `message`, `switched_at` |
| `stall_retry.json`, `format_retry.json` | Adapter pool | Receipt of the one repeat after a stalled stream (`stall`) or a Claude answer in the wrong format (`claude_structured_output`) |
| `prompt_size.json` | Adapter pool | Prompt length, limit and the provider left out for it |
| `output_schema.json` | Adapter | The strict schema sent |
| `metadata.json` | Adapter | Provider, models, versions, usage, cost and search figures (below) |
| `response.json` | Adapter | The validated answer |
| `rejected_output.json` | Adapter | A parsed answer the contract rejected; it is model output like an accepted answer, and the call stays charged |
| `failure.json` | Adapter | Code, message, exit code, model, level, prompt version and CLI version of a failed call, never raw provider output or the prompt; with `rejected_output` the rejected fields; with `codex_failed` Codex's own short reason (`provider_message`, redacted, at most 300 characters) |
| `search_events.json` | Subscription adapters | Observed search and open-page tool events |
| `diagnostics.json` | `call_activity.CallActivity` | Cleaned technical diagnostics, also after timeout or stop (times, request size, events, error categories such as connection, rate limit or answer format); no raw error messages, prompts, tool output or credentials |
| `activity.json` | `call_activity.CallActivity` | Public activity record of the call |
| `work_context.json` | `research_status.record_request` | A limited selection of the call's actual inputs (question, criteria, material) without full prompts or source texts |

`metadata.json` holds `provider`, `auth_mode` (`claude.ai`, `chatgpt` or `api_key`), `requested_model`,
`requested_reasoning_effort`, `prompt_version`, `usage` and the search figures (`research_performed`,
`web_search_requests`, `observed_search_queries`), plus per adapter the CLI or adapter version, the reported model
(`actual_model`; for Claude the key of `modelUsage`), duration and, for Claude, `num_turns` and the last `rate_limit`;
never the `session_id`. The requested fields document the requested values, not a confirmation by the provider.
`reported_cost_usd` with `cost_basis` is the equivalent value a CLI reports; `separately_billed_cost` is an amount
billed separately, OpenRouter's reported USD cost and `null` for the subscriptions. A missing cost means unknown cost,
not free use; these metadata do not replace OpenRouter's billing.
