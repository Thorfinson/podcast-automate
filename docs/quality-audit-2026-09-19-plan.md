# Implementation plan for the quality audit of 19 September 2026

Status: 19 September 2026. Implements the sixteen proposals P-01 to P-16 in [quality-audit-2026-09-19.md](quality-audit-2026-09-19.md). Written for the operator and for the agents who will carry out the work packages. Every file and line reference was checked against the working tree on 19 September 2026, including uncommitted changes; where the audit's citations have drifted, section 1 gives the current location.

The plan groups the proposals into sixteen work packages in three phases. Phase 1 fixes the two confirmed defects and the brief violations with prompt lines, validators and one new deterministic probe. Phase 2 gives the operator control over speech delivery. Phase 3 adds series-level repair, source independence and the learning loop. Each work package names the files to touch, the tests that must change, and an acceptance check that can be run without a paid call unless stated.

## 1. Verified baseline

**Working tree.** Forty modified and thirteen untracked files are uncommitted, including the Claude Code backend (`claude_code.py`, `provider_pool.py`, `subscriptions.py`) and the question-level research lane. The audit and this plan cite that tree. The first step is therefore to land the pending work as its own commit with both suites green, so every work package below starts from a known state.

**Citations that moved since the audit.**

| Audit says | Current location |
| --- | --- |
| `research.py:498` coverage gate | `research.py:574-579` in `publish_stage` (`research_coverage_incomplete`) |
| `question_answering.py:198` primary-source flag | `question_answering.py:232-237`, the validator inside `web_search` |
| `studio.py:513` revision | `studio.py:582-690` `Studio.start()`; `studio_worker.py:120-121` |
| `web/app.js:476-477` reading page | That is the production page (`renderProductionDetails`). The reading page is `renderScript()` at `app.js:504-521` and renders no review output at all. |

**Structural facts the proposals depend on.**

- There is no warning tier. Every deterministic check in `script_checks.py` blocks after a bounded repair, and every model `limitations` list is stored whole in `reports/script_quality.yaml` (`script_artifacts.py:72-91`) and read by nothing. P-04, P-05, P-11, P-15 and P-10 all need a non-blocking channel that does not exist yet.
- The lexical corpus search is `SourceReader.search` in `research_reader.py:34`, a pure BM25-style scorer over the in-memory source index. It costs no model call and is already used model-free by `seed()` in `question_answering.py:91-98`. P-01 can be built on it directly.
- Gap text enters the script lane in exactly one place: `script_pipeline.py:120-122` splices `dossier.open_questions` and every `coverage.gap` into the knowledge model's `uncertainties`. `open_questions.md` has one writer, `research.py:620-623`.
- The supplementary research path already exists: `teaching.py:311-322` writes `research_needed.json`, `teaching_research.py:53-65` reads it, `research_foundations` adds findings with verified excerpts. P-01 can route probe hits through it instead of inventing a second reader.
- The polishing pass is the only generation step that never sees `prerequisite_context` (`polishing.py:66-71`, `script_pipeline.py:232-234`). It cannot know which terms earlier episodes established.
- `Concept` in `teaching.py:31-36` has a `concept_id` and a `meaning` but no spoken term. A term registry needs a new field.
- Segment text reaches both engines verbatim: `speech.py:137` and `qwen_worker.py:144-146`. The cache keys differ: Qwen hashes device, dtype, seed and package versions (`qwen_worker.py:130-136`), Gemini only text, voice and format (`speech.py:68-71`). Both verifiers (`speech.py:208-219`, `episode_audio.py:117-133`) compare `text`.
- Pauses are applied once, `audio.py:120-125`, after every segment including the last. The timeline rows already carry `chapter_id`, so chapter boundaries are known at assembly.
- The only MP3 tag written is `title` (`audio.py:151`). No ID3 library, no speech-recognition library, and no `mutagen` exist anywhere in the repository or its setup scripts. Controller dependencies are pydantic, PyYAML, pypdf and psutil.
- The series review has no repair path. A `blocked` verdict is persisted, bound by hash, and re-loaded on resume without spending a call (`series_review.py:106-136`).
- Per-project state lives in `project.yaml` (`TopicBrief`, hash-gated) and `studio/*.json` (provider choices, not hash-gated). `style_profile_id` in `models.py:81` is a dead field that no code reads.
- `script_checks.py:236` hashes the literal `"script_review.v7-framing"` while the call site uses `v8-evidence`. Harmless today because `script_pipeline.py:263-266` catches version drift separately, but it will bite when the tag is bumped again.
- New model calls that are minimums must be added to `STAGE_CALLS` in `script_budget.py:15-20`; `tests/test_run_budget.py:49` and `tests/test_script_budget.py:26` fail until they are. Repairs are excluded from the minimum by design.

