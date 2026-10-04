---
title: Claude Code as a second subscription provider plan
doc_type: plan
status: done
date: 2026-09-19
---

# Claude Code as a second subscription provider plan

As of 19 September 2026. Goal: the text pipeline can run every model call either through the Codex CLI (ChatGPT subscription, GPT-6 Astra) or through the Claude Code CLI (Claude Max subscription, Claude Opus 5). Before every call it checks which subscription still has quota. If both have quota, Claude is used since 26 September 2026 (`text_settings.AUTO_PREFERENCE`; planned and implemented until then was Codex first). If only one has quota, that one is used. If neither has quota, the run pauses with `waiting_for_quota` and names the earliest reset time. The current state is in the addenda at the end of section 7.

## 1. Verified baseline

All statements were checked on the target machine on 19 September 2026, not taken from the documentation.

**Claude Code CLI 2.1.92** (`claude`, on the PATH):

- Non-interactive call: `claude -p --output-format json --json-schema '<schema>' --model claude-opus-5 --effort <low|medium|high|max> --tools "" --permission-mode dontAsk --no-session-persistence --disable-slash-commands --strict-mcp-config --setting-sources "" --max-budget-usd <n>`; the prompt is passed via stdin.
- Response envelope (one JSON line): `type: "result"`, `subtype: "success"`, `is_error`, `num_turns` (2 with a schema), `stop_reason`, `structured_output` (schema-conforming object), `usage` with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` and `server_tool_use.web_search_requests`, `modelUsage["claude-opus-5"]` with `costUSD`, `contextWindow` 200000 and `maxOutputTokens` 32000, `total_cost_usd`, `terminal_reason`, `permission_denials`, `session_id`.
- Login: `claude auth status --json` returns `loggedIn`, `authMethod: "claude.ai"`, `apiProvider: "firstParty"`, `subscriptionType: "max"`. The credentials are in `~/.claude/.credentials.json`; `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` take precedence over the subscription and would bill through the API.
- Quota: there is no non-interactive way to read the remaining 5-hour or weekly quota. An exhausted limit appears only as an error result (`is_error: true`, text of the form "You've hit your … limit"). Opus has its own limit beside Sonnet/Haiku.
- Limitations of this version: `--bare` disables the subscription login completely (API key only) and is therefore unusable. `--effort` knows `low`, `medium`, `high`, `max`; there is no `xhigh`. There is no `--max-turns`. `--json-schema` takes the schema as an argument; the project's largest strict schema (`ResearchDossier`) has 6.6 K characters, and the Windows limit is 32,767 characters per command line.
- A test call with the flags above cost 0.07 USD equivalent according to the envelope, about 9 K of it cached system prompt tokens of Claude Code itself.

**Codex CLI 0.154.0-alpha.6.2** (VS Code extension, found by `codex.executable_command`; login "Logged in using ChatGPT", `planType: "prolite"`):

- The app server returns, via JSON-RPC `account/rateLimits/read` and without a model call: `rateLimits.primary` and `.secondary`, each with `usedPercent`, `windowDurationMins`, `resetsAt` (Unix seconds), plus `rateLimitReachedType`, `credits` (`hasCredits`, `balance`), `planType`, `spendControlReached`, `rateLimitsByLimitId` (several limits, here `codex`). `account/read` returns `type: "chatgpt"` and `planType`; `account/usage/read` returns daily token counters.
- Current state: `primary.usedPercent: 100`, weekly window (10080 minutes), `resetsAt` 1790109078 (22 September 2026, 22:31 CEST), `rateLimitReachedType: "rate_limit_reached"`, no credits. Codex is therefore unusable until then; this is exactly the case the switch is meant to cover.
- The existing connection (`codex_stream.run_app_server`) starts its own app server for every call via `initialize`, `config/read`, `thread/start`, `turn/start`; the quota query can use the same connection before `thread/start`.

## 2. Decisions

1. **Two concrete providers, one selection rule.** New provider id `claude_code` beside `codex_cli` and `openrouter`. In addition the selection rule `auto`: Codex if Codex has quota; otherwise Claude if Claude is not marked as exhausted; otherwise pause. Since 26 September 2026 the reverse order applies, Claude first (`text_settings.AUTO_PREFERENCE`); saved runs keep their order. `auto` becomes the default preset for new projects; existing projects keep their saved choice.
2. **Definition of quota.** Codex has quota when every window present has `usedPercent < 100` and `rateLimitReachedType` is empty. Paid credits are not used automatically (`credits` is ignored; a project switch can allow it later). Claude has quota when no entry in the local quota log is active; the entry is created at the first limit error and carries the reset time read from the error message or, if the message contains none, a conservative block (5 hours for a session or Opus limit, until the next Monday for a weekly limit, 30 minutes for an unclear message).
3. **Switch per call, not per run.** The choice is made before every model call, because quota changes during a run. A run with `auto` stores both model configurations in `text_generation`; which provider actually served a call is recorded in `runs/<run_id>/calls/call_NNN/metadata.json`. Saved checkpoints stay valid because they are bound to the prompt text and the inputs, not to the provider.
4. **No API-key operation for Claude.** The environment of the child process removes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`, `CODEX_API_KEY` and `OPENROUTER_API_KEY`. The adapter requires `authMethod: "claude.ai"`; anything else is rejected as `subscription_required`, as with Codex.
5. **Research stays on Codex at first; Claude research is a phase of its own.** Claude Code can search live with `--tools "WebSearch,WebFetch"` and reports search events (`usage.server_tool_use.web_search_requests`, `tool_use` events in the stream). The research's evidence duty (observed search events) can be mapped onto this, but is implemented only in Phase 4.
6. **Effort mapping.** Codex `xhigh` corresponds to `high` for Claude (the default); `max` remains an explicit choice. The mapping is a table in the catalog, not a silent conversion. Since Claude Code 2.1.280 the CLI knows `xhigh`; since then the table maps every level to itself (`text_settings.EFFORT_EQUIVALENTS`).

