---
title: Scripts
doc_type: business-logic
status: current
last_reviewed: 2026-10-07
covers:
  - src/podcast_automate/scripting.py
  - src/podcast_automate/script_pipeline.py
  - src/podcast_automate/script_checks.py
  - src/podcast_automate/script_evidence.py
  - src/podcast_automate/script_artifacts.py
  - src/podcast_automate/script_checkpoints.py
  - src/podcast_automate/script_models.py
  - src/podcast_automate/polishing.py
  - src/podcast_automate/series_review.py
  - src/podcast_automate/cli.py
  - src/podcast_automate/prompts/series_plan.txt
  - src/podcast_automate/prompts/episode_framing.txt
---

# Scripts

`pla script` turns a finished research run into a series draft (table of contents), source-checked teaching plans
and reviewed, readable dialogue scripts, in the stages `planning`, `teaching`, `writing`, `polishing`, `review` and
`publish`. It produces no audio. Teaching plans and the binding editorial reviews: [Teaching design](TEACHING.md).

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

- Without `--episode`, the run writes every episode of the new draft; their number follows from the content
  ([Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length)).
- A new `script` call plans anew; `resume` reuses the saved plan and every valid draft.
- The research run must be complete and its stored files must match their checksums. Sources are not downloaded
  again.
- Local sources (`local_sources`) must lie inside the project folder ([Local files](SECURITY.md#local-files)).
- Every model call counts against the run's call limit (`research_limits.model_calls`). Lower bound per episode
  (`script_budget.STAGE_CALLS`), projection (`runs/<run_id>/budget_projection.json`), expected calls and the stop
  `script_budget_insufficient`: [Budgets](BUSINESS_LOGIC.md#budgets). Input binding: [Runs, resume and input
  binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding).

## Choosing the provider on the command line

The Studio sets text model and reasoning level on the Settings page
([Choosing the text model](STUDIO.md#choosing-the-text-model)). `pla script` and `pla resume` take:

| Option | Meaning |
| --- | --- |
| `--backend codex_cli\|claude_code\|auto\|openrouter\|claude_api` | Text provider for scripts and reviews; default `codex_cli`. `claude_api` is Claude on your Anthropic API key, billed per call and only with a money limit ([Claude on your own API key](BUSINESS_LOGIC.md#claude-on-your-own-api-key)). |
| `--model` | Model ID, for example `gpt-6-astra` (Codex), `claude-sonnet-5-5` or `claude-opus-5-5` (Claude). Required for OpenRouter. |
| `--reasoning-effort` | `low`, `medium`, `high` or `xhigh` (`text_settings.REASONING_EFFORTS`); Claude also `max` (`text_settings.CLAUDE_EFFORTS`). Optional for OpenRouter, where the model must support the level. |
| `--api-key` | OpenRouter key, or the Anthropic key with `claude_api`; without a value it is asked for hidden. |
| `--max-output-tokens` | Output limit per OpenRouter call. |
| `--web-search` | `model` (default) or `perplexity`: how a new run's supplementary research searches the web; the Perplexity key comes from `PERPLEXITY_API_KEY` ([Research runs and their web search](BUSINESS_LOGIC.md#research-runs-and-their-web-search)). |

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
provider (**„Weiter mit …“** (continue with …), `pla approve --text-switch`, `text_switch.json`): see [Text providers
and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection).

### OpenRouter for a script run

The Codex subscription stays the default. Optionally, planning, teaching, writing, dialogue polishing and every model
review of one script run go through OpenRouter, paid per call from its API credit. Research and the automatic
supplementary research stay on the subscriptions, unless the run searches through Perplexity (`--web-search
perplexity`), which lets the OpenRouter model research too; speech is generated separately
([Recording flow](AUDIO.md#recording-flow)).

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

- `finding_ids` are an episode's core, the findings its explanation needs. Its scenes cover exactly these and develop
  them fully; the dialogue must cite each.
- `supporting_finding_ids` are study details that can back a statement: citable in any scene, not required, part of
  no scene, never core of the same episode.
- Every finding gets a place, as core or support in at least one episode, or a reasoned omission, never both
  (`script_checks.validate_plan`).
- Where the series goal weights *understanding*, the core stays with what the theories explain in their own logic.
- Teaching planning, writing and reviews see core, supporting and recapped findings; the show notes name the sources
  of core and supporting findings.
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

The limits the research run noted for the script in `research_quality_gate.json` reach planning, writing and the
script review as `research_limits` (`script_checks.research_limits`) (why: D-072), each with a `limit_id` and, where
known, the affected findings: source limitations and guiding questions noted as limits, noted limits, objections
noted after two revisions, and the „Hinweise fürs Skript“ (notes for the script) of the follow-up assessment
(`script_notes`).

- The plan enters each `limit_id` in the `research_limit_ids` of the one episode that first uses the affected
  statement. An unplaced limit goes to the first episode whose core or supporting findings it concerns, else to the
  final episode (`script_checks.episode_limits`).
- The script names each limit there once, briefly and in its own words, in the segment with the affected statement:
  no limits scene, no list of caveats at the end.
- The script review counts a limit named this way as supported, as a gap probe supports a statement about something
  missing. A missing limit goes under `limitations`, not as an objection; a repair keeps a named limit.
- Every limit is probed against the stored sources like a gap ([Gap probe](RESEARCH.md#in-the-script-run)), with Jev
  where it is switched on. Unread hits in an episode's sources go to its supplementary research before its teaching
  plan. A limit whose row that research resolves is answered by the sources after all: writing and the script review
  no longer get it (`ScriptRun.stated_limits`); the plan, made before the probe, still names it. (why: D-131)
- An older quality file without these details yields no limits.

## Writing and framing

### Intro and outro

Every episode gets a spoken intro with a short greeting, an orientation and a transition to its opening question. Its
outro answers this question, gives a fitting outlook when a follow-up episode is actually planned, and says goodbye.
A subject example at the start and an open question at the end are not enough. The intro names the question and what
is at stake, not the answer, which the episode earns at its end
([Listenability and narrative arc](TEACHING.md#listenability-and-narrative-arc)).

- The first episode also introduces **the overall topic, its significance and the path through the series**.
- The final episode is **as a whole the synthesis of the series** ([Role, recap and theory first](#role-recap-and-theory-first)).
- A series of one episode combines these tasks.
- Intro and outro stay inside the existing first and last chapters and the word budget: no fixed length, no invented
  show name, no host identity derived from the TTS voices, no advertising formula. Greetings outside the subject need
  no sources; subject recaps do.

### What each step receives

- Planning, teaching plan, writing and polishing get the episode's position in the complete series plan, the central
  question and the planned topic path with each episode's question and `series_role`
  (`editorial.episode_series_context`), also when only one episode is generated.
- The framing rules are one shared prompt block (`prompts/episode_framing.txt`) for the teaching plan and its review,
  writing, polishing and its comparison, the script review, and the editorial and teaching reviews.
- The listenability rules are another (`prompts/listenability.txt`), for writing, polishing and its comparison and the
  script review; the teaching plan plans the arc in its own fields
  ([Listenability and narrative arc](TEACHING.md#listenability-and-narrative-arc)).
- Writing gets only the plan's `scope_note` and the dependencies between the episode's findings, not the whole series
  plan; order, roles and questions come from the series context (why: D-073).
- Writing gets its word budget computed, as `length` in the payload (`script_checks.word_budget`,
  `prompts/write_episode_length.txt`): at least the words of 85 % of `target_minutes` (the check below), a target, and
  that target spread over the scenes by the number of their explanation steps. Until 2026-10-04 the writer derived the
  budget itself, and every first draft of the Transformer series came in at 58 to 75 % of it. (why: D-127)
- The chapter-end recaps and reflection beats of the listenability rules count toward this budget; empty repetition
  does not. The planned minutes stay, so since 2026-10-06 the same time carries fewer facts. The floor, the hour and
  the target factors are unchanged; the factors were measured before these rules.
- **Only for Claude Sonnet 5.5** the target is set above the plan, at 1.3 times its words
  (`script_checks.WRITER_TARGET_FACTORS`): shown the plan itself, Sonnet wrote 71 % of it. Every other model is shown
  the plan until its own drafts are measured. The factor follows the run's first writer: its fixed model, or under
  `auto` the preferred candidate, so an `auto` call that falls to Codex gets the Claude candidate's target (1.0 since Haiku 5.5 became the
  default, D-166). The hour limit stays with
  the check below, not the target.
- A draft accepted under another budget, or before there was one, stands on resume while it still passes every check,
  also after a text switch to a writer with another target; only the open episodes are written anew.

### Reading before recording

The user wants **to read the script first and decide on audio afterwards**, so `episodes/audio_review.yaml` records
the current script state with `audio_approved: false`; a model review grants no user approval
([Human approvals](BUSINESS_LOGIC.md#human-approvals)). The voices in the Windows pilot are Aiden (`host_a`) and
Vivian (`host_b`).

## Dialogue polishing

After `writing`, a separate call processes the complete draft with the teaching plan, the audience and the role
split. Its task is linguistic and dramaturgical: understandable sentences, fitting reactions, transitions that arise
from the line of thought. It must not invent facts, numbers or examples, nor delete a necessary derivation or
qualification.

Paragraph boundaries and speaker assignments may change: the call first judges the flow of a whole chapter and
regroups dense explanations. It applies the listenability rules: long expert turns broken where the thought allows,
the partner's reactions, summaries and questions, a short recap and the next question at each chapter end, and an
opening that announces the answer turned into the question. Many rephrased sentences alone do not show a good
revision; turn length and partner share are aims, and there is no quota for new segments or speaker changes. The
roles (`polishing.HOST_ROLES`) are independent of the TTS voice and apply to Aiden and Vivian alike: [Two hosts and
storytelling](TEACHING.md#two-hosts-and-storytelling).

### Comparison

A separate comparison call checks `meaning`, `completeness`, `speaker_roles`, `spoken_language` and
`episode_framing` (`polishing.POLISH_CRITERIA`); the last covers intro and outro, including the series opening or
overall conclusion.

- Every positive verdict needs actual text evidence: for meaning and completeness from both versions, for framing
  from the first and last chapter.
- It checks difficult passages and transitions for unclear references, explanations merely set side by side, and empty
  repetition. It judges `spoken_language` by the listenability rules with the candidate's measured `dialogue_shape`
  (`script_advisories.dialogue_shape`); a recap, reflection beat or question that restates the original is no new
  fact for `meaning`. It names the most demanding passages under `demanding_passages`
  (`polishing.DEMANDING_PASSAGES`, 3 where the episode has them) and states how the new version resolves each unclear
  reference; one left open becomes a `spoken_language` objection for the repair loop.
- A comparison with missing criteria or invented evidence is asked again with its defects. Its objections go into a
  repair, at most two; resume keeps candidate, comparison, scope and attempt counter.

### Scoped comparison after a repair

Every comparison after a repair is scoped (why: D-074): it gets the previous comparison's points with their outcome
and the segments changed since. A `meaning` or `completeness` point blocks wherever it points
(`polishing.FIDELITY_CRITERIA`), so a step the polish lost never passes as a note. Any other point blocks only if the
previous comparison sent its criterion to repair, it cites a changed segment, or it cannot be placed in a segment; a
new point on unchanged text is a note in `polishing/<episode>/accepted_notes.json`.

### After both repairs

- If only spoken-language objections remain, the polished version stands, its points in `accepted_notes.json`.
- For any other remainder (meaning, completeness, roles, framing, or a deterministic defect such as a too-short
  version), the reviewed draft stays the script if it passes the structure check (`kept_draft.json`, `result.json`
  with `status: kept_draft`) (why: D-075), so the polished version's defect never reaches publication. Only if the
  draft fails it too does the run stop with `dialogue_polish_failed`.
- Script, editorial and teaching review then check the kept draft's framing.
- An episode kept this way counts as polished for Studio progress and budget projection
  (`script_checkpoints.finished`).

### Files

`runs/<run_id>/polishing/<episode_id>/` holds `before.md`, `after.md`, `script.json`, `review.json`, `result.json`
and `checkpoint.json`; the original draft stays under `drafts/`. `reports/script_quality.yaml` takes the comparison
into `episodes.<episode_id>.dialogue_polish` and names the roles. Its evidence applies to the version right after
polishing, which later subject repairs can still change; for audio, only the finally reviewed `script.yaml` the user
approved counts.

### Reviews after polishing

Source review, reader and teaching reviewer then check the actually revised text. The source review also sees the
original draft and the roles, so later repairs keep the explanations and the conversation. A passed before/after
comparison claims no subject truth of the original text; that is still checked against the sources. The final export
sets the audio approval back to pending.

### Version changes

- The intro and outro requirements apply to newly executed steps; a running process keeps the code it loaded
  ([GOTCHAS](GOTCHAS.md)). An old passed comparison does not count as a framing check: existing texts need a targeted
  revision with a new review. `result.json` names the prompt version. Inputs and approvals of running jobs are not
  rewritten.
- After a correction of the editorial review version, a saved verdict is checked again on resume; the latest revised
  text and the correction rounds used are kept. Reading and teaching reviews are each bound to their own prompt; old
  verdicts without this binding are not taken over. None of this grants an approval or resets attempt counters or the
  model budget. Concrete objections of the final review show in the Studio under **„Ausarbeitung“** (drafting).

## Script review

### Structure and quotation checks

- A script must reference its episode's core findings and assign chapters and speakers correctly.
- With an [assembled dossier](RESEARCH.md#assembled-dossier), an episode quotes each source verbatim for at most 25
  words (`script_checks.QUOTED_WORDS_PER_SOURCE`, `script_checks.quotation_errors`): words matching a section the
  episode received as a source in runs of at least six words (`script_checks.QUOTE_RUN`), ignoring case and
  punctuation. Shorter matches are common phrases; a translation is no verbatim quote. A draft above the limit goes
  back to the writer with source, word count and an example passage, at first writing and after polishing and review.
- At writing, a draft that breaks these checks or falls short of its planned duration (the 85 % rule in
  [Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length)) is corrected up to three times, each
  correction reworking the latest attempt against its own defects; a short script is told how many words it has and
  needs at `script_artifacts.SPOKEN_WORDS_PER_MINUTE`. Then writing stops with `invalid_script`, and the message names
  the three corrections.

### Evidence review

A separate call checks what is said against the assigned findings and source sections, and also the required depth,
the worked-out explanation steps, how start and end connect, understandability, the example, the limits of metaphors
and the dialogue.

- Up to three revisions (`script_pipeline.MAX_REVIEW_REPAIRS`).
- If then only objections about understandability, depth or dialogue remain (`script_checkpoints.NOTED_CATEGORIES`:
  `clarity`, `depth`, `dialogue`), the script is adopted; the points stay in the review report and
  `reviews/<episode>_accepted_notes.json`, visible when reading before the audio approval.
- Evidence errors and objections about scope or structure keep blocking; otherwise remaining objections block
  adoption, and drafts and objections stay saved. **„Mit neuen Anläufen fortsetzen“** (resume with fresh attempts) or
  `pla approve <project> --fresh-attempts` gives a review stopped this way three new revisions against the same
  review report.
- A point the review itself files as an advisory (`advisories`) is its own verdict that the point does not block, and
  it stays a note in the report and `accepted_notes.json`, in the first review and after a revision. Only an evidence
  or scope point (`script_pipeline.STRICT_CATEGORIES`: `grounding`, `scope`) counts as an objection wherever the scope
  reaches it. Until 2026-10-04 every first-review advisory counted, and after a revision every one but clarity, depth
  and dialogue. (why: D-126)
- A wall of facts goes back as a `dialogue` objection: findings strung together without a question the listener wants
  answered, the answer announced before it is earned, dense blocks that end without a recap or reflection beat, or the
  expert holding the floor. The review gets the measured `dialogue_shape`; a partner share under 25 % or more than two
  turns over 120 words is such an objection, a long turn for one step of a worked example is not. Being a `dialogue`
  point, it stops nothing once the three revisions are spent. (why: D-142)
- After a subscription pause too, a text already corrected need not be written again.

### Scoped follow-up review

Every review after a revision is scoped (why: D-066): it gets the previous review's objections and the segments the
revision changed. Only three kinds of objections may block:

1. a previous objection still open,
2. an objection about a changed segment,
3. a factual error or source contradiction anywhere (`script_pipeline.CRITICAL_BASIS`).

An objection about the whole episode, without a segment, blocks only if the previous review raised one of the same
category about the whole episode, or as a factual or source error. Everything else, for example a missing reference
or a statement about something missing without a gap probe in a segment the previous review passed unchanged, is an
advisory (`advisories`) in the report and notes. Code sets the scope by comparing the versions; the verdict only
marks what is critical. So the review converges instead of finding new details in the same text every round. Within
the scope, a point the review filed as an advisory blocks only as an evidence or scope point
([above](#evidence-review)).

If a version had no blocking objections and a later revision (say, for understandability) breaks its evidence, the
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
- Today's version is `script_review.v14-listenability` (`script_checks.SCRIPT_REVIEW_VERSION`). A
  `script_review.v11-core-limits`, `script_review.v12-source-corrected`, `script_review.v13-reviewer-advisories` or
  `script_review.v14-listenability` (v15 only loosens its dialogue point: chapter endings vary by design, D-143)
  verdict that blocked nothing stays valid on resume (`script_checks.RELAXED_REVIEW_VERSIONS`), so an episode in flight
  is not reworked for the listenability point; a blocking one is reviewed again before the next correction, also when
  its repairs are spent.

### Reader, editorial and teaching reviews

A fresh reader call, a separate editorial review and a teaching review also check each episode; what each sees:
[What the application enforces](TEACHING.md#what-the-application-enforces); their perspectives:
[Three editorial reviews](TEACHING.md#three-editorial-reviews).

- In parallel text mode, reader call and editorial review ask at the same time.
- An episode's evidence review is saved before its teaching review starts, so a stop during the teaching review does
  not ask it again (`teaching_pending` in the checkpoint).
- The application checks the evidence and completeness of the reviews. A missing required explanation step or a
  negative verdict leads to a revision and, if problems persist, to a block.

### Limits of the review

A passed model review replaces neither the reading nor the listening review. The series draft is editorial
planning; the overall review rates the final scripts, not how the produced series sounds. Human reading and listening
acceptance stay a separate step ([Acceptance procedure](QUALITY.md#acceptance-procedure)). Nothing is published
publicly.

## Series review

New runs store `series_review_version` in their inputs. Once all planned episodes are reviewed in the same run, one
more call checks the complete final texts against (`series_review.series_criteria`):

- `coverage`: the guiding question and each episode's core findings (supporting findings may be missing);
- `prerequisites`, `progression` and `deferred_questions`;
- `synthesis`: the final episode assembles all episodes' contributions into one answer;
- `arc`: each episode says what it contributes and what one now knows;
- `exposition` or `guidance` when the series goal weights *understanding* or *applying* with 2 or 3
  (`series_review.GOAL_CRITERIA`).

Rules for the verdict:

- Each criterion is judged exactly once. Positive verdicts need verbatim segment evidence; a passed series overall
  needs evidence from every episode.
- Every failed check names under `episode_ids` the episodes whose script must change (otherwise the answer is asked
  again) and says with `source_limit` whether only the research lacks the material.
- No script correction can fix a source limit, so it does not block but is an advisory (`criterion`, `reason`,
  `episode_ids`, `basis: source_limit`) under `report.advisories` in `runs/<run_id>/series_review.json` and
  `series_review.advisories` in `reports/script_quality.yaml`.
- Non-blocking repetitions are stored as `warnings`.
- Every other substantial objection blocks providing the series, and with it its new audio approval.
- The report version stays `series_review.v1` (`series_review.SERIES_REVIEW_VERSION`), so saved verdicts and the
  audio approvals bound to them stay valid; new calls carry the prompt tag `series_review.SERIES_REVIEW_PROMPT`,
  currently `series_review.v4-core`.

### Saved verdicts

- The report records the criteria it asked for (`criteria`); a saved verdict is checked on load against exactly these
  (why: D-076). An older report without them is judged by the checks it contains, if the five base criteria
  `coverage` to `synthesis` are among them (`series_review.LEGACY_CRITERIA`).
- `runs/<run_id>/series_review.json` is bound by checksum to the plan, the inputs and all texts. Interruptions reuse
  a valid report; a negative verdict stays saved too, so merely resuming buys no new verdict.

### Correction round

A negative verdict is followed by exactly one bound correction across the episodes: the evidenced segments of each
affected episode are revised once, the result gets its own evidence review, and the series review checks again;
three calls per affected episode. If a corrected episode fails its evidence review, a second attempt answers those
objections and still the series review's original objection (why: D-077); only then is the correction rejected
(`script_pipeline.SERIES_REPAIR_ATTEMPTS` = 2, up to five calls per episode).

- **Scoped re-review.** The renewed series review gets the corrected verdict's checks with their outcome
  (`previous_checks`) and the changed episodes (`changed_episodes`). Only an earlier objection the scripts still have
  (the same criterion on an episode it named) or an error in a changed episode may block; a new point on an unchanged
  episode is an advisory with `basis: unchanged`. This scope is saved in `series_repair.json` before the call, so a
  resume asks the same question. A second negative verdict in this scope blocks (`series_review.MAX_SERIES_REPAIRS`,
  one round).
- **Fresh attempts.** **„Mit neuen Anläufen fortsetzen“** (or `--fresh-attempts`) sets a failed correction round
  aside, and likewise the spent round after a negative series verdict
  (`series_repair_superseded_NN.json`). The next start corrects against the saved verdict if it belongs to today's
  text, else first judges the series anew; either way it has one round again.
- **Stopped rounds.** A round stopped before its final series review, for example by **„Auftrag anhalten“** (stop
  job), is not spent; resuming picks it up (`finished` in the record).
- Polishing and teaching review are not repeated: the change is limited to named segments and the learning objectives
  are unchanged.
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
- No other failure is recorded (`series_review.resumable`): a timeout, a stall, a quota pause, **„Auftrag
  anhalten“**, a missing sign-in or key, a spent call limit, a busy project, a malformed answer, or a provider error
  (`codex_failed`, `claude_failed`, `claude_structured_output`, `openrouter_unavailable`, `openrouter_connection`).
  Resuming continues the round for the episodes whose correction is not yet adopted (why: D-078).
- A corrected episode's audio approval lapses, because it hangs on the old script hash. Changed or missing reports
  prevent audio from an affected new run.

### Scope and limits of the series review

- For a partial job, the report names the missing episodes and claims no overall review; separately generated
  episodes are not merged into a reviewed collection. A one-episode series can be reviewed fully.
- The call counts against the normal production budget and uses the selected text model. Texts are passed in full,
  without silent truncation; for large series a provider's context limit can block the review.
- Older runs already started keep their original inputs and approvals and are not marked series-reviewed on resume.

### Standalone `pla series-review`

`pla series-review <project> [--run <run_id>]` reviews a finished run's scripts afterwards as a series; without
`--run`, the most recently published run. It also takes `--backend codex_cli|claude_code|claude_api|auto` (`claude_api`
reads its key from `ANTHROPIC_API_KEY`), `--model` and `--reasoning-effort`.

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

- A revision starts a new run with the previous series plan and the existing canonical script. Old text and feedback
  are stored as inputs and the research is kept, so no new planning or search calls are needed.
- Style changes in `project.yaml` are applied; the teaching plan is created and reviewed anew.
- The revised script must pass the source, structure and explanation reviews again; then the new text, with its own
  hash, waits again for the reading review before audio, which none of these reviews replaces.
- If the previous order itself is unsuitable, the research job changed or the plan is damaged, start a regular
  `script` run instead.
- Existing script versions are not rewritten automatically. For a newer stage (such as polishing or `teaching`) on an
  existing text, use a new `script` run or `--revise`; older runs on `resume`: [Existing projects](TEACHING.md#existing-projects).

## Host names and style notes

### Host names

The host names are two optional fields in the Studio panel **„Sprechformen und Hostnamen“** (spoken forms and host
names) on the audio page: both or none (`TopicBrief.host_names`).

- With names, the hosts may address each other by them, and transcript, show notes and reading page show them;
  without, they stay "Host A" and "Host B".
- A voice name is never a host name, and no model invents host names.
- The names are stored in `project.yaml` and are part of the project hash. Changed names apply to new script runs;
  existing audio approvals stay valid, being bound to script hash and voices.
- A project without names keeps the project hash its earlier runs carry.

### Style notes

`projects/<id>/style_notes.md` holds the operator's standing editorial corrections, edited in the Studio panel
**„Redaktionelle Notizen“** (editorial notes). It enters a script run's inputs as `style_notes` and reaches writing,
dialogue polishing, the polishing comparison and the script review; the evidence rules take precedence. A change
changes the input hash and so leads to a new run.

## Outputs

| File | Content |
| --- | --- |
| `models/knowledge_model.yaml` | Evidenced findings taken over unchanged, concept, mechanism and example IDs, explanation dependencies and open questions |
| `models/series_plan.yaml` | Justified explanation path, episodes, scenes, prerequisites and deferred topics |
| `research/series_outline.md` | Readable overview; distinguishes planned episodes from the scripts reviewed here |
| `episodes/ep_001/episode_plan.yaml` | The episode's question, scenes, core and supporting findings, earlier findings taken up, and its assigned research limits |
| `episodes/ep_001/teaching_plan.md` and `.yaml` | Learning objectives, prerequisites, worked example and synthesis |
| `episodes/ep_001/script.yaml` | Canonical speaker segments with knowledge references |
| `episodes/ep_001/script.md` | The same dialogue as readable text, labelled with the host names or roles, never the voice |
| `episodes/ep_001/show_notes.md` | Chapters, source links and open topics for further study |
| `reports/script_quality.yaml` | Source and structure check, reader answers, evidenced teaching review, word count and estimated speaking time per episode; each episode entry names under `run_id` the run that wrote it |
| `episodes/audio_review.yaml` | Script hashes and the pending reading review before audio |

The files lie in the private project folder and are excluded from Git. `runs/<run_id>/` keeps inputs, model answers,
drafts and reviews for resuming; per-call records: [Run folder and manifest](ARCHITECTURE.md#run-folder-and-manifest).
`research/series_outline.md`, `teaching_plan.md` and `show_notes.md` use the fixed words of the project's language,
German or English ([Languages](ARCHITECTURE.md#languages)).

### Rejected answers

A readable model answer that violates its answer contract (`rejected_output`), for example a script with a gap in its
chapter sequence, is requested again up to twice with the fields objected to. Each attempt is a counted call with
`failure.json` and `rejected_output.json` in its call folder; only the third rejection stops the stage. A `resume`
repeats this episode's call, because it leaves no checkpoint.

The same holds for every correction loop of a script run that keeps no rejections: the draft and its repairs, the
script review and its repair, the teaching design, its review and focused repair, the reader, editorial and teaching
reviews, the polishing comparison, the series review and the supplementary research's own checks
(`run_budget.REASKED_CODES`, for example `invalid_script` or `invalid_teaching_review`). Their stop message ends with
„die abgewiesenen Antworten liegen bei den Aufrufen“ (the rejected answers are with the calls;
`run_budget.REASKED_MARKER`). Since 2026-10-07 such a stop also takes **„Mit neuen Anläufen fortsetzen“** and a
fresh-attempt [pre-approval](BUSINESS_LOGIC.md#pre-approvals): nothing is set aside, and the resume asks the stage anew
(`run_budget.reasked_stop`). The same codes without this message replay a saved state, such as a changed checkpoint or a
series correction's recorded failure, and take no fresh attempts this way. (why: D-155)

### Earlier series

A run that publishes the scripts of a different table of contents first moves the earlier series' episode folders to
`episodes/archive/<time>_<run_id>/` (with `receipt.json`); nothing is deleted.

- A folder belongs to the run's table of contents when its `episode_plan.yaml` equals the plan entry of the same
  episode, as on every resume, revision and single-episode run.
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
- Under `gap_probes_unowned`, the report lists at series level the reported gaps whose corpus hits lie only in sources
  no episode uses (`gap_id`, `text`, `status`, `references`). Nobody in this run can read those sections, so they
  block nothing and are only reported ([Gap probe](RESEARCH.md#gap-probe)).

### Canonical script

`script.md` is the generated reading view; `script.yaml` is the canonical text. Manual changes to it are detected on
`resume`, not overwritten, and need a new subject review before the run continues.