## 2. Decisions that cut across the work packages

1. **One advisory tier, deterministic, never blocking.** A new module `script_advisories.py` produces `{code, episode_id, segment_ids, count, detail}` rows from the final script text. They are computed at publish, stored under `episodes.<ep>.advisories` in `reports/script_quality.yaml`, and shown to the reader by the same panel that shows reviewer limitations (P-10). No repair loop consumes them in this plan. If the counts stay high after the prompt changes, a later change can feed them into the review payload.
2. **A probe hit is a section to read, not proof that the gap is false.** A term-overlap search over a 652-section corpus will match almost anything. The deterministic rule is therefore "no spoken gap with unread corpus hits", not "no hits". A hit is routed to a reader; the reader either resolves the gap with a referenced finding or confirms it, and the confirmation is recorded. Only the unread state blocks.
3. **Spoken text is a second field, never a rewrite of the script.** Every synthesis request carries `text` (the reviewed script, used for transcripts and hashes) and `spoken_text` (what the engine hears). Both cache keys and both verifiers switch to `spoken_text`. Script hashes, approvals and the reading page never change because of a spoken form.
4. **Where new settings live.** Pause policy goes into the audio choice (`studio/audio.json`), because it is a rendering parameter and belongs with the voices. The spoken-form table goes into `studio/spoken_forms.json`; per-segment overrides go into the per-episode `audio_review.yaml`, which is already the mutable audio decision. Host names go into `project.yaml`, because they shape the text. Style notes are a Markdown file the operator edits, read into the run inputs so the input hash covers it.
5. **Model calls.** No work package adds a mandatory call, so `STAGE_CALLS` stays at nine per episode. The gap probe can trigger the existing supplementary research (a conditional call), and the series repair adds bounded repair calls. Both count against the run budget as repairs do today.
6. **Prompt versions are bumped once per phase, not per work package.** Several fragments (`continuity.txt`, `episode_framing.txt`, `plain_language.txt`) are composed into four or five call sites. Editing them in three separate commits would invalidate the same checkpoints three times. Section 4 lists the batched tags.
7. **No new controller dependencies.** MP3 chapters are written through FFmpeg's metadata input, with a small pure-Python ID3 writer as the fallback if the bundled build does not emit chapter frames. Speech recognition, if it proves installable, lives in `.venv-tts` or a sibling environment and is optional.
8. **Heuristics are German and language-keyed.** The hedging, definition and non-German-token patterns are small regex lists keyed by `de`, with an empty list for other languages so nothing fires silently on an English project.

## 3. Work packages

Size: S is under a day of focused work, M one to two days, L more. Effort excludes the real-run validations, which cost subscription quota rather than time.

### Phase 1: the confirmed defects and the brief violations

#### WP1 Advisory tier and counting script (foundation for P-04, P-05, P-11, P-15) — S to M

- **Change.** New module `src/podcast_automate/script_advisories.py` with pure functions over an `EpisodeScript`, its `EpisodePlan`, the language and an `established_terms` list:
  - `redefined_terms`: for each established term, count sentences that define it (patterns such as `<Term> ist …`, `Ein(e) <Term> ist …`, `<Term>, also …`, `nennen wir <Term>`, `heißt <Term>`). Advisory when a term is defined more than once in an episode after the first.
  - `repeated_hedging`: count matches of a small list (`kein(e) (gemessene|beobachtete|ausgelesene)`, `Gedankenbeispiel`, `nicht gemessen`, `kein echter Modelllauf`, `keine konkrete … eines Modells`, `hypothetisch`). Advisory above two per episode.
  - `long_cold_open`: first spoken segment above 100 words.
  - `over_target_duration`: `estimated_minutes` above 120 percent of `target_minutes`.
- **Files.** `script_advisories.py` (new); `script_artifacts.py:72-91` adds `advisories` per episode to the report; `scripts/audit-counts.py` (new) runs the same functions over a project's published `episodes/*/script.yaml` and prints the three counts the audit's verification plan asks for.
- **Tests.** New `tests/test_script_advisories.py` with fixed German sentences for each pattern, including one that must not fire ("in unserem Beispiel"). `test_scripting` asserts the report field. Add the module to the mapping in `AGENTS.md`.
- **Acceptance.** `scripts/audit-counts.py projects/die-entwicklung-der-transformer-architek-13325a` reproduces the audit's "before" numbers within tolerance: terms redefined in five or six of six episodes, hedging three to nine per episode, first segment of ep_001 at 157 words.

#### WP2 Established-terms registry (P-04) — S

