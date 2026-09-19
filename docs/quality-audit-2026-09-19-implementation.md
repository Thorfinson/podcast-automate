# What was built from the quality-audit plan of 19 September 2026

Status: 19 September 2026, after a review pass on the first implementation. Records what landed for
each work package of [quality-audit-2026-09-19-plan.md](history/quality-audit-2026-09-19-plan.md), where
the implementation deviates from the plan and why, what the review of the first attempt found and
how it was fixed, and which acceptance checks still need a real run, a human listener or hardware
this repository does not have.

Both suites are green: the Python suite (667 tests, one pre-existing skip) and
`node --test tests/studio_ui.test.cjs` (81 tests). Every number below comes from a run recorded in
this document, not from an estimate.

## Landed

| WP | What it does now |
| --- | --- |
| WP1 | `script_advisories.py` counts redefinitions of established terms, repeated hedging, a long cold open and an over-target duration. Rows go to `reports/script_quality.yaml` under `episodes.<ep>.advisories`; `over_target_duration.count` is the estimate as a whole-number percentage of the target. `scripts/audit-counts.py` runs the same functions over a published project and reports the raw hedging match count per episode. |
| WP2 | `Concept.terms` carries the spoken names; `prerequisite_context` adds `established_terms` per reviewed prerequisite row; `continuity.txt` states the rule; the polishing pass and its review receive `prerequisite_context` and the continuity fragment. |
| WP3 | `write_episode.txt` and `dialogue_polish.txt` mark an invented example once; `dialogue_polish_review.txt` fails `spoken_language` on repeated reminders; `episode_framing.txt` caps the first spoken segment at 90 words and separates orientation from the episode question. |
| WP4 | Both examiners record `dismissed_gaps` with their reasons. `Studio.detail()` exposes `review_notes` per published episode, and the reading page renders one collapsible "Hinweise der Prüfungen" panel, everything escaped. `reports/script_quality.yaml` keeps the entries of episodes published by earlier runs, each tagged with the run that produced it, so a single-episode revision no longer erases the other episodes' notes. |
| WP5 | `DialoguePolishReview` gains `demanding_passages` with per-referent `resolved_by`; `validate_polish_review` enforces the passage contract and turns an unresolved referent into a `spoken_language` issue for the existing repair loop. `compare_dialogue` is callable alone; `evals/dialogue_polishing/run.py` replays it on the archived ep_002 passage and the seasons control. |
| WP6 | `research_gap_probe.py` probes declared gaps against the stored corpus with no model call. A coverage gap carries `gap_terms`, search words in the language of the sources, written by the composing model; the probe uses them beside the gap text and counts whole tokens only. The research lane probes at planning and at synthesis, pins hits as first reads, settles each gap against what was read, blocks on `hits_unread` in the quality gate and annotates `open_questions.md`. The script lane keeps one run-level `runs/<run_id>/gap_probes.json` with owner episodes: each episode routes the unread gaps whose hits sit in its own sources into the supplementary research, a supplement may confirm a gap it has demonstrably read, later episodes see earlier settlements, and the review guard blocks only on gaps the episode itself could route. Hits in sources no episode uses are reported, never blocking. |
| WP8 | `spoken_forms.py` holds the table at `studio/spoken_forms.json`; a synthesis request carries `spoken_text` beside `text` only when they differ, so every cache entry made before the table existed stays valid; both cache keys and both verifiers apply one shared rule. The pronunciation report is computed without a model in `Studio.detail()` from the current script, table and overrides and stands above the approval checkbox; a copy reaches `reports/<ep>_audio.json` at publish. A dot between word characters binds a token, a hyphen separates. The probe texts gained the difficult tokens. |
| WP9 | `spoken_overrides` in `episodes/<ep>/audio_review.yaml`, a validated `POST /api/projects/<id>/spoken_override` route, and a per-utterance control on the reading page of a published episode with audio. The re-render button sends `rerender: true`; the Studio honours it only when the saved approval covers the current script hash and voices, by the same rule the pipeline applies. One override costs one synthesis call; the export README, show notes and audio report carry the receipts. |
| WP10 | `AudioChoice.pauses` with the planned defaults; `assemble` raises each pause to the minimum for its transition and records `pause_ms` and `pause_reason`; partitioning sizes the parts with the same applied pauses, so a near-cap episode is split before synthesis instead of failing after it. A default policy is not stored in the hashed record, so a stored approval without a policy stays valid and a changed policy needs a fresh approval. |
| WP11 | Chapters are written into the MP3 through an FFmpeg metadata input with escaped titles and read back with `ffprobe`; `chapters_embedded` compares the titles. The export gains `show_notes.md` with timestamps. Script-stage show notes deduplicate sources by `work_id`. |
| WP12 | `TopicBrief.host_names`, editable in the speech panel of the audio page as a pair or not at all; the readable script, the transcript and the reading page name the host or the role, never the voice preset. An unset field does not enter the project hash. |
| WP13 | `pla series-review <project> [--run]` reviews a published run's scripts in its own run folder of kind `series_review`, counts against the run budget, leaves `runs/latest.json` alone and mirrors the verdict into `reports/script_quality.yaml` only when the reviewed run is the published one. A blocked verdict triggers one bounded cross-episode repair: one repair call and one script review per affected episode, then one re-check. The published review is the review of the repaired text; the round is recorded in `series_repair.json`, so a resume after a failed repair blocks with the saved failure instead of spending a second round. |
| WP14 | `research_discovery.txt` asks for an independent source or a stated limitation; `single_group_findings` is an advisory in the evidence summary and the quality report. The writing and script-review payloads carry the episode's single-group findings whose research group is known; unknown-group rows stay in the research report. `write_episode.txt` asks for one audible attribution per episode and `script_review.txt` records a missing attribution as a limitation, never as a blocking issue. |
| WP15 | `projects/<id>/style_notes.md` is part of the run inputs and reaches the writing, polishing and both review payloads with one shared prompt sentence. The audio publish writes `listening_sheet.md`; a Studio checkbox and note set `human_listening_reviewed` through `POST .../listening_review`. |
| WP16 | The deterministic half only: `transcription_check.py` aligns a supplied transcript against `spoken_text` with `difflib`, reports a word error rate with missing and inserted words, and flags without blocking. Nothing calls it yet. |

