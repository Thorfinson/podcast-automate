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
| V-16 | The planning rate of 130 spoken words per minute (`script_artifacts.SPOKEN_WORDS_PER_MINUTE`) matches real recordings. | Compare `estimated_minutes` from the script metrics with the measured durations in the audio export reports of published episodes, separately for Qwen and Gemini. | Episode length planning, the 85 % rule, Gemini scope estimates; [Episode and series length](../BUSINESS_LOGIC.md#episode-and-series-length) | open: the German Transformer recordings of 2026-10-02 measured 125 to 131 words per minute (`script_artifacts.py`); English Gemini measured 144 through OpenRouter (Ontologies ep_001, 2026-10-04) and 151 through Google (the same episode rewritten, 2026-10-07), pauses included, before the English pace of D-147; German through Google is missing |
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
| V-28 | How much slower Gemini through Google speaks with the calm delivery („unhurried“ added to the styles, D-147), and whether 93 % tempo on top of it is still too fast or already too slow for English. | Record one English episode with the pace of D-147 and measure its words per minute per segment as on 2026-10-07 (word timestamps of a local recogniser aligned to the script; before: 163 per segment, 151 with pauses); listen to it. | [Speaking pace per language](../AUDIO.md#speaking-pace-per-language) | open |
| V-29 | `claude -p` with `ANTHROPIC_API_KEY` set while logged in to claude.ai uses the key, not the login; and which `apiKeySource` values the stream's `system/init` event reports for a key and for a login. The `claude_api` guard relies on `"none"` for a call that ran on a login (D-145). | One `claude_api` call and one `claude_code` call on a machine logged in to claude.ai with a key set; compare the `system/init` event of both and the Anthropic Console's usage. | [Claude Code](../ARCHITECTURE.md#claude-code), [Subscription logins](../SECURITY.md#subscription-logins) | open |
| V-30 | Claude Code's `total_cost_usd`, which a `claude_api` run counts as billed, matches the Anthropic Console bill, including web-search fees, prompt caching and long context. | Run one research run and one script run on the key; compare the sum of `billed_usd` in their `budget.json` with the Console's cost for the key over the same period. | [Money limit](../BUSINESS_LOGIC.md#money-limit) | open |
| V-31 | The stream fields Claude Code reports in API mode for exhausted credits, a refused key and a final 429 (assumed: the error categories `billing_error`, `authentication_failed`, `rate_limit` of the last `assistant` event), which `classify_claude_failure` maps to `anthropic_credits`, `anthropic_authentication` and `anthropic_rate_limit`. | Provoke each case with a test key (empty balance, revoked key, a tier's rate limit) and read the `result` and `assistant` events. | [Claude Code](../ARCHITECTURE.md#claude-code) | open |
| V-32 | The per-call maximum and the money of runs on the key: `claude_code.MAX_BUDGET_USD` (12), the overshoot of a money limit with up to five calls in flight (`execution.MAX_PARALLEL_TEXT`), and the `cost_estimate.DEFAULT_USD_PER_CALL` table measured on subscription runs at API prices. | Recalibrate all three against billed `claude_api` runs: the largest billed call, the money spent past the limit when a run stopped at it, and the mean per call by run kind and model. | [Money limit](../BUSINESS_LOGIC.md#money-limit) | open |
| V-33 | The Anthropic organisation's rate-limit tier carries five parallel research calls with a 1M-token context. | One parallel research run on the key; count `anthropic_rate_limit` stops and compare with the tier's input-token limits in the Console. | [Claude on your own API key](../BUSINESS_LOGIC.md#claude-on-your-own-api-key), [Sequential or parallel](../STUDIO.md#sequential-or-parallel) | open |
| V-34 | Claude Code's legal and compliance page still allows this use of credentials (subscription login for personal use, API keys for anything else) as read on 2026-10-07 (D-145). | Re-read the page's authentication and credential-use sections before a release; record the date and any change in DECISIONS. | [Subscription logins](../SECURITY.md#subscription-logins) | open |
| V-35 | The Perplexity Search API as the search adapter uses it (2026-10-07): the response fields for several queries in one request, the price per request (`web_search.USD_PER_REQUEST`, 0.005 USD from third-party pages), the status codes for a refused key, exhausted credit and a rate limit, and the rate limit of the key's tier against five parallel research tasks. | One real request with a key; compare `search_results.json` and the account's usage page; note the status codes Perplexity documents. | Perplexity search (D-151); [Research](../RESEARCH.md) | open |
| V-36 | Whether the Perplexity search reaches the kind of sources the model's own search found. | `evals\web_search\run.py` on a finished project, then read a sample of differing cases. | Recommending the Perplexity search in the Studio (D-151) | open |
| V-37 | A human read of the English Studio catalog `locales/en.json` (704 keys on 2026-10-07): wording, tone, and that every placeholder and plural reads right. | Read every value of `en.json` next to its German original in `de.json`, then use the Studio set to English and note wrong, stiff or misleading texts. | Bilingual Studio (D-152); [Languages](../ARCHITECTURE.md#languages) | open |
| V-38 | The legacy default on the user's own workspace: with projects and no `.studio/ui.json` the Studio stays German whatever the browser asks for, and choosing English or automatic switches it. | Start the updated Studio on the user's workspace with an English browser; check the language before and after a choice. | Bilingual Studio (D-152); [Interface language](../CONFIGURATION.md#interface-language) | open |
| V-39 | The English research page against a live run: activity lines (the research templates are mapped to English; a changed pipeline wording falls back to the German line), the plan projection, the budget-block causes and the stop cards. | Follow one live research run in the Studio set to English from the plan approval to a stop; note German text other than pipeline messages, and wrong values. | Bilingual Studio (D-152); [The plan approval hold](../RESEARCH.md#the-plan-approval-hold) | open |
| V-40 | One English and one German turn of the editorial chat (`studio_brief.v8-ui-language`: it answers in the language of the user's latest message, else in the interface language) and one status brief in each language (`studio_status.v2-ui-language`: written in the language its job started in, while its facts stay German). No eval covers these prompts. | Run one chat turn and one research job with a status brief in each interface language and read the answers. | Bilingual Studio (D-152); [Status briefs](../STUDIO.md#status-briefs) | open |
| V-41 | Whether Spotify for Creators and Apple Podcasts keep or strip the AI tags of an uploaded episode MP3 (`comment`, `DIGITAL_SOURCE_TYPE`, `AI_GENERATED`); they may re-encode it. | Upload an exported episode, download the file the platform serves and read its tags with `ffprobe`. | AI marking (D-154); [Exports and listening sheet](../AUDIO.md#exports-and-listening-sheet) | open |
| V-42 | Which players and podcast apps show the AI comment, which FFmpeg 9 writes as a TXXX frame named "comment", not as COMM; a real COMM frame would need a tagging library, a new dependency. | Open an exported episode MP3 in the common players and podcast apps and note which show the comment. | AI marking (D-154); [Exports and listening sheet](../AUDIO.md#exports-and-listening-sheet) | open |
| V-43 | A legal check that the transparency note plus the MP3 tags satisfy the EU AI Act Art. 50 for the intended use; a spoken disclosure stays an open option, since it would change approved audio. | Have the note, the tags and the intended publication checked by someone qualified; record the verdict in DECISIONS. | AI marking (D-154); [Source rights and privacy](../SECURITY.md#source-rights-and-privacy) | open |
| V-44 | A human read of the English transparency note, AI notice and MP3 comment. | Read them in an English episode's `show_notes.md`, `listening_sheet.md`, the companion kit's `description.txt` and the MP3's tags. | AI marking (D-154); [Languages](../ARCHITECTURE.md#languages) | open |
| V-45 | A real re-login after a login stop (`authentication_required`): the scheduler checks the run's subscription login at most every five minutes without a model call (`subscriptions.logged_in`, `studio.LOGIN_CHECK_SECONDS`) and resumes the run once it passes. | Let a run on a fixed subscription stop on an expired login, log in again (`claude auth login` or `codex login`) and watch the run resume within five minutes. | Fewer expert stops (D-155); [Automatic resume](../STUDIO.md#automatic-resume) | open |
| V-46 | How often the one repeat inside the call clears `search_not_observed`; each repeat costs a second model turn. | Count the `search_retry.json` files in the call folders of the next research runs and how many of those calls then passed. | Fewer expert stops (D-155); [Adapter pool](../ARCHITECTURE.md#adapter-pool) | open |
| V-47 | The plan projection's default rates of 5 sources and 2 search rounds per sub-question (`question_budget.DEFAULT_SOURCES_PER_TASK`, `DEFAULT_SEARCH_ROUNDS_PER_TASK`) against what the next runs measure. | Compare `sources_per_task` and `search_rounds_per_task` in `research/calibration.json` of the next published research runs with the defaults, and check whether a run still stopped on these limits after its plan gate. | Fewer expert stops (D-155); [Research projection](../BUSINESS_LOGIC.md#research-projection) | open |
| V-48 | An end-to-end trial project: three sub-questions and one episode of about 20 minutes, each run within the trial limits (110 calls, 16 search rounds, 40 sources, at most 45 USD). | `pla init <project> --trial`, then research, script, reading and recording; note every stop on a limit and the calls and money each run used. | Trial project (D-157); [Trial project](../BUSINESS_LOGIC.md#trial-project) | open |
| V-49 | Spotify for Creators and Apple Podcasts Connect accept the podcast kit's texts as the show's description: `description_short.txt` within the 300 characters third-party pages give for Spotify's show description (no Spotify article states it), `description.txt` within Apple's 4,000 (Apple's "Content Setup" page, read 2026-10-07); and the real-model wording of `podcast_kit.v1` reads right for a whole series (no eval under `evals/` covers it). | Make the podcast kit of a finished series, paste both texts into the show settings of Spotify for Creators and Apple Podcasts Connect, check the stored text, and read both against the episode list. | Podcast companion kit (D-165); [Exports and listening sheet](../AUDIO.md#exports-and-listening-sheet) | open |

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
