# Changelog

User-visible changes. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). There is no released
version yet; everything since 0.1.0 is under Unreleased, newest first within each group, with the date in front.
Why a rule exists is in [DECISIONS](docs/DECISIONS.md).

## [Unreleased]

### Added

- 2026-10-06 · Review notes `long_turns` and `low_partner_share`: more than two turns over 120 words, a partner share under 25 %.
- 2026-10-06 · „Begleitmaterial“ per episode for Spotify and Apple Podcasts, on the recording page and with `pla publish-kit <project> --episode ep_001`: a short description, an episode description of at most 4,000 characters with chapter marks and sources, and the source list, in `publish/` next to the recording and in the podcast ZIP; „Kopieren“ next to each text.
- 2026-10-06 · Gemini audio through Google's own API („Gemini TTS · Google“), the new default for Gemini: both hosts speak a passage of up to 20 turns in one request, each role in its own short style, with the other host's short reactions (`|mhm|`) placed by the expression stage. Gemini via OpenRouter stays selectable.
- 2026-10-06 · Settings „Audio“ for Google: a style per role with presets („Neugierig, gespannt auf das, was kommt“ is the default) or own words, „Rollen von Folge zu Folge tauschen“ (on by default, Erinome explains in episode 1, Sadachbia in episode 2), and „▶ Gesprächsprobe“ of exactly the selected voices and styles, also with the roles swapped.
- 2026-10-06 · „Google-Key“ on the settings page, in hold cards and in the queue, with its own „Google-Key fehlt“ note; Google's own reasons in the stop cards (`google_key_required`, `google_authentication`, `google_quota`, `google_speech_request`).
- 2026-10-04 · „OpenRouter-Key fehlt“ at the top of every Studio page while no key is stored but Gemini audio, Jev or an OpenRouter text model needs one, with the projects concerned and a key field; also after a restart on a freshly loaded page.
- 2026-10-03 · Settings page „Einstellungen“ next to „Übersicht“: text model, audio, execution, pre-approvals, limits, call time limit and OpenRouter key now apply to all projects (`projects/.studio-settings.json`).
- 2026-10-03 · Settings switch „Zusatzkontingent gekauft: gespeicherte Claude-Sperren übergehen“ for bought Claude extra usage: every call tries Claude despite stored blocks.
- 2026-10-02 · The script budget shows the expected calls, calibrated on the project's last completed script run, next to the lower bound.
- 2026-10-02 · The Studio resumes research and script runs stopped by a transient technical error automatically after 10, 30 and 90 minutes.
- 2026-10-02 · Every Gemini take passes a plausibility check (speaking rate, unexplained silence); a failed take is requested once more, then the recording stops with `invalid_speech`, naming the segment.
- 2026-10-02 · Follow-up assessment from the second review round of an assembled dossier, with a stored scope (`assessment_scope.json`).
- 2026-10-02 · Objection routing with a fixed scope per objection (`routing_scope`); a routing beyond it is re-asked (`invalid_question_routing`).
- 2026-10-02 · The review of a reworked answer sees its earlier rejecting verdict and a code-fixed `review_scope`.
- 2026-10-02 · Dependent answers are revalidated after a prerequisite is reworked or reopened, and answers that fail a tightened rule are revalidated too (`revalidations`).
- 2026-10-02 · Failed series-review checks name the episodes to change (`episode_ids`) and mark source limits (`source_limit`), which become non-blocking advisories.
- 2026-10-02 · Core and supporting findings in the series plan.
- 2026-10-02 · Research limits reach the series plan, the writing and the script review; the script names each limit once.
- 2026-10-02 · The series goal reaches the teaching lane; worked examples may be qualitative cases, and misconception and correction are optional.
- 2026-10-02 · English patterns for the redefined-term and hedging advisories.
- 2026-09-30 · Series goal, task aims, source types, idea sources and recency steer research planning.
- 2026-09-30 · A missing evidence type counts as an answer result after a web search of the sub-question.
- 2026-09-30 · Gatekeeper rule for prerequisite dependencies: one question may not hold up more than a third of a research plan.
- 2026-09-30 · Each episode has a series role it states at the start and end; theory first where the goal is understanding, recommendations where it is applying.
- 2026-09-30 · The series review also checks the arc and, depending on the series goal, exposition and guidance; reports with the five older criteria stay valid.
- 2026-09-29 · A script or research job can continue with another text provider („Weiter mit …“, `pla approve --text-switch`).
- 2026-09-29 · Gemini expression stage: the text model places a few inline audio tags, shown on the reading page; „Ausdruck setzen“ and „Ausdruck neu setzen“ in the side column.
- 2026-09-19 · Claude Code as a second subscription provider, with the selection rule `auto`, `pla quota` and doctor checks.
- 2026-09-19 · Script advisories, the established-terms registry, the single-group advisory and the gap probe for reported research gaps.
- 2026-09-19 · Spoken-form table, pronunciation report, per-segment spoken form with re-render, and pause minimums at assembly.
- 2026-09-19 · MP3 chapters, timed show notes, host names, style notes, the listening sheet and `pla series-review` with one bounded repair.
- 2026-09-17 · Evidence contracts (`evidence.v1`) in the research pipeline.

