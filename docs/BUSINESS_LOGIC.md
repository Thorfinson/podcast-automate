---
title: Business logic
doc_type: business-logic
status: current
last_reviewed: 2026-10-04
covers:
  - src/podcast_automate/provider_pool.py
  - src/podcast_automate/subscriptions.py
  - src/podcast_automate/text_settings.py
  - src/podcast_automate/claude_code.py
  - src/podcast_automate/run_budget.py
  - src/podcast_automate/script_budget.py
  - src/podcast_automate/question_budget.py
  - src/podcast_automate/studio_allowances.py
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
  Claude Code with a claude.ai login (Claude Max). OpenRouter, the paid API alternative, runs only when you choose it,
  on your API credit. No API payment details are required.
- Subscription quotas apply to automated calls too. Finished stage results are saved; an exhausted quota pauses the
  run with `waiting_for_quota`, never producing incomplete final results or switching automatically to a paid API.
  Retries after technical or validation failures are bounded.
- Paid Codex credits are never used automatically (`credits` is ignored). Codex has quota when no window is at
  100 %, no `rateLimitReachedType` is set, no spend control is reached and ordinary usage is allowed
  (`subscriptions.normalize_codex_limits`).
- Changing the provider is an explicit decision: the rule `auto` or an
  [explicit switch](#switching-a-job-to-another-provider).

### Fixed providers and the rule `auto`

- A fixed choice (Sonnet 5.5, Opus 5.5, Astra, an OpenRouter model) never switches, stops even on an unusable
  subscription and never queries quotas for the rule.
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

- Unusable means an expired login, no subscription login, or a CLI that is missing, cannot start or is too old
  (`provider_pool.UNAVAILABLE_CODES`). Under `auto` such a failure moves the call to the other subscription as a quota
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

Two stages ask at most at `medium`, even when the run is set higher: the first-time reader (`listener_readback`),
which should take in only what the dialogue itself explains, as the one review at the role A1
(`text_settings.A1_EFFORT`, [below](#roles-of-review-calls)), and placing the expression tags for the recording
(`audio_expression`, `text_settings.STAGE_EFFORT_CAPS`). Evidence review, teaching review, writing and every
correction keep the run's level. A run on OpenRouter keeps its level for both. (why: D-023)

### Roles of review calls

Each call plays a role, read off its prompt version (`text_settings.call_role`) and recorded as `role` in its
`provider_choice.json`:

| Role | Calls | Model and level |
| --- | --- | --- |
| A1 | routine reviews (`text_settings.STAGE_AUTHORITY`; today only `listener_readback`) | the run's choice, at most at `medium` |
| A2 | every other review, and every call that is no review | the run's choice at its level, as before |
| A3 | the final step where spent repairs would stop the run (prompt version with `+a3`; [SCRIPTS](SCRIPTS.md#final-step-at-a3), [TEACHING](TEACHING.md#revisions-and-stops)) | under `auto` Codex alone, Astra at `xhigh`, even while Claude has quota; a fixed choice uses its own model and level |

- Under `auto`, A3 never moves to Claude: an Opus limit would block the whole Claude subscription for 5 hours, and Codex
  quota can be read before the call. Without Codex quota an A3 call pauses the run until Codex's reset; with Codex
  missing or logged out the stage stops as before. Either way a resume tries A3 again. (why: D-123)
- A role chooses only within the run's own choice: it adds no provider, and the fixed path reads no quota.
- Calls with web search keep the rule of their run; roles do not apply to them.

### Research always runs on a subscription

Live research and supplementary web research run on the chosen subscription; with OpenRouter text, and with `auto`,
they follow the automatic rule (Claude, else Codex) with the catalog defaults, because OpenRouter has no search tools.
`pla research --backend` accepts only `codex_cli`, `claude_code` and `auto`.

### The choice is bound to the run

- The text provider choice is fixed when a script or research run starts (`script_request.json`,
  `research_request.json`) and is part of its inputs; for `auto` both candidates (`claude-sonnet-5-5`/`high`,
  `gpt-6-astra`/`xhigh`), the first choice (`prefer: claude_code`) and, since 2026-10-04, the A3 rung (`ladder`) are
  stored. A run saved without `ladder` serves A3 with its candidate pair, like every other call.
- Resume uses the stored choice: a different `--backend` or candidate list is refused as a changed input. Changes
  apply to new runs; the only exception is an explicit switch (next section).
- Audio runs store the audio provider and both voices separately; resume keeps them too.
- Checkpoints stay valid across a provider change, being bound to the prompt text, not the provider, so a draft and
  its review may come from different models. `reports/script_quality.yaml` names the selection, each call its
  provider.

### Switching a job to another provider

Every script and research job can continue with another text provider at any time: in the Studio with
**„Weiter mit …“** (continue with …) under the job's saved text choice ([STUDIO](STUDIO.md#choosing-the-text-model)),
on the command line with
`pla approve <project> --run-id <run_id> --text-switch claude|astra|claude-only|astra-only|openrouter [--switch-model MODEL]`
(without a value `claude`; `run_budget.TEXT_SWITCHES`):

- **Claude, else Astra** (`claude`) and **Astra, else Claude** (`astra`): the subscription asked first answers until
  its quota is spent, then the other takes over.
- **Only Claude** (`claude-only`) and **only Astra** (`astra-only`): the job stays on one subscription.
- **OpenRouter** (`openrouter`): one model from the list (`--switch-model`), paid per call, with the key; web searches
  stay on the subscriptions, because OpenRouter has no search tools.

Rules of a switch (why: D-024):

- Astra works over the Codex subscription at `xhigh`; Claude with the catalog default Sonnet 5.5 at `high`, even when
  the job started with Opus.
- `claude` and `astra` also take the catalog's A3 rung (Codex first); `claude-only`, `astra-only` and `openrouter`
  serve A3 with their one model ([Roles of review calls](#roles-of-review-calls)).
- The receipt `runs/<run_id>/text_switch.json` applies from the job's next start. A later choice replaces it;
  choosing the original selection removes it.
- Inputs, hash, checkpoints and approvals stay unchanged, and `script_request.json` or `research_request.json` keeps
  the original selection; finished work stays valid.
- The status brief, the quality report, the expression level of a later recording and the handover of the OpenRouter
  key follow the current choice; a job receives the key only while it works with OpenRouter
  ([SECURITY](SECURITY.md#secrets-and-keys)).

## Budgets

### Default limits

- New projects get 750 model calls, 48 search rounds and 150 source candidates per research or script run
  (`ResearchLimits.model_calls`, `.search_rounds`, `.sources` in `models.py`). A saved project keeps its explicit
  limits; the Studio settings set them for every project ([CONFIGURATION](CONFIGURATION.md#studio-settings)).
  (why: D-034)
- Every model decision and every independent review counts; local search and reading need neither a model nor the
  network. The source limit also counts failed and duplicate fetches ([RESEARCH](RESEARCH.md#limits-and-resume)).
- Limits never cut the agreed topic scope automatically; a run that reaches a limit stays saved with its concrete
  gaps.
- A call without a model answer (time limit, stalled stream, quota pause, abort) is not charged: `budget.json` lists
  it under `refunded`, and call numbers stay unique. A charged failure (invalid output, a failed provider turn) keeps
  its charge.
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
  the state so far stays saved.
- After the scope check the Studio and the question report show the **minimum need of further calls** (dossier and
  closing reviews included) and the available budget. Finished answers and reusable checkpoints lower the need; more
  search, reading and corrections may raise it. If the budget does not cover even the minimum, the run stops with
  `research_budget_insufficient`; answers and topic scope stay. Optional calls may not use the closing reserve.
- A script run stops with `script_budget_insufficient` when the approved limit does not cover its
  [lower bound](#script-lower-bound-and-expected-calls).

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

### Raising a limit

- Call, search-round and source limits are raised per run explicitly:
  `pla approve <project> [--run-id <run_id>] --model-calls N --search-rounds M --sources Q` (without `--run-id` the
  project's last run) or the buttons in the job status.
- The raise is a receipt `runs/<run_id>/budget_approval.json`, bound to the run id and the run's input hash.
  Counters, checkpoints, project configuration and outline or audio approvals stay untouched; the next call takes the
  raise into account.
- A new limit must be a whole number at least as high as the current one, otherwise the approval is refused with
  `invalid_budget_approval`. Raising one limit keeps an earlier raise of the others.
- The run uses the higher of its own receipt and the project's current limit (`run_budget.effective_limits`). A
  receipt of another run or input hash, or an unreadable one, stops the run with `invalid_budget_approval`.

### Pre-approvals

Under **„Ohne Rückfrage“** (without asking) on the settings page you set what the Studio may give a stopped run by
itself, the **„Vorab-Erlaubnisse“** (pre-approvals; storage: [CONFIGURATION](CONFIGURATION.md#studio-settings),
display: [STUDIO](STUDIO.md#stopping-and-resuming)):

- **„Neue Anläufe je Lauf“** (fresh attempts per run, up to three; `studio_allowances.FRESH_ATTEMPT_CHOICES`) when a
  step has used up its automatic corrections: the stops whose card offers **„Mit neuen Anläufen fortsetzen“**
  (continue with fresh attempts), and only where the run would accept them (`run_budget.fresh_attempts_available`).
- **„Aufruflimit erhöhen je Lauf“** (raise call limit per run, by up to 100, 250, 500 or 1000 calls;
  `studio_allowances.EXTRA_CALL_CHOICES`) when the approved limit is not enough, each time by the need the stop card
  suggests, at most by the rest still allowed.
- The Studio's scheduler then writes the same approval as the button and resumes the run within half a minute, also a
  run set aside. The hold card announces it, the engine room counts what was given, and `studio/allowance_log.json`
  records every use.
- An allowance is applied only when the run can actually start (project free, a slot free). If the resume then fails,
  the scheduler resumes later without spending the allowance again.
- Without a setting every stop waits for you. Editorial decisions and an exhausted search-round or source limit never
  follow from an allowance ([Decisions that stay yours](#decisions-that-stay-yours)). (why: D-036)

## Human approvals

- Paid jobs start only through your actions on the page. Opening, navigating and playing stored Qwen or Gemini
  samples use no model calls; new Gemini samples and Gemini recordings use your API credit.
- Navigation and reloading start no model calls and grant no approvals. The chat grants no plan or audio approval;
  **„Diese Auswahl übernehmen“** (accept this selection) only saves the reviewed summary. Intermediate states in the
  live window are unreviewed and grant no approval.

### Research plan

- The research plan approval is a gate: a research run stops after source search, planning and scope check, before
  the first sub-question, and uses no further model call until the plan is approved.
- The approval is a receipt `runs/<run_id>/plan_approval.json`, bound to exactly this plan and written only by an
  explicit action: **„Rechercheplan freigeben und starten“** (approve research plan and start) in the Studio or
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
  anew after reading need a new reading. A changed pause rule is audible and needs a new approval.
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
approving plans and audio, and raising an exhausted search-round or source limit are never taken automatically.
Pre-approvals cover only fresh attempts and call-limit raises ([Pre-approvals](#pre-approvals)). Blocked
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

- An episode lasts at most 60 minutes (`script_models.MAX_EPISODE_MINUTES`); the recording splits it into parts of at
  most 30 minutes. (why: D-003)
- `max_episode_minutes` in `project.yaml` is fixed at 30: the longest audio part of a recording, not a cap on an
  episode. Series planning does not see it, because the planner reads only the editorial fields of the brief
  ([SCRIPTS](SCRIPTS.md#series-plan)). (why: D-070)
- The series plan sizes `target_minutes` per episode by the explanation needed, 15 to 60 minutes
  (`prompts/series_plan.txt`); one connected explanation stays in one episode.

### Estimating and checking the duration

- Script duration is estimated from the spoken words, the speaking rate and the planned pauses: 130 words per minute
  including pauses for planning (`script_artifacts.SPOKEN_WORDS_PER_MINUTE`; the German Transformer recordings of
  2026-10-02 measured 125 to 131), plus a slow comparison estimate at 100. The check per audio provider is open (see
  V-16).
- A script estimated above 60 minutes is returned; one under 85 % of its planned duration is returned for revision of
  its content (`script_checks.py`). This catches a gross miss of the scope, not missing depth of explanation. The slow
  estimate is no measured duration and does not limit the text further.
- After rendering, the measured audio duration counts: each audio part is checked against the 30-minute limit before
  export, and episodes that are too long are revised or split before the final export; the speaking rate is never
  raised to get around the limit. Splitting into parts: [AUDIO](AUDIO.md#recording-flow); the gate `duration_check`:
  [QUALITY](QUALITY.md#quality-gates).
