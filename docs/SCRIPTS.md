---
title: Scripts
doc_type: business-logic
status: current
last_reviewed: 2026-11-12
covers:
  - src/podcast_automate/scripting.py
  - src/podcast_automate/script_pipeline.py
  - src/podcast_automate/story_agent.py
  - src/podcast_automate/data/story_agent_task.md
  - src/podcast_automate/data/story_agent_revision.md
  - src/podcast_automate/cast.py
  - src/podcast_automate/narration.py
  - src/podcast_automate/script_checks.py
  - src/podcast_automate/script_evidence.py
  - src/podcast_automate/script_artifacts.py
  - src/podcast_automate/script_checkpoints.py
  - src/podcast_automate/script_models.py
  - src/podcast_automate/series_review.py
  - src/podcast_automate/cli.py
  - src/podcast_automate/prompts/series_plan.txt
  - src/podcast_automate/prompts/episode_framing.txt
  - src/podcast_automate/prompts/story_critique.txt
  - src/podcast_automate/prompts/story_review_repair.txt
---

# Scripts

`pla script` turns a finished research run into a series draft (table of contents) and reviewed, readable dialogue
scripts. Every episode is written by the [story agent](#story-agent), in the stages `planning`, `teaching`, `story`,
`review` and `publish` (`scripting.STORY_STAGES`). It produces no audio. The teaching stage prepares each episode's
material (story material, story research, a checked orientation sheet, supplementary research): [Teaching
stage](TEACHING.md#teaching-stage); the editorial standard the reviews hold an episode to: [Editorial
standard](TEACHING.md#editorial-standard).

Until 2026-11-01 a script run could also be written by the writing prompt, with a teaching design, the stages
`writing`, `narration` (narration review) and `polishing` and the listener, editorial and teaching reviews; they were
removed, and the story agent is the only writer (why: D-184). Episodes those runs published stay readable and
recordable; a run begun with them: [Runs begun before D-184](#runs-begun-before-d-184).

## Running a script job

```powershell
# First a complete research run, if there is none yet:
.\.venv\Scripts\pla.exe research .\projects\windows-pilot

# Write the first episode early for the reading review:
.\.venv\Scripts\pla.exe script .\projects\windows-pilot --episode ep_001

# Progress, and resuming the same job:
.\.venv\Scripts\pla.exe status .\projects\windows-pilot
.\.venv\Scripts\pla.exe resume .\projects\windows-pilot
```

- Without `--episode`, the run plans and writes every episode of its new draft; their number follows from the content
  ([Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length)). Its sessions read only the episodes
  published before the run started, so a series written so does not build on what its own episodes said; the Studio
  writes a series [in order](#writing-a-series-in-order), one run per episode.
- A new `script` call plans anew; `resume` reuses the saved plan and every valid draft.
- The research run must be complete and its stored files must match their checksums. Sources are not downloaded
  again.
- Local sources (`local_sources`) must lie inside the project folder ([Local files](SECURITY.md#local-files)).
- `--episode ep_007 --from-outline <run_id>` writes one episode of another script run's approved outline in a new
  run, without planning again (`scripting.outline_inputs`): the outline must be complete, approved as it stands
  (`plan_approval_required` otherwise) and made from today's research (`inputs_changed`). The outline's run stays as
  it was. This is how the Studio writes every episode ([Writing a series in order](#writing-a-series-in-order)).
  `--minutes N` (10 to 60) writes the episode at N minutes instead of its planned length (D-192): the session writes
  the outline's entry at those minutes (`story_agent.revision_entry`), so the word budget, the agent's floor of 95 %,
  `check.py`, the run's script checks, the recording's check and the published `episode_plan.yaml` read them, as for a
  [revision's own length](#revising-a-script); the run binds them in its inputs (`episode_minutes`, only when given,
  so other runs keep their hash), and its `series_plan.json` stays the outline's, byte for byte.
- A new run writes and checks its lengths at the project's speaking rate, bound in its inputs
  ([Estimating and checking the duration](BUSINESS_LOGIC.md#estimating-and-checking-the-duration)).
- Every model call counts against the run's call limit (`research_limits.model_calls`). Lower bound per episode
  (`script_budget.STAGE_CALLS`), projection (`runs/<run_id>/budget_projection.json`), expected calls and the stop
  `script_budget_insufficient`: [Budgets](BUSINESS_LOGIC.md#budgets). Input binding: [Runs, resume and input
  binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding).

## Choosing the provider on the command line

The Studio sets text model and reasoning level on the Settings page
([Choosing the text model](STUDIO.md#choosing-the-text-model)). `pla script` and `pla resume` take:

| Option | Meaning |
| --- | --- |
| `--backend codex_cli\|claude_code\|auto\|openrouter\|claude_api` | Text provider for planning, the teaching stage, the agent's critiques and the reviews; default `codex_cli`. `claude_api` is Claude on your Anthropic API key, billed per call and only with a money limit ([Claude on your own API key](BUSINESS_LOGIC.md#claude-on-your-own-api-key)); it also runs the story agent's sessions on the key ([Calls and access](#calls-and-access)). |
| `--model` | Model ID, for example `gpt-6-astra` (Codex), `claude-sonnet-5-5` or `claude-opus-5-5` (Claude). Required for OpenRouter. |
| `--reasoning-effort` | `low`, `medium`, `high` or `xhigh` (`text_settings.REASONING_EFFORTS`); Claude also `max` (`text_settings.CLAUDE_EFFORTS`). Optional for OpenRouter, where the model must support the level. |
| `--api-key` | OpenRouter key, or the Anthropic key with `claude_api`; without a value it is asked for hidden. |
| `--max-output-tokens` | Output limit per OpenRouter call. |
| `--web-search` | `model` (default) or `perplexity`: how a new run's supplementary research searches the web; the Perplexity key comes from `PERPLEXITY_API_KEY` ([Research runs and their web search](BUSINESS_LOGIC.md#research-runs-and-their-web-search)). |

The story agent itself does not follow `--model` and `--reasoning-effort`: its sessions are Opus 5.5 at `xhigh`
([Story agent](#story-agent)).

A new Codex run with `--model gpt-6-astra --reasoning-effort xhigh` matches the Studio's Codex choice. Defaults per
provider, presets and minimum Claude Code versions: [Providers and models](PRODUCT.md#providers-and-models). All
values are stored with the run and reused on resume; a different selection needs a new run, unless the job is
explicitly switched to another provider.

```powershell
# Fixed on Claude Code (Claude Max subscription, claude.ai sign-in):
.\.venv\Scripts\pla.exe script .\projects\windows-pilot --episode ep_001 --backend claude_code

# Per call Claude while it has quota, otherwise Codex; pauses only when both are empty:
.\.venv\Scripts\pla.exe script .\projects\windows-pilot --backend auto
.\.venv\Scripts\pla.exe research .\projects\windows-pilot --backend auto

# Quota of both subscriptions, without a model call:
.\.venv\Scripts\pla.exe quota
```

`--backend auto` picks a subscription by quota before each call and uses the catalog defaults. Its records
(`script_request.json`, `provider_choice.json`, `provider_switch.json`), quota pauses and switching a job to another
provider (**Continue with …** („Weiter mit …“), `pla approve --text-switch`, `text_switch.json`): see [Text providers
and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection).

### OpenRouter for a script run

The Codex subscription stays the default. Optionally, planning, the teaching stage, the agent's critiques and every
model review of one script run go through OpenRouter, paid per call from its API credit; the agent's sessions still
need the Claude subscription. Research and the automatic supplementary research stay on the subscriptions, unless the
run searches through Perplexity (`--web-search perplexity`), which lets the OpenRouter model research too; speech is
generated separately ([Recording flow](AUDIO.md#recording-flow)).

```powershell
# Replace provider/model-id with an OpenRouter model ID that supports JSON schemas.
# --api-key without a value asks for the key hidden in the terminal.
.\.venv\Scripts\pla.exe script .\projects\windows-pilot --episode ep_001 --backend openrouter --model "anbieter/modell-id" --api-key

# Resume the same run: provider, model and token limit are reused.
.\.venv\Scripts\pla.exe resume .\projects\windows-pilot --run-id "RUN_ID" --api-key
```

- The key serves the current process only and can also come from `OPENROUTER_API_KEY`. Key handling, and why a key
  given as a value is refused: [Secrets and keys](SECURITY.md#secrets-and-keys).
- Provider, model, token limit and adapter version are stored inputs: changing one needs a new `script` run,
  optionally with `--revise` (same provider options); a rotated key does not.
- `reports/script_quality.yaml` names the selection under `text_generation`.
- The run needs a money limit in USD (`research_limits.cost_usd`, or `pla approve --cost-usd N` for this run); without
  one it stops at its first OpenRouter call with `cost_limit_required` ([Money limit](BUSINESS_LOGIC.md#money-limit)).
- Structured outputs, provider sorting, the `--max-output-tokens` default, truncated or rejected answers, per-call
  costs, and pauses for missing credits, rate limits or network errors: [Text provider
  adapters](ARCHITECTURE.md#text-provider-adapters).

## Series plan

### Planning principles

The series is the standard product. How its length follows from the content (no fixed length or episode count,
episodes added as the content needs, content that does not fit moved to a further episode, a requested total duration
weighed against the content) is in [Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length). In
addition:

- A series is complete when its prioritised questions are answered at the requested depth or their subject limits
  are placed comprehensibly.
- With insufficient material, the plan proposes a shorter series or targeted supplementary research; scripts are not
  stretched.

### Coherence across episodes

The plan orders episodes by required foundations and questions that build on each other. Suitable purposes are
foundations, mechanism, deepening, counterposition, case study, application and synthesis; a series need not give
each its own episode. Each episode briefly names what it presupposes and answers its own question substantially.
Short recaps are allowed; explained foundations should not form the main part of every episode again.

### What the planner reads

- Only the project's editorial fields (`script_pipeline.PLANNING_BRIEF`: `topic`, `language`, `audience_level`,
  `prior_knowledge`, `depth_request`, `focus_questions`, `excluded_topics`, `seed_people`, `target_total_minutes`,
  `series_goal`), not the whole configuration (why: D-070); the unused `max_episode_minutes` stays hidden.
- An [assembled dossier](RESEARCH.md#assembled-dossier) without quote excerpts and claim profiles
  (`script_checks.planning_dossier`): the plan assigns findings by their statement, and each episode reads its
  findings in full when written.

### Plan validity

- The plan must not invent findings, and explanation dependencies must not form a cycle.
- Later chapters may take up and build on findings already introduced; core findings not yet introduced stay
  excluded. The source assignment should allow a coherent argument.

### Core and supporting findings

The plan assigns findings at two levels (`EpisodePlan` in `script_models.py`) (why: D-071):

- `finding_ids` are an episode's core, the findings its explanation needs. The dialogue must cite each; the story
  agent may regroup them into its own chapters but never demotes one ([Story agent](#story-agent)).
- `supporting_finding_ids` are study details that can back a statement: citable in any scene, not required, part of
  no scene, never core of the same episode.
- Every finding gets a place, as core or support in at least one episode, or a reasoned omission, never both
  (`script_checks.validate_plan`).
- Where the series goal weights *understanding*, the core stays with what the theories explain in their own logic.
- **Core limit** (why: D-180): at most one core finding per `CORE_MINUTES` = 4 planned minutes and never fewer than
  `MIN_CORE` = 4 in an episode (`script_checks.core_limit`, 11 for 45 minutes), checked by
  `validate_plan(density=True)` and repaired like every plan defect; a side case or another example of the same point
  goes into the supporting findings, and a story the dossier holds goes into the core of its episode
  (`prompts/series_plan_density.txt`). Orlagau's episodes held 13 to 26 core findings, about 300 spoken words each,
  and their stories got one or two sentences. An inherited plan (`--revise`, `--from-outline`) is not checked again.
- The story agent and the reviews see core, supporting and recapped findings; the show notes name the sources of core
  and supporting findings.
- A plan without these fields counts as before: every assigned finding is core, and stored form and hash stay
  unchanged.

### Role, recap and theory first

- Every episode gets a `series_role`, its contribution to answering the guiding question. It states this at the start
  in its own words and says at the end what one now knows about the guiding question.
- Length follows the need to explain, 15 to 60 minutes per episode (`prompts/series_plan.txt`). One connected
  explanation stays in one episode, split only at a natural break: a large theory may get its own long episode, and
  fewer, longer episodes are preferred to several short parts of one subject.
- An episode that builds on earlier ones gets under `recap_finding_ids` the findings it takes up from them; the final
  episode for every episode it brings together. Without them, an episode names only an earlier episode's question and
  does not repeat its content.
- The final episode is as a whole the synthesis of the series, not a last topic with a recap at the end. From its
  first chapter on, its scenes are the steps of a reasoned answer to the guiding question, assembled from each
  episode's contribution and naming the important remaining limits and open questions; its last chapter brings the
  answer together, and new material comes only where the answer needs it. A synthesis scene that
  follows a case through this answer can replace the worked example otherwise required per episode; a qualitative case
  without numbers counts as an example everywhere. It may cite its `recap_finding_ids` without covering all of them
  (why: D-067).
- Where the series goal weights *understanding*, an episode first explains a theory in its own logic, attributed to
  its originator, then its tests and critiques. Where it weights *applying*, it gives concrete recommendations,
  introduced once as the series' own and based on the cited practice findings.
- The origin of a statement stays audible (author's view, test, critique, our interpretation, our recommendation).
  With a recency rule set, the script names the year of an older practice source.
- Limit scenes are not required.

### Research limits in the script

The limits the research run noted for the script in `research_quality_gate.json` reach planning, the story agent and the
script review as `research_limits` (`script_checks.research_limits`) (why: D-072), each with a `limit_id` and, where known, the
affected findings: source limitations and guiding questions noted as limits, noted limits, objections noted after two
revisions, and the „Hinweise fürs Skript“ (notes for the script) of the follow-up assessment (`script_notes`).

- The plan enters each `limit_id` in the `research_limit_ids` of the one episode that first uses the affected
  statement. An unplaced limit goes to the first episode whose core or supporting findings it concerns, else to the
  final episode (`script_checks.episode_limits`).
- The script names each limit there once, briefly and in its own words, in the segment with the affected statement:
  no limits scene, no list of caveats at the end. The story agent gets its episode's limits in `material.json`
  (`research_limits`, the same rows the review gets) beside each finding's own limits (its claim contract's
  qualifications).
- The script review counts a limit named this way as supported, as a gap probe supports a statement about something
  missing. A missing limit goes under `limitations`, not as an objection; a repair keeps a named limit.
- Every limit is probed against the stored sources like a gap ([Gap probe](RESEARCH.md#in-the-script-run)), with Jev
  where it is switched on. Its unread hits go to the supplementary research of the episode that states it, in that
  episode's teaching stage, wherever the hit sections lie (why: D-173). A limit whose row that research resolves is
  answered by the sources after all: the script review no longer gets it (`ScriptRun.stated_limits`); the plan, made
  before the probe, still names it. (why: D-131)
- An older quality file without these details yields no limits.

## Writing a series in order

A series is written episode by episode, in the outline's order, one script run per episode, so that each episode
reads what the episodes before it actually said (why: D-184). Until 2026-11-01 one series run wrote every episode at
once, and an episode knew the others only by their titles and questions.

- **The outline's run plans only.** In the Studio the table of contents is drafted by a run that stops after
  `planning` and waits for its approval ([Table of contents](BUSINESS_LOGIC.md#table-of-contents)).
- **The approval writes the first episode.** `scripting.approve_outline` records the approval of exactly the outline
  the user read (`plan_approval.json`, bound to `script_checks.outline_hash`) without writing anything itself; the
  Studio then writes the first unwritten episode in a run of its own from that outline (`--from-outline`,
  `scripting.outline_inputs`).
- **Which episodes are written.** `scripting.outline_episodes` lists the outline's episodes in order, each with the
  run that published it from this outline: an episode counts as written when its `episodes/<ep>/latest.json` names a
  run whose `series_plan.json` is byte-identical to the outline's, as every run from that outline and every revision
  copies it. An episode published from another outline counts as not written. `scripting.next_episode` is the first
  one not written.
- **The next one, or all remaining ones.** The Studio writes the next episode, or every remaining one one after the
  other, never in parallel, stopping where one stops; with every episode written it refuses with `series_written`.
  "Resume" („Fortsetzen“) on the outline's own run, once approved, writes its next episode in the same way. A resume of an
  episode that stopped while every remaining one was being written, by hand, after a quota wait or by an allowance,
  finishes it and then goes on with the remaining ones, as long as the outline is the current one and still approved
  (`Studio.series_outline`); until 2026-11-04 it finished that episode alone. The batch may be given one length for
  every episode it writes (`minutes`, D-192); a resumed episode carries the series on at the length bound in its
  inputs. The buttons and the queue the batch can wait in: [Writing the episodes](STUDIO.md#writing-the-episodes).
- **Each episode reads the earlier ones.** The session of each episode gets the published text of every episode
  before it in the plan's order ([What the session gets](#what-the-session-gets)).
- **Then the series review.** Once the outline's last episode is published, the series review reads all of them
  together and offers its points per episode as revisions ([Over an outline's published
  episodes](#over-an-outlines-published-episodes)).

On the command line the same order is `pla script <project> --episode <ep> --from-outline <run_id>`, episode by
episode; the outline must have been approved in the Studio.

## Story agent

Every episode is planned and written by the story agent (`story_agent.py`; why: D-181, D-184). Per episode one Claude
Code session (`claude -p`, `story_agent.AGENT_MODEL` Opus 5.5, effort `story_agent.AGENT_EFFORT` `xhigh`) in
`runs/<run_id>/story/<episode_id>/` reads the episode's material and the episodes before it, plans its own chapters,
writes the dialogue, checks it and revises it after an editor's critique. In the narration lab (`evals/narration_lab`,
2026-10-27) its drafts of three ten-minute cases averaged 8.0 to 8.1 on the lab's judge, where the writing prompt's
stack averaged 5.5 on the same inputs. There is no teaching design, no narration review and no polishing: the agent's
own plan takes the design's place, and its critiques the narration review's; in the lab, polishing made narrative
drafts 14 % longer without making them better.

### What the session gets

`story_agent.write_episode` prepares the folder, which is the session's working directory:

- **`material.json`**, everything the agent may use, as data (`story_agent.material`): the brief (language, audience,
  host names and roles, the series goal where the brief sets one, style notes, a third host), the outline entry, the
  series context (`editorial.episode_series_context`), the word budget ([Word budget](#word-budget)), the rule for
  opening and ending the review checks (`framing`, [Opening and ending](#opening-and-ending)), the story material, the
  episode's findings with their limits, its research limits ([Research limits in the script](#research-limits-in-the-script)),
  the findings resting on one research group, which the review expects attributed once (`single_group_findings`), the
  source sections they cite, the checked orientation sheet ([Teaching stage](TEACHING.md#teaching-stage)), and the core
  findings no story item tells. Until 2026-11-01 only the review knew the research limits, the single-group findings
  and the series goal.
- **`earlier/`**, the published text of every episode before this one in the plan's order that `episodes/` holds,
  one Markdown file each, with its chapters and turns and each turn's speaker (`story_agent.earlier_episodes`), listed
  in `material.json` under `earlier_episodes` (why: D-184). The task asks the agent to read the episode just before in
  full and to search the others for every person, term, story and study it means to use: what an earlier episode
  explained is referred back to, not explained again; a difference is said as a correction, never a silent
  contradiction; opening story, perspective and closing image differ from theirs. An episode the series context lists
  before this one that `earlier/` lacks is not written yet: only its question may be used.
- **`TASK.md`**, the agent's instructions (`data/story_agent_task.md`), written for the listeners the brief's audience
  describes (until D-190 for "curious adults"): find the turn the material holds, choose a perspective, plan, write,
  check every fact, run the checks and the critiques. A project with a third host appends
  its cast ([Three hosts](#three-hosts)); a revision appends its own steps ([Revising a script](#revising-a-script)).
- **Four command scripts**, the only commands the session may run besides the file tools (Read, Write, Edit, Glob,
  Grep; [Local files](SECURITY.md#local-files)):
  - `check.py`: the run's script checks on the draft (structure and finding references; the quotation limit over the
    given passages where the run applies it, for an assembled dossier, and always over the corpus sections in
    `sources_used.json`), echo lines (a turn that opens with only the other host's last words) and the word budget;
  - `critique.py`: an editor's verdict on tension, interest and storytelling with what keeps the draft from 10
    (`prompts/story_critique.txt`), at most four times per session (`story_agent.CRITIQUES`). The editor hears the
    script as the brief's audience, with the hosts by name and part (`story_agent.critique_input`, D-190). The request
    goes to the run as a file and the run makes the call, so the budget, the receipts and the provider choice apply;
  - `search.py` and `read.py`: the project's own research corpus, every section of every source the research
    stored, so the agent finds the scenes a finding only names, and of every document of the research run's starting
    library the research did not take in (`story_agent.corpus_documents`, D-190). Geld verstehen S1's best case for
    ep_003 stood only there.

A session's draft stands on resume while its material and task are unchanged; a repeated session moves the earlier
one's files to `attempts/NN/`.

### Opening and ending

The script review holds every episode to the framing rule (`prompts/episode_framing.txt`, `editorial.EPISODE_FRAMING`),
and the agent gets it as `framing`:

- Every episode gets a spoken intro with a short greeting, an orientation (its question and what it adds to the
  series) and a transition to its opening question; a short cold open may come first. Its outro answers this
  question, says what one now knows toward the series' question, gives an outlook when a following episode is planned,
  and says goodbye. The intro names the question and what is at stake, not the answer.
- **Episode 1 introduces the series.** Once its opening story has raised the question, the hosts give the series an
  introduction of one to two minutes (about 150 to 300 words, over several turns): what the series is about, why its
  central question matters, the path it takes and how the episodes build on each other; then the episode's own
  question takes over (`data/story_agent_task.md`; why: D-184). There is no trailer and no episode 0.
- The final episode is **as a whole the synthesis of the series** ([Role, recap and theory first](#role-recap-and-theory-first)).
- A series of one episode combines these tasks.
- Intro and outro stay inside the first and last chapters and the word budget: no fixed length, no invented show
  name, no host identity derived from the TTS voices, no advertising formula. Greetings outside the subject need no
  sources; subject recaps do.

### Word budget

The agent gets its word budget computed at the plan's length, or a revision's own ([Revising a script](#revising-a-script)) (`script_checks.word_budget` with
`story_agent.TARGET_FACTOR` 1.0) and the run's speaking rate (`script_artifacts.run_rate`,
[Estimating and checking the duration](BUSINESS_LOGIC.md#estimating-and-checking-the-duration)):

- a target, the planned minutes at that rate, spread over the scenes by the number of their explanation steps;
- a floor of 95 % of the target (`story_agent.TARGET_SHARE`), above the 85 % every script must reach
  (`script_checks.MIN_DURATION_SHARE`), and a cap of 1.1 times the target (`story_agent.LENGTH_SLACK`); `check.py`
  and the final check refuse a draft outside them.

Asimov v2 ep_007 (2026-11-01) stopped at 89 % of its target, inside the old window, and ran 35:47 for 45 planned
minutes, also because ElevenLabs spoke faster than the 130 words a minute every budget assumed (why: D-184). The
writing prompt's target factor of 1.3 for Sonnet 5.5 (D-127) ended with it; the agent counts its words.

### What the session leaves

- `plan.md` (the turn, the idea, the perspective, the chapters and their beats), `draft.json`, optionally
  `outline.json` (its own two to four chapters, which must hold every core finding in exactly one chapter; core
  findings are never demoted) and `sources_used.json` (every corpus section used beyond the material).
- Code checks the draft again when the session ends (`story_agent.draft_errors`); a draft that fails stops the
  episode with `story_agent_failed` and the defects in `errors.json`. A session over its time limit stops with
  `story_agent_timeout` (eight minutes per planned minute, at least two hours: `story_agent.session_timeout`; the
  lab's ten-minute sessions took 31 to 60 minutes).
- `story/<episode_id>/entry.json` holds the episode with the agent's chapters, which this run's review and publishing
  use; the series plan is unchanged. `drafts/<episode_id>.json` holds the accepted draft.

### Citing corpus sections

A segment that tells something no finding holds, such as a scene the agent found in the corpus, names its section in
`source_refs` (`Segment.source_refs`, `src_…#sec_…`; why: D-182). `check.py` accepts only a section of
`material.json` or one listed in `sources_used.json`, and the run only one its review is given
(`ScriptRun.script_errors`). Empty, the field is left out of every dump, so scripts written before keep their bytes
and hashes. Show notes and companion kits list the cited sections' sources beside the findings'
(`script_artifacts.render_show_notes`, `publish_kit.episode_sources`), a starting library's document too
(`story_agent.library_documents`); the run's inputs keep the research's sources only, so no hash changes.

### Calls and access

- The session counts as one call of the run (`calls/call_NNN`, title `StorySession`, with the usage, the turns and
  the cost the CLI reports); each critique is a call of its own. Per episode at least 3 calls in teaching, 2 in the
  story stage and 1 in review ([Budgets](BUSINESS_LOGIC.md#script-lower-bound-and-expected-calls)). The critiques
  and every other call follow the run's provider choice.
- **On the Claude subscription** by default, whatever the run's text provider: the session needs the claude.ai
  sign-in and stops with `authentication_required` without it (`story_agent.session_access`).
- **On your Anthropic API key** when the run's text provider is `claude_api` (why: D-184): the session is billed. It
  is reserved under the run's [money limit](BUSINESS_LOGIC.md#money-limit), capped at
  `story_agent.SESSION_BUDGET_USD` = 40 USD (`--max-budget-usd`), and settled in `billing.json` with the cost the CLI
  reports; without a key it stops with `anthropic_key_required`. Ep_007's session reported 12.39 USD.

### Outputs of an agent episode

Publishing writes `story_plan.md` (the agent's plan) beside the script, and the quality report's episode entry gains
`story_agent`: the critique scores in order, the corpus sections used, whether the agent set its own chapters, and the
session's turns, duration and reported cost (`script_artifacts.story_report`). Its `teaching_review` reads
`not_applicable`.

### Reading before recording

The user wants **to read the script first and decide on audio afterwards**, so `episodes/audio_review.yaml` records
the current script state with `audio_approved: false`; a model review grants no user approval
([Human approvals](BUSINESS_LOGIC.md#human-approvals)). The voices in the Windows pilot are Aiden (`host_a`) and
Vivian (`host_b`).

## Script review

### Structure and quotation checks

- A script must reference its episode's core findings and assign chapters and speakers correctly
  (`script_checks.validate_script`), against the agent's chapters where it set its own.
- With an [assembled dossier](RESEARCH.md#assembled-dossier), an episode quotes each source verbatim for at most 25
  words (`script_checks.QUOTED_WORDS_PER_SOURCE`, `script_checks.quotation_errors`): words matching a section the
  episode received as a source in runs of at least six words (`script_checks.QUOTE_RUN`), ignoring case and
  punctuation. Shorter matches are common phrases; a translation is no verbatim quote. The corpus sections the agent
  used count like the given passages.
- A script under 85 % of its planned duration or over 60 minutes at the run's speaking rate is refused (the rule in
  [Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length)); the agent's own floor is higher
  ([Word budget](#word-budget)).
- The agent runs these checks itself through `check.py`, the run again when the session ends, and every repair must
  pass them.

### Evidence review

A separate call checks what is said against the assigned findings and source sections, and also the required depth,
the worked-out explanation steps, how start and end connect, understandability, the example, the limits of metaphors
and the dialogue. It reads the draft as the agent wrote it, with the corpus sections of `sources_used.json` added to
the episode's passages (`ScriptRun.review_sources`), and is told that a segment may cite them
(`prompts/script_review_corpus.txt`, version suffix `+corpus`): such a statement is grounded when a cited section
holds it, and its receipt is `preserved` or `drift` against those sections, never `no_research_claim`
(`script_evidence.receipt_defects`). Each receipt quotes its segment verbatim; only the case of the quote's first
letter may differ, since a reviewer quoting from mid-sentence capitalizes the word it starts with (`script_evidence.quoted`;
why: D-208). It also gets the measured `dialogue_shape` and the listenability rules
([Listenability](TEACHING.md#listenability)).

- **Repairs only for what blocks.** Up to three repairs (`script_pipeline.MAX_REVIEW_REPAIRS`), each answering only
  the blocking issues (grounding, scope, structure) from the agent's material and its corpus sections
  (`script_pipeline.story_repair`, `prompts/story_review_repair.txt`, `story_review_repair.v3-source-refs`). A repair
  may change only the segments the issues name and their neighbours (`narration.with_neighbours`,
  `narration.scope_errors`), or any segment when a blocking issue names none because it concerns the whole episode,
  such as its length. A segment outside that scope whose words a repair kept but whose `knowledge_refs` or
  `source_refs` it changed is put back as it was before the check (`narration.unnamed_refs_kept`); a changed word
  there still refuses the answer. Until 2026-11-04 such an answer was refused too, and Asimov v2 ep_008 stopped four
  times after twelve repairs that had copied the review's sections into unnamed segments. The review's other points
  stay notes, so the telling the agent built is not rewritten by a reviewer (why: D-181).
- If then only objections about understandability, depth or dialogue remain (`script_checkpoints.NOTED_CATEGORIES`:
  `clarity`, `depth`, `dialogue`), the script is adopted; the points stay in the review report and
  `reviews/<episode>_accepted_notes.json`, visible when reading before the audio approval.
- Evidence errors and objections about scope or structure keep blocking: the run stops with `script_review_failed`,
  and drafts and objections stay saved. **Resume with fresh attempts** („Mit neuen Anläufen fortsetzen“) or
  `pla approve <project> --fresh-attempts` gives a review stopped this way three new repairs against the same
  review report.
- A point the review itself files as an advisory (`advisories`) is its own verdict that the point does not block, and
  it stays a note in the report and `accepted_notes.json`, in the first review and after a repair. Only an evidence
  or scope point (`script_pipeline.STRICT_CATEGORIES`: `grounding`, `scope`) counts as an objection wherever the scope
  reaches it. Until 2026-10-04 every first-review advisory counted, and after a revision every one but clarity, depth
  and dialogue. (why: D-126)
- After a repair, what the review keeps of a previous objection as an advisory stays a note, even about evidence or
  scope: an advisory of that objection's category naming only segments it named, or a whole-episode advisory after a
  whole-episode objection of its category. It is the review's verdict that the objection is settled, since one still
  open is reported again as an objection. A drift found by the claim checks still blocks there; a new point on the
  same segments cannot be told from such a note and stays one too. Until 2026-11-12 such a
  note counted, and Geld S2 ep_013 spent six repairs on research density in chapter 4 that its review called "a
  non-blocking note" in every round. (why: D-210)
- A wall of facts goes back as a `dialogue` point: findings strung together without a question the listener wants
  answered, the answer announced before it is earned, dense blocks that end without a recap or reflection beat, or the
  expert holding the floor. A partner share under 25 % or more than two turns over 120 words is such a point, a long
  turn for one step of a worked example is not. Being a `dialogue` point, it is a note for the reader. (why: D-142)
- An episode whose gap probe still has unread hits it should have read stops with `research_gap_unread` before its
  review ([Gap probe](RESEARCH.md#in-the-script-run)).
- After a subscription pause too, a text already corrected need not be written again.

### Scoped follow-up review

Every review after a repair is scoped (why: D-066): it gets the previous review's objections and the segments the
repair changed. Only three kinds of objections may block:

1. a previous objection still open,
2. an objection about a changed segment,
3. a factual error or source contradiction anywhere (`script_pipeline.CRITICAL_BASIS`).

An objection about the whole episode, without a segment, blocks only if the previous review raised one of the same
category about the whole episode, or as a factual or source error. Everything else, for example a missing reference
or a statement about something missing without a gap probe in a segment the previous review passed unchanged, is an
advisory (`advisories`) in the report and notes. Code sets the scope by comparing the versions; the verdict only
marks what is critical. So the review converges instead of finding new details in the same text every round. Within
the scope, a point the review filed as an advisory blocks only as an evidence or scope point, and not when it is what
the review kept of a previous objection ([above](#evidence-review)).

If a version had no blocking objections and a later repair (say, for a structure point) breaks its evidence, the
evidenced version stands with its notes; the discarded version and its review are kept in
`reviews/<episode>_kept_draft.json`. The same scoping applies to the evidence review after a
[series review](#series-review) correction.

### Spot check against the source

The review compares each segment that cites findings with the cited source sections themselves and names them in
`source_refs`; without them, or with unknown sections, it is asked again (why: D-069).

- If a finding deviates from its source and the segment repeats the error, that is a deviation in the field `source`,
  and the repair follows the section.
- If the segment already follows the section, the verdict is `source_corrected` (since
  `script_review.v12-source-corrected`): no objection, but a note in the review limitations that the dossier
  finding is inaccurate, shown in the Studio when reading (why: D-085). A deviation therefore does not enter the
  follow-up review as a previous objection; that review judges each segment afresh.
- A segment may cite the findings of its own scene and of earlier scenes, besides supporting and recalled ones
  (`script_checks.validate_script`). For a statement whose finding a later scene introduces, as an opening teaser
  can make, the review asks to move it to that scene or to turn it into the open question that scene answers, never
  to add the later finding's ID (why: D-175).

### Saved verdicts

Today's version is `script_review.v17-scene-order` (`script_checks.SCRIPT_REVIEW_VERSION`) with the corpus rule
(`+corpus`). A saved verdict stands on resume only when it was given with exactly this version and rule
(`script_pipeline.saved_verdict_stands`), with today's `teaching.EDITORIAL_REVIEW_VERSION` and, for a dossier with an
evidence version, today's `evidence_models.EVIDENCE_VERSION`; otherwise the saved draft is reviewed again, and its
latest text and spent repairs stay. The editorial version also changes with the rule of which points block, so a
review stopped under the old rule is judged by today's (`editorial_review.v6-residual`, D-210). None of this grants an approval or resets the model budget.

### Limits of the review

A passed model review replaces neither the reading nor the listening review. The series draft is editorial
planning; the overall review rates the final scripts, not how the produced series sounds. Human reading and listening
acceptance stay a separate step ([Acceptance procedure](QUALITY.md#acceptance-procedure)). Nothing is published
publicly.

## Series review

One call checks the complete final texts of a series together (`series_review.series_criteria`):

- `coverage`: the guiding question and each episode's core findings (supporting findings may be missing);
- `prerequisites`, `progression` and `deferred_questions`;
- `synthesis`: the final episode assembles all episodes' contributions into one answer;
- `arc`: each episode says what it contributes and what one now knows;
- `exposition` or `guidance` when the series goal weights *understanding* or *applying* with 2 or 3
  (`series_review.GOAL_CRITERIA`).

It runs in one of two ways:

- **Inside a run that writes every episode of its outline** (`pla script` without `--episode`): once all planned
  episodes are reviewed, before publishing, with one [correction round](#correction-round). New runs store
  `series_review_version` in their inputs; a run that writes one episode gets a partial report that claims no
  overall review.
- **Over an outline's published episodes**, each written by a run of its own: the Studio's way
  ([Over an outline's published episodes](#over-an-outlines-published-episodes)).

Rules for the verdict:

- Each criterion is judged exactly once. Positive verdicts need verbatim segment evidence; a passed series overall
  needs evidence from every episode.
- Every failed check names under `episode_ids` the episodes whose script must change (otherwise the answer is asked
  again) and says with `source_limit` whether only the research lacks the material.
- No script correction can fix a source limit, so it does not block but is an advisory (`criterion`, `reason`,
  `episode_ids`, `basis: source_limit`) under `report.advisories` in `runs/<run_id>/series_review.json` and
  `series_review.advisories` in `reports/script_quality.yaml`.
- Non-blocking repetitions are stored as `warnings`.
- Inside a run, every other substantial objection blocks providing the series, and with it its new audio approval.
- The report version stays `series_review.v1` (`series_review.SERIES_REVIEW_VERSION`), so saved verdicts and the
  audio approvals bound to them stay valid; new calls carry the prompt tag `series_review.SERIES_REVIEW_PROMPT`,
  currently `series_review.v4-core`.

### Saved verdicts of the series review

- The report records the criteria it asked for (`criteria`); a saved verdict is checked on load against exactly these
  (why: D-076). An older report without them is judged by the checks it contains, if the five base criteria
  `coverage` to `synthesis` are among them (`series_review.LEGACY_CRITERIA`).
- `runs/<run_id>/series_review.json` is bound by checksum to the plan, the inputs and all texts. Interruptions reuse
  a valid report; a negative verdict stays saved too, so merely resuming buys no new verdict.

### Correction round

Inside a run, a negative verdict is followed by exactly one bound correction across the episodes: the evidenced
segments of each affected episode are repaired once as a blocking review issue is (`script_pipeline.story_repair`,
held to the agent's chapters), the result gets its own evidence review, and the series review checks again; three
calls per affected episode. If a corrected episode fails its evidence review, a second attempt answers those
objections and still the series review's original objection (why: D-077); only then is the correction rejected
(`script_pipeline.SERIES_REPAIR_ATTEMPTS` = 2, up to five calls per episode). Until 2026-11-01 the correction of a
story run called the writing prompt and would have stopped with a file error (why: D-184).

- **Scoped re-review.** The renewed series review gets the corrected verdict's checks with their outcome
  (`previous_checks`) and the changed episodes (`changed_episodes`). Only an earlier objection the scripts still have
  (the same criterion on an episode it named) or an error in a changed episode may block; a new point on an unchanged
  episode is an advisory with `basis: unchanged`. This scope is saved in `series_repair.json` before the call, so a
  resume asks the same question. A second negative verdict in this scope blocks (`series_review.MAX_SERIES_REPAIRS`,
  one round).
- **Fresh attempts.** **Resume with fresh attempts** (or `--fresh-attempts`) sets a failed correction round
  aside, and likewise the spent round after a negative series verdict
  (`series_repair_superseded_NN.json`). The next start corrects against the saved verdict if it belongs to today's
  text, else first judges the series anew; either way it has one round again.
- **Stopped rounds.** A round stopped before its final series review, for example by **Stop job** („Auftrag anhalten“) (stop
  job), is not spent; resuming picks it up (`finished` in the record).
- No other stage is repeated: the change is limited to named segments.
- In parallel text mode, all affected episodes of a round are corrected at once. Passed corrections are adopted even
  if another episode is rejected; the message names each rejected report.
- Each correction answer is stored per round under `reviews/series_corrections/`, so a stop during its evidence review
  does not pay for it again.

### Correction files

- `reviews/<episode>_series_adopted.json` binds an adopted correction to the draft the episode review passed, so
  resuming the review stage publishes the correction instead of writing back the uncorrected text from that review's
  checkpoint (why: D-065).
- An adopted correction replaces `reviewed/<episode>.json` and `reviews/<episode>.json`, so
  `reports/script_quality.yaml` shows under `model_review` the review of exactly the published text next to its
  `script_sha256`; the review before the correction stays as `reviews/<episode>_before_series_repair.json`.
- A rejected correction (for example an evidence deviation in the revised segments) changes neither file; draft and
  objections are in `reviews/<episode>_series_repair_rejected.json`.
- `runs/<run_id>/series_repair.json` records the round, bound to plan and inputs instead of the texts it changes. It
  stores only a verdict on the correction itself: its evidence review rejected it (`script_review_failed`), or it
  still broke source mapping or structure after its repeated attempts (`invalid_script`)
  (`series_review.CORRECTION_VERDICTS`). A `resume` then buys neither a new series verdict nor a second round but
  reports this error again.
- No other failure is recorded (`series_review.resumable`): a timeout, a stall, a quota pause, **Stop job**, a missing sign-in or key, a spent call limit, a busy project, a malformed answer, or a provider error
  (`codex_failed`, `claude_failed`, `claude_structured_output`, `openrouter_unavailable`, `openrouter_connection`).
  Resuming continues the round for the episodes whose correction is not yet adopted (why: D-078).
- A corrected episode's audio approval lapses, because it hangs on the old script hash. Changed or missing reports
  prevent audio from an affected new run.

### Over an outline's published episodes

`scripting.run_series_review(outline_run=…)` reviews the published episodes of one outline, each written by a run of
its own ([Writing a series in order](#writing-a-series-in-order); why: D-184). The Studio starts it by itself once the
outline's last episode is published, or on request ([Writing the episodes](STUDIO.md#writing-the-episodes)).

- It needs every episode of the outline written (`series_incomplete` otherwise) and reads each episode's published
  `script.yaml` with the outline's plan.
- Its run is of kind `series_review`; its inputs name the outline (`outline_run_id`) and the run that published each
  episode (`episode_runs`). `series_review.json` also holds `episode_issues`: the blocking checks per episode, with
  the segments they cite (`series_review.series_issues`). `episodes/series_review.json` points at the latest such
  review of the outline (`scripting.SERIES_REVIEW_POINTER`, read by `scripting.outline_series_review`), and its
  verdict is mirrored into `reports/script_quality.yaml`.
- It has no correction round and blocks nothing: the episodes stay readable and recordable. Its points are offered
  per episode as a [revision](#revising-a-script) with the points as feedback; after a revision the episode's
  published run differs from `episode_runs`, and the series can be reviewed again.

### Scope and limits of the series review

- For a partial job, the report names the missing episodes and claims no overall review; separately generated
  episodes are merged into a reviewed collection only by the review over an outline's published episodes. A
  one-episode series can be reviewed fully.
- The call counts against the normal production budget and uses the selected text model. Texts are passed in full,
  without silent truncation; for large series a provider's context limit can block the review.
- Older runs already started keep their original inputs and approvals and are not marked series-reviewed on resume.

### Standalone `pla series-review`

`pla series-review <project> [--run <run_id>]` reviews a finished run's scripts afterwards as a series; without
`--run`, the most recently published run. It also takes `--backend codex_cli|claude_code|claude_api|auto` (`claude_api`
reads its key from `ANTHROPIC_API_KEY`), `--model` and `--reasoning-effort`. The review over an outline's published
episodes has no command-line option; the Studio starts it.

- Its one call counts against the new run's budget (`runs/<run_id>/budget.json`); the verdict lands in a run of its
  own, of kind `series_review`.
- The verdict is mirrored into `reports/script_quality.yaml` only if the reviewed run is the published state (the
  report's `run_id`). A verdict on an older run stays in its review run under `series_review.json`, and the output
  says so (`report_mirrored`, `script_run_id`).
- The reviewed run's files stay byte-identical, so a later report does not change the evidence it judges.
- `runs/latest.json` keeps pointing at the last pipeline run, so `pla resume` and `pla status` without a run argument
  still reach the script run. A review run cannot be resumed (`pla resume --run-id <review run>` fails with
  `invalid_run`); `pla status <project> --run-id <id>` shows it.

## Revising a script

```powershell
# Feedback: "Phrase more directly and cut repetition."
.\.venv\Scripts\pla.exe script .\projects\windows-pilot --revise ep_001 --feedback "Direkter formulieren und Wiederholungen kürzen."
```

A revision is a story agent session given the published episode and the feedback (why: D-184). In the Studio it is
**Have this episode revised** („Diese Folge überarbeiten lassen“) on the reading page, and **Revise with these notes** („Mit diesen Hinweisen überarbeiten“) for an episode the series review names.

- It starts a new run with the previous series plan and the episode as published: `scripting.revision_inputs` reads
  the episode's latest published run, which must be complete, with a valid plan and today's research and dossier
  (`inputs_changed` otherwise). An episode the agent wrote keeps its own chapters (`story_agent.revision_entry`).
  Old text and feedback are stored as inputs and the research is kept, so no new planning or search calls are needed;
  the teaching stage collects the episode's material again.
- The session gets `published.json` (the episode as published), the feedback in `material.json` under `revision`, and
  the corpus sections the published episode used (its run's `sources_used.json`), which the draft may keep citing.
  `TASK.md` adds the revision's steps (`data/story_agent_revision.md`): copy the published episode, change what the
  feedback asks and what the change touches, keep every other segment as it was, check the changed facts, and use at
  most two critiques (`story_agent.REVISION_CRITIQUES`) only where the telling changed.
- **Its own length.** A revision keeps the length the episode was written for unless it is given another, 10 to 60
  minutes (`--minutes N`; on the reading page **New length in minutes** („Neue Länge in Minuten“), empty for the plan's, which the field
  shows; why: D-190). The revision's episode carries it (`scripting.revision_inputs`), so the word budget, the
  agent's floor of 95 %, `check.py`, the run's script checks, the recording's check and the published
  `episode_plan.yaml` read it; the approved outline stays unchanged and still shows its planned minutes. A revision
  from the series review's points keeps the plan's length.
- An episode the writing prompt wrote can be revised the same way, from the outline's scenes.
- The revised script must pass the structure checks and the script review again; then the new text, with its own
  hash, waits again for the reading review before audio, which no review replaces.
- If the previous order itself is unsuitable, the research job changed or the plan is damaged, start a regular
  `script` run instead.

## Runs begun before D-184

A script run begun with the writing prompt's stages (`scripting.PROMPT_STAGES`: `writing`, `narration`,
`polishing`) moves onto the story agent's stages on resume while nothing after `planning` and `teaching` was written
(`scripting.retire_prompt_stages`): its outline and the outline's approval stay, since `outline_hash` binds the outline
and its inputs, not the stages; a teaching design it began stays in `teaching/` unread, the teaching stage runs again
for the agent, and `writer_switch.json` in the run folder records the move. A run that has written an episode keeps
its stages and stops on resume with `writer_retired`; start a new run, or write the next episode from its approved
outline. Orlagau v2's series run, stopped in the teaching design after its outline was approved, continued this way
on 2026-11-01.

- The keys of a run's inputs stay as they were (`teaching.TEACHING_VERSION`, `DESIGN_VERSION`,
  `cast.POLISH_VERSION`, `host_roles`), so a waiting run keeps its input hash.
- Episodes such runs published stay readable, exportable and recordable: the recording reads the stages their run
  had (`episode_audio.reviewed_episode`), and their review notes stay on the reading page.
- An outline such a run planned can be approved and written episode by episode like any other.

## Host names and style notes

### Host names

The host names are two optional fields in the Studio panel **Spoken forms and host names** („Sprechformen und Hostnamen“) (spoken forms and host
names) on the audio page: both or none (`TopicBrief.host_names`).

- With names, the hosts may address each other by them, and transcript, show notes and reading page show them;
  without, they stay "Host A" and "Host B".
- A voice name is never a host name, and no model invents host names.
- The names are stored in `project.yaml` and are part of the project hash. Changed names apply to new script runs;
  existing audio approvals stay valid, being bound to script hash and voices.
- A project without names keeps the project hash its earlier runs carry.
- A third host is named in its own field (`third_host.name`, [Three hosts](#three-hosts)); the panel shows it and
  keeps it when the two names are saved.

### Three hosts

A project may have a third host, `host_c` (`TopicBrief.third_host`: name, role and Qwen voice; why: D-179). It is set
in `project.yaml`; [examples/geld-podcast](../examples/geld-podcast/README.md) is such a project.

- Every model call of a script run that is no search starts with the cast (`cast.cast_note`): the rule for three
  hosts (`prompts/third_host.txt`) and a JSON line with the third host's speaker ID, name and role. The prompts
  themselves are unchanged, so a project of two hosts sends exactly what it sent before. The story agent's `TASK.md`
  ends with the same cast under „Three hosts“, and its brief names the third host.
- The host roles the agent, the reviews and the run inputs carry (`cast.host_roles`) add host_c's role to
  `cast.HOST_ROLES`; the expert and the partner keep theirs ([Hosts and conversation](TEACHING.md#hosts-and-conversation)).
- `validate_script` holds a script to the project's hosts (`models.host_roles_of`): all three speak in every episode,
  and host_c in a project of two hosts is a defect.
- `partner_share` counts every word host_a does not speak, so with three hosts host_b's and host_c's together; under
  the floor the advisory is `low_partners_share`.
- Transcript, reading page, show notes and export name host_c by its `name` (`models.host_labels`).
- The third host's role is what the project says it is; a role that asks for facts the research does not hold is
  still bound by the evidence rules.

### Style notes

`projects/<id>/style_notes.md` holds the operator's standing editorial corrections, edited in the Studio panel
**Editorial notes** („Redaktionelle Notizen“). It enters a script run's inputs as `style_notes` and reaches the story
agent's brief and the script review, not the series plan; the evidence rules take precedence. A change changes the
input hash and so leads to a new run.

## Outputs

| File | Content |
| --- | --- |
| `models/knowledge_model.yaml` | Evidenced findings taken over unchanged, concept, mechanism and example IDs, explanation dependencies and open questions |
| `models/series_plan.yaml` | Justified explanation path, episodes, scenes, prerequisites and deferred topics |
| `research/series_outline.md` | Readable overview; distinguishes planned episodes from the scripts reviewed here |
| `episodes/ep_001/episode_plan.yaml` | The episode's question, scenes (the agent's chapters where it set its own), core and supporting findings, earlier findings taken up, and its assigned research limits |
| `episodes/ep_001/story_plan.md` | The story agent's plan; an episode published before D-184 has `teaching_plan.md` and `.yaml` instead |
| `episodes/ep_001/script.yaml` | Canonical speaker segments with knowledge references and, where the agent cited the corpus, section references |
| `episodes/ep_001/script.md` | The same dialogue as readable text, labelled with the host names or roles, never the voice |
| `episodes/ep_001/show_notes.md` | Chapters, source links and open topics for further study |
| `reports/script_quality.yaml` | Source and structure check, the script review, the story agent's critiques and session (`story_agent`), word count and estimated speaking time per episode at the run's rate, and the series review; each episode entry names under `run_id` the run that wrote it. Entries of episodes published before D-184 keep their reader answers, teaching review and, in a narrated run, the narration review |
| `episodes/audio_review.yaml` | Script hashes and the pending reading review before audio |
| `episodes/series_review.json` | The latest series review over an outline's published episodes ([above](#over-an-outlines-published-episodes)) |

The files lie in the private project folder and are excluded from Git. `runs/<run_id>/` keeps inputs, model answers,
the agent's folders, drafts and reviews for resuming; per-call records: [Run folder and
manifest](ARCHITECTURE.md#run-folder-and-manifest). `research/series_outline.md` and `show_notes.md` use the fixed
words of the project's language, German or English ([Languages](ARCHITECTURE.md#languages)).

### Rejected answers

A readable model answer that violates its answer contract (`rejected_output`), for example a script with a gap in its
chapter sequence, is requested again up to twice with the fields objected to. Each attempt is a counted call with
`failure.json` and `rejected_output.json` in its call folder; only the third rejection stops the stage. A `resume`
repeats this episode's call, because it leaves no checkpoint.

The same holds for every correction loop of a script run that keeps no rejections: the review repair, the script
review, the orientation check, the series review and the supplementary research's own checks
(`run_budget.REASKED_CODES`, for example `invalid_script`). Their stop message ends with „die abgewiesenen Antworten
liegen bei den Aufrufen“ (the rejected answers are with the calls; `run_budget.REASKED_MARKER`). Since 2026-10-07
such a stop also takes **Resume with fresh attempts** and a fresh-attempt
[pre-approval](BUSINESS_LOGIC.md#pre-approvals): nothing is set aside, and the resume asks the stage anew
(`run_budget.reasked_stop`). The same codes without this message replay a saved state, such as a changed checkpoint or a
series correction's recorded failure, and take no fresh attempts this way. (why: D-155)

### Earlier series

A run that publishes the scripts of a different table of contents first moves the earlier series' episode folders to
`episodes/archive/<time>_<run_id>/` (with `receipt.json`); nothing is deleted.

- A folder belongs to the run's table of contents when its `episode_plan.yaml` equals the plan entry of the same
  episode but for its scenes and its length, as on every resume, revision and single-episode run: the story agent
  publishes its own chapters, and a revision or a new episode may have its own minutes
  ([Revising a script](#revising-a-script)). Until 2026-11-03 the scenes counted too, and each
  episode run of Asimov v2 archived the episode before; until 2026-11-09 the length did, and publishing Geld S1's
  revised ep_002 (36 of 48 minutes) archived the revised ep_003 (38 of 58).
- Recordings stay under `exports/<episode>/<audio run>/`, where the archived `audio_latest.json` still finds them.
- If a file in such a folder is open, for example during a recording, the run stops with `episodes_locked` and can be
  resumed afterwards.

### The quality report

The report speaks for all published episodes, not only the latest run.

- A single-episode run (`--episode`, `--revise`) replaces only that episode's entry under `episodes`. Other entries
  stay while their `script_sha256` matches the published `script.yaml` and keep the `run_id` of the run that reviewed
  them; an entry whose text is no longer on disk is dropped. The top-level `run_id` names the latest run. So the
  Studio and the reading view keep the review notes (review limitations, dismissed gaps, advisories) of earlier
  episodes.
- Under `gap_probes_unowned`, the report lists at series level the reported gaps that are no research limit and whose
  corpus hits lie only in sources no episode uses (`gap_id`, `text`, `status`, `references`). Nobody in this run reads
  those sections, so they block nothing and are only reported ([Gap probe](RESEARCH.md#gap-probe)).

### Canonical script

`script.md` is the generated reading view; `script.yaml` is the canonical text. Manual changes to it are detected on
`resume`, not overwritten, and need a new subject review before the run continues.