## What the review of the first attempt found, and what changed

A read-only review of the first implementation on 19 September 2026 ran both suites green and still
found two blocking defects, several high ones and a set of untested acceptance claims. All of them
are fixed in the current tree.

- **Script-lane deadlock.** The first probe routed only gaps whose hits sat in the episode's own
  sources but blocked the review on every unread gap, so five of the six sample episodes could never
  pass review. The probe file is now run-level with owner episodes, the guard matches the routing
  scope, a supplement can confirm a gap it has read, and a gap owned by several episodes is settled
  once.
- **Re-render could not start.** The reading page sent no approval and the Studio refused the
  request before the pipeline's saved-approval rule ran. The `rerender` flag now reuses that rule.
- **Every existing run reported changed inputs.** The optional `host_names` field entered the
  project hash even when unset. `storage.project_hash` drops the unset field, and every hash gate
  uses it; the stored hashes of the sample project's runs match again.
- **Partitioning ignored the pause policy**, so a near-cap episode could fail after synthesis was
  paid. Both use the same applied pause now.
- **The series repair published the pre-repair review** next to the repaired text, the repair
  count was lost on resume, and `pla series-review` repointed `runs/latest.json` at a run kind the
  resume command cannot dispatch. All three are fixed and tested end to end.
- **The attribution rule was blocking per finding**, and on the legacy sample every claim would
  have qualified. It is advisory per episode and limited to findings with a known research group.
- **Every existing speech cache entry was orphaned** by an unconditional `spoken_text` key field.
  The field enters the key only when it differs from the script text.
- **Three prompt consumers were not bumped** after their fragments changed:
  `teaching_review.v4-audit`, `editorial_review.v4-audit` and `series_plan.v4-audit` now are.
- **Heuristics misfired.** The hedging counter matched the ordinary verb "erfunden", everyday
  "keine echte …" and the back-references to a named thought experiment that the prompts themselves
  recommend; the spoken-form boundary treated a dot as a separator and a hyphen as not. Both were
  narrowed and are pinned by tests.
- **Untested claims** now have tests: one override costs exactly one Gemini call, the Qwen cache key
  is tested through the worker's own settings function, dismissed gaps travel the production
  teaching path, review-version drift keeps the draft and its repair count, the `open_questions.md`
  suffix reaches publish, the discovery tag and sentence are pinned, and `repair_series` runs end to
  end with a seeded contradiction.

## Deviations from the plan, and why

- **WP6, the language limit.** The probe is a term-overlap search, so a German gap sentence alone
  cannot find an English section. The audit's own case has exactly that shape: the f13 gap text
  returns no candidate at all from the lexical search. The plan's fix is `gap_terms`: with the
  corpus-language terms `expert load balancing bias rule overloaded`, the section holding the bias
  rule (`src_79bf6b4435bc1b72#sec_6891d807643ea0ef`) is the first hit of 158 candidates on the
  sample index, verified on 19 September 2026. `tests/test_gap_probe.py` pins both directions. The
  sample dossier predates the field and carries empty `gap_terms`, so on that project the probe stays
  text-only until research is re-run (WP7); the WP6 acceptance therefore holds for dossiers written
  by the current prompts, not for the archived one.
- **WP6, routing scope.** The knowledge model's uncertainties are series-wide. An episode routes a
  gap only when a hit sits in its own sources, so no supplement is asked to answer another episode's
  material. A gap no episode can route is reported as `hits_unowned` in the publish report and never
  blocks.
- **WP6, composed gaps.** Synthesis probes the gaps a run declares at composition, not only those
  known at planning, and settles them against every section any task read.
- **WP5, short episodes.** The review owes three demanding passages, or every segment that is
  neither the greeting nor the sign-off when the episode has fewer.