- **Change.** `Concept` gains `terms: list[NonEmpty] = []`, the spoken names of the concept. `teaching_design.txt` line 6 asks for them. `prerequisite_context` (`teaching.py:211-225`) adds `established_terms` to each reviewed row, built from that episode's concepts, falling back to the humanised `concept_id` for plans written before the field existed. `continuity.txt` gets the audit's sentence: terms in `established_terms` were defined earlier, use them without redefinition, at most one short recall clause per term per episode. `polish_dialogue` receives `prerequisite_context` (`script_pipeline.py:232-234`, `polishing.py:66-71`) so the polishing pass and its review see the registry and the inherited example. WP1's counter uses the same list.
- **Files.** `teaching.py:31-36, 211-225`, `prompts/teaching_design.txt`, `prompts/continuity.txt`, `script_pipeline.py:232-234`, `polishing.py:66-71, 96-103`.
- **Tests.** `test_teaching`: context carries terms from reviewed plans only, never from outlines or future episodes (extend `test_context_uses_transitive_prior_plans_and_never_future_or_unreviewed_examples`). `test_polishing`: the polish prompt contains the context. `test_prompts`.
- **Acceptance.** On a regenerated series, WP1 reports no established term defined more than once per episode after episode 1.

#### WP3 Hedging rule and cold-open rule (P-05, P-11) — S

- **Change.** `write_episode.txt` and `dialogue_polish.txt`: mark an invented example as illustrative exactly once when it is introduced; later references say "in unserem Beispiel". `dialogue_polish_review.txt`, under spoken_language: repeated reminders that an example is hypothetical are a failure. `episode_framing.txt`: keep the first spoken segment under 90 words; series orientation and the episode question go in separate turns; the partner may voice the episode question. `script_review.txt` already rejects "excessive reminders that an analogy is imaginary" and stays as is.
- **Files.** The four prompt files. Versions are bumped with the Phase 1 batch (section 4).
- **Tests.** `test_prompts` composition; `test_episode_framing` for the new version tags.
- **Acceptance.** WP1 counts on a regenerated episode: at most two hedging matches, first segment under 90 words, the episode question spoken by the second turn.

#### WP4 Reviewer caveats, dismissed gaps and advisories on the reading page (P-10) — S to M

- **Change.** `teaching.py:473-482` records `dismissed_gaps: [{objective_id, gap, reason}]` from gap assessments with `required_for_objective` false, and the design-stage review at `teaching.py:311-325` records the gaps it dropped the same way. `Studio.detail()` (`studio.py:411-465`) reads `reports/script_quality.yaml` for each published episode and exposes `review_notes` with the limitations from the evidence review (`model_review.limitations`), the teaching and editorial reviews, the polishing review, plus `dismissed_gaps` and WP1's `advisories`. `renderScript()` (`app.js:504-521`) renders a collapsible panel "Hinweise der Prüfungen" per episode, grouped by source, with every string escaped. The frozen snapshot behaviour of the reader is unchanged.
- **Files.** `teaching.py`, `studio.py:411-465`, `web/app.js:504-521`, `web/style.css` for the panel.
- **Tests.** `tests/studio_ui.test.cjs`: a project fixture with five evidence limitations renders five list items; an empty notes object renders no panel; a limitation containing `<script>` is escaped; polling does not move the reading position (extend the existing snapshot tests at lines 600 and 730). `test_studio`: `detail()` exposes `review_notes`. `test_teaching`: dismissed gaps are recorded with their reasons.
- **Acceptance.** The reading page for ep_001 of the sample series shows the five evidence-review limitations recorded in `reports/script_quality.yaml`.

#### WP5 Densest-passage rule in the polishing review (P-06) — S to M

- **Change.** `DialoguePolishReview` gains `demanding_passages: list[DemandingPassage]` where `DemandingPassage` is `{segment_id, load, referents: [{expression, resolved_by}]}` and `resolved_by` is a segment id or null. `dialogue_polish_review.txt` gets the audit's instruction: name the three most demanding passages, state what the listener must hold and which earlier sentence resolves each referent, and fail spoken_language when a passage has more than three unresolved referents or a referent with no resolving sentence. `validate_polish_review` (`polishing.py:42-63`) enforces: exactly three distinct segment ids that exist in the candidate, none of them the first segment of the first chapter or the last of the last chapter, every `resolved_by` an existing earlier segment. When the passages show an unresolved referent while spoken_language passes, the code adds a deterministic spoken_language issue rather than raising, so the existing repair loop (`polishing.py:107-118`) handles it.
- **Eval.** Extract the review call at `polishing.py:96-105` into `compare_dialogue(original, candidate, ...)` so it can be invoked alone. Archive the ep_002 case as `evals/dialogue_polishing/cases/ep002_dense_passage.json`: original segments 031 to 033, the published polished text as candidate, expected verdict spoken_language fail. Add `evals/dialogue_polishing/run.py` modelled on `scripts/evaluate-teaching.py`, with the seasons dialogue as the positive control. The 2026-09-13 record stays as history.
- **Files.** `polishing.py:29-63, 96-105`, `prompts/dialogue_polish_review.txt`, `evals/dialogue_polishing/`.
- **Tests.** `test_polishing`: passages must exist, must not be greeting or sign-off, an unresolved referent turns a pass into a repair, exactly three required; fixtures in `tests/polishing_fixtures.py` gain the new field.
- **Acceptance.** The archived passage fails spoken_language under the new prompt in a real-model run; the seasons control passes.