## 3. Building blocks

### 3.1 `claude_code.py`: adapter with the same contract as `CodexAdapter`

`ClaudeCodeAdapter(settings, *, model, reasoning_effort, cancel_check)` with `structured(prompt, output_type, directory, *, prompt_version, search=False) -> (output, metadata)`.

- Command: `claude -p --output-format stream-json --verbose --json-schema <compact strict schema> --model <id> --effort <level> --permission-mode dontAsk --no-session-persistence --disable-slash-commands --strict-mcp-config --setting-sources "" --max-budget-usd <cap> --tools ""` (with `search=True`: `--tools "WebSearch,WebFetch" --allowedTools "WebSearch,WebFetch"`). The prompt goes via stdin; the working directory is the call directory (empty, so that no project `CLAUDE.md` is read). Phase 0 clarifies whether `--output-format stream-json` delivers the envelope with `structured_output` as the last line; otherwise Phase 1 starts with `--output-format json` and without a live display.
- Schema: reuse `openrouter.strict_schema`; above 30,000 characters abort with `AppError(code="invalid_output_schema")` instead of starting a broken command.
- Environment: extend `subscription_environment()` from `codex.py` (additionally remove `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`) and set `DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` and `CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000` (the CLI's ceiling for this model id; 32,000 without the setting). `CLAUDE_CONFIG_DIR` stays unchanged, because the subscription credentials live there.
- Process: reuse `process.run_process` with `on_stdout_line`, `cancel_check` and `text_timeout_seconds`; stop through `stop_process_tree`.
- Result check: `type == "result"` and `subtype == "success"` and `is_error == false`; validate `structured_output` with `output_type.model_validate`; if it is missing, `invalid_model_output`. With `search=True` the call counts as research only with `web_search_requests > 0` or observed `tool_use` events for `WebSearch` (`search_not_observed`, as with Codex today).
- Error classes (`classify_claude_failure`): result text or `subtype` with "You've hit your", "limit", "rate limit", "429" → `AppError(code="claude_quota_exhausted", status="waiting_for_quota")` with the parsed reset time; "not logged in", "authentication", "401" → `authentication_required`; `--max-budget-usd` reached → `claude_budget_cap`; truncated answer (`assistant.error == "max_output_tokens"` or "output token maximum" in the result text, result `success` with `is_error` and exit code 1) → `claude_output_limit`, `blocked`; everything else `claude_failed`. As with Codex, raw texts are not stored, only `failure.json` with code and version.
- Metadata: `provider: "claude_code"`, `auth_mode: "claude.ai"`, `requested_model`, `actual_model` (key from `modelUsage`), `requested_reasoning_effort`, `cli_version` (`claude --version`), `usage` (input, output, cache), `reported_cost_usd` with the note that it is an equivalent value and not an invoice, `research_performed`, `web_search_requests`, `observed_search_queries` (from the `tool_use` inputs), `num_turns`; `session_id` is not stored.
- Live display: `CallActivity` gets `observe_claude(line)` for stream events: `assistant` messages with `text` blocks go into the trace, `tool_use` named `WebSearch` appears as "Websuche gestartet: <query>" (web search started), `system` events with `api_retry` become the diagnosis `rate_limit`/`retry`. The CLI provides no reasoning summaries; the display then shows only text progress.
- System prompt: in Phase 3, check whether `--system-prompt` with a short fixed text replaces the default system prompt of about 9 K tokens, and whether `~/.claude/CLAUDE.md` is still read then. Both affect quota consumption and hermeticity.

### 3.2 `subscriptions.py`: quota query and selection rule

- `codex_rate_limits(settings) -> dict | None`: starts the app server, sends `initialize`, `initialized`, `account/read` and `account/rateLimits/read`, and ends the process (time limit 20 seconds). Normalised return value: `{"provider": "codex_cli", "available": bool, "plan": "prolite", "windows": [{"name": "primary", "used_percent": 100, "window_minutes": 10080, "resets_at": "2026-09-22T20:31:18Z"}], "reason": "rate_limit_reached"}`. `None` when the CLI is not installed or not logged in. The result is cached with a timestamp and read again at most every two minutes; after every quota error it is read again at once.
- `claude_login(settings) -> dict | None`: `claude auth status --json` with a time limit of 20 seconds; only `loggedIn`, `authMethod` and `subscriptionType` are taken over, e-mail address and IDs are not stored.
- `claude_quota_state() -> dict`: reads the quota log; an entry `{"blocked_until": "<ISO>", "reason": "opus_limit", "detected_at": ..., "message_excerpt": ...}` applies as long as `blocked_until` lies in the future.
- Location of the log: `~/.podcast-automate/subscriptions.json` (account-wide, not per project), written atomically through `storage.atomic_text`, never with credentials. On Windows under `%USERPROFILE%`.
- `choose_subscription(settings, *, prefer="codex_cli", require=None) -> Choice`: returns `provider`, `model`, `reasoning_effort`, `reason` (`"codex_available"`, `"codex_exhausted_until …"`, `"claude_blocked_until …"`) and `snapshots`. The rule, in this order: Codex available → Codex; otherwise Claude not blocked → Claude; otherwise `AppError(code="subscriptions_exhausted", status="waiting_for_quota")` with the earlier reset. `require="claude_code"` or `require="codex_cli"` skips the rule for fixed providers but still checks the login.
- `record_quota_failure(provider, error)`: Codex → discard the cache and read again; Claude → write a log entry.

### 3.3 Selection in the run

- `text_settings`: `CLAUDE_MODELS = {"claude-opus-5": "Claude Opus 5"}`, `CLAUDE_EFFORTS = ("low", "medium", "high", "max")`, `EFFORT_EQUIVALENTS = {"xhigh": "high", ...}`, the presets `claude_opus_sub` (**„Opus 5 · Claude-Abo“**, Opus 5 · Claude subscription) and `auto_subscriptions` (**„Automatisch · Codex, sonst Claude“**, automatic · Codex, otherwise Claude). `validate_reasoning` gains the provider `claude_code`; `provider_model` normalises `opus` → `claude-opus-5`.
- `text_generation` (in `script_request.json`, `research_request.json`, `inputs.json`): unchanged for fixed providers; for `auto` the form `{"provider": "auto", "prefer": "codex_cli", "candidates": {"codex_cli": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"}, "claude_code": {"model": "claude-opus-5", "reasoning_effort": "high"}}, "adapter_versions": {"claude_code": "claude_code.v1"}}`. This form goes into the `input_hash`; a resume uses it unchanged. `text_generation_settings` in `scripting.py` and the selection in `research.py` accept the new form and, as before, reject any change on resume.
- `ScriptRun.invoke` and the `invoke` function in `run_research` get an `AdapterPool`: `pool.adapter(search=...)` calls `choose_subscription` (without a query for a fixed provider), returns the matching adapter and writes the decision as `provider_choice.json` into the call directory. On `waiting_for_quota` from an adapter in `auto` mode: `record_quota_failure`, choose again, and repeat the same call once with the other provider (same call directory, `provider_switch.json` with the reason). If no provider remains, the original error is passed on; `execute_stages` stores `waiting_for_quota` as it does today.
- `build_adapter` in `scripting.py`, `run_research` and `studio_worker.perform` (assistant) build the pool instead of a single adapter. The supplementary research path in `ScriptRun.invoke` (today: an OpenRouter run uses Codex for `research=True`) stays on Codex until Phase 4.
- `status_summary` (status briefs every three minutes) uses the same pool with the cheap model of the chosen provider (`gpt-5.6-luna` or `claude-haiku-4-5`); status calls still do not count against the production budget.
- `run_budget`/`script_budget` stay unchanged: the call limit counts calls, not providers.

### 3.4 Studio, CLI, Doctor

- `TextChoice.provider` allows `claude_code` and `auto`; `kwargs()` returns the candidate form for `auto`. The job assistant gets the extended catalog and the sentence that Claude runs through the Claude Max subscription, causes no API costs and steps in automatically when Codex is empty.
- UI (`app.js`): preset buttons **„Opus 5 · Claude-Abo“** and **„Automatisch · Codex, sonst Claude“**; for `auto` the summary shows both models; the job status gets a line „Aktueller Anbieter: Codex (Wochenfenster 62 %) · Claude bereit“ (current provider: Codex (weekly window 62 %) · Claude ready) from the `provider_choice.json` of the last call; quota messages name the reset time.
- CLI: `--backend claude_code|auto`; `--model` and `--reasoning-effort` apply to the fixed provider, and for `auto` the catalog defaults apply. `pla doctor` gets the checks `claude_login` (version, login, subscription type) and `subscription_quota` (Codex window in percent with reset, Claude block if active); neither blocks `ready` as long as at least one provider is usable.
- New command `pla quota [--json]`: prints the same overview without a project and is the quickest diagnosis when a run pauses.

## 4. Phases

**Phase 0: spike (half a day).** A script in the scratch area that (a) checks `stream-json` with `--json-schema` and confirms the last line with `structured_output`, (b) provokes a call deliberately too large for `--max-budget-usd 0.001` and records the error envelope, (c) checks whether `~/.claude/CLAUDE.md` flows into a call with `--setting-sources ""` (marker line), and (d) measures `--system-prompt` with a minimal text. The result is added to this document as a table; only if (a) fails does Phase 1 start with `--output-format json`.

**Phase 1: adapter and quota module (1 to 2 days).** `claude_code.py`, `subscriptions.py`, the extension of `subscription_environment`, `CallActivity.observe_claude`, catalog entries in `text_settings.py`, `pla quota`, doctor checks. Tests with a fake `claude` program file (a Python script like `SERVER` in `tests/test_codex_stream.py`) that simulates success, schema violation, a limit error with reset time, a login error and search events; a fake app server for `account/rateLimits/read`; table-driven tests of the selection rule (both available, only Codex, only Claude, none, Codex cache expired). Acceptance: `pla text-probe --backend claude_code` returns a validated probe with metadata; `pla quota` shows both subscriptions.

**Phase 2: selection in the script run (1 to 2 days).** `AdapterPool`, the `auto` form of `text_generation`, the per-call switch with `provider_switch.json`, the status monitor. Tests: a script run whose Codex fake reports a limit at the third call switches to Claude without repeating finished stages and completes; a resume with a changed candidate form is rejected as `inputs_changed`; both fakes empty → `waiting_for_quota` with the earlier reset; call counter and budget projection unchanged. Acceptance: `pla script --backend auto` on the pilot project with Codex at 100 % runs completely through Claude.

**Phase 3: Studio (1 day).** Presets, assistant catalog, provider line in the status, doctor display in the Studio, `docs/studio.md`, the README provider section, `docs/windows-quickstart.md` (Claude login with `claude auth login`, check with `claude auth status --json`). UI tests in `tests/studio_ui.test.cjs` for preset rendering and the status line.

**Phase 4: research through Claude (1 to 2 days, optional).** `search=True` in the Claude adapter with `WebSearch`/`WebFetch`, proof through `web_search_requests` and `tool_use` events, `observed_search_queries` from the tool inputs, `search_events.json` in today's format. `run_research` and the supplementary research path use the pool. Acceptance: a research run without Codex quota produces a dossier with evidenced search events; `research_performed` stays `false` when events are missing, and the run is rejected as today.

## 5. Risks and open points

- **Claude quota cannot be read in advance.** For Claude, the rule "both have quota" is an assumption until the first error. The price is a single failed call, after which the block applies; the message in the Studio must explain this.
- **Double system prompt share.** Claude Code adds its own system prompt (about 9 K tokens, mostly from the cache). Phase 0 measures whether `--system-prompt` lowers this share; otherwise it has to be priced in as quota consumption per call.
- **Two models in one run.** With `auto`, draft and review can come from different models. The reviews are designed for this (independent reviewers), but `reports/script_quality.yaml` must show the actual provider per call so that a result stays traceable.
- **Version drift of the CLI.** Flags such as `--setting-sources` and the envelope are as of 2.1.92. The adapter checks `claude --version` against a minimum version and stores it per call; doctor warns on a deviation.
- **Output limit per answer.** The CLI does not know `claude-opus-5` in its model table and limits the output to 32,000 tokens without the setting; a dossier over eight reviewed answers exceeded that on 20 September 2026. The adapter therefore sets `CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000`, a truncated answer ends as `claude_output_limit` and is not taken over, and the synthesis limits the material per composing call (`question_synthesis.ANSWER_BUDGET_CHARS`).
- **Credits.** Codex offers paid credits once the limit is reached; the plan ignores them deliberately. A later project switch can release them; `choose_subscription` must then take `credits.hasCredits` into account.
- **Hermeticity without `--bare`.** Phase 0 decides whether `~/.claude/CLAUDE.md` and user hooks really stay out with `--setting-sources ""`. If the test is negative, the way out is a separate `CLAUDE_CONFIG_DIR` with copied credentials, which needs extra care with file permissions.

## 6. Affected files

New: `src/podcast_automate/claude_code.py`, `src/podcast_automate/subscriptions.py`, `tests/test_claude_code.py`, `tests/test_subscriptions.py`, `tests/test_provider_pool.py`.

Changed: `codex.py` (`subscription_environment`), `codex_stream.py` (quota query as a function of its own), `call_activity.py`, `text_settings.py`, `scripting.py` (`text_generation_settings`, `build_adapter`), `script_pipeline.py` (`invoke` through the pool), `research.py` (selection and `invoke`), `studio.py` (`TextChoice`, bootstrap catalog), `studio_worker.py` (assistant, status monitor), `status_summary.py`, `doctor.py`, `cli.py` (`--backend`, `pla quota`), `web/app.js`, and these docs:

- `docs/studio.md` (now: [Choosing the text model](../STUDIO.md#choosing-the-text-model))
- `docs/windows-quickstart.md` (now: [Install on Windows 11](../OPERATIONS.md#install-on-windows-11) and [Check the subscriptions](../OPERATIONS.md#check-the-subscriptions))
- `docs/scripts.md` (now: [Choosing the provider on the command line](../SCRIPTS.md#choosing-the-provider-on-the-command-line))
- `README.md` (now: [Providers and models](../PRODUCT.md#providers-and-models))
- `SPEC.md` (§9 text backends) (now: [Text providers and model selection](../BUSINESS_LOGIC.md#text-providers-and-model-selection) and [Text provider adapters](../ARCHITECTURE.md#text-provider-adapters))

## 7. Implementation status

As of 19 September 2026, all four phases are implemented; tests are in `tests/test_claude_code.py`, `tests/test_subscriptions.py` and `tests/test_provider_pool.py`.

**Phase 0, measured with Claude Code 2.1.92 on the target machine:**

| Question | Result |
| --- | --- |
| (a) `--output-format stream-json` with `--json-schema` | Yes. The stream ends with a `result` line that carries `structured_output`. The answer is produced through a tool call `StructuredOutput`; `num_turns` is 2. With `--include-partial-messages`, `input_json_delta` parts of the answer arrive for the live display. |
| (b) `--max-budget-usd 0.001` | Envelope `subtype: "error_max_budget_usd"`, `is_error: true`, `errors: ["Reached maximum budget ($0.001)"]`. The first model call still takes place (0.035 USD equivalent); the limit takes effect only afterwards. |
| (c) Hermeticity with `--setting-sources ""` | A `CLAUDE.md` with a marker line in the working directory was not read; the answer reported "no marker". Personal settings (`~/.claude/settings.json` with a different model) did not apply. |
| (d) `--system-prompt` with a minimal text | Replaces the CLI system prompt completely: 849 instead of 8,841 newly cached and 691 instead of 8,658 read tokens, 0.009 instead of 0.064 USD equivalent per call. The adapter therefore uses a short fixed system prompt. |
| Additional finding | The stream contains a `rate_limit_event` with `status`, `resetsAt` and `rateLimitType` (`five_hour`). Successful calls store it as `rate_limit` in `metadata.json` and in the quota log; a rejected status supplies the exact reset time for the block. |

**Deviations from the plan:**

- No new field in `RuntimeSettings`: an additional field would have changed the project hash of every existing project and blocked their resumption. `claude` is found through the PATH, `~/.local/bin` and npm shims.
- `auto` is the preselection of new Studio projects (`text_defaults` in the bootstrap) and the first preset button; the programmatic default of `TextChoice` and of `text_backend` in `project.yaml` stays `codex_cli`, so that existing projects and the CLI without an option run unchanged.
- If both subscriptions are empty after a quota error, the run reports `subscriptions_exhausted` with both reset times instead of the original adapter error; the original error stays chained as the cause. If the other provider is not usable at all (not installed or not logged in), the original quota error is kept.
- With a fixed provider, the connection does not read the Codex quota again after an error (that would only serve the rule); a Claude block is recorded in every mode.
- Phase 4 is implemented: research and supplementary research follow the chosen provider; OpenRouter runs do their research through the subscriptions by the automatic rule.
- The location of the quota log can be overridden with `PLA_SUBSCRIPTIONS_STORE`; the tests use this so that they never write into `~/.podcast-automate`.

**Addendum of 2 October 2026, provider choice and quota:**

- A `rate_limit_event` with `allowed_warning` no longer counts as exhausted quota; only a rejected event blocks Claude. `classify_claude_failure` decides first by the structured fields (result `subtype`, rejected rate-limit event, error category of the last `assistant` event) and reads the text only after that, and then only whole words. Before, partial words and a warning event had made a format error, an expired login and a Node warning look like a quota block of all projects until the weekly reset.
- Under `auto`, an unusable subscription no longer leads to a stop but, like a quota error, to the other subscription: expired login, no subscription login, a CLI that is too old or missing (`provider_pool.UNAVAILABLE_CODES`). The quota log marks the subscription as unusable for ten minutes (`subscriptions.UNAVAILABLE_SECONDS`), after which it is checked again; a successful Claude call clears the mark. A fixed provider stops with the error.
- A quota pause keeps the reset time of the provider that reported the error instead of taking over that of the other subscription. Without a known reset, the automatic resume waits 30 minutes and doubles the wait with every further resume, that is one and then two hours (`subscriptions.RETRY_BACKOFF_SECONDS`, `quota_retry_at`). Before, three resumes were used up within 90 minutes.

**Addendum of 2 October 2026, output cap:** The output cap now follows the model: Opus 5.5 and Sonnet 5.5 get the 128,000 tokens that CLI 2.1.286 lists as default and maximum for both (`claude_code.MODEL_OUTPUT_TOKENS`); other model ids keep 64,000. The flat cap had cut off the table of contents of the Transformer series (18 episodes, 40 to 60 minutes) as `claude_output_limit`.

**Addendum of 29 September 2026:** Claude Code updated to 2.1.284; the CLI lists Sonnet 5.5 (`claude-sonnet-5-5`, native window 1,000,000 tokens, 128,000 output tokens, levels up to `max`, default `high`). A text probe through the subscription at `high` answered. Since then Sonnet 5.5 at `high` is the Claude default for the fixed and the automatic choice and for **„Weiter mit Claude“** (continue with Claude), instead of Opus 5.5; the adapter requires 2.1.284 for it (`claude_code.MODEL_MINIMUM_CLI`) and names `claude update`. Opus 5.5 stays selectable, and the advice on blocked sub-questions still asks Opus 5.5 at `xhigh` (`research_advisor.ADVISOR_MODEL`).

**Addendum of 26 September 2026:** Claude Code updated to 2.1.283 and Codex CLI to 0.157.1 (npm). The Claude default is now `claude-opus-5-5` with `xhigh` (2.1.92 rejects both; the adapter's minimum version is 2.1.280). The selection rule `auto` asks new runs for Claude first (`prefer: claude_code`) and switches to Codex only when the Claude quota is exhausted; saved runs keep their order. The stream envelope with `structured_output` is unchanged with 2.1.283 (text probe with Opus 5.5 at `xhigh`). The CLI reports a context window of 1,000,000 and 128,000 output tokens for Opus 5.5; the adapter's output cap (64,000) and prompt limit (300,000 characters) are unchanged.
