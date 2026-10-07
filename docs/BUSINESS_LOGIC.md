---
title: Business logic
doc_type: business-logic
status: current
last_reviewed: 2026-10-07
covers:
  - src/podcast_automate/provider_pool.py
  - src/podcast_automate/subscriptions.py
  - src/podcast_automate/text_settings.py
  - src/podcast_automate/claude_code.py
  - src/podcast_automate/web_search.py
  - src/podcast_automate/run_budget.py
  - src/podcast_automate/script_budget.py
  - src/podcast_automate/question_budget.py
  - src/podcast_automate/cost_estimate.py
  - src/podcast_automate/studio_allowances.py
  - src/podcast_automate/trial.py
  - src/podcast_automate/storage.py
  - src/podcast_automate/models.py
  - src/podcast_automate/episode_audio.py
  - src/podcast_automate/script_models.py
  - src/podcast_automate/script_artifacts.py
  - src/podcast_automate/script_checks.py
---

# Business logic

The cross-cutting rules every run follows. Stage rules are in [RESEARCH](RESEARCH.md), [SCRIPTS](SCRIPTS.md),
[TEACHING](TEACHING.md) and [AUDIO](AUDIO.md).

## Run kinds and order

Three run kinds build on each other within a project (stages: [ARCHITECTURE](ARCHITECTURE.md#pipeline-stages-and-run-kinds);
commands: [PRODUCT](PRODUCT.md#commands)).

| Run kind | Command | Ends with |
| --- | --- | --- |
| `research` | `pla research <project>` | the reviewed dossier and its quality report |
| `script` | `pla script <project> [--episode ep_001]` | series plan, teaching plans, reviewed scripts and the quality report, ready for reading |
| `episode_audio` | `pla audio <project> --episode ep_001 --approve-audio` | recording, assembly, check and export of one episode |

- There is no overall `run` command; the Studio starts the run kinds one after another.
- `pla doctor` checks the local prerequisites, the CLI logins and the audio backend before a first run.
- `research` and `script` end at the quality report, without audio approval. A script run without `--episode`
  handles all planned episodes; `--episode ep_001` pulls one script forward for editorial reading. A new `script`
  call plans anew; `resume` keeps the stored plan and the valid drafts.
- An `episode_audio` run records exactly one episode; only `--approve-audio` starts generation, assembly, check and
  export ([Human approvals](#human-approvals)). Exporting publishes nothing automatically.
- `series_review`, a fourth run kind (`pla series-review`), reviews the scripts of a published script run afterwards
  and cannot be resumed ([SCRIPTS](SCRIPTS.md#series-review)).

## Text providers and model selection

Presets, default models and minimum CLI versions: [PRODUCT](PRODUCT.md#providers-and-models); adapter mechanics:
[ARCHITECTURE](ARCHITECTURE.md#text-provider-adapters).

### Subscriptions first, never an automatic switch to paid APIs

- Text runs on the existing subscriptions through their official CLI logins: Codex CLI with a ChatGPT subscription,
  Claude Code with a claude.ai login (Claude Max). Two paid alternatives run only when you choose them: OpenRouter on
  your OpenRouter credit, and Claude Code on your own Anthropic API key (`claude_api`,
  [below](#claude-on-your-own-api-key)); likewise the web search through Perplexity on your Perplexity key
  ([Research runs and their web search](#research-runs-and-their-web-search)). A subscription user needs no API
  payment details.
- Subscription quotas apply to automated calls too. Finished stage results are saved; an exhausted quota pauses the
  run with `waiting_for_quota`, never producing incomplete final results or switching automatically to a paid API.
  Retries after technical or validation failures are bounded.
- Paid Codex credits are never used automatically (`credits` is ignored). Codex has quota when no window is at
  100 %, no `rateLimitReachedType` is set, no spend control is reached and ordinary usage is allowed
  (`subscriptions.normalize_codex_limits`).
- Changing the provider is an explicit decision: the rule `auto` or an
  [explicit switch](#switching-a-job-to-another-provider).

### Fixed providers and the rule `auto`

- A fixed choice (Sonnet 5.5, Opus 5.5, Astra, an OpenRouter model, Claude on the API key) never switches, stops even
  on an unusable subscription and never queries quotas for the rule.
- **„Automatisch“** (automatic) is the preset of new Studio projects (`--backend auto`); without `--backend` the CLI
  uses `codex_cli`.
- Under `auto` the provider is chosen before every model call, because quota changes during a run: Claude
  (`text_settings.AUTO_PREFERENCE = "claude_code"`) with Sonnet 5.5 at `high` over the Claude Max subscription while
  no Claude block is noted, then Codex with GPT-6 Astra at `xhigh` (both catalog defaults). Only when both are out
  does the job pause, until the earliest reset. Runs saved before 2026-09-26 with Codex first keep that order on
  resume.
- Under `auto` a prompt larger than Claude's context limit goes to Codex.
- A job can thus switch models; each call records its subscription and any switch within it
  ([ARCHITECTURE](ARCHITECTURE.md#run-folder-and-manifest)).

### Claude on your own API key

`claude_api` runs the Claude Code CLI on your Anthropic API key instead of the claude.ai login; every call is billed
to your Anthropic account (presets „Sonnet 5.5 · high · Anthropic-API-Key“ and „Opus 5.5 · high · Anthropic-API-Key“).
It is its own provider, so no rule written for the subscription `claude_code` applies to it (why: D-145):

- It is always a fixed choice: never a candidate of `auto`, never chosen automatically, and a switch to it is
  [explicit](#switching-a-job-to-another-provider).
- It queries no quota and writes nothing to the quota store `~/.podcast-automate/subscriptions.json`; an API rate
  limit never blocks the subscription.
- Every run on it needs a [money limit](#money-limit).
- Stops: `anthropic_rate_limit` pauses with `waiting_for_quota` and the Studio resumes it like a quota pause;
  `anthropic_credits` (no credit left) also pauses with `waiting_for_quota`, but is never resumed automatically,
  because waiting does not top up the account ([Automatic resume](STUDIO.md#automatic-resume));
  `anthropic_key_required` (no key),
  `anthropic_authentication` (key refused) and `claude_api_auth_mismatch` (Claude Code reports it used no API key)
  stop the run `blocked`. `claude_budget_cap` ends one call at `claude_code.MAX_BUDGET_USD` (12 USD), which on the key
  is money spent, not an equivalent value.
- The status brief is off for such a run (why: D-149), and the research advisor keeps the run's own model instead of
  Opus 5.5 at `xhigh` ([Advisor](RESEARCH.md#advisor)).
- Codex on an OpenAI API key is not offered; OpenAI models are available through OpenRouter (why: D-150).

### Quota: what counts as "limit reached"

- **Codex.** The Studio reads the quota from `account/rateLimits/read` of the local app server, at most every two
  minutes (`subscriptions.CODEX_CACHE_SECONDS = 120`) and right after a quota failure.
- **Claude.** Claude reports no quota in advance: a reached limit costs one failed call, then a block is noted until
  the reported reset in the account-wide store `~/.podcast-automate/subscriptions.json` (override:
  `PLA_SUBSCRIPTIONS_STORE`). Without a reported reset the block is conservative (`claude_code.claude_block_window`):
  5 hours for a session or Opus limit, until the next Monday 00:00 UTC for a weekly limit, 30 minutes for an unclear
  message. The block is noted in every mode, a fixed Claude choice included.
- Only a rate-limit event with which the CLI refused the request (`rejected`) counts as reached, never the warning
  `allowed_warning` of a window running low. Failures of both subscriptions are classified by the CLI's structured
  fields first, their text only afterwards and as whole words; `classify_claude_failure` checks the result `subtype`,
  a refused rate-limit event, the error category of the last `assistant` event, then the text. (why: D-025)
- A successful Claude call clears only block or unavailability notes recorded before it started
  (`subscriptions.record_claude_success`). (why: D-030)
- **Extra usage for Claude.** With the extra-usage switch on ([Studio settings](CONFIGURATION.md#studio-settings),
  `subscriptions.claude_extra_usage`), every call tries Claude despite a stored block; a refusal costs one failed
  attempt, then the run moves to Codex or pauses as usual. (why: D-031)

### An unusable subscription under `auto`

- Unusable means an expired login, no subscription login, a CLI that is missing, cannot start or is too old, a
  Codex call that failed for a reason the adapter cannot name (`codex_failed`, since 2026-10-04; why: D-130), or a
  Claude call that failed so (`claude_failed`, since 2026-10-07; why: D-155) (`provider_pool.UNAVAILABLE_CODES`).
  A failed turn's output is lost either way, so the call loses nothing by moving. Under `auto` such a failure moves the call to the other subscription as a quota
  failure does, and the rule passes the failed one over for ten minutes (`subscriptions.UNAVAILABLE_SECONDS`, 600 s,
  as long as a cached login is trusted). For a login reason (`subscriptions.LOGIN_REASONS`) it re-checks the login at
  every choice, so a new login such as `claude auth login` counts at once. (why: D-026)
- If the other subscription cannot take over either:
  - both out of quota: the run pauses with `subscriptions_exhausted`, naming both reset times;
  - current one out of quota, other unusable: the quota failure stays and the run pauses;
  - current one unusable, other out of quota: the run stops with the unusable one's own failure, which you can fix
    now, instead of waiting for the other's reset.

### Pauses and reset times

A quota pause takes the reset time of the provider that reported the limit (`subscriptions.quota_retry_at`): the one
named in the failure, else that provider's stored one. Without a known reset the automatic resume waits 30 minutes,
doubling with each further resume up to eight hours (`subscriptions.RETRY_BACKOFF_SECONDS = 1800`). (why: D-027)
When the Studio resumes by itself: [STUDIO](STUDIO.md#stopping-and-resuming).

### Lower effort for two stages

Two stages ask at most at `medium`, even when the run is set higher (`text_settings.STAGE_EFFORT_CAPS`): the
first-time reader (`listener_readback`), which should take in only what the dialogue itself explains, and placing the
expression tags for the recording (`audio_expression`). Evidence review, teaching review, writing and every correction
keep the run's level. (why: D-023)

### Research runs and their web search

Live research and the supplementary web research of script runs search the web in one of two ways, chosen when a run
starts (Studio setting **„Websuche“** (web search), `--web-search model|perplexity`; why: D-151):

- **Through the text model** (`model`, the default): the chosen subscription searches with its own tools, or with
  `claude_api` Claude Code's own WebSearch and WebFetch, billed to your key. With OpenRouter text, and with `auto`, the
  search calls follow the automatic rule (Claude, else Codex) with the catalog defaults, because OpenRouter has no
  search tools; those search calls bill nothing.
- **Through Perplexity** (`perplexity`): the run's own text model plans the queries, Perplexity's Search API runs them
  on your Perplexity key, and the same model chooses its sources from those results only
  ([Web search through Perplexity](ARCHITECTURE.md#web-search-through-perplexity)). Every text model can research
  this way, an OpenRouter model included, so research then needs no subscription at all. Every search request is
  billed, so such a run needs a [money limit](#money-limit) even on a subscription.

`pla research --backend` accepts `codex_cli`, `claude_code`, `claude_api` and `auto`, and `openrouter` only with
`--web-search perplexity` (otherwise `invalid_backend`). The web search is bound to the run like its text model
([next section](#the-choice-is-bound-to-the-run)).

### The choice is bound to the run

- The text provider choice is fixed when a script or research run starts (`script_request.json`,
  `research_request.json`) and is part of its inputs; for `auto` both candidates (`claude-sonnet-5-5`/`high`,
  `gpt-6-astra`/`xhigh`) and the first choice (`prefer: claude_code`) are stored.
- Resume uses the stored choice: a different `--backend` or candidate list is refused as a changed input. Changes
  apply to new runs; the only exception is an explicit switch (next section).
- The web search is part of the same saved selection: `web_search: perplexity` with the search adapter's version
  (`web_search_version`; `provider_pool.with_web_search`); a selection that searches through the text model adds
  nothing, so runs saved before keep their hash, while a research run without an explicit text choice then saves the
  Codex default with the search. A resume keeps the run's web search whatever the settings say now, and a newer search adapter needs a
  new run (`inputs_changed`).
- Audio runs store the audio provider and both voices separately; resume keeps them too.
- Checkpoints stay valid across a provider change, being bound to the prompt text, not the provider, so a draft and
  its review may come from different models. `reports/script_quality.yaml` names the selection, each call its
  provider.

### Switching a job to another provider

Every script and research job can continue with another text provider at any time: in the Studio with
**„Weiter mit …“** (continue with …) under the job's saved text choice ([STUDIO](STUDIO.md#choosing-the-text-model)),
on the command line with
`pla approve <project> --run-id <run_id> --text-switch claude|astra|claude-only|astra-only|openrouter|claude-api [--switch-model MODEL] [--cost-usd N]`
(without a value `claude`; `run_budget.TEXT_SWITCHES`):

- **Claude, else Astra** (`claude`) and **Astra, else Claude** (`astra`): the subscription asked first answers until
  its quota is spent, then the other takes over.
- **Only Claude** (`claude-only`) and **only Astra** (`astra-only`): the job stays on one subscription.
- **OpenRouter** (`openrouter`): one model from the list (`--switch-model`), paid per call, with the key; web searches
  stay on the subscriptions, because OpenRouter has no search tools, unless the run searches through Perplexity.
- **Claude on the Anthropic API key** (`claude-api`): Claude on your key, Sonnet 5.5 or a Claude model given with
  `--switch-model`, paid per call; web searches run on the key too.

Rules of a switch (why: D-024):

- Astra works over the Codex subscription at `xhigh`; Claude, on the subscription or the key, at `high` with the
  catalog default Sonnet 5.5, even when the job started with Opus (on the key `--switch-model` may name another Claude
  model).
- A switch to a billed provider (OpenRouter, `claude-api`) needs the run's [money limit](#money-limit): `--cost-usd`
  (Studio: the cost field next to the choice) sets it with the switch; without one the switch is refused with
  `cost_limit_required`.
- The receipt `runs/<run_id>/text_switch.json` applies from the job's next start. A later choice replaces it;
  choosing the original selection removes it.
- Inputs, hash, checkpoints and approvals stay unchanged, and `script_request.json` or `research_request.json` keeps
  the original selection; finished work stays valid.
- A switch changes only the text model: a run that searches through Perplexity keeps that search
  (`run_budget.approve_text_switch`).
- The status brief, the quality report, the expression level of a later recording and the handover of the OpenRouter
  and the Anthropic key follow the current choice; a job receives the OpenRouter key only while it works with
  OpenRouter, the Anthropic key only while it works with `claude_api` ([SECURITY](SECURITY.md#secrets-and-keys)).

## Budgets

### Default limits

- New projects get 750 model calls, 48 search rounds and 150 source candidates per research or script run
  (`ResearchLimits.model_calls`, `.search_rounds`, `.sources` in `models.py`), and no money limit
  ([Money limit](#money-limit)). A saved project keeps its explicit limits; the Studio settings set them for every
  project ([CONFIGURATION](CONFIGURATION.md#studio-settings)). (why: D-034)
- Every model decision and every independent review counts; local search and reading need neither a model nor the
  network. The source limit also counts failed and duplicate fetches ([RESEARCH](RESEARCH.md#limits-and-resume)).
- Limits never cut the agreed topic scope automatically; a run that reaches a limit stays saved with its concrete
  gaps.
- A call without a model answer (time limit, stalled stream, quota pause, abort) is not charged: `budget.json` lists
  it under `refunded`, and call numbers stay unique; on a key its money still counts. A charged failure (invalid
  output, a failed provider turn) keeps its charge.
- **„Auftrag anhalten“** (stop job) ends the worker hard; its open calls keep their reservation until `resume`
  releases each that left neither an answer nor a charged failure, in research and script runs alike
  (`research.reconcile_budget`).
- The short status reports have their own counter outside the production budget
  ([STUDIO](STUDIO.md#progress-and-telemetry)).
- The editorial conversation has its own persistent call limit: the project's `research_limits.model_calls`, or a
  raise by 50 with **„Gesprächslimit auf … erhöhen“** (raise the chat limit to …), for this project without
  changing the brief (`studio/assistant/limit.json`).

### How a run stops on its budget

- A research run stops with `research_budget_exhausted` at its call or search-round limit (`research.reserve_call`);
  the state so far stays saved, and the stop names the limit it reached in its manifest entry
  ([Run folder and manifest](ARCHITECTURE.md#run-folder-and-manifest)). A sub-question whose web search the run's
  search-round or source limit ends records that cause too ([Run limits](RESEARCH.md#run-limits)).
- After the scope check the Studio and the question report show the **minimum need of further calls** (dossier and
  closing reviews included) and the available budget. Finished answers and reusable checkpoints lower the need; more
  search, reading and corrections may raise it. If the budget does not cover even the minimum, the run stops with
  `research_budget_insufficient`; answers and topic scope stay. Optional calls may not use the closing reserve.
- A script run stops with `script_budget_insufficient` when the approved limit does not cover its
  [lower bound](#script-lower-bound-and-expected-calls).
- A run billed to a key stops with `cost_limit_required` or `cost_limit_reached` at its [money limit](#money-limit).

### Research projection

Before its first sub-question a research run stops at its plan with a projection ([a gate](#human-approvals); process
and stored fields: [RESEARCH](RESEARCH.md#scope-check-and-plan-approval)).

- **Projected calls** = open sub-questions × calls per sub-question + calls for the dossier and the closing review;
  **hours** = these calls × the duration per call.
- **Calls per sub-question** are measured, not fixed: once three sub-questions of the run are verified, the median of
  the calls they actually needed (recorded per call under `call_timings` in the question state; rejected answers
  count). Before that, the value the project's last published research
  run left in `research/calibration.json` (`calls_per_task`, `seconds_per_call`, `tasks`, `verified_tasks`, `date`);
  without history **16** (`question_budget.DEFAULT_CALLS_PER_TASK`), since the 30 verified sub-questions of the two
  Opus 5.5 runs of 26 and 27 September 2026 needed a median of 15.5 calls (10 to 55 without one outlier).
  (why: D-035)
- **Duration per call**, in the same order: this run's median once three calls are answered (each answered call
  writes `calls/call_NNN/timing.json`; source search, planning and scope check always precede the stop), else the
  project value, else 240 seconds (`question_budget.DEFAULT_SECONDS_PER_CALL`).
- **Sub-questions the limit carries** = (limit − used − closing reserve) ÷ calls per sub-question, rounded down, at
  least 1 (`question_budget.affordable_tasks`; closing reserve `question_budget.CLOSING_RESERVE`, currently 4): 46
  for the default 750 calls at 16 per sub-question when few calls are used ((750 − 4) ÷ 16, rounded down).
- Planning receives this number. A larger plan is requested again up to twice with this hint; then the minimum
  projection decides. The Studio and the question report show this experience value next to the minimum need.
- **Sources and search rounds** are projected for the run's end the same way: used so far plus the open sub-questions
  times the sources (or search rounds) per sub-question, rounded up (`question_budget.plan_projection`). The rates are
  what the project's last published research run used per sub-question after its plan gate (`sources_per_task` and
  `search_rounds_per_task` in `research/calibration.json`), else 5 sources and 2 search rounds
  (`question_budget.DEFAULT_SOURCES_PER_TASK`, `DEFAULT_SEARCH_ROUNDS_PER_TASK`): the final research runs of
  30 September 2026 used 4.4 to 4.5 sources and 1.5 to 1.7 search rounds per sub-question (Asimov, Ontologies),
  rounded up. (why: D-155)
- **Limits that carry the plan** (`raise_to`): when the projected calls, search rounds or sources exceed their limit,
  the projection names what one approval would set. A limit that does not fit rises to what is used plus the expected
  rest and a tenth more, rounded up; one that fits keeps its value; while all three fit, `raise_to` is `null`. The
  plan-approval message names the limits that rise. So the plan gate asks once at the right size, instead of the run
  stopping on these limits later: 9 of the 20 stops that needed the user in three series of 30 September to 4 October
  2026 came from them. The raise stays your explicit click before the plan approval
  (`run_budget.approve_model_call_limit`), never a pre-approval. (why: D-155)

### Script lower bound and expected calls

Without repairs a script run needs at least one call for the table of contents, nine per episode
(`script_budget.STAGE_CALLS`) and, for a complete series, one for the series review:

| Stage | Calls per episode | Purpose |
| --- | --- | --- |
| `teaching` | 2 | teaching plan and independent review of the plan |
| `writing` | 1 | dialogue draft |
| `polishing` | 2 | spoken-language pass and before/after comparison |
| `review` | 4 | source review, listener readback, editorial review, teaching review |

- Every rejected version costs more: up to three script repairs with a new review, two polishing corrections, two
  teaching-plan revisions, one targeted correction round, and up to three supplementary research runs per episode. A
  series repair costs three calls per affected episode, up to five with its second attempt
  ([SCRIPTS](SCRIPTS.md#series-review)).
- **Lower bound.** Before every paid stage the run computes the minimum of remaining calls from the unfinished
  episodes and saves it in `runs/<run_id>/budget_projection.json`; an episode whose polishing kept the reviewed draft
  counts as polished. If the approved limit does not cover it, the run stops with `script_budget_insufficient`
  before any money is spent; finished checkpoints stay, and after an explicit raise `resume` continues.
- **Expected calls** (`expected_remaining_calls`) scale the episode stages by the actual calls per episode of the
  project's last completed script run (run, calls and episodes under `calibration`); without one they equal the lower
  bound. They are a hint; only the lower bound stops a run. The Studio shows the lower bound next to the call counter
  and, once calibrated, the expectation; a stop card suggests a new limit from the expectation, without a calibration
  from the lower bound plus a quarter for corrections.
- **What 750 calls carry.** Arithmetically 83 episodes without repairs (1 + 83 × 9 + 1 = 749). The completed runs
  Asimov and Ontologies actually needed 31 and 42.5 calls per episode (measured on 2026-10-02), because repairs, new
  reviews, supplementary research and series corrections come on top; at that rate 750 calls cover about 17 to 24
  episodes. Longer series or many corrections need a higher limit.

### Money limit

A run whose calls bill your key, on OpenRouter, on `claude_api` or through the Perplexity search, also has a limit in
USD (why: D-146, D-148, D-151):

- **Required, without a default.** `research_limits.cost_usd` (above 0, at most 100 000;
  [CONFIGURATION](CONFIGURATION.md#project-brief)) stays unset until you set it: on the settings page as
  **„Kostengrenze je Lauf in USD“** (money limit per run in USD), for one run with `pla approve --cost-usd N`, in a stop
  card or with a [switch](#switching-a-job-to-another-provider). The settings page refuses to save a billed text model
  or the web search „Über Perplexity“ (through Perplexity) without it (`cost_limit_required`). Subscription runs that
  search through the text model count no money.
- **Billed calls.** A call is billed when it goes to OpenRouter or to `claude_api`, and a search call when the run
  searches through Perplexity, whatever its text model (`provider_pool.AdapterPool.billed`). Otherwise the web searches
  of an OpenRouter run go to the subscriptions and bill nothing, so a research job with OpenRouter text and the model's
  search needs no limit.
- **Stops.** Before every billed call `research.reserve_call` checks the limit: without one the run stops with
  `cost_limit_required`, once the money spent has reached it with `cost_limit_reached`, both `blocked` and before the
  call is charged; the state so far stays saved. A call already running may overshoot the limit by its own cost; in
  parallel mode up to five calls run at once (`execution.MAX_PARALLEL_TEXT`; see V-32). The Studio refuses a billed
  action (chat, plan, script, revision, research on `claude_api`, expression tags, companion kit; with the Perplexity
  search also research, plan, script and revision on any text model) without a limit before a worker starts.
- **Counting per attempt.** Every billed attempt leaves a row in `calls/call_NNN/billing.json` (`started`, then
  `priced`, `unpriced` or `free`); a repeat after a stall or a wrong format, a rejected answer and a cut answer cost
  money too. A search through Perplexity adds one row per search request at `web_search.USD_PER_REQUEST` (0.005 USD),
  and the rows of its query-planning step count with its call. `research.settle_call` adds a call's rows to `budget.json` once (`billed_usd`, `priced_attempts`,
  `estimated_usd`, `unpriced_attempts`, `settled`). An attempt without a reported cost (time limit, stall, killed
  worker) counts at the run's mean per priced attempt, before the first one at the higher measured value of its model
  (`cost_estimate.fallback_usd`). A refunded call returns its call, not its money. On resume `research.reconcile_budget`
  settles what a killed worker left. A subscription run's `budget.json` gets none of these keys.
- **Spent** = `billed_usd` + `estimated_usd` + `external_usd`. The billed amount is the cost the provider reports:
  OpenRouter's, or Claude Code's own `total_cost_usd`, an estimate at API prices; the Anthropic Console bill is
  authoritative (see V-30); a Perplexity request counts at the fixed price per request, and Perplexity's usage page is
  authoritative (see V-35).
- **Jev.** With a limit set, a script run stops before the Jev scan once its limit is reached; the scan's money counts
  as `external_usd` when the scan is complete (`research.settle_external`).
- **Side jobs.** Expression tags and the companion kits of an episode and of the whole podcast each run under a
  small call allowance of their own with the
  project's money limit; the editorial conversation counts its money in `studio/assistant/budget.json` against the same
  limit.
- **Projection.** The projections of a billed run (a research run's budget projection in its question state and its
  plan projection, a script run's `budget_projection.json`) gain a `cost` block (`cost_estimate.cost_view`): limit,
  spent, money per call and its origin, expected remaining and total, unpriced attempts. The money per call comes from
  the run's own mean after ten priced attempts, else the project's last completed run of the same kind and model
  (subscription runs count, since Claude Code reports their value at API prices), else the table below, else unknown.
  It is a hint; only the limit stops a run. Other projections stay as they were.

`cost_estimate.DEFAULT_USD_PER_CALL`, the mean Claude Code `total_cost_usd` per call over the completed runs created
2026-09-26 to 2026-10-04, measured on 2026-10-07:

| Run kind | Sonnet 5.5 | Opus 5.5 |
| --- | --- | --- |
| research | 0.37 USD (3,042 calls) | 0.75 USD (1,439 calls) |
| script | 0.83 USD (1,071 calls) | 1.17 USD (735 calls) |

A whole series (research and scripts) took 1,389 to 1,771 calls, worth 605 to 809 USD at API prices, with 33 to 35
hours of research and 45 to 54 hours of script work (three series, measured on 2026-10-07).

### Raising a limit

- Call, search-round, source and money limits are raised per run explicitly:
  `pla approve <project> [--run-id <run_id>] --model-calls N --search-rounds M --sources Q --cost-usd N` (without
  `--run-id` the project's last run) or the buttons in the job status.
- The raise is a receipt `runs/<run_id>/budget_approval.json`, bound to the run id and the run's input hash.
  Counters, checkpoints, project configuration and outline or audio approvals stay untouched; the next call takes the
  raise into account.
- A new limit must be a whole number at least as high as the current one, otherwise the approval is refused with
  `invalid_budget_approval`; a money limit is an amount in USD above 0 (at most 100 000), and it too can only rise.
  Raising one limit keeps an earlier raise of the others, a money raise included. Receipts written before the money
  field still validate.
- The run uses the higher of its own receipt and the project's current limit (`run_budget.effective_limits`). A
  receipt of another run or input hash, or an unreadable one, stops the run with `invalid_budget_approval`.
- In the Studio the stop cards „Kostengrenze fehlt“ and „Kostengrenze erreicht“ suggest a money limit: the larger of
  1.5 times the limit and 1.25 times the money spent, or without a limit the larger of 10 USD and twice the money
  spent ([STUDIO](STUDIO.md#stop-reasons)).

### Pre-approvals

Under **„Ohne Rückfrage“** (without asking) on the settings page you set what the Studio may give a stopped run by
itself, the **„Vorab-Erlaubnisse“** (pre-approvals; storage: [CONFIGURATION](CONFIGURATION.md#studio-settings),
display: [STUDIO](STUDIO.md#stopping-and-resuming)):

- **„Neue Anläufe je Lauf“** (fresh attempts per run, up to three; `studio_allowances.FRESH_ATTEMPT_CHOICES`) when a
  step has used up its automatic corrections: the stops whose card offers **„Mit neuen Anläufen fortsetzen“**
  (continue with fresh attempts), and only where the run would accept them (`run_budget.fresh_attempts_available`),
  a script correction loop that keeps no rejections included ([Rejected answers](SCRIPTS.md#rejected-answers)).
- **„Aufruflimit erhöhen je Lauf“** (raise call limit per run, by up to 100, 250, 500 or 1000 calls;
  `studio_allowances.EXTRA_CALL_CHOICES`) when the approved limit is not enough, each time by the need the stop card
  suggests, at most by the rest still allowed.
- The Studio's scheduler then writes the same approval as the button and resumes the run within half a minute, also a
  run set aside. The hold card announces it, the engine room counts what was given, and `studio/allowance_log.json`
  records every use.
- An allowance is applied only when the run can actually start (project free, a slot free). If the resume then fails,
  the scheduler resumes later without spending the allowance again.
- **A new workspace** (no `projects/.studio-settings.json` and no project yet) starts with 2 fresh attempts and 250
  extra calls (`studio_allowances.NEW_WORKSPACE`): its settings page shows them, and its first project gets them in
  `studio/allowances.json`. Two fresh attempts per run covered every one granted in the three series of 30 September
  2026, and 250 extra calls the research overshoot of two of their three topics. Existing workspaces keep their
  values; a second project of a new workspace gets none (0/0) until the settings page is saved, which then sets them
  for every project. (why: D-155)
- Without a setting every stop waits for you. Editorial decisions, an exhausted search-round or source limit and a
  money limit never follow from an allowance: a pre-approval never sets or raises money
  ([Decisions that stay yours](#decisions-that-stay-yours)). (why: D-036, D-146)

### Trial project

A trial project („Probelauf“, trial run) runs the whole pipeline once on a narrow topic for little money and time
(`trial.py`; why: D-157). It is an ordinary project whose brief carries `trial: true`
([Project brief](CONFIGURATION.md#project-brief)); `pla init <project> --trial` without `--topic` takes the sample
topic of the brief's language, „Wie entsteht ein Regenbogen?“ or "How does a rainbow form?" (`trial.TRIAL_TOPICS`).

| Limit | Trial value | Where it applies |
| --- | --- | --- |
| Sub-questions of the research plan | 3 (`trial.TRIAL_SUB_QUESTIONS`) | `trial.plan_cap`, applied by `QuestionResearch.planning_allowance`, below any requested cap; a smaller `--max-tasks` still applies |
| Planned total duration | at most 20 minutes (`trial.TRIAL_MINUTES`) | `target_total_minutes`, a planning wish ([Episode and series length](#episode-and-series-length)) |
| Model calls, search rounds, sources per run | 110, 16, 40 (`trial.TRIAL_LIMITS`) | Research and script runs alike |
| Money limit per run | at most 45 USD | Only lowers a limit you set |

- `storage.load_project` keeps each limit at the lower of the project's (or the workspace settings') value and the
  trial value, so the [Studio settings](CONFIGURATION.md#studio-settings) never lift a trial. A money limit is only
  lowered, never introduced: unset, it still refuses every billed call (`cost_limit_required`), and setting one stays
  your decision.
- A raise for one run ([Raising a limit](#raising-a-limit), a stop card, a pre-approval) works as for every run.
- Why these values: the completed runs measured 16.4 to 23.4 calls per sub-question, so three take up to about 70,
  and discovery, planning, an advisor call and the closing calls about 15 more; 110 leaves a margin above that. The
  same limit carries the one-episode script run: its lower bound is 11 calls, and the completed script runs took 31 to
  42.5 calls per episode (2026-10-02). 45 USD covers each run of a trial on Sonnet 5.5 at the
  [measured rates](#money-limit): research about 85 calls at 0.37 USD, the script run about 44 calls at 0.83 USD. On
  Opus 5.5 a run may reach it and stops with `cost_limit_reached`. Whether a trial ends within its limits is open
  (see V-48).

## Human approvals

- Paid jobs start only through your actions on the page. Opening, navigating and playing stored Qwen or Gemini
  samples use no model calls; new Gemini samples and Gemini recordings use your API credit, and text jobs on
  OpenRouter or `claude_api` your OpenRouter or Anthropic account and searches through Perplexity your Perplexity
  account, within the [money limit](#money-limit).
- Navigation and reloading start no model calls and grant no approvals. The chat grants no plan or audio approval;
  **„Diese Auswahl übernehmen“** (accept this selection) only saves the reviewed summary. Intermediate states in the
  live window are unreviewed and grant no approval.

### Research plan

- The research plan approval is a gate: a research run stops after source search, planning and scope check, before
  the first sub-question, and uses no further model call until the plan is approved.
- The approval is a receipt `runs/<run_id>/plan_approval.json`, bound to exactly this plan and written only by an
  explicit action: **„Plan freigeben und starten“** (approve plan and start), or **„Plan freigeben und Limits
  anheben“** (approve plan and raise limits) when the plan needs higher limits, in the Studio, or
  `pla approve <project> --research-plan [<run_id>] [--max-tasks N]`.
- No automatic resume passes this stop, neither after a subscription reset nor after a technical stop; without
  approval `pla research`, `pla resume` and every Studio job stop at the plan again.
- `pla research <project> --approve-plan` starts a run without this stop: the run writes the receipt itself and needs
  no approval on later resumes either.
- Process, receipt fields and plan cap: [RESEARCH](RESEARCH.md#scope-check-and-plan-approval).

### Table of contents

**„Plan freigeben & Skripte schreiben“** (approve plan & write scripts) approves exactly the displayed plan state and
starts the elaboration. Only a finished draft can be revised or approved. An interrupted planning grants no script
approval.

### Audio

- `pla audio` needs `--approve-audio`, a current quality report without blocking findings for the episode and, for a
  series-reviewed run, a passed series verdict. A passed report does not replace the audio approval.
- In the Studio the approval is the checkbox on the **„Vertonung“** (recording) page, confirming the read script state
  with the displayed provider and voices ([AUDIO](AUDIO.md#recording-flow)). Previews receive no audio approval.
- The approval is bound to the episode's script hash, the audio provider and both voices
  (`episode_audio.saved_approval`), and for Gemini with expression to the expression tags that were read; tags placed
  anew after reading need a new reading. A changed pause rule is audible and needs a new approval, and so does a
  changed pace of the project's language; the pace of another language is not part of the approval
  ([Speaking pace per language](AUDIO.md#speaking-pace-per-language)).
- A series repair invalidates the audio approval of a corrected episode, because it is bound to the old script hash.
  A spoken form is not an editorial decision: re-rendering one segment uses the saved approval
  ([AUDIO](AUDIO.md#re-rendering-one-segment)).
- The approval of a whole run covers its automatically reviewed scripts and bounded repairs. Before each rendering the
  current quality checks and the approved input hashes are logged; automatic text corrections within the run need
  passed checks again.
- A manual change to inputs or production configuration needs a new audio approval. Merely resuming an unchanged run
  keeps its approval; an interrupted audio run still needs its matching approval.

### Decisions that stay yours

Accepting a gap, deciding a disputed objection, finishing with remaining objections, requesting a new teaching plan,
approving plans and audio, raising an exhausted search-round or source limit, setting or raising a money limit, and
switching to a provider billed to a key are never taken automatically. Pre-approvals cover only fresh attempts and
call-limit raises ([Pre-approvals](#pre-approvals)). Blocked
sub-questions and their decisions: [RESEARCH](RESEARCH.md#blocked-sub-questions-and-decisions).

## Runs, resume and input binding

- **Statuses.** A run or stage is `pending`, `running`, `completed`, `waiting_for_quota`, `blocked` or `failed`
  (`models.State`).
- **Every step is resumable.** Finished results are bound by hash; changed inputs force a new run instead of a silent
  recomputation. Checkpoints are bound to the hash of the complete prompt, so a text change repeats only the affected
  calls ([ARCHITECTURE](ARCHITECTURE.md#prompts-and-versioning)).
- `pla status` shows progress, finished episodes and concrete pause or failure reasons. `pla resume` continues the
  last interrupted run with its unchanged valid results; `--run-id <run_id>` picks another run.
- **Content changes need a new run.** A content change to `project.yaml` or to a local source file needs a new run and
  still counts as a changed input after a resume.
- **Operational fields do not bind a resumed run** (`storage.bound_brief`, `OPERATIONAL_FIELDS`,
  `RESEARCH_OPERATIONAL_FIELDS`): `runtime` (for example deadlines and program paths) and `research_limits` in every
  run kind, in research also `voice_profile`, `tts_backend`, `text_backend` and `host_names`. They enter the input hash
  as the run's `project_snapshot.yaml` recorded them at the start, so a higher limit or a longer time limit saved while
  a run waits keeps it resumable; the resumed run uses the current values. (why: D-038)
- In the Studio, changing the brief, host names, editorial notes or spoken forms while a run that depends on them
  rests (a table of contents awaiting approval included) first shows a warning and a question; afterwards that run can
  no longer be resumed. Changes on the settings page ask no such question.
- **Stopping a job.** **„Auftrag anhalten“** keeps finished stages and Qwen sections; the model call or section running
  at that moment may have to be repeated. **„Fortsetzen“** (resume) uses the stored inputs and releases open call
  reservations ([Budgets](#default-limits)). Process handling and the automatic resume:
  [STUDIO](STUDIO.md#stopping-and-resuming).

## Episode and series length

### Series length follows the content

- There is no fixed total length or episode count, and no general minimum or maximum length of a series. The length
  follows from the sub-questions, the necessary foundations, the dependencies between explanations and the requested
  depth: the content needed is planned first, and the episodes and their estimated total duration follow from it.
  Complex topics get the additional episodes their thorough explanation needs.
- The estimated total duration, the sum of the episode durations, is a result of planning and need not hit a given
  range of hours. A topic that needs more room gets further episodes justified by content, which is no quality
  defect. Well-delimited content that does not fit an episode's time budget moves to a further episode; evidence,
  examples and counter-positions must not be dropped wholesale.
- Additional episodes and changes of the estimated total duration must be traceable from the series plan.
- Only an explicitly wanted total duration is recorded, with `pla init <project> --total-minutes <minutes>`, as a
  planning wish (`target_total_minutes`; no default, `null`, no implicit total limit). The plan weighs it against the
  content needed; if the requested depth does not fit, it states the additional listening time needed or concrete
  scope changes, and content is not cut silently.

### Episode length

- An episode lasts at most 60 minutes (`script_models.MAX_EPISODE_MINUTES`). (why: D-128)
- **One episode is one MP3**, whatever its length; the recording is not split (`episode_audio`). Until 2026-10-04 it
  was split into parts of at most 30 minutes. (why: D-128)
- `max_episode_minutes` in `project.yaml` is fixed at 30 and unused since then; it stays because every run's project
  hash includes it, and removing it would refuse every resume with `inputs_changed`. Series planning does not see it,
  because the planner reads only the editorial fields of the brief ([SCRIPTS](SCRIPTS.md#series-plan)). (why: D-070)
- The series plan sizes `target_minutes` per episode by the explanation needed, 15 to 60 minutes
  (`prompts/series_plan.txt`); one connected explanation stays in one episode.

### Estimating and checking the duration

- Script duration is estimated from the spoken words, the speaking rate and the planned pauses: 130 words per minute
  including pauses for planning (`script_artifacts.SPOKEN_WORDS_PER_MINUTE`; the German Transformer recordings of
  2026-10-02 measured 125 to 131), plus a slow comparison estimate at 100. The check per audio provider is open (see
  V-16).
- A script estimated above 60 minutes is returned; one under 85 % of its planned duration is returned for revision of
  its content (`script_checks.MIN_DURATION_SHARE`). This catches a gross miss of the scope, not missing depth of
  explanation. The slow estimate is no measured duration and does not limit the text further. The writer gets this
  floor and a target as a computed word budget; only for Claude Sonnet 5.5 is that target above the plan
  ([SCRIPTS](SCRIPTS.md#what-each-step-receives)).
- The script check before the recording is the length limit: the montage writes the whole episode as one MP3 and
  stops at no length, and the speaking rate is never raised to shorten an episode. The measured duration is in the
  audio report ([AUDIO](AUDIO.md#output-format-and-chapters)); the gate `duration_check`:
  [QUALITY](QUALITY.md#quality-gates).