#### WP6 Gap probe (P-01) — M

- **Change.** New module `research_gap_probe.py` with `probe(index, gaps: dict[str, str], *, limit=5)` built on `SourceReader.search(text, key_terms=terms(text))`. A section is a hit when at least two distinct key terms match. Each row is `{gap_id, text, key_terms, hits: [{reference, title, key_term_matches, preview}], status}` with status `no_hits`, `hits_unread`, `hits_read_confirmed` or `resolved`.
- **Research lane.** After `_gaps` (`question_research.py:64-69`) the probe runs over all gap texts and writes `question_research/gap_probes.json`. For a task whose `gap_ids` have unread hits, `seed()` (`question_answering.py:91-98`) pins those references as the first read windows, at no extra call. When the task ends with the gap still open, the ledger sets `hits_read_confirmed` if the read receipts cover the references, otherwise `hits_unread`. In `quality_report` (`research_quality.py:88-95`) an `hits_unread` gap is a blocking gap that names the section; the report gains `gap_probes` and `render_quality` a section "Korpusprobe der Lücken". `research.py:620-623` appends the probe status to each line of `open_questions.md`.
- **Script lane.** Before the teaching design of each episode, the probe runs over the knowledge model's `uncertainties` (the list built at `script_pipeline.py:120-122`) against the run's source index and writes `teaching/<ep>/gap_probes.json`. Unread hits become `research_needed.json` questions whose `why_needed` names the matching sections, which routes them through the existing `teaching_research_required` path. `research_foundations` pins the hit sections into the supplement's source context so the reader cannot miss them; verify at implementation whether `source_context`'s `extra_queries` is enough or explicit references are needed. The script review payload (`script_pipeline.py:277-288`) gains `gap_probes` with statuses only, `script_review.txt` gets the audit's sentence, and a deterministic guard refuses to accept a review while any probe is still `hits_unread`. This also applies to the legacy Transformer dossier, because its gaps reach the script lane through `uncertainties` without a research re-run.
- **Files.** `research_gap_probe.py` (new), `question_research.py`, `question_answering.py:91-98`, `research_ledger.py`, `research_quality.py:88-135`, `research.py:620-623`, `script_pipeline.py:120-122, 135-137, 277-288`, `teaching_research.py:109-130`, `prompts/script_review.txt`.
- **Budget.** The test fixture corpus must produce no hits for fixture gaps, so `test_run_budget` keeps its count. A new test seeds one hit and asserts exactly one supplement round.
- **Tests.** New `tests/test_gap_probe.py` including the exact V3 sentence "decrease the bias term by γ if overloaded, increase it if underloaded" against the f13 gap text; `test_question_research` (pinned reads, statuses), `test_research_quality` (unread hit blocks, confirmed hit does not), `test_research` (`open_questions.md` suffix), `test_teaching_research` (routing and context pinning), `test_scripting` and `test_episode_framing` (payload and version). Add the false gap to `evals/research_evidence/corpus.json` as an offline case. Update the `AGENTS.md` mapping.
- **Acceptance.** A new script run on the Transformer project routes the f13 gap to supplementary research, the supplement references `sec_6891d807643ea0ef`, and no segment of the regenerated ep_003 claims the rule is missing. The research-lane half is verified by WP7.

#### WP7 Validate the research gate on a real run (P-03) — S engineering, quota cost

- **Runbook.** After WP6: start a research run on the Transformer project from the Studio with the default limits (40 calls, 3 rounds, 30 sources). Published scripts are untouched by a research run. Collect `reports/research_quality.json`, `question_research/gap_probes.json`, `budget.json` and the ledger. Check that the V3 bias rule appears as a finding referencing `sec_6891d807643ea0ef`, that every remaining gap has status `no_hits` or `hits_read_confirmed`, and whether the run passed or blocked with specific gaps. Record calls, blocks and coverage in `docs/research-validation-2026-09.md`.
- **Precondition.** Subscription quota. Per [claude-backend-plan.md](claude-backend-plan.md) the Codex weekly window resets on 22 September; the Claude backend is in the uncommitted tree and must be landed first if it is to serve this run.
- **Acceptance.** As the audit states: either all six questions answered with no false gap, or a block with corpus-verified gaps.

