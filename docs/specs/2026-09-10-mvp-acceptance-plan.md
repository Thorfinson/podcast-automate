---
title: MVP acceptance plan
doc_type: plan
status: active
date: 2026-09-10
---

# MVP acceptance plan

Goal: the complete listenable series is the acceptance target of the MVP; a script-only prototype does not meet it.
This plan holds the definition of done, the state of each item, the open steps and the list of things still to verify.
The criteria for the qualitative assessment and the acceptance procedure are in [QUALITY](../QUALITY.md).

## Status

**Not met.** As recorded in the sources (the latest is of 2 October 2026):

- **Met:** blocking pre-checks stop rendering and faulty audio is not exported as final; audio is produced only after
  an approval of the read script; the export contains MP3, chapters, transcripts and show notes (see [Definition of done](#definition-of-done)).
- **Partly met:** a complete six-episode sample series with exported MP3s exists
  ([State of the assessment](../QUALITY.md#assessment-criteria)), but it was researched with the previous research pipeline, and the audit of
  19 September 2026 found a false evidence gap, redundant definitions and repeated hedging in it
  ([quality audit](2026-09-19-quality-audit.md#a-executive-summary)). The fixes landed in code
  ([Status of the quality-audit plan](2026-09-19-quality-audit-plan.md#status)) but reach the series only through a
  regeneration that has not run. Resume, budget and approvals are covered by the fixture-based test suites with
  simulated models, not by a recorded real run.
- **Not met:** no human listening review of any series is recorded; every audio report says
  `human_listening_reviewed: false` (V-1). Neither subject-matter pilot from the product's main cases has been run
  ([Subject-matter pilots](../QUALITY.md#subject-matter-pilots)). The real research run that validates the current
  research pipeline (WP7) is not recorded.

## Steps

### Definition of done

The MVP is done when all of the following hold. Status: **met**, **partly** or **not met**.

- A topic without a prepared source folder leads to a researched series. **Partly:** the September 2026 sample series
  came from real topic research ([State of the assessment](../QUALITY.md#assessment-criteria)), but with the previous
  research pipeline; a real run of the current pipeline is the WP7 step below.
- All required artefacts exist and their structured data validates against the implemented schemas. **Partly:** the
  fixture modules under `tests/*_fixtures.py` run the complete flow against the schemas ([Evals](../QUALITY.md#evals));
  no check of a real project is recorded (V-7).
- A complete pilot series covers its brief in the desired depth, and every audio part stays within the part limit
  ([Episode and series length](../BUSINESS_LOGIC.md#episode-and-series-length)). **Partly:** the sample series exists
  with exported MP3s; its depth was found wanting in places by the audit, the regeneration step below is open,
  and the listening review is outstanding (V-1).
- Additional episodes that the content needs can be planned and produced without a fixed limit on total time or number
  of episodes. **Partly:** planning follows the content
  ([Episode and series length](../BUSINESS_LOGIC.md#episode-and-series-length)), and a table of contents of 18
  episodes of 40 to 60 minutes was planned for the Transformer series
  ([Claude backend plan, addendum of 2 October 2026](2026-09-19-claude-backend-plan.md#7-implementation-status)); the
  complete production of such a series is not recorded.
- Planned and actual total duration are visible; an explicitly stated time wish is shown separately. **Partly:** an
  explicit time wish is recorded as `target_total_minutes` apart from the planned duration
  ([Episode and series length](../BUSINESS_LOGIC.md#episode-and-series-length)); a view of planned against measured
  total duration is not described in the sources (V-8).
- Episodes build on each other and answer central questions thoroughly. **Partly:** the audit found a real teaching
  layer, but also terms re-defined in five or six of six episodes and repeated hedging; advisories, the
  established-terms registry and the bounded series repair landed (WP1 to WP3, WP13), the series review of the sample
  is open (V-4).
- Statements, counter-positions and uncertainties can be traced back to verifiable sources. **Partly:** the four
  traceability probes of the audit matched the stored source text, but a false gap reached the listener; the gap probe
  (WP6) and the single-group advisory (WP14) landed, and their validation on a real run is the WP7 step below.
- Blocking pre-checks prevent rendering, and faulty audio is not exported as final. **Met:**
  [Quality gates](../QUALITY.md#quality-gates) and
  [Automated checks and early audio test](../QUALITY.md#automated-checks-and-early-audio-test); covered by the test
  suites with real FFmpeg processing.
- Scripts can be reviewed before audio, and audio is produced only after approval. **Met:**
  [Human approvals](../BUSINESS_LOGIC.md#human-approvals).
- MP3 episodes, chapters, transcripts and show notes are exported completely. **Met** for exports made with the
  current code ([Exports and listening sheet](../AUDIO.md#exports-and-listening-sheet)); the exports of the September
  2026 sample series predate the embedded chapters and the show notes with timestamps (WP11).
- The complete main case works on Windows 11 with a subscription text backend, local TTS and automatic assembly,
  without manual audio editing. **Partly:** the early German Qwen test on Windows 11 is technically proven
  ([Automated checks and early audio test](../QUALITY.md#automated-checks-and-early-audio-test)); a complete main case
  on the target machine is not recorded (V-9).
- The fixtures and an editorial listening check of the pilot have passed. **Partly:** both test suites pass
  ([AGENTS.md](../../AGENTS.md)); the listening check is outstanding (V-1).
- Interrupted runs, including subscription pauses, can be resumed without a complete recomputation. **Partly:** the
  fixture modules run the complete flow including resume with simulated models ([Evals](../QUALITY.md#evals)); a
  deliberate trial in a real run (acceptance step 7) is not recorded (V-6).

### Open items moved from the quality-audit implementation record

These come from "Not done, and what it needs" in the
[Status of the quality-audit plan](2026-09-19-quality-audit-plan.md#status); the eval runs, the standalone series
review and the listening review are in the verification list as V-3, V-4 and V-1.

- **Real research run with the current pipeline (WP7).** **Not met.** Run the research of the Transformer project
  with the current pipeline. Collect `reports/research_quality.json`, `question_research/gap_probes.json`,
  `budget.json` and the ledger, and record calls, blocks and coverage in a results note (the plan named it
  `docs/research-validation-2026-09.md`). Acceptance: either all six questions are answered with no false gap, or the
  run blocks with corpus-verified gaps. The composing model fills `gap_terms`, so the probe on this German-on-English
  corpus is expected to find the V3 bias rule; a `no_hits` for a gap whose answer is in an English section means the
  terms were not filled or were too generic.
- **Regenerate the sample series after Phase 1.** **Not met.** Regenerate the Transformer series from its existing
  research and compare the counts of `scripts/audit-counts.py` with the baseline in the Status of the quality-audit
  plan. Ep_003 must no longer claim the missing rule, and the ep_002 dense passage must fail the new review.
- **Held-out teaching topic (WP15).** **Not met.** Create a real project outside machine learning and add its verdict
  to `evals/teaching_quality/`.
- **Back-transcription spike (WP16).** **Not met**, and not required by the definition of done above. `faster-whisper`
  is not installed; `asr_worker.py` and its environment do not exist. The alignment and reporting half
  (`transcription_check.py`) is implemented and tested with a fake transcriber
  ([Roadmap](../PRODUCT.md#roadmap)).

### Further steps

- **Subject-matter pilot.** **Not met.** Research concrete sources for one of the main cases
  ([Subject-matter pilots](../QUALITY.md#subject-matter-pilots)) and produce the complete pilot series.
- **Acceptance procedure.** **Not met.** Run the [acceptance procedure](../QUALITY.md#acceptance-procedure) on the pilot
  series and document the remaining defects concretely.

## Verification list

| ID | What to verify | How | Relevant for | Status |
| --- | --- | --- | --- | --- |
| V-1 | Human listening review of the September 2026 sample series and of the pilot: pronunciation of the probe terms, pause feel at a chapter boundary, voice consistency across thirty minutes and where attention drops. | Listen to the export with its `listening_sheet.md` and record the review in the Studio with **„Hörprüfung eintragen“** (enter listening review), which sets `human_listening_reviewed`. | Definition of done (pilot depth, listening check); [Exports and listening sheet](../AUDIO.md#exports-and-listening-sheet) | open |
| V-2 | Editorial suitability of the early German Qwen voice sample (both voices, long explanations, speaker changes, technical terms, numbers and units); its technical execution on Windows 11 with the Radeon RX 9070 XT is proven. | Listen to the sample and record a verdict on intelligibility, voice consistency and pronunciation. | Choice of Qwen model and voices; [Automated checks and early audio test](../QUALITY.md#automated-checks-and-early-audio-test) | open |
| V-3 | The three real-model eval sets after the prompt changes of 19 September 2026; only the offline half of the dialogue-polishing eval has run. | `scripts\evaluate-teaching.py <project> --output <dir> --live`; `evals\dialogue_polishing\run.py <project> --output <dir> --live` (expected: the ep_002 dense passage fails `spoken_language`, naming `ep_002_seg_031` or `ep_002_seg_032`); `evals\research_evidence\run.py` with captured model reviews (`--export-prompts`, `--responses`). | Prompt changes; [Evals](../QUALITY.md#evals) | open |
| V-4 | Standalone series review of the September 2026 sample series (WP13). The command is covered end to end by `test_cli` but was never pointed at the sample. | `.\.venv\Scripts\pla.exe series-review <project> [--run <run_id>]`; spends model calls. | Definition of done (episodes build on each other); [Series review](../SCRIPTS.md#series-review) | open |
| V-5 | Independent human annotation of the labels in `evals/research_evidence/corpus.json`, which the implementation agent authored. | A person labels the cases without seeing the existing labels; a correction gets a new corpus version with a stated reason. | Any claim about research quality; [Evals](../QUALITY.md#evals) | open |
| V-6 | In a real run, an interruption, a subscription pause and a defective segment can each be resumed or repaired without recomputing finished results (acceptance step 7). The fixture suites cover this with simulated models only. | In a real project, stop a run mid-stage, let a subscription pause occur and damage one recorded segment; resume each time and check that finished results are kept and reused. | Definition of done (resume); [Acceptance procedure](../QUALITY.md#acceptance-procedure) | open |
| V-7 | All required artefacts of a complete real project exist and validate against the implemented schemas. | Load every artefact of a finished real project with its schema and list missing or invalid ones. | Definition of done (artefacts); [Project folder layout](../ARCHITECTURE.md#project-folder-layout) | open |
| V-8 | Planned and measured total duration of a series are visible, with an explicit time wish (`target_total_minutes`) shown separately. | Check the Studio and the reports of a finished, recorded series for planned total, measured total and time wish. | Definition of done (durations); [Episode and series length](../BUSINESS_LOGIC.md#episode-and-series-length) | open |
| V-9 | The complete main case runs on Windows 11 with a subscription text backend, local Qwen TTS and automatic assembly, without manual audio editing. | Produce the pilot series end to end on the target machine with Qwen and note every manual step. | Definition of done (Windows main case); [Set up local Qwen on Windows](../OPERATIONS.md#set-up-local-qwen-on-windows) | open |
| V-16 | The planning rate of 130 spoken words per minute (`script_artifacts.SPOKEN_WORDS_PER_MINUTE`) matches real recordings. | Compare `estimated_minutes` from the script metrics with the measured durations in the audio export reports of published episodes, separately for Qwen and Gemini. | Episode length planning, the 85 % rule, Gemini scope estimates; [Episode and series length](../BUSINESS_LOGIC.md#episode-and-series-length) | open: the German Transformer recordings of 2026-10-02 measured 125 to 131 words per minute (`script_artifacts.py`); the comparison per provider is missing |
| V-17 | Whether Claude Code's `rate_limit_event` carries `isUsingOverage` and `overageStatus` while bought extra usage is in effect, and whether the provider choice should act on them; today they are only stored as `using_overage` and `overage_status`. | Run a Claude call with extra usage active and inspect `rate_limit` in `calls/call_NNN/metadata.json`. | Claude extra-usage rule; [Claude Code adapter](../ARCHITECTURE.md#claude-code), [Text providers and model selection](../BUSINESS_LOGIC.md#text-providers-and-model-selection) | open |
| V-18 | A complete Gemini run with a new topic through all six Studio steps (research, script, reading with expression tags, approval, recording, export) and a human listening test of the generated voices and episodes. | Run a new topic end to end in the Studio with Gemini and expression tags, listen to every episode with its `listening_sheet.md` and record the review with **„Hörprüfung eintragen“**. | Gemini audio acceptance (expression tags, take-check thresholds, pause trimming, loudness); [Gemini via OpenRouter](../AUDIO.md#gemini-via-openrouter), [The guided flow](../STUDIO.md#the-guided-flow) | open |
| V-19 | Whether Gemini via OpenRouter records an episode faster than local Qwen. | Record the same approved episode with both providers and compare `tts_report.json` (Qwen `render_seconds` and `model_load_seconds`, Gemini `elapsed_seconds` per take) and the wall-clock time. | Choice of the speech provider; [Speech](../PRODUCT.md#speech), [Gemini via OpenRouter](../AUDIO.md#gemini-via-openrouter) | open |
| V-20 | Qwen recording on Apple Silicon (MPS, Float32) produces usable speech. | `.venv/bin/python scripts/setup-qwen.py --device mps --project …`, then `pla audio-probe … --approve-audio` and listen. | macOS users who want local Qwen; [Set up local Qwen on macOS and Linux](../OPERATIONS.md#set-up-local-qwen-on-macos-and-linux) | open |
| V-21 | Qwen recording on Linux (CPU, NVIDIA CUDA 12.8, AMD ROCm 6.4) produces usable speech. | Run `setup-qwen.py` with the matching `--torch-index-url`, then `pla audio-probe … --approve-audio` and listen. | Linux users who want local Qwen; [Set up local Qwen on macOS and Linux](../OPERATIONS.md#set-up-local-qwen-on-macos-and-linux) | open |
| V-22 | The macOS and Linux legs of `.github/workflows/tests.yml` have run and passed. | Check the GitHub Actions history of the repository. | Anyone relying on macOS or Linux support; [Install on macOS and Linux](../OPERATIONS.md#install-on-macos-and-linux) | open |
| V-23 | The optional WebMCP tools `read_podcast_workspace` and `navigate_podcast_step` work in a supported live browser and cannot approve anything. | Open the Studio in a browser that exposes `document.modelContext`, call both tools and try to reach an approval. | [WebMCP](../STUDIO.md#webmcp) | open |
| V-24 | Whether the podcast adaptation of the IES self-explanation advice (a serious objection plus a short pause for thought) helps listeners. | Human reading and listening review of episodes that use it, compared with episodes without it. | [Examples, dialogue and synthesis](../TEACHING.md#examples-dialogue-and-synthesis), [Acceptance procedure](../QUALITY.md#acceptance-procedure) | open |
| V-25 | Spotify for Creators and Apple Podcasts Connect accept the companion kit's `description.txt` as it is: at most 4,000 characters (the limit is from third-party help pages; Spotify's article "Episode chapters" does not state it), and Spotify turns its `00:00 Title` lines into chapters (format, first mark at 00:00, at least three chapters 30 seconds apart: that article, read 2026-10-06). | Paste `exports/<ep>/<run_id>/publish/description.txt` of a recorded episode into Spotify for Creators and Apple Podcasts Connect and check the stored text, the links and Spotify's chapter view. | Companion kit (`publish_kit.py`, `pla publish-kit`); [Commands](../PRODUCT.md#commands) | open |
| V-26 | Whether the listenability rules (2026-10-06) make episodes easier to follow without losing depth, and whether drafts stay within their word budget and the hour. | Listen to one episode written under write_episode.v12-listenability next to its predecessor; compare dialogue_shape, the advisories and estimated against planned minutes, Sonnet 5.5 separately; run evals/teaching_quality and evals/dialogue_polishing by hand. | Listenability rules, WRITER_TARGET_FACTORS; [Listenability and narrative arc](../TEACHING.md#listenability-and-narrative-arc) | open |
| V-27 | Gemini through Google (2026-10-06) beyond the English listening rounds: a German two-host passage sounds natural; the Lite model exists on Google; the requests per minute and per day of the user's key tier carry parallel recordings; the plausibility check's speaking-rate band holds for whole passages; passages longer than 3.7 minutes come back whole. | A German conversation sample on the settings page; one German and one English episode recorded through Google, watching for google_quota, 429 waits and invalid_speech; the key's limits in Google AI Studio. | [Gemini via Google](../AUDIO.md#gemini-via-google) | open |

## Sources

- `SPEC.md` §14 "Evaluation und Definition of Done" (the checklist of the definition of done and the acceptance
  target), §1 (main cases); its evaluation basis now lives in [Evals](../QUALITY.md#evals).
- `docs/system-quality-assessment.md` (assessment criteria, state of the sample series, early audio test, acceptance
  procedure); now [QUALITY](../QUALITY.md).
- `docs/quality-audit-2026-09-19-implementation.md` (what landed, measured state, "Not done, and what it needs"); now
  the [Status of the quality-audit plan](2026-09-19-quality-audit-plan.md#status).
- [Podcast Studio quality audit, 19 September 2026](2026-09-19-quality-audit.md).
- [Claude Code as a second subscription provider plan](2026-09-19-claude-backend-plan.md), addendum on the output cap.
- `evals/research_evidence/README.md` (labels authored by the implementation agent, independent annotation pending).