### Changed

- 2026-10-06 · Scripts are written to be heard: one big idea per episode, a question held open until the end, a short recap and the next question at each chapter end, reflection beats after dense passages, shorter turns and a partner who speaks about a third of the words. The teaching plan plans the arc („Spannungsbogen“), and the script review sends a wall of facts back.
- 2026-10-04 · Stopped recordings with one shared reason name it in the job bar, with „Alle fortsetzen“ and, for a key stop, a button to the key; an OpenRouter refusal (403) names OpenRouter's own short reason, such as a key's credit limit.
- 2026-10-04 · A script run checks the limits the research noted for the script against the stored sources (with Jev where switched on), and no longer states one the sources answer; with an assembled dossier the gap probe had searched nothing.
- 2026-10-04 · Under „Automatisch“ a Codex call that fails for an unknown reason moves to Claude instead of stopping the run, and such a stop names Codex's own reason.
- 2026-10-04 · One episode is one MP3: a recording is no longer split into parts of at most 30 minutes.
- 2026-10-04 · The script review's own advisories stay notes unless they concern evidence or scope; a review that stopped on such a point is reviewed once more on resume.
- 2026-10-04 · The writer gets each episode's word budget computed, per episode and per scene; for Claude Sonnet 5.5 only, its target is set 30 % above the plan. Drafts accepted before the budget, or under another one, are kept on resume.
- 2026-10-02 · A supplement to an assembled dossier counts only its own quoted and paraphrased words against the per-source limits.
- 2026-10-03 · The setup conversation no longer proposes text model, audio or execution; a proposal carries only the brief.
- 2026-10-03 · The Studio offers exactly four OpenRouter text models; Astra Pro and Claude Fable 5.1 are no longer offered.
- 2026-10-03 · A script-review segment that already follows its source while the dossier finding deviates gets `source_corrected`, a note instead of an objection.
- 2026-10-02 · Under „Automatisch“ an unusable subscription (expired login, missing or old CLI) no longer stops the job; the other subscription takes over for ten minutes.
- 2026-10-02 · Series planning no longer sees `max_episode_minutes`, the audio part length.
- 2026-10-02 · Research and script runs refuse local sources outside the project folder; the Studio accepts only `inputs/` paths from the browser.
- 2026-10-02 · Raising a limit or a deadline while a run waits keeps it resumable; operational fields no longer bind the run.
- 2026-10-02 · `--api-key` no longer accepts a value on the command line.
- 2026-10-02 · The scope check's second pass decides finally; the stop `question_scope_unresolved` is gone, and a remaining split share shows as a note at plan approval.
- 2026-10-02 · A rework that ends blocked returns the previously verified answer in the same pass instead of stopping the run.
- 2026-10-02 · An unchanged failed guiding question whose objection was everywhere noted, disputed, accepted or blocked is recorded as a limit instead of being reassessed.
- 2026-10-02 · When a merged review in parts fails although every part passed, each part is rechecked and re-requested with the defect.
- 2026-10-02 · The stop code after exhausted attempts is the rejecting check's code or `rejected_output`; `invalid_model_output` now means that no readable response came.
- 2026-10-02 · `search_web` is offered only while a web search can actually run (`web_open`).
- 2026-10-02 · For completeness objections, the follow-up assessment's `remedy` verdict decides first.
- 2026-10-02 · The last permitted attempt settles form errors of the answer review conservatively and trims a web search instead of rejecting the answer.
- 2026-10-02 · A provided work named with its `citation` counts as independent evidence of an empirical test.
- 2026-10-02 · Re-reading a section that dropped out of view counts as new evidence.
- 2026-10-02 · Guillemets » « › ‹ count as typographic quote variants.
- 2026-10-02 · Dependent research questions see digests of their prerequisites instead of the full answers.
- 2026-10-02 · The advisor reads every earlier advice on the question and its outcome (`advice_history`).
- 2026-10-02 · Parallel research releases the shared lock while a web search downloads and imports sources.
- 2026-10-02 · Books (provided works, primary works, open book archives) are read with the book limits: 2,000 pages, 6 million characters, 900 s.
- 2026-10-02 · `model_review` separates remaining objections, accepted gaps, noted limits and `no_remaining_issues` instead of reporting every such close as `accepted_gaps_remaining`.
- 2026-10-02 · The final episode is the synthesis of the whole series, not a last topic with a recap at the end.
- 2026-10-02 · The final episode need not cite all its recap findings, and limit scenes are no longer required.
- 2026-10-02 · Writing receives only the plan's scope note and the finding dependencies instead of the whole series plan.
- 2026-10-02 · Polishing comparisons after a repair are scoped; a polishing that still fails keeps the reviewed draft instead of stopping the run.
- 2026-10-02 · Episode-wide objections block a follow-up script review only when repeated or critical.
- 2026-10-02 · Series corrections: the second attempt keeps the series objection, the re-review is scoped, and an interrupted correction round resumes.
- 2026-10-02 · Code decides which teaching-plan review points block.
- 2026-10-02 · Teaching plans get the full context of the nearest direct prerequisite only, and summaries of the others.
- 2026-10-02 · The terminology rule is topic-neutral; machine-learning names appear only for machine-learning topics.
- 2026-10-02 · The overview builds each project card from a few cached file reads.
- 2026-10-02 · Paused runs are kept per run kind (`studio/paused_<kind>.json`), so a chat or another run no longer hides a paused run.
- 2026-10-02 · A Gemini rate limit (429) makes the recordings and voice samples of all projects wait together (`projects/.gemini_throttle.json`).
- 2026-10-02 · `pla init` writes the Qwen revision already used on the computer into `runtime.tts_revision` instead of `main`.
- 2026-10-01 · New research runs assemble the dossier from the verified answers without a model call (prompt generation 3).
- 2026-10-01 · Completeness objections about criteria an answer already covers are noted as limits instead of reopening the sub-question.
- 2026-10-01 · Episodes built from an assembled dossier quote each source verbatim for at most 25 words.
- 2026-09-30 · Episodes may last up to 60 minutes (before: 30); recordings are split into parts of at most 30 minutes.
- 2026-09-30 · The reading model sees the earlier sections of its sub-question, up to 80,000 characters.
- 2026-09-30 · An answer that fails its fixed checks is re-requested immediately in the same step.
- 2026-09-30 · The web search accepts every source type except idea sources (before: only primary sources).
- 2026-09-29 · Claude's default is Sonnet 5.5 at `high` instead of Opus 5.5; it needs Claude Code 2.1.284.
- 2026-09-29 · The first-time reader and the expression tagging ask at most at `medium`.
- 2026-09-29 · The research projection measures calls per sub-question; without history it assumes 16 (before: 5).
- 2026-09-27 · New projects get 750 model calls, 48 search rounds and 150 source candidates per run by default.
- 2026-09-26 · „Automatisch“ asks Claude first for new runs (saved runs keep their order); Claude's default becomes Opus 5.5 at `xhigh`, needing Claude Code 2.1.280.