### Phase 2: speech delivery

#### WP8 Spoken-form table and pre-synthesis report (P-07, first half) — M

- **Change.** New module `spoken_forms.py`: `SpokenForms` model `{version, entries: [{written, spoken}]}` stored at `studio/spoken_forms.json`; `apply(text, table)` replaces longest entries first, case-sensitive, at word boundaries; `report(script, table)` lists digits, all-caps abbreviations, version strings (`V3.2-Exp`, `H800`), tokens with characters outside the German alphabet, and the entries that fired. The audio request built at `audio.py:27` gives every segment a `spoken_text`. `qwen_worker.py:130-146` synthesises `spoken_text`, keeps `text` in the settings for the record and hashes both. `speech.py:68-71, 137, 199` do the same for Gemini. The verifiers `speech.py:208-219` and `episode_audio.py:117-133` compare `spoken_text` against a fresh `apply(segment.text, table)`. The chapter checkpoint (`episode_audio.py:247`) changes automatically because the batch contains the new field. The report is written to the audio run folder and to `reports/<ep>_audio.json`, and the audio card in `app.js` shows it before the approval checkbox. The transcript keeps the script text.
- **Probe.** Extend segment `seg_003` of `data/audio_probe.json` with "j'ai mal aux pieds", "DeepSeek-V3.2-Exp", "H800", "Kullback-Leibler" and three multi-digit numbers, and the English probe accordingly.
- **Files.** `spoken_forms.py` (new), `audio.py:20-35`, `qwen_worker.py:130-146`, `speech.py:68-71, 117, 137, 199, 208-219`, `episode_audio.py:117-133`, `studio.py` (save route for the table, `detail()` exposure), `web/app.js` (table editor and report panel), `data/audio_probe*.json`.
- **Tests.** New `tests/test_spoken_forms.py` (replacement order, word boundaries, report categories). `test_speech`: cache key changes when the table changes, transcript keeps script text, `check_gemini_rows` compares spoken text. `test_episode_audio`: verifier. `test_worker_cache`: key composition now covered. `test_audio`: request carries `spoken_text`. `studio_ui.test.cjs`: report panel. Update the `AGENTS.md` mapping.
- **Acceptance.** The probe renders with all table entries applied and the report lists the difficult tokens. A human listener confirms the flagged terms on two episodes; that step is recorded, not automated.

#### WP9 Per-segment spoken-form override and re-render (P-08) — M

- **Change.** `episodes/<ep>/audio_review.yaml` gains `spoken_overrides: {segment_id: spoken}`, written through `save_decision` (`episode_audio.py:158-162`). An override takes precedence over the table. New route `POST /api/projects/<id>/spoken_override` with `{episode, segment_id, spoken}`, validated like the existing episode id pattern (`studio.py:595-599`). On the reading page of a published episode with audio, each utterance gets a "Sprechform" control; a button "Nur diesen Abschnitt neu rendern" starts the existing `audio` action with the saved approval, which remains valid because script hash and voices are unchanged (`episode_audio.py:206-213`). The new audio run re-synthesises only the changed segment: the chapter checkpoint changes for that chapter, the per-segment caches serve every other segment, assembly runs again. `reports/<ep>_audio.json` and the export README list the override receipts; the transcript keeps the script text and gains an appendix "Aussprache-Hinweise" naming the overrides.
- **Files.** `episode_audio.py:158-162, 206-238`, `studio.py` (route), `studio_worker.py`, `web/app.js:504-521`, `audio.py:177-186`.
- **Tests.** `test_episode_audio`: one override costs exactly one synthesis call in the fake engine, the other segments are served from cache, the approval is reused. `test_studio`: route validation, unknown segment rejected, no key in the payload. `studio_ui.test.cjs`: control renders only for published episodes with audio, POST carries the CSRF token.
- **Acceptance.** As the audit: one override, one synthesis call, episode reassembled, receipt in the report.

#### WP10 Pause policy at assembly (P-09) — S

