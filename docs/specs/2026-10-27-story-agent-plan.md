---
title: Story Agent Plan
doc_type: plan
status: active
date: 2026-10-27
---

# Story Agent Plan

Goal: script runs write narrated episodes the way the narration lab's best process did, and the Studio runs it. Scope:
the writing of a narrated script run, the stages around it, the run's budget and receipts, the Studio's progress and
settings, tests and docs. Research and recording are unchanged.

Written for the operator and the agents who continue it. The evidence is the narration lab
(`evals/narration_lab`, [README](../../evals/narration_lab/README.md)); the narration rules so far are owned by
[SCRIPTS](../SCRIPTS.md#narration-review) and the [Narration Plan](2026-10-23-narration-plan.md). (why: D-181)

## Status

**Steps 1 to 3 built on 2026-10-27 (uncommitted); step 4 waits for the operator.** The operator said „Los gehts“
after the lab reached its goal. Built: `story_agent.py` and its task document, the story stage and the teaching branch
without a design, the scoped review with the agent's corpus sections, budget and receipts, the project's writer
switch in the Studio's job summary, the production view's stage and the progress texts, de and en catalogs, the CLI's
`--writer`, `tests/test_story_agent.py` and two browser tests. The full Python suite passed before the Studio changes
(1,326 tests); the mapped tests after them (952 tests), the browser suite (223) and `test_story_agent` after the last change. Not built: the reading page's view of `plan.md` and the corpus sections
used (published as `story_plan.md` and in the quality report instead), and stop cards of their own for
`story_agent_failed`, `story_agent_timeout` and `authentication_required` (the generic stop card shows them).
Owner docs: [SCRIPTS](../SCRIPTS.md#story-agent), [STUDIO](../STUDIO.md), D-181.

## 1. What the lab showed

Three ten-minute cases (Milgram from Asimov v2 ep_007, Kuchavez from Orlagau v2 ep_002, Heinrich IV. of Weida from
Orlagau v2 ep_012), three samples each, rated by the lab judge v3 (Sonnet and Opus averaged; the operator confirmed
its ranking against their own reading on 2026-10-27):

| Process | Milgram | Kuchavez | Heinrich | Mean |
| --- | --- | --- | --- | --- |
| Today's pipeline (writing prompt stack, frozen inputs) | 6.05 | 4.95 | 5.45 | 5.48 |
| Lean narrative writer, three rewrite rounds, hand-made outline | 8.0 | 7.83 | — | — |
| Agent: Claude Code session, Opus 5.5 xhigh, own chapters, up to four critiques (agent4) | 8.27 | 7.83 | 7.89 | 8.00 |
| agent4 plus search in the project's own research corpus (agent7) | 8.45 | 7.89 | 7.83 | 8.06 |

What did not help: line edits instead of rewrites, more rules in one prompt, a dramaturg plan in one structured call
followed by a single-call writer, polishing after the narrative draft (no gain, 14 % longer), best-of-N selection.

What the agent does that single calls do not: it reads all the material and the cited passages, finds the turn that
changes how the listener sees a person, plans the chapters after reading the story material, writes, checks every
fact against the passages itself, runs the code checks, asks an editor up to four times and changes only what a point
names. With the corpus search it finds the scenes the findings only name (Heinrich's divorce before the Bishop of
Naumburg, Jutta's land exchange to found Cronschwitz, Gregory IX's bull for the children of the "deceased" Heinrich).

## 2. Design

A narrated script run gets the **story agent** as its writer, chosen per project (default off until step 4 below).

- **Teaching stage**: story material and story research as now; new, a checked orientation sheet per episode (drafted
  by the text model, every entry checked by a second model; only entries found correct go to the agent). The teaching
  design is not made for an agent-written episode.
- **Writing stage**: per episode a work folder `runs/<run_id>/story/<episode_id>/` with `material.json` (outline entry,
  story material, findings with their limits, the cited passages, the orientation sheet, the word budget, the core
  findings no story item tells), `TASK.md` (from `data/story_agent_task.md`) and four commands the agent may run:
  `check` (the script checks of the run, echo lines, quotation limit, length cap), `critique` (the editor's rubric,
  at most four times), `search` and `read` (the project's research corpus). One Claude Code session (`claude -p`,
  Opus 5.5, xhigh, tools Read, Write, Edit, Glob, Grep and only those commands, no network, no session persistence)
  writes `plan.md`, `draft.json`, optionally `outline.json` (its own two to four chapters holding every core finding)
  and `sources_used.json`. Code then checks the draft as every draft is checked; a draft that fails stops the episode
  with its defects.
- **Own chapters**: the agent's chapters replace the episode's scenes for this run only
  (`story/<episode_id>/entry.json`); `ScriptRun.selected()` returns the adapted entry after writing. Core findings
  stay core; demoting a core finding is not offered, so the series plan's coverage holds.
- **No narration stage, no polishing** for an agent-written episode: the agent's own critique replaces the narration
  review, and polishing made the lab drafts longer without making them better.
- **Script review**: kept, it found real errors in the lab (an invented sequence of steps, "most" for a modal value).
  For an agent-written episode only grounding, scope and structure block; repairs change only the named segments and
  their neighbours (as the narration rework does), written from the agent's material; a declared cold open may state
  a fact before its scene.
- **Budget and receipts**: a session counts as one agent call per episode with its own ceiling in
  `script_budget.STAGE_CALLS`; its usage and cost from the CLI's JSON result are recorded like other calls; the
  editor's critiques are ordinary calls of the run.
- **Studio**: the project's settings choose the writer ("Erzählung: Agent (Opus)"); the progress view shows per episode
  what the agent has done (plan, draft, checks, critiques used, corpus sections); the reading page shows `plan.md` and
  the corpus sections used.

## 3. Steps

1. Backend: `story_agent.py` (work folder, commands, session, result), the writing hook for narrated runs with the
   setting, orientation sheet in the teaching stage, adapted entry, fake-CLI tests. Mapped tests.
2. Stages around it: skip design, narration and polishing for agent episodes; the scoped script review; budget and
   receipts. Mapped tests, then the full suite.
3. Studio: setting, progress, reading page, stop cards; de and en catalogs; browser suite.
4. One real episode in the Studio (Asimov v2 ep_007, the Milgram case), read by the operator; then three episodes; then
   a series. Only then the setting's default.

## 4. Decisions taken by default (the operator may change them)

| Question | Default | Why |
| --- | --- | --- |
| Teaching design for agent episodes | Not made; the agent's plan names the one idea | The lab's best drafts had none; a design stop (`teaching_design_failed`) blocked heinrich2's run |
| Approve the agent's plan in the Studio before it writes | No; the plan is shown for reading | One session plans and writes; an approval gate would split it |
| Demote core findings | Not allowed | Series coverage; the lab's gain came from chapters and corpus search, not demotion |
| Agent model | Opus 5.5, xhigh | The lab's measurements |

## 5. Verification list

- V-64: a real agent session in a Studio run writes a passing draft for Asimov v2 ep_007 (step 4). Started on
  2026-11-01 as `pla script … --episode ep_007 --from-outline run_20261025_182003_335297_5efe1a9a` after the operator
  switched the waiting series run to the agent and stopped it after approval (one episode first); a Studio button
  for one episode follows once this episode reads well. Partly seen on
  2026-10-30/11-01 in the CLI runs of the [lean research test](../../evals/research_lean/README.md): four sessions of
  47 to 61 minutes each wrote drafts that passed the code checks; the review then stopped one (below).
  ep_007 ran through on 2026-11-01 (`run_20261101_132556_067674_ed3b4560`, 1 h 43 min):
  - the session took 48 minutes and 125 turns, asked for 3 critiques and used 17 corpus sources;
  - the draft has 138 segments and 5,212 words (about 40 of 45 planned minutes), 75 of them citing sections;
  - the review passed after two repairs: three overstated conclusions, then two small citation points.

  The reading by the operator is open.
  - Five of the eight review answers were rejected and asked again (about 22 minutes, 6.5 USD). In four of them a
    partner's echo of a fact without refs was judged `preserved`. In four, a quote differed from the text only in
    the case of its first letter, or misquoted it.
- V-65: the scoped script review keeps the agent draft's judge-v3 score within 0.3 (lab `post` measured polish plus
  review at the old judge only). Open, and the first real reviews show why it must be settled before a series:
  - Length: the agent was given 1.3 times the plan and reached it (55 minutes for a planned 44); the review blocked it
    as scope. Fixed on 2026-11-01: the agent's target is the plan's length (`story_agent.TARGET_FACTOR` 1.0).
  - A blocking issue without segments (the length) could not be repaired by the scoped repair, which spent all three
    repairs. Fixed on 2026-11-01: such an issue opens every segment (`story_review_repair.v2-whole-episode`, since `v3-source-refs`).
  - Resuming after the story stage refused the supplementary research (`invalid_supplement`): its receipt binds the
    planned episode, the run gave the agent's chapters. Fixed on 2026-11-01 (`ScriptRun.refresh_foundations`).
  - A scene the agent found in the corpus could cite no finding, so the review counted it as unsupported although
    it had the section. Decided and built on 2026-11-01: segments cite corpus sections in `source_refs` (D-182).
  - The review's framing rules (series orientation, intro and outro) were not in the agent's task; both first
    reviews raised them as structure issues. Since 2026-11-01 the agent gets the rule itself in `material.json`
    (`framing`) and a task section on it (`story_agent.v3-framing`).
- V-66: cost per episode by the CLI's count (the lab: about 7 USD per session at list prices; subscription use).
  Measured on 2026-10-30: 11.6 to 16.0 USD per session for episodes of 34 to 44 planned minutes. Asimov v2 ep_007
  (45 minutes, 2026-11-01), as subscription equivalent, 28.56 USD in all:
  - teaching: 2.95 USD;
  - session: 12.39 USD;
  - critiques: 0.40 USD;
  - review and repairs: 12.83 USD.
- V-67: the corpus search never brings a fact into a draft that its section does not hold (spot check of
  `sources_used.json` against the draft).
