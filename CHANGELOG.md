# Changelog

User-visible changes. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). There is no released
version yet; everything since 0.1.0 is under Unreleased, newest first within each group, with the date in front.
Why a rule exists is in [DECISIONS](docs/DECISIONS.md).

## [Unreleased]

### Added

- 2026-10-07 · „Begleitmaterial für den Podcast“ on the recording page and `pla publish-kit <project> --podcast`: the whole podcast's companion kit in `publish/` with a short description and a description for the show page of Spotify and Apple Podcasts (one text-model call, reused while the episodes' plans are unchanged), every source of the series with the episodes that use it, and `transcript.md`, the transcript of every published episode with its chapters and their measured times. The podcast ZIP brings it as `Begleitmaterial Podcast/` while it covers the latest recordings (D-165).
- 2026-10-07 · Studio „Vertonung“: one player docked at the foot of the page for all recordings, with speed, previous and next; the list shows one row per episode with its length, where listening stopped and which were heard. Read marks on „Skripte lesen“ („Als gelesen markieren“, previous and next episode). Both are kept in the project, so they follow you from the computer to the phone (D-163).
- 2026-10-07 · Studio: a recording made for an earlier state says what changed since („geändert: Sprechtempo“), once on the recording page and in each row (D-160).

- 2026-10-07 · Settings „Audio“: „Sprechtempo Deutsch“ and „Sprechtempo Englisch“ (80 to 100 %: the montage slows the recording with its pitch kept, the pauses keep their length) and for Google „… ohne Eile sprechen“ per language („unhurried“ added to both roles' styles). A pace binds only recordings of its language; the conversation sample plays the pace of its language, the approval card shows it under „Tempo“.
- 2026-10-07 · Claude on your own Anthropic API key, for use without a subscription: presets „Sonnet 5.5 · high · Anthropic-API-Key“ and „Opus 5.5 · high · Anthropic-API-Key“, `--backend claude_api` for `pla research`, `script`, `resume` and `series-review` (research included, with Claude Code's own web search), „Weiter mit … Claude über den Anthropic-API-Key“ and `--text-switch claude-api`. The key is entered under „Anthropic-Key“ on the settings page (memory only) or comes from `ANTHROPIC_API_KEY`; `pla research --api-key` asks for it hidden; `pla doctor` checks it without a model call. Every call is billed to your Anthropic account; such a run writes no „Kurzbericht“. Own stop cards for a missing or refused key, used-up credit, the API's rate limit and a call that did not run on the key.
- 2026-10-07 · Money limit per run in USD for every run billed to a key (Claude on the API key, OpenRouter): „Kostengrenze je Lauf in USD“ under „Limits“, `pla approve --cost-usd N`, the stop cards „Kostengrenze fehlt“ and „Kostengrenze erreicht“ with a suggested limit, and „Kosten X von Y USD“ in the job bar. Every billed attempt counts, also a repeated or rejected one; the research plan names the expected cost, the settings page the measured cost per call, and the production report the attempts without a reported cost.
- 2026-10-07 · Web search through Perplexity: settings „Websuche“ with „Über das Textmodell“ (the default) and „Über Perplexity“, and `--web-search model|perplexity` for new `pla research` and `pla script` runs. The run's text model plans the queries, Perplexity's Search API runs them (about 0.005 USD per request) and the model chooses only among the results, so every text model can research, an OpenRouter model included (`pla research --backend openrouter --model … --web-search perplexity`), without any subscription. Only the search queries go to Perplexity. The key is entered under „Perplexity-Key“ (memory only) or comes from `PERPLEXITY_API_KEY`; such a run needs a money limit, also on a subscription. Own stop cards for a missing or refused key, used-up credit, Perplexity's rate limit, an unreachable search and a selection that named sources the search had not found; `evals/web_search` compares the search with a finished project's model searches.
- 2026-10-07 · The Studio speaks English or German: the interface language is automatic (following the browser) or fixed, for the whole workspace (`.studio/ui.json`); a workspace that already has projects stays German until you change it. The server's own messages follow it, the editorial chat answers in the language of your latest message or else the interface language, and the status brief is written in the language its job started in; the pipeline's own stop reasons stay German and are marked as such. The translation of the browser pages is in progress (D-152).
- 2026-10-07 · Exports in the project's language: an English project gets English download and ZIP names („Episode 01 - …“, „All episodes“, „Companion kit“), README, transcript notice, show notes, listening sheet, companion kit, series outline, teaching plan and research files; a German project's files and names stay as they were (D-153).
- 2026-10-07 · AI marking: every exported MP3, voice sample and conversation sample carries AI tags (`comment`, `DIGITAL_SOURCE_TYPE` with the IPTC value `trainedAlgorithmicMedia`, `AI_GENERATED=true`), recorded in `audio_report.json`; the show notes, the listening sheet and the companion kit's description end with a transparency note. A companion kit made before is shown again once rebuilt („Neu zusammenstellen“), without a model call (D-154).
- 2026-10-07 · Fewer stops that need an expert: the research plan also projects sources and search rounds and names what a limit that does not fit would have to be raised to (the raise stays your click), and a new workspace starts with the pre-approvals of 2 fresh attempts and 250 extra calls per run; existing workspaces keep theirs. Gap, dispute, residual, retry, plan and audio decisions stay yours (D-155).
- 2026-10-07 · Trial project: `pla init <project> --trial` creates a project that runs the whole pipeline once, small: a research plan of at most three sub-questions, one episode of at most 20 minutes and per run at most 110 model calls, 16 search rounds, 40 sources and, where a money limit is set, 45 USD; without `--topic` it takes a narrow sample topic („Wie entsteht ein Regenbogen?“ or "How does a rainbow form?"). The Studio's server creates one too (`POST /api/projects` with `"trial": true`) (D-157).
- 2026-10-07 · Releases: a version tag `vX.Y.Z` builds the package, runs both test suites against the built wheel, publishes it to PyPI as `podcast-automate` and creates the GitHub release from the version's CHANGELOG section. New repository files: CONTRIBUTING.md, SECURITY.md (report vulnerabilities privately), CODE_OF_CONDUCT.md, issue and pull request templates (D-158).
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

- 2026-10-07 · Studio design review (D-159..D-164): the overview lists each project once, waiting ones first with their next action, deleting in a „⋯“ menu; the first step is called „Auftrag“; a finished project's brief page leads with the saved brief and folds the conversation, and the partner's replies show their formatting; the input box no longer covers the conversation; the missing-key reminder is one folded line, and the recording page has one key field; the research dossier opens with its guiding questions and folds its findings under them, without internal ids (about 6,300 instead of 490,000 px for Ontologies); the settings group the text models by how they are paid, link their sections and mark unsaved edits; the hold card makes „Mit neuen Anläufen fortsetzen“ the primary button where plain „Fortsetzen“ would not help; „Dialog-Polishing“ is called „Dialogschliff“ everywhere.
- 2026-10-07 · Studio „Maschinenraum“ shows only telemetry: status brief first, live output (folded once a run stopped), calls and money, call times, the run's text model with the provider switch folded, and the production report, which breaks a research run down by step. Finished recordings, the stop reason and the run time are no longer repeated there; a connection check and a new voice sample answer on „Auftrag“; the engine room stays closed when there is nothing to show (D-164).
- 2026-10-07 · Studio: a project page polls a light status (37 to 84 KB instead of 2.5 to 3.7 MB every 2.5 s) and loads the whole project only when its content or a job changes; the overview's poll fell from about 1 MB to 13 KB (D-159).

- 2026-10-07 · Episodes leave room to breathe: scripts give the ear a breather where new information has piled up, and the expression layer sets pauses where the conversation invites them, with a budget of their own; the content sets the rhythm, not a schedule. Existing episodes get the pauses with „Ausdruck neu setzen“ before recording.
- 2026-10-07 · OpenRouter runs need a money limit: a run without one, a saved one included, stops with „Kostengrenze fehlt“ at its next billed call, and the settings page saves an OpenRouter text model only with a limit. Research with OpenRouter text still searches on the subscriptions and needs none, unless it searches through Perplexity.
- 2026-10-07 · The rule that refuses API-key logins and strips every key from the CLI environment now applies to the subscription providers only; Claude on the API key is a separate provider that uses the key and nothing else.
- 2026-10-07 · Automatic resumes: the Studio now also resumes a research call without an observable web search (which the call repeats once first) and an answer without a readable result; a run stopped on a saved state the code no longer reads or on a program error resumes once after a code update; a run stopped for an expired subscription login resumes once the login works again (checked at most every five minutes, without a model call); under „Automatisch“ a failed Claude call moves to Codex. The limit of three automatic resumes now counts only resumes in a row without progress (D-155).
- 2026-10-07 · A script run's correction loop that spent its attempts (such as `invalid_script` or `invalid_teaching_review`) now takes „Mit neuen Anläufen fortsetzen“ and the fresh-attempt pre-approval: the resume asks the stage anew, and nothing is set aside (D-155).
- 2026-10-07 · The command line is English: help texts, prints, the CLI's own messages and `pla doctor`'s details (`OK` or `MISSING`). Stop codes and `--json` keys are unchanged, and the pipeline messages that `pla status`, `pla quota` and `pla approve` print stay German (D-156).
- 2026-10-06 · Every episode is told its own way: the teaching plan chooses a dramaturgy (such as Rätsel, Entdeckungsgeschichte, Streitgespräch, Gedankenexperiment, Fallanalyse), an opening, the partner's stance and an ending for each chapter, different from the episodes just before; the readable plan names them under „Spannungsbogen“.
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

- 2026-10-07 · Studio: the overview said „Podcast verfügbar“ while the same project's steps showed two decisions; the header said „Arbeitsschritt abgeschlossen“ after a companion kit; ages beyond an hour read „vor 7475 Min.“; a stopped run's status brief read „wird gerade erstellt“; the reader's sticky bar hid the first chapter and the top of the margin; the settings page showed a new project's steps in the sidebar (D-160, D-161, D-164).

- 2026-10-07 · After the switch to Google an episode whose expression tags were placed before could not be approved for audio („Der Ausdruck wurde seit dem Lesen neu gesetzt“); such a reading now counts as none, and the recording places its own tags with listener reactions.
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
