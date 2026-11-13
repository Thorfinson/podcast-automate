# Changelog

User-visible changes. Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Releases are on
[GitHub](https://github.com/Thorfinson/podcast-automate/releases); within a version the entries are newest first in
each group, with the date in front.
Why a rule exists is in [DECISIONS](docs/DECISIONS.md).

## [Unreleased]

### Added

- 2026-11-10 · **Nach dieser Folge anhalten** ("Stop after this episode") beside "Stop" while all remaining episodes are being written: the episode in progress is finished, published and read for its tags, then the job ends instead of starting the next one; **Doch weiterschreiben** takes it back, and "All remaining" goes on later (D-207).

### Changed

- 2026-11-12 · For contributors: pull requests and `main` are tested on Linux with Python 3.13 only; macOS, Windows and Python 3.12 run on a release and in its dry run by hand (Actions › Release › Run workflow), which builds and tests without releasing (D-209).

### Fixed

- 2026-11-13 · The Jev gap probe tells why OpenRouter refused a request (HTTP 403): a spent key or workspace limit stops as used-up credit, to be raised at OpenRouter; a section OpenRouter's moderation blocks is left out instead of stopping the whole probe; any other refusal stops with OpenRouter's reason. Before, every refusal stopped as "Jev does not answer" (D-211).
- 2026-11-12 · A script review after a repair no longer stops the run on a point it kept only as a note about an earlier objection ("the density stays a non-blocking note"); Geld season 2 episode 13 stopped twice after spending its repairs on such a note (D-210).
- 2026-11-12 · A script review is no longer rejected because one of its receipts quotes a segment from mid-sentence with a capital first letter ("Ganz recht" for "und ganz recht"); a run of Geld season 2 stopped after three such reviews (D-208).

## [0.2.0] - 2026-11-10

The first public release. Podcast Automate turns a topic into a researched, reviewed dialogue podcast series and
records it as MP3, on your own computer. Listen to a sample in English and German:
https://thorfinson.github.io/podcast-automate/

Highlights since 0.1.0:

- **Story agent.** Every episode is written by a Claude Code session, in order, building on the episodes before it,
  and reviewed against the research (D-181, D-184).
- **Research.** A verified dossier from answered sub-questions; starting libraries, works you provide (PDF, EPUB, a
  saved page), a list of refused sources to fetch yourself, and the Internet Archive for pages that are gone.
- **Studio.** In English and German, organised around the series, the episode and the basics; in the English
  interface every stop is explained in English.
- **Speech.** Gemini through Google, ElevenLabs (Eleven v4 and v4 Turbo), Gemini through OpenRouter or local Qwen; up
  to three hosts, a piece of music, one MP3 per episode with chapters and the AI marking.
- **Publishing.** A companion kit for each episode and for the whole podcast, with its full transcript.
- **Cost control.** Trial projects, a money limit for every run billed to a key, and the measured cost of a season in
  the README.

Experimental: Claude on your own Anthropic API key and the web search through Perplexity are tested against simulated
APIs only. The installation needs a checkout (launchers, FFmpeg and Qwen setup); the wheel attached below provides the
`pla` command alone. The detailed changes follow, newest first within each group.

### Added

- 2026-11-10 · A project page, https://thorfinson.github.io/podcast-automate/, plays the listening samples in the browser, now in English and in German (the published German recording's first chapter); the README links there instead of starting a download (D-204).
- 2026-11-10 · The approved outline has a minutes-per-new-episode field (10 to 60, empty keeps the plan), used by both the next-episode and remaining-episodes buttons, including queued batches.
- 2026-11-10 · The English interface explains every stop and refusal in English: where a German pipeline message stood, the Studio shows a plain sentence for its stop code (for example "The story agent exceeded its time limit."), with the German original under "Technical details". German pages are unchanged (D-200).
- 2026-11-10 · The README has a sample to listen to, the first chapter of „Geld verstehen“ episode 1 in English, recorded with the ElevenLabs route on Eleven v4 Turbo (`docs/media/`, with its transcript), three screenshots of the Studio, and the measured cost of season 1 beside the October series.
- 2026-11-10 · Refused sources to fetch yourself: „Fehlende Werke“ on the research page now also lists **„Gesperrte Quellen“**, the addresses a publisher or a bot check refused for questions that still need them. Each opens in your browser; upload what you save there in its own field, and the research reads it and tries the question again.
- 2026-11-10 · Uploads under „Fehlende Werke“ take **EPUB** e-books without copy protection, read chapter by chapter.
- 2026-11-10 · „Eleven v4 Turbo“ for ElevenLabs at half the credits: choose it under „Sprachmodell“ on the settings page or in „Sprachanbieter dieses Projekts“, which now has its own model field. v4 stays the default; a passage recorded with Turbo is a new take, so switching records the episode anew.
- 2026-11-10 · The recording queue on the audio page can be held, emptied and reordered: **„Warteschlange anhalten“** keeps the waiting episodes and their approvals and starts none of them, and new approvals wait with them, until **„Warteschlange fortsetzen“**; **„Alle entfernen“** empties it after asking; **↑** and **↓** move an episode one place. „Entfernen“ is now a visible button, and the page says that taking an episode out drops its approval.
- 2026-11-08 · Episodes recorded before the music can get it in one step: **„{n} Folgen mit Musik neu zusammensetzen“** on the audio page approves only the recorded episodes whose one change is the music; their speech comes from the cache, so no new speech is sent. „{n} gelesene Folgen freigeben“ still covers every episode without a current recording, the never voiced ones too (D-193).
- 2026-11-08 · The remaining episodes can wait their turn too: while the project works, „Alle {n} restlichen vormerken“ on the outline page queues every remaining episode behind the queued revisions, and the outline page lists the queue as the reading page does. New episodes of an approved outline can be written at their own length, 10 to 60 minutes, with `pla script <project> --episode ep_005 --from-outline <run_id> --minutes 35` or a queued or API request; the outline page has no field for it yet (D-192).
- 2026-11-08 · Revisions wait their turn: „Diese Folge überarbeiten lassen“ no longer waits for a running job. Asked for while the project works, the revision is queued („Überarbeitung vormerken“) with its feedback and length, the reading page lists the queued revisions with „Entfernen“, and the Studio starts them one after another, in the order asked, once nothing of the project runs. A stopped run of the project holds the queue until it is resumed and done (D-191).
- 2026-11-08 · Music for a podcast: one piece plays alone before the first word and fades under it, two bars of it lead into every chapter with the new chapter's first word on the beat, and it comes in under the last words and plays on after them. The chapter marks begin with the music. The approval card names it under „Musik“, recordings made before it show „geändert: Musik“ and are made again from the cache with a new approval, without new speech. Show notes and the companion kits name the piece as its license asks. The piece is set in `studio/music.json`; there is no Studio form for it yet (D-189).
- 2026-11-08 · A revision can change an episode's length: **„Neue Länge in Minuten“** (10 to 60) under „Diese Folge überarbeiten lassen“ on the reading page, or `pla script <project> --revise ep_003 --minutes 38`. Empty keeps the planned length, which the field shows. Until now a revision had to keep 95 % of its planned minutes, so an episode could not be retold shorter (D-190).
- 2026-11-08 · The story agent writes for the audience the brief describes, and its editor hears each draft as that audience, with every host by name and part; until now both assumed curious adults and two hosts, and a chain of studies told to teenagers still scored 8/8/7. The agent may also search, read and cite the documents of the research's starting library that the research itself did not take in; show notes and the companion kit name them (D-190).
- 2026-11-06 · ElevenLabs records at most three episodes at once across all projects, the limit of its Starter plan: further approvals wait in the queue and start by themselves, and so does „Fortsetzen“ on a stopped ElevenLabs recording, also with „Alle fortsetzen“. Ten at once were about half as fast as three, because every refused request paused all of them, and episodes stopped with „ElevenLabs ausgelastet“. That stop card no longer quotes ElevenLabs' raw answer or claims „Eine automatische Prüfung hat ein Ergebnis abgewiesen“; the answer stays under „Technische Details“ (D-188).
- 2026-11-03 · **„Kontaktadresse für Unpaywall“** on the settings page: with an address, the search for a free copy of a blocked source also asks Unpaywall, which receives the address with every request; empty leaves it out. Until now only the variable `PLA_UNPAYWALL_EMAIL` did that, so Studio runs never asked Unpaywall.
- 2026-11-01 · One episode of an approved outline: `pla script <project> --episode ep_007 --from-outline <run_id>` writes that episode in a new run without planning the outline again, so a series can start with one episode; the series run waits as it was.
- 2026-11-01 · Research plans follow the brief instead of the call limit: a new plan holds at most two sub-questions per focus question (at least four), where it planned about 40 for any topic before. The plan card says so, and its field „Plan mit so vielen Teilfragen neu zuschneiden, auch mehr“ plans again with more when you want them; `pla approve --research-plan … --max-tasks N` does the same. A plan saved before keeps its size (D-183).
- 2026-11-01 · The story agent cites the corpus sections it tells from, and the script review checks those scenes against their sections instead of counting them as unsupported; show notes and the companion kit name their sources (D-182). It also gets the series' rule for opening and ending an episode (welcome, orientation, conclusion, sign-off), which the review checks. Its episodes are as long as planned (they came out a quarter longer), the review's repair can now shorten a whole episode, and resuming a story run after its writing no longer stops on the supplementary research. Its episodes can also be recorded: until now the recording refused them with `invalid_script`, because they have no writing or polishing stage and their chapters are the agent's own. On Windows the agent's commands hand their answers on in UTF-8, so a critique with umlauts or a name like Doliński reaches it intact.
- 2026-10-27 · Story agent: „Schreiber“ in a project's job summary on „Auftrag“ (**„Erzähl-Agent verwenden“**, **„Schreib-Prompt verwenden“**) and `pla script --writer agent` have each episode of a new script run planned and written by one Claude Code session with Opus 5.5 at xhigh on the Claude subscription. It reads the episode's story material, cited sections and a checked orientation sheet, may set its own two to four chapters, searches the project's own research corpus for scenes the findings only name, runs the script checks itself and asks an editor up to four times. Such a run has no teaching design, narration review or polishing; the script review still checks every fact, and repairs only what blocks, in the segments it names. The production view shows the stage „Erzähl-Agent“, and the published episode the agent's plan (`story_plan.md`). Off by default; the outline page shows the writer under an outline that waits for approval and switches that run too, while a run past its approval keeps its writer (D-181).
- 2026-10-23 · Narration review: a script run started now checks each draft for tension, interest and storytelling before dialogue polishing, tells briefly told stories more fully from the sources' own material, and checks the rework once more. It never stops the run; the reading page's „Hinweise der Prüfungen“ show its scores („Spannung 6/10 · Interesse 7/10 · Erzählweise 5/10“) and under „Narrationsprüfung“ what it left open and which stories the material lacks. The Studio shows the step „Narrationsprüfung“ and the preview state „Erzählweise geprüft“ (D-180).
- 2026-10-25 · A teaching plan whose core story shares its scene with other topics moves those topics into a later scene instead of asking for a new one, which no plan may add; before, the plan stopped with „Lehrkonzept bleibt unvollständig“. That stop now lists its open points under „Offene Punkte“, one line each that opens to the full point, instead of one long paragraph.
- 2026-10-24 · The narration rework may also adjust the turn just before and after a passage it tells more fully, and stories whose sources disagree about who did what are told as their versions. A supplementary research that answers a question in part may name the open part, as its instructions ask; before, such an answer was refused and the run stopped. A script run with story material no longer stops at its second episode's teaching plan with „Datei nicht lesbar: …/teaching_plan.yaml“: the episode before counts as reviewed again, and a new project or version without published episodes is no longer read for one.
- 2026-10-23 · Story research: where the cited sources only point to a story (a chronicle a scholar summarises, a case named in passing), the script run reads it once per episode before the teaching plan; not finding it is noted and never stops the run.
- 2026-10-23 · Story material and core story: before each teaching plan the script run collects what the cited sources tell as story (events, people, scenes, surprising results), and the plan names the story the episode tells first („Kerngeschichte“ in the readable plan) and who voices the first guess, so host_b is no longer the one who guesses wrong in every episode (D-180).
- 2026-10-21 · A third host: `third_host` in `project.yaml` (name, role, Qwen voice) gives a podcast three speakers. Writer, polishing and reviews are told the cast, every episode lets all three speak, transcript and show notes name the third host, and every route records a third voice: **„Stimme Host C“** on the settings page and in **„Sprachanbieter dieses Projekts“** (automatic by default, on ElevenLabs one of your account's voices you choose) and for Google and ElevenLabs **„Stil von Host C“**; Google speaks at most two hosts per passage, ElevenLabs all three. The approval card names the third voice, and a recording without one does not start. Projects of two hosts are unchanged, also when a third voice is set. `examples/geld-podcast` is a German podcast for twelve-year-olds about money with three hosts (D-179).
- 2026-10-19 · ElevenLabs as a speech route: „ElevenLabs · v4“ in the settings' „Audio“, with the voices of your own ElevenLabs account (your own first, „Stimmen neu laden“), a style per role and alternating roles as for Google, and the „ElevenLabs-Key“ in the settings, kept in the credential store. A passage of dialogue goes to ElevenLabs' Text to Dialogue with both voices; the expression tags and listener reactions are spoken in ElevenLabs' words. Own stop cards for a missing or refused key, used-up credits, a busy ElevenLabs and a refused request (D-176).
- 2026-10-19 · A project's own speech choice: „Sprachanbieter dieses Projekts“ on the recording page gives one project its own route and voices over the settings, „Wieder wie Einstellungen“ removes it (D-177).
- 2026-10-08 · Studio overview: a research or script run's card shows its numbers under its state, such as „49 von 74 geprüft · 6 in Arbeit · 6 blockiert, 4 davon nur wegen Vorfragen · Aufrufe 1119 von 1500“, and in the overall review its round and the reworked sub-questions done („Gesamtprüfung Runde 2 · 10 von 10 nachgebesserten Teilfragen fertig“).
- 2026-10-08 · „Weiter mit …“: „Nur Claude“ and Claude on the Anthropic API key name their model in a „Claude-Modell“ list (`pla approve --text-switch claude-only --switch-model claude-sonnet-5-5`), so one run can continue on Sonnet 5.5 at high while the default stays Haiku 5.5 (D-170).
- 2026-10-08 · Studio overview: a stopped project's card offers the step its page recommends as one click, without opening the project — „Fortsetzen“, „Mit neuen Anläufen fortsetzen“, „Empfehlungen übernehmen und fortsetzen“, „Limits anheben und Empfehlungen übernehmen“ or the call limit's raise; „Ansehen“ stays beside it (D-169).
- 2026-10-07 · New version of a podcast: „Neue Version anlegen“ in an overview card's ⋯ menu and `pla new-version <project>` start a new project with the old one's brief, attachments, provided works, style notes and choices, but none of its runs, so research, scripts and audio are made again by the current pipeline; the old version stays as it was. Versions are numbered per podcast („Version 2“ beside the topic). The newest completed research run's sources come along as a starting library that the new version's first research offers (preselected; `pla research <version> --seed-corpus <run_id>`) instead of downloading them again (D-168).
- 2026-10-07 · „Transkript herunterladen“ and „Quellen herunterladen“ in „Begleitmaterial für den Podcast“: the podcast's transcript and source list as single files, without downloading the ZIP of every MP3.
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

- 2026-11-10 · For contributors: the Python suite takes about 11 instead of 21 minutes, since the story agent's run and session no longer look for each other only every two seconds; CI splits it into three shards (two on macOS) per system and Python version and skips the test legs for a change of only docs, and `main` requires the checks `lint` and `tests passed` (D-205).
- 2026-11-10 · Releases stay on GitHub: a version tag creates the GitHub release with the built wheel and sdist attached and publishes nothing to PyPI (D-203).
- 2026-11-10 · Local Qwen keeps its pinned packages: GitHub's 15 advisories for `transformers` 4.57.3, `accelerate` 1.12.0 and PyTorch 2.9.1 are analysed in docs/SECURITY.md and dismissed, since none reaches how the worker loads its one pinned model and no upgrade runs with `qwen-tts` 0.1.1 (D-202).
- 2026-11-10 · The launchers and `pla studio`'s own console lines are in English, like the rest of the command line (D-156).
- 2026-11-10 · For contributors: a `lint` job (Ruff, `ruff check .`, installed with `pip install -e ".[dev]"`) runs beside the test legs, and `main` is protected by a ruleset that requires these seven checks for a pull request; CODEOWNERS, Dependabot, labels and Discussions are set up, and CONTRIBUTING.md says how a change reaches `main` and how a version is released (D-201).
- 2026-11-09 · The podcast's companion kit fits Spotify: **„Podcastbeschreibung für Spotify“** is a text of its own of at most 600 characters, the AI notice included, the limit of Spotify for Creators for a show's description; until now the one podcast description had about 1,600 characters. **„Podcastbeschreibung für Apple Podcasts“** keeps the longer text with the whole transparency note. The music's credit is no longer in the podcast's descriptions, since every episode's description has it; the podcast's source list still names it. An existing podcast kit shows „Neu zusammenstellen“; the rebuild asks the text model once for the new Spotify text (D-194).
- 2026-11-01 · Studio redesign (D-185, D-186). A project has three places instead of six step pages: „Serie“, „Folgen“ and „Grundlagen“. Each place shows its pages as tabs that say their state.
  - „Serie“ shows an approved outline as one row per episode, with its way (Plan, Text, Prüfung, Ton) and its next step; „Nächste Folge schreiben“ stands in the page head.
  - Episodes are numbered by the outline everywhere („Folge 7 von 20“).
  - „Folgen“ is one page per episode, with the tabs „Lesen“, „Prüfhinweise“, „Hören“ and „Begleitmaterial“.
  - Explanations under the controls fold under „Wie das funktioniert“.
  - A stopped project's card shows the first sentence of its stop message.
  - The running job stands in one strip at the top of the page.
  - Each card on the overview has one state badge and one mark per episode; the download is in the ⋯ menu.
  - The new look „Sendestudio“ uses IBM Plex, in light and dark. „Darstellung“ in the sidebar and in the settings chooses „Wie das Gerät“, „Hell“ or „Dunkel“, per device.
  - On a phone the places become a tab bar at the foot, and the reading page shows „Deine Rückmeldung“ above the script.
  - Voices are shown by name, never as ElevenLabs ids.
- 2026-11-01 · The story agent writes every script (D-184). The writing prompt is gone, and with it:
  - the teaching plan, the narration review and dialogue polish;
  - „Schreiber“ in the job summary and `pla script --writer`;
  - the stop card „Lehrkonzept bleibt unvollständig“ and `pla approve --redesign-teaching`.

  Episodes written before stay readable and recordable. A run begun with the writing prompt that has written nothing
  yet continues with the agent.
- 2026-11-01 · A series is written in order, one episode at a time:
  - „Freigeben & erste Folge schreiben“ approves the table of contents and writes its first episode.
  - The page then offers „Nächste Folge schreiben“ and „Alle … restlichen nacheinander“, and marks each episode
    „geschrieben“ or „als Nächste“.
  - Each new episode first reads the published episodes before it and builds on what they said.
  - „Fortsetzen“ on an approved table of contents writes its next episode instead of all of them at once.
- 2026-11-01 · Series review over the published episodes:
  - It runs once every episode is written: by itself after the last one, or with „Serienprüfung starten“.
  - Its points appear per episode, each with „Mit diesen Hinweisen überarbeiten“.
- 2026-11-01 · Revisions are written by the story agent: „Diese Folge überarbeiten lassen“ and `pla script --revise`. It
  starts from the published episode and your feedback and changes only what you ask for.
- 2026-11-01 · Episode 1 introduces the series in one to two minutes after its opening story.
- 2026-11-01 · The story agent is given three things only the review knew so far, so it no longer misses what the
  review then asks for:
  - the research limits its episode should state;
  - the findings that rest on one research group, which the review expects named;
  - the series goal.
- 2026-11-01 · Episode length: an episode planned at 45 minutes runs about 45 minutes in its voices.
  - A new script run counts words at the speaking rate measured on the project's latest recording. Before the first
    recording it assumes about 150 words a minute for ElevenLabs and about 148 for Gemini; until now every run assumed 130.
  - The story agent writes at least 95 % of its word target.
  - Asimov's episode 7 ran 35:47 of a planned 45 minutes.
- 2026-11-01 · The story agent also runs with Claude on your Anthropic API key. It is billed, counts against the run's
  money limit and stops at 40 USD per episode.

- 2026-10-23 · Script runs started now keep each episode's core small enough to tell: at most one core finding per four planned minutes (at least four), the rest supporting detail; scripts say how certain a statement is by the kind of its evidence („eine Jahrzehnte spätere Chronik“) instead of what the research did or did not read, and end on the payoff instead of announcing the next episode's question. The minimum per episode in the call budget rises from nine to eleven calls; runs started before keep nine and resume unchanged (D-180).
- 2026-10-23 · Research runs started now also ask for the story a listener follows (what happened, who acted and why, how something was found, a case), keep the scene beside the number in their findings and state limits by the kind of evidence; stopped runs resume with their earlier prompts (D-180).
- 2026-10-09 · Jev gap probe: a script run makes at most 10,000 Jev requests per scan; with a larger corpus each gap is asked about its best-matching sections. A project with many provided editions (Orlagau: 405,050 requests, about six hours) now finishes the probe in minutes (D-172).
- 2026-10-08 · Research: a sub-question's working pass has 15 steps instead of 10; runs started before keep 10 (D-171).
- 2026-10-07 · The Studio keeps every key in the operating system's credential store (Windows: Anmeldeinformationsverwaltung) and loads it at the next start, so keys are no longer typed in after every restart; „Sitzungs-Key entfernen“ is now „Key entfernen“ and removes the stored key. New on the settings page: „CORE-Key“ for free copies of blocked sources, instead of the `PLA_CORE_API_KEY` variable; each key panel says where its key comes from. Needs the new dependency `keyring` (`pip install -e .`) (D-167).
- 2026-10-07 · Claude's default is Haiku 5.5 at `xhigh` instead of Sonnet 5.5 at `high`, for new runs on the subscription, under „Automatisch“, on the Anthropic API key and for „Weiter mit Claude“; new presets „Haiku 5.5 · Claude-Abo · xhigh“ and „Haiku 5.5 · xhigh · Anthropic-API-Key“, and `--model haiku`. Haiku 5.5 needs Claude Code 2.1.293 (`claude update`). Saved choices and started runs keep their model (D-166).
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

- 2026-11-10 · CI is green again: every push to `main` since 2026-11-01 failed on Linux and macOS (five Studio tests stopped a mocked worker through psutil) and on macOS and Windows (one audio test compared an unresolved temporary path); both were faults of the tests.
- 2026-11-10 · Private vulnerability reporting, the only channel SECURITY.md names, is turned on; it was off.
- 2026-11-10 · Sources whose servers leave out an intermediate certificate (bundestag.de, bundesrat.de) download again: the import checks certificates like the operating system and browsers do. A page that is gone (404, 410 or a server that no longer answers) is read from the Internet Archive's latest capture, named with its date.
- 2026-11-10 · The recording and revision queues on an open page follow the server: a row refused at its start, or rows taken out in another window, no longer stay on the page until a job changes.
- 2026-11-09 · Publishing an episode no longer moves another episode of the same outline to the archive because it has its own length: a revised episode (D-191) and a new one written at the batch length (D-192) counted as another outline's, so publishing Geld S1's revised ep_002 archived the revised ep_003, and each episode of a batch would have archived the one before.
- 2026-11-09 · An episode's supplementary research whose found sources all fail to download reads the stored sections the gap probe matched instead of stopping with „Die zusätzlichen Quellen sind derzeit nicht abrufbar“, which every resume would have repeated for the same addresses.
- 2026-11-08 · A recording of a podcast with a third host no longer reads „geändert: Stimmen“ when it speaks the chosen voices: the audio page compared them without the third host's voice, so such a recording never counted as current, and with the music it seemed to need new speech instead of the cache.
- 2026-11-04 · A review repair of a story agent's episode no longer stops with `invalid_script` when it only adds or
  drops source references in segments no review point names: those segments are kept as they were and the repair
  stands. A changed word there is still refused.
- 2026-11-04 · When an episode stops while „Alle weiteren Folgen“ writes the series, its resume, by hand, after a quota
  wait or by an allowance, goes on with the remaining episodes after it. Until now the resume finished that one episode
  and the rest of the series was never begun.
- 2026-11-03 · „Neue Version anlegen“ also takes the project's own audio choice along. Until now a new version spoke
  with the voices of the workspace settings.
- 2026-11-03 · While a text job of the project runs, the approval of a recording through Google, OpenRouter or ElevenLabs
  can be ticked again. „Freigeben und einreihen“ queues the episode, and it starts once the job is done. Until now the
  checkbox stayed greyed out, although the queue was meant for exactly this case. Local Qwen still waits for the job.
- 2026-11-03 · Writing a series one episode at a time no longer moves the episodes written before into `episodes/archive/`: the story agent's own chapters made each earlier episode look like one of another outline, so it vanished from the Studio while its recording stayed under `exports/`. An episode archived this way can be moved back from `episodes/archive/<time>_<run>/` to `episodes/`.
- 2026-11-01 · Studio:
  - A long URL in a script segment no longer widens the page on a phone.
  - Long file names of source materials stay inside the research page's margin.
  - The numbers of „Leitfragen und Abdeckung“ stand at the first line of each question.
  - „geschrieben“ looks different from „als Nächste“.
- 2026-11-01 · Recording with Gemini through Google works again. Since 2026-10-23 Google refused every request with
  two voices („Unknown parameter 'mode'“). Recordings already made keep their passages.
- 2026-11-01 · When the series review of a story agent's run asked for corrections, the run stopped with a file error.
  It now corrects as the episode review does.
- 2026-11-01 · The reading preview of a running story agent's episode shows the reviewed text once its review passed. It
  used to stay at the draft.
- 2026-11-01 · A script run stopped during the check of an orientation sheet asks only that check again on resume. Before,
  it drafted the sheet anew.
- 2026-11-01 · A failure while the story agent's session runs ends the session too. Before, it kept waiting up to half an
  hour for an answer.

- 2026-10-19 · A Google or ElevenLabs recording's progress counts the segments of its finished chapters: it showed their passages against all segments, „9 von 240“ for an episode that was at 138.
- 2026-10-19 · A project copied or moved to another folder no longer stops its scripts and recordings with „Lokale Quelle seit der Recherche geändert“: a local source is recognized by the content the research read (D-178).
- 2026-10-19 · Script review: a teaser that names a fact a later scene explains no longer stops the review. The review asked to attach the later scene's evidence, which the script check rejects, so every repair failed; it now asks to move the fact to its scene or to keep it as that scene's open question (D-175).
- 2026-10-09 · Script run: a supplementary research no longer stops with „Erklärgrundlagen fehlen“ when the model quotes a long question only up to the end of one of its sentences; the cut quote counts when no other question starts the same way (D-174).
- 2026-10-09 · Script run: a research limit with unread passages in the sources is now read by the episode that states the limit, not by the first episode whose sources hold a passage. Before, one episode citing large editions read the limits of the whole series (Orlagau ep_001: 19 gaps, 16 of them other episodes'), and a limit it resolved left its own episode with neither the limit nor the answer (D-173).
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