### Fixed

- 2026-10-04 · `scripts/setup-ffmpeg.ps1` installs FFmpeg again: the pinned 9.0.1 build now comes from gyan.dev's GitHub mirror (the gyan.dev package link returned 404).
- 2026-10-04 · `pla status` no longer reports a script run as changed after a raised limit or time limit; it now agrees with `pla resume`.
- 2026-10-04 · Hold cards that need another text model, audio provider or the OpenRouter key point to the settings page and offer „Einstellungen öffnen“; the key messages name the settings page too.
- 2026-10-03 · Script writing corrects a draft that breaks its plan or falls short from the latest attempt, naming the words it has and needs; after three corrections it stops with `invalid_script`.
- 2026-10-02 · A polish that loses meaning or completeness no longer passes in a later round; such points block in every round.
- 2026-10-02 · A raised limit or time limit no longer invalidates a saved teaching-plan supplement.
- 2026-10-04 · Log files (`studio.log`, `worker.log`, `pla.log`), terminal output and the crash tail under „Technische Details“ no longer contain known keys or key patterns, tracebacks included.
- 2026-10-04 · `pla approve --help` says correctly that `--text-switch` runs Claude with the catalog's default model and effort, not the run's level.
- 2026-10-03 · A successful Claude call no longer clears a Claude block recorded while it was running.
- 2026-10-02 · Only a refused (`rejected`) rate-limit event blocks Claude; warnings, format errors and expired logins no longer count as exhausted quota.
- 2026-10-02 · A quota pause waits for the reset of the provider that reported the limit; without a known reset the wait starts at 30 minutes and doubles.
- 2026-10-02 · Long tables of contents are no longer cut off: Claude's output cap follows the model (128,000 tokens for Opus 5.5 and Sonnet 5.5).
- 2026-10-02 · `resume` releases the call reservations of a hard-stopped script run too.
- 2026-10-02 · Pre-approvals apply only when the run can start; a failed resume no longer spends an allowance twice.
- 2026-10-02 · A project limit raised above a run's own approval now applies instead of stopping the run with `invalid_budget_approval`.
- 2026-10-02 · A source fetch carrying a service key follows redirects only on the same host and never from https to http.
- 2026-10-02 · While a new table of contents is drafted or revised, the replaced plan is hidden; revising it no longer stops with `invalid_plan`.
- 2026-10-02 · „Mit neuen Anläufen fortsetzen“ is offered only where the run accepts fresh attempts.
- 2026-10-02 · A worker that survived a Studio restart is recognised by pid and start time and can be stopped.
- 2026-10-02 · A resume continues a round with its existing assessment after an assessment prompt changed, instead of stopping with `invalid_research_checkpoint`.
- 2026-10-02 · Stored rejected responses are checked as the attempt they answered, not as a first attempt.
- 2026-10-02 · Finishing with remaining objections keeps objections closed in earlier rounds closed.
- 2026-10-02 · A review disagreement replayed on resume is recorded only once.
- 2026-10-02 · The overall review receives all cited passages; a missing passage stops with `research_context_incomplete` instead of being dropped silently.
- 2026-10-02 · No dossier, completion or publication while a sub-question is still open or blocked.
- 2026-10-02 · The series report records its criteria; older verdicts no longer stop publishing, resume and audio approval with `invalid_series_review`.
- 2026-10-02 · Gemini recordings retry gateway and overload errors (502, 503, 504) and broken connections twice instead of stopping the episode.
- 2026-10-02 · Dead air after pause tags is trimmed (silences over 1.5 s cut to 1.2 s), and a `<long pause>` no longer opens a segment.
- 2026-10-02 · Loudness uses linear gain with a true-peak limiter at -2 dBFS; `audio_report.json` records the method and the loudness measured on the MP3.
- 2026-10-02 · Re-rendering one segment is bound to the expression tags you read; tags placed again need reading and approving again.
- 2026-10-02 · The listening review and per-segment spoken forms can be entered while other episodes record; only the same episode answers `episode_busy`.