- **WP11, no ID3 fallback.** The bundled FFmpeg 9.0.1 build writes CHAP and CTOC frames and
  `ffprobe -show_chapters` reads them back, including umlauts and escaped metadata characters.
- **Prompt versions.** Phase 2 adds no tag of its own, because the host-name sentence lands in
  `episode_framing.txt` in the same working tree as the Phase 1 batch. Phase 3's `-notes` suffix is
  applied to all four consumers. The three review and planning consumers of the changed fragments
  carry `-audit`.
- **`script_checks.py` signature constant.** The stale literal is `REVIEW_SIGNATURE_VERSION`,
  deliberately independent of `SCRIPT_REVIEW_VERSION`: a review-policy bump re-reviews the saved
  draft through the version stored in the checkpoint instead of discarding the draft and its repair
  allowance. That mechanism is now tested for the script review as it was for the editorial review.
- **In-flight audio runs.** An audio run started before this change cannot be resumed, because the
  worker files it hashed (`speech.py`, `qwen_worker.py`) changed. Approvals are unaffected, and a
  fresh run reuses every cached segment whose spoken text equals its script text.
- **Studio settings.** The Studio has no settings form; the brief is set conversationally. The
  spoken-form table, the pause fields, the host names and the style notes therefore live in
  collapsible panels on the audio page and save through the existing `/save` route.
- **WP13, three calls per repaired episode.** As the plan states: repair, an evidence review of the
  repaired text, then the series re-check.

## Measured state of the sample series

`scripts/audit-counts.py projects/die-entwicklung-der-transformer-architek-13325a --terms
"Token,Query,Key,Value,KV-Cache,Inference,Training,Softmax"`, 19 September 2026, on the published
run `run_20260913_182611_981976_0b7a6dfc`, with the narrowed hedging patterns. The Hedging column is
the raw match count.

| Folge | Begriffe definiert | davon ≥ 2× | Hedging | 1. Abschnitt | Min. | Ziel | Hinweise |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ep_001 | 0 | 0 | 6 | 158 | 29.5 | 30 | repeated_hedging, long_cold_open |
| ep_002 | 2 | 0 | 2 | 92 | 23.7 | 25 | – |
| ep_003 | 2 | 1 | 2 | 92 | 25.8 | 27 | redefined_term |
| ep_004 | 2 | 0 | 2 | 136 | 19.1 | 20 | long_cold_open |
| ep_005 | 1 | 0 | 3 | 69 | 22.5 | 23 | repeated_hedging |
| ep_006 | 2 | 0 | 3 | 50 | 20.5 | 22 | repeated_hedging |

Five of the five episodes after the first redefine a term the series already established; the
audit read "five or six of six". Hedging lands at two to six per episode where the audit counted
three to nine by hand; ep_002 to ep_004 are within the advisory limit of two. Before the patterns
were narrowed the same script counted 6, 4, 5, 4, 5 and 4: the difference is eight
"erfundene Geschichte/Buchtext/Ausschnitt" back-references, two "in unserem Gedankenexperiment" and
one "keine Behauptung über reale Token-Grenzen", none of which is a status reminder. The first
segment of ep_001 measures 158 words against the audit's 157.

The `--terms` flag exists because the sample's teaching plans predate `Concept.terms` and carry
opaque concept IDs (`c01`, `c07_kv_cache`). A regenerated series needs no flag.

## Not done, and what it needs

- **WP7, the real research run.** Not run: it spends subscription quota, and the plan's own
  precondition is that the Codex weekly window resets on 22 September. Nothing in the code is
  waiting on it. When it runs, collect `reports/research_quality.json`,
  `question_research/gap_probes.json`, `budget.json` and the ledger into
  `docs/research-validation-2026-09.md`. The composing model now fills `gap_terms`, so the probe on
  this German-on-English corpus is expected to find the V3 bias rule; a `no_hits` for a gap whose
  answer is in an English section would mean the terms were not filled or were too generic.
- **The series regeneration after Phase 1.** Not run, for the same reason. The numbers above are
  the baseline it should be compared against; `scripts/audit-counts.py` produces the after-numbers
  with the same command.
- **The three real-model eval sets.** `scripts/evaluate-teaching.py`,
  `evals/dialogue_polishing/run.py --live` and `evals/research_evidence/run.py` were not run against
  a model. The offline half of the dialogue-polishing eval passes and validates the archived case.
- **WP13's standalone review of the sample.** The command works and is covered end to end by
  `test_cli`, but it was not pointed at the sample series, which would spend a call.
- **WP16's spike.** `faster-whisper` was not installed. The alignment and reporting half is
  implemented and tested with a fake transcriber; `asr_worker.py` and its environment are not.
- **WP15's held-out topic.** Creating a real project outside machine learning and adding its verdict
  to `evals/teaching_quality/` is an operational task with a real run behind it.
- **Human listening.** Pronunciation of the probe terms, pause feel at a chapter boundary, voice
  consistency across thirty minutes and where attention drops. The reports keep saying
  `human_listening_reviewed: false` until someone uses the checkbox honestly.