- **Change.** `AudioChoice` (`speech.py:35-50`) gains `pauses: {same_speaker_ms: 250, speaker_change_ms: 450, chapter_break_ms: 900}` with those defaults. The approval comparison at `episode_audio.py:206-213` treats a stored `audio_generation` without `pauses` as the defaults, so existing approvals stay valid; a changed policy needs a fresh approval, which is correct because it changes what the listener hears. `audio.py:120-125` applies `max(segment.pause_after_ms, minimum for the transition to the next segment)`; the model value stays for the last segment of the episode. Timeline rows gain `pause_ms` and `pause_reason`. The audio report records the total applied pause seconds. The duration estimate in `script_artifacts.py:16-20` is left unchanged; the timeline is the measurement.
- **Files.** `speech.py:35-50`, `episode_audio.py:206-213`, `audio.py:120-131, 172-176`, `web/app.js` (three number fields in the audio settings), `studio.py` (save).
- **Tests.** `test_audio` with real FFmpeg: measured gaps in the timeline meet the minimums at a speaker change and a chapter boundary while a larger model value is kept. `test_episode_audio`: legacy approval without `pauses` is still accepted; changed pauses are rejected before synthesis. `studio_ui.test.cjs`: fields render with defaults.
- **Acceptance.** Timeline shows the minimums; a human listener compares one chapter before and after.

#### WP11 MP3 chapters and show-note fixes (P-14) — S

- **Spike first.** Write the timeline chapters to an FFmpeg metadata file with `[CHAPTER]` blocks, pass it as a second input with `-map_metadata 1 -id3v2_version 3`, and check `ffprobe -show_chapters` on the bundled build under `tools/ffmpeg`. If the build does not write CHAP and CTOC frames, add `id3_chapters.py`, a minimal ID3v2.3 chapter writer without dependencies.
- **Change.** `audio.py:150-167` embeds the chapters and records `chapters_embedded` in the audio report after reading them back with ffprobe. At audio publish (`episode_audio.py:306-333`) write `exports/<ep>/<run>/show_notes.md` with chapter timestamps; the script-stage `show_notes.md` cannot carry timestamps because it is written before audio exists (`script_artifacts.py:61-71`). Deduplicate sources there by `work_id` from the source assessment, falling back to a normalised title.
- **Files.** `audio.py:150-176`, `episode_audio.py:306-333`, `script_artifacts.py:61-71`, possibly `id3_chapters.py` (new).
- **Tests.** `test_audio`: ffprobe reports the chapter count and titles on the produced MP3. `test_episode_audio`: export show notes contain timestamps. `test_scripting`: two URLs of the same work appear once.

#### WP12 Host names and role labels (P-13) — S

- **Change.** `TopicBrief` (`models.py:48-91`) gains optional `host_names: {host_a, host_b}`; the project settings form gets two fields. `episode_framing.txt`: if `brief.host_names` are supplied the hosts may address each other by them, otherwise they stay unnamed; never invent names. Speaker labels in `script_artifacts.render_script`, the transcript at `audio.py:185` and the reading page at `app.js:516` use the host names, falling back to "Host A" and "Host B" instead of the voice preset. Adding the field changes the config hash the UI compares, so open pages need a reload; persisted approvals are bound to script hash and voices and stay valid.
- **Files.** `models.py:48-91`, `prompts/episode_framing.txt`, `script_artifacts.py:20-30, 57`, `audio.py:177-186`, `web/app.js:516`, `studio.py` (settings).
- **Tests.** `test_episode_audio:76-77` and `test_speech:165-166` currently pin `**Aiden:**` and `**Sadaltager:**`; change them to the role labels. This is a justified behaviour change: `episode_framing.txt` already states that voice preset names are not host identities. `studio_ui.test.cjs`: labels on the reading page.

### Phase 3: series consistency, source independence, learning loop

#### WP13 Series review on the sample and a bounded cross-episode repair (P-12) — M

- **Validation first.** New CLI command `pla series-review <project> --run <run_id>` that runs the existing review on a published run's scripts with one call and writes the verdict under a new run folder of kind `series_review`, mirrored into `reports/script_quality.yaml`. It never writes into the old run folder, which keeps the resume invariant. Run it on `run_20260913_182611_981976_0b7a6dfc` and archive the warnings in the docs.
- **Repair.** After a `blocked` verdict in `assess_series` (`series_review.py:106-136`): group the failing checks' cited evidence by episode; for each affected episode issue one `script_review_repair` call with the series check as a `ScriptIssue(category="structure")` naming the segments, run `validate_script`, then one script review call so the claim checks catch drift in the repaired segments, then re-run the series review. One round only (`MAX_SERIES_REPAIRS = 1`). Repaired scripts replace `reviewed/<ep>.json`; polishing and the teaching review are not re-run because the edit is bounded to named segments and the objectives are unchanged. This is three calls per repaired episode, not the two the audit names; the extra script review is the price of keeping the evidence discipline on every published text.
- **Files.** `series_review.py`, `script_pipeline.py:306-320` (reuse the repair helper), `cli.py`, `script_budget.py` (repair calls are not minimums, so `STAGE_CALLS` is unchanged; the projection note already says repairs need further calls).
- **Tests.** `test_series_review`: a seeded contradiction between two episodes fails, the repair resolves it within one round, a second failure blocks, resume does not spend more calls. `test_cli`: the standalone command and its run folder.
- **Acceptance.** The series review report exists for the sample; the seeded contradiction resolves in one round.

