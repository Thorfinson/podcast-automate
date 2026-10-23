---
title: Narration Plan
doc_type: plan
status: active
date: 2026-10-23
---

# Narration Plan

Goal: scripts that tell their stories instead of listing claims about them. Scope: the research prompts (prompt
generation 4), the script run's series plan, teaching design and writing, a new narration stage between writing and
polishing, the review notes in the Studio, and a hand-run evaluation. Recording is unchanged.

Written for the operator and the agents who continue it. Every code reference was checked against the working tree on
23 October 2026. The rules themselves are owned by [RESEARCH](../RESEARCH.md#story-rules-prompt-generation-4),
[SCRIPTS](../SCRIPTS.md#narration-review), [TEACHING](../TEACHING.md#story-material-and-core-story) and
[BUSINESS_LOGIC](../BUSINESS_LOGIC.md#script-lower-bound-and-expected-calls); this plan states why and what is still
open. (why: D-180)

## Status

**Built on 2026-10-23, uncommitted; the evaluation under `evals/narration` is being built.** No real-model call has run
under the new prompts yet (V-58 to V-63).

The operator decided on 2026-10-23: build all of it, with the narration review after writing and before dialogue
polishing („eher nach dem Schreiben und vor der Dialogglättung“), and test it before the whole Orlagau series is
written anew.

## 1. Problem

The operator listened to Orlagau episodes 1 to 4 (2026-10-21) and found none really good: „die coolen Sachen werden
auch oft in einem oder zwei Sätzen abgehandelt, wo manchmal deutlich mehr drin ist“. A reading of all 15 transcripts
rated them 4.9 for tension, 5.7 for interest and 4.3 for storytelling out of 10 (best ep_003 and ep_009, weakest
ep_004, ep_008 and ep_010). The series has 92,039 spoken words, about 11.8 hours.

Three examples traced through the run files showed where the story got lost:

| Story | Research | Plan and script |
| --- | --- | --- |
| Kuchavez' warriors hanged after a judicial duel (ep_002) | One 248-character `illustration` of a claim, from Billig's paraphrase of Thietmar; Thietmar was not read for it | One step of a scholars' dispute (Hengst against Billig) |
| The Vogtländischer Krieg, at least sixty castles (ep_012) | A Wikipedia article and the Residenzstädte handbook; Finkenwirth (1912) cited only for his verdict | One reasoning step among nine; four lines in the script |
| Heinrich IV. of Weida divorced at Mildenfurth, Jutta later prioress (ep_014) | In a finding and in the teaching plan, „feierlich vor dem Bischof von Naumburg“ | Turned into a transfer question „Wer gründete Cronschwitz?“ |

The causes, each in today's code:

- **Research.** The reader answers one fixed question to its acceptance criteria (`prompts/question_reader.txt`); a
  story enters a finding only as the illustration of a claim. The 74 planned questions of each v2 project (Asimov v2,
  Ontologies v2, researched 2026-10-07 with the same pipeline as Orlagau) never ask for events, people, a discovery or
  a plot. Read against the sections their findings cite, 5 of 13 Asimov v2 findings and 3 (plus 2 slight) of 12
  Ontologies v2 findings left story material of the same section out: Milgram's sweating and trembling subjects, the
  Mule's unmasking („there were two of him“), the odds of Seldon's trial, 99 % face recovery from deleted embeddings,
  Claude Opus annotation at 0.26 to 0.43 USD against 7.41 to 54.35 USD for human curation. Where a question asked for
  an experiment's setup (Asch, Milgram, the music experiment), the material was captured richly.
- **Series plan.** Orlagau's episodes held 13 to 26 core findings in 38 to 56 minutes, about 300 spoken words per
  finding with every question, caveat and transition. In the older Transformer series the episodes with 8 to 10 core
  findings developed their mechanism in full; the one with 23 toured it.
- **Teaching design.** Scenes are lists of 3 to 9 reasoning steps; a story becomes one step of an argument.
- **Dramaturgy check (D-143).** It checks labels in the plan, never the script. Orlagau's designs varied them as asked,
  yet 13 of 15 used a dramaturgy that sets a guess against the evidence (mystery 4, debate 4, thought experiment 3,
  myth check 2), the discovery dramaturgy was never chosen, and 14 of 15 designs let host_b voice the first answer the
  evidence overturns. Every episode also closed with the framing's outlook formula („In der nächsten Folge fragen
  wir …“).
- **Workshop talk.** „gelesen“ 207 times and „Wikipedia“ 55 times in the 15 Orlagau transcripts; Ontologies v1 2.4 to 3.7
  such phrases per 1,000 words in English („we've only examined the abstract“). In Asimov v2 the phrases are already in
  the findings, partly in the first person („habe ich nicht gelesen“).
- **Style notes** (`style_notes.md`) reach writing, polishing and the script review, not the series plan or the
  teaching design, where the compression happens.

## 2. What was built

| Part | Where | Applies to |
| --- | --- | --- |
| Story rules for research plan, reader and answer review | `prompts/story_plan.txt`, `story_reader.txt`, `story_verify.txt`; `question_answering.STORY_GENERATION` | Research runs of prompt generation 4, started from 2026-10-23 |
| Core limit per episode | `prompts/series_plan_density.txt`; `script_checks.core_limit` (one per `CORE_MINUTES` = 4 planned minutes, at least `MIN_CORE` = 4) | Narrated script runs |
| Story material per episode | `narration.story_material`, `prompts/story_material.txt`; `teaching/<ep>/story_material.json` | Narrated script runs |
| Core story and who guesses | `TeachingPlan.core_story`, `story_item_ids`, `story_scene_id`, `first_answer_voice`; `prompts/teaching_design_story.txt`, `teaching_design_review_story.txt`; `dramaturgy.FAMILIES`, `variety_defects(narrated, story)` | Narrated script runs |
| Story research | `StoryMaterial.missing`, `ScriptRun.research_story`, `teaching_research.research_foundations(optional=True)`; `teaching/<ep>/story_research.json` | Narrated script runs, once per episode where the sections only point to a story |
| Story rules for writing | `prompts/write_episode_story.txt` and the material in the writing payload | Narrated script runs |
| Narration stage | `narration.narrate_episode`, `ScriptRun.narrate`; `prompts/narration_review.txt`, `narration_review_followup.txt`, `narration_repair.txt`; `narration/<ep>/` | Narrated script runs |
| Report and Studio | `narration` block in `reports/script_quality.yaml`; review notes with scores and categories; preview state „Erzählweise geprüft“; stage „Narrationsprüfung“ | Narrated script runs |

A narrated run is one whose manifest has the `narration` stage: every `pla script` run, `--revise` and Studio script
job started from 2026-10-23. A run started before keeps its stage list, its prompts and its checkpoints, and resumes as
it would have.

## 3. Evaluation (evals/narration)

Planned, run by hand with real models; nothing here runs in either test suite.

1. **Rubric calibration.** The narration review, round one only, on the 15 published Orlagau scripts. The operator's
   listening note is the anchor: episodes 1 to 4 heard, the beginning of episode 2 „ganz ok“, none really good. The
   reading of 2026-10-21 gives the comparison per episode (4.9 / 5.7 / 4.3 on average). It passes when no episode
   scores above 6 for storytelling, the ranking roughly follows the reading, and the review names the stories that
   reading found told too briefly (Adolf von Altena in ep_009, Jutta in ep_014, the Brauweiler forgers in ep_004) and
   marks Kuchavez (ep_002) as missing from the material.
2. **Story-material recall.** `story_material` over the sections the v2 findings cite, with the known cases as ground
   truth: Milgram's subjects, the Mule's unmasking, Seldon's trial odds (Asimov v2), the 99 % face recovery, the Opus
   costs (Ontologies v2), Jutta at Mildenfurth (Orlagau). It passes when it finds most of them and every kept item's
   anchor is verbatim (checked in code).
3. **A/C/D comparison.** Orlagau ep_014 (story already in the material) and ep_002 (story needs the sources) on a copy of
   the project, through `pla script --revise`, which keeps the series plan:
   - A: the published script;
   - C: the new pipeline with the narration stage left out;
   - D: the whole new pipeline.

   Every version passes the dialogue comparison and the evidence review as usual. A negative control adds an invented
   detail to D's narrated draft; the evidence review must flag it. The operator judges the story chapters blind. The
   core limit is not tested this way, since `--revise` keeps the plan; a plan-only run shows the new core counts.

## 4. Open decisions

- Whether the whole Orlagau series is written anew, after the comparison.
- Whether stories the material lacks (`research_points`, such as Kuchavez in Thietmar) are researched for Orlagau or
  only for new projects.

## 5. Verification list

| ID | Open point | How to check | When | Status |
| --- | --- | --- | --- | --- |
| V-58 | Real-model behaviour of the story rules in plan, reader and answer review: whether plans ask for events and people, readers keep the scene beside the number, and reviews accept such findings without calling them scope creep | The first research run of generation 4; count story questions in the plan and story findings per answer, and the review issues of the `.story` calls | First new research run | open |
| V-59 | Whether story material from the sections the episode's findings cite suffices, or a story's section is often uncited | Evaluation 2; for Orlagau ep_002, whether Kuchavez is reported as `missing_story` | Evaluation | open |
| V-60 | The narration review's calibration and the share of rewrite, research and note points | Evaluation 1 | Evaluation | open |
| V-61 | Whether reworks pass within `NARRATION_GROWTH` = 1.15 and the scope check, or are refused (`kept_draft`), and whether the evidence review flags reworked stories more often | Evaluation 3, then the `status` in each `narration/<ep>/result.json` of the first narrated series | Evaluation, first series | open |
| V-62 | Whether `CORE_MINUTES` = 4 leaves the plan valid without many repairs and the episodes room to tell, and whether the `first_answer_voice` rule and the dramaturgy families vary what a listener hears | The first narrated plan's repairs and core counts; the operator listens to three episodes in a row | First narrated series | open |
| V-63 | Calls and cost per episode of the new parts: one story-material call, one narration review, a rework and its review when points are named, plus the evidence repairs a rework causes | Count `calls/*` by prompt family in the production report of the first narrated series | First narrated series | open |
| V-64 | Whether the story research finds the stories the material names as missing (Kuchavez in Thietmar for Orlagau ep_002), how often it ends `not_found` or `refused`, and its calls per episode | Evaluation 3 (ep_002), then `story_research.json` of the first narrated series | Evaluation, first series | open |
