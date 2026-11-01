---
title: Agent-Only Writer Plan
doc_type: plan
status: active
date: 2026-11-01
---

# Agent-Only Writer Plan

Goal: the story agent ([D-181](../DECISIONS.md)) is the only writer of scripts, and the Studio runs all of it. A
series is written episode by episode, in order, each episode knowing what the earlier ones actually said. The
writing prompt and everything that serves only it go: the writing stage, the narration review, dialogue polishing,
the teaching design and the writer switch. Episodes written before stay readable and recordable. The current
projects continue at today's quality, and a project is set back only where it cannot continue.

Written for the operator and the agents who continue it. The evidence is the first real agent episode, Asimov v2
ep_007 of 2026-11-01. The operator listened to it („sehr großartig“), and its run files are summarised in the
[Story Agent Plan](2026-10-27-story-agent-plan.md) under V-64 and V-66. (why: D-184)

## Status

**Steps 1 to 5 built on 2026-11-01, uncommitted.**

- Step 4 needed no reset. Every current project continues from where it stands:
  - Asimov v2's next episode is ep_001 of 20, with ep_007 written.
  - Orlagau v2's next is ep_001 of 13 from its approved outline; its old run moves onto the agent's stages if resumed
    by command.
  - Geld verstehen and Ontologies v2 have no outline yet.
- Also built, from the reviews of the change:
  - The agent gets its episode's research limits, the single-group findings and the series goal.
  - Earlier episodes count only when published from the same outline.
  - The orientation sheet is kept before its check.
  - A failed run ends the agent's session.
- Not built:
  - `prompts/listenability.txt` and `continuity.txt` still name teaching-design fields, and the script review
    composes them. Rewording them changes the review's meaning, so it waits for a decision.
  - A quota stop during a critique is answered to the agent as an error, and it goes on without that critique, as
    D-181 had it.

## Decisions of the operator, 2026-11-01

1. The agent writes every script; the prompt writer is removed („das alte kommt weg“), and all of it runs in the
   Studio.
2. A series is written in order, one episode per run, from the approved outline:
   - each new episode reads the published text of the episodes before it, at least the previous one in full;
   - the Studio offers „Nächste Folge schreiben“ and „Alle restlichen nacheinander“, sequential and never parallel;
   - once every episode is published, a series review reads them together, and its corrections are agent
     revisions;
   - Asimov v2 ep_007, written out of order, stays.
3. A revision with the listener's feedback is an agent session given the published episode and the feedback.
4. The agent also runs with an Anthropic API key. Such a session is billed and counts against the money limit.
5. Episode 1 gives the series a real introduction after its opening story, about one to two minutes long: what the
   series is about, why its question matters, the path and how the episodes build on each other. There is no
   trailer and no episode 0.
6. An episode planned at 45 minutes runs 45 minutes („soll ruhig 45 Minuten sein, wenn sie so geplant ist“).
   Ep_007 ran 35:47 for a planned 45 minutes. Two causes added up:
   - the agent wrote 5,212 of its 5,850 target words (89 %, inside its window of 85 % to 110 %);
   - ElevenLabs spoke 146 words a minute where every budget assumes 130.

   So the agent must reach at least 95 % of its target. A new script run takes the project's speaking rate: the
   rate measured on its latest recording, or the voice engine's starting value. The word budget, the length checks,
   the review's metrics and the Studio's minutes all use that rate. Runs written before keep 130.

## Steps

1. **Agent complete.**
   - Two or three hosts in the task and the cast.
   - The series introduction in episode 1.
   - The earlier episodes in the session's folder (`earlier/`).
   - Revision by the agent: the published episode and the feedback.
   - The series repair in story runs: the scoped repair call. Until now it called the writing prompt and crashed.
   - API-key sessions (`claude_api`): billed, with the cost the CLI reports.
2. **Old path removed.**
   - Stages: `writing`, `narration`, `polishing` and the teaching design and its review.
   - The prompts, the stop rules and the CLI flags (`--writer`, `approve --redesign-teaching`).
   - The writer switch, the evals that only measure removed code, and the tests of removed code.
   - The script fixture writes with a fake agent.
   - What stays:
     - reading, exporting and recording episodes of old runs (`episode_audio.reviewed_episode`, `review_notes`,
       `content_text`);
     - the keys of `run_inputs`, so waiting runs resume.
3. **Studio.**
   - The outline page writes the next episode, or all remaining ones, in order.
   - The reader's revision runs through the agent.
   - The series review over the published episodes, with its corrections offered as revisions.
   - The stage display and the reading preview of agent runs.
4. **Projects.**
   - Orlagau v2: the approved outline of `run_20261024_043539…` (stopped in the old teaching design) becomes the
     agent's.
   - Asimov v2: unchanged.
   - Geld verstehen: three hosts.
   - Ontologies v2: its research continues.
   - Orlagau (first version): stays as it is.
5. **Docs and suites.** D-184, SCRIPTS, STUDIO, AGENTS map, CHANGELOG, GOTCHAS; both suites in full.

## Verification

| ID | Item | Status |
| --- | --- | --- |
| V-68 | A whole series written in order: does an episode build on what the earlier ones said, without repeating it? | open |
| V-69 | Episode 1's series introduction, heard by the operator | open |
| V-70 | An agent revision changes what the feedback asks and keeps the rest | open |
| V-71 | Three hosts written by the agent (Geld verstehen) | open |
| V-72 | An English episode written by the agent (Ontologies v2) | open |
| V-73 | An API-key session: its cost in the run's money and receipts | open |