#### WP14 Independent sources for effect claims (P-02) — S to M

- **Change.** `research_discovery.txt` gets the audit's sentence: for every empirical or performance claim family include one source not authored by the organisation making the claim, or state in limitations that none was found. `research_evidence.py` gains `single_group_findings(findings, assessments)`: findings of kind claim or mechanism whose evidence sources all share one known `research_group`. It is added to `evidence_summary` (advisory, next to `concentration`) and to the quality report as `advisories.single_group_findings`, rendered in `research_quality.md`. The writing and script-review payloads (`script_pipeline.py:180-182, 285`) include the episode's single-group findings, and `write_episode.txt` and `script_review.txt` ask for one audible attribution per episode. A deterministic attribution check is not attempted; wording varies too much.
- **Files.** `prompts/research_discovery.txt`, `research_evidence.py:111-160`, `research_quality.py:96-135`, `script_pipeline.py`, `prompts/write_episode.txt`, `prompts/script_review.txt`.
- **Tests.** `test_evidence_contracts` (the advisory lists the right findings, unknown groups are reported as unknown, nothing gates), `test_research_quality`, `test_prompts`, `test_scripting` (payload).
- **Acceptance.** The quality report lists single-group findings for the sample; on a topic with known third-party evaluations, at least one independent source is retrieved.

#### WP15 Style notes, evals and the listening sheet (P-16) — M

- **Change.** `projects/<id>/style_notes.md`, edited by the operator in a Studio textarea. `run_script` (`scripting.py:244-245`) reads it into `inputs["style_notes"]` so the input hash covers it, and passes it as `brief.style_notes` to the writing, polishing, script review and polishing review payloads with one prompt sentence: these are the operator's standing editorial corrections, follow them unless they conflict with the evidence rules. At audio publish, write `exports/<ep>/<run>/listening_sheet.md`: chapters with timestamps and columns for confusion, lost interest and mispronunciation. A Studio checkbox "Hörprüfung durchgeführt" with a note sets `human_listening_reviewed` in `audio_review.yaml`.
- **Evals.** WP5 and WP6 archive the two confirmed defects as fixed cases. The held-out topic outside machine learning is an operational task: create one real project, run it, and add its rejected or accepted episode to `evals/teaching_quality/` with the README's caveats.
- **Files.** `scripting.py:239-272`, `script_pipeline.py`, `polishing.py`, the four prompt files, `episode_audio.py:306-333`, `studio.py`, `web/app.js`.
- **Tests.** `test_scripting`: style notes change the input hash and reach the payloads. `test_episode_audio`: sheet written. `test_studio` and `studio_ui.test.cjs`: textarea, checkbox, no key in payloads.

#### WP16 Back-transcription (P-07, second half) — L, spike-gated

- **Spike.** Try installing `faster-whisper` into `.venv-tts` next to the Qwen pin of PyTorch on the CUDA machine. If the CTranslate2 wheel conflicts, create `.venv-asr` with its own setup script modelled on `scripts/setup-qwen.ps1`. Record model, revision and load time as `docs/qwen-windows.md` does for TTS.
- **Change.** `asr_worker.py` invoked like `qwen_worker.py` through the `platforms.py` interpreter resolution, transcribing each cached segment WAV. `transcription_check.py` aligns the transcript against `spoken_text` word by word with `difflib` and writes `transcription_check.json` per episode: per segment word error rate, missing and inserted words. Findings appear in the audio report and on the reading page next to the override control from WP9. They flag, never block, as the audit and `SPEC.md` require.
- **Tests.** Alignment and reporting only, with a fake transcriber; no model in the suites. A deliberately dropped sentence in the fixture must be flagged.
- **Acceptance.** As the audit: a deliberately dropped sentence is flagged; a human confirms the flagged terms on two episodes.

## 4. Prompt version batches

Each bump invalidates in-flight checkpoints keyed by the composed prompt, which is documented behaviour. Fix the literal in `script_checks.py:236` by making the script review version a shared constant before the first bump.

| Phase | Files edited | Call-site tag today | New tag |
| --- | --- | --- | --- |
| 1 | `write_episode.txt`, `continuity.txt`, `episode_framing.txt` | `write_episode.v6-framing` | `write_episode.v7-audit` |
| 1 | `script_review.txt`, `continuity.txt`, `episode_framing.txt` | `script_review.v8-evidence` | `script_review.v9-gaps` |
| 1 | `dialogue_polish.txt`, `episode_framing.txt` | `dialogue_polish.v2-framing` | `dialogue_polish.v3-audit` |
| 1 | `dialogue_polish_review.txt`, `episode_framing.txt` | `dialogue_polish_review.v2-framing` | `dialogue_polish_review.v3-density` |
| 1 | `teaching_design.txt`, `continuity.txt` | `teaching_design.v1` | `teaching_design.v2-terms` |
| 1 | `continuity.txt`, `episode_framing.txt` | `teaching_design_review.v4-framing` | `teaching_design_review.v5-terms` |
| 2 | `episode_framing.txt` (host names) | Phase 1 tags | `-names` suffix on the four consumers |
| 3 | `research_discovery.txt` | `research_discovery.v3-attachments` | `research_discovery.v4-independence` |
| 3 | `write_episode.txt`, `script_review.txt`, both polishing prompts (style notes, attribution) | Phase 2 tags | `-notes` suffix |

`continuity.txt` currently carries a tokenizer sentence and `plain_language.txt` a map-versus-territory example that both leaked from the Transformer pilot. Remove them in the Phase 1 batch; they are project-specific text in a general prompt.

## 5. Order and dependencies

1. Land the uncommitted working tree with both suites green.
2. WP1, then WP2 and WP3 together (they share WP1's counters), then WP4 (the channel that makes WP1 visible), then WP5. Bump Phase 1 prompt versions once, run both suites, run the three real-model evals.
3. WP6, verified first on the existing Transformer research through a new script run, then WP7 as the real research run.
4. Regenerate the Transformer series from its existing research and compare the WP1 counts before and after. Ep_003 must no longer claim the missing rule; the ep_002 passage must fail the new review.
5. WP8, WP9 and WP10 in that order (WP9 depends on WP8's `spoken_text`), then WP11 and WP12. Human listening of ep_001 and ep_004 with the listening sheet, which can be produced by hand before WP15 lands.
6. WP13, WP14, WP15, then the WP16 spike.

## 6. Verification protocol

- After every work package: the test modules mapped in `AGENTS.md` for each touched source module, and the full Python suite whenever `models.py`, `storage.py`, `runner.py` or a fixture changes. New modules are added to the mapping table.
- Before each phase commit: both full suites green on Windows, then CI on the three platforms.
- After each prompt batch: `scripts/evaluate-teaching.py` on the three teaching cases, the new `evals/dialogue_polishing/run.py`, and the offline `evals/research_evidence/run.py`. Record the prompt hashes in the results files as the evals already do.
- Regression cases fixed by this plan: the ep_002 dense passage (WP5) and the ep_003 false gap (WP6). Both must stay in the evals when prompts change again.
- Real-run validations that cost quota: WP7 (research), the series regeneration after Phase 1, WP13's standalone series review. Each gets a short results note under `docs/`.
- Checks that need a human listener and cannot be automated: pronunciation of the probe terms, pause feel at chapter boundaries, voice consistency across thirty minutes, and where attention drops. The reports keep saying `human_listening_reviewed: false` until WP15's checkbox is used honestly.

## 7. Deviations from the audit, stated

- **P-01.** A corpus hit is treated as something to read, not as proof that the gap is false. The blocking condition is an unread hit. Hits are routed through the existing supplementary research rather than a new reader, so references stay intact.
- **P-09.** The pause policy lives in the audio choice, not in `project.yaml`, so a change of pauses triggers a fresh audio approval and not a text approval.
- **P-12.** The repair costs three calls per affected episode rather than two, because the repaired segments get their own script review before the series is re-checked.
- **P-14.** Chapter timestamps go into the export's show notes; the script-stage show notes cannot carry them.
- **P-04.** Needs a `terms` field on `Concept`; plans written before the field falls back to humanised concept ids.
- **P-13.** Two existing tests that pin voice presets as transcript labels are changed, with the reason recorded in the commit message.
- **P-07.** Back-transcription is gated on a spike and may need its own environment.

## 8. Not in this plan

- The 30-minute cap and the 85 percent floor stay as they are; P-15 only adds the upper advisory.
- The evidence validators (`research_evidence.py:36-59`, `script_evidence.py:12-33`) are not touched.
- The sample series is not rewritten by hand. Every improvement reaches it through a regenerated run so that references remain intact, as the audit requires.
- Automatic repair driven by advisories. If the counts stay high after the Phase 1 prompts, that is the next change, not this one.
