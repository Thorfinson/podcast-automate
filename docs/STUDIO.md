---
title: Studio
doc_type: frontend
status: current
last_reviewed: 2026-10-08
covers:
  - src/podcast_automate/studio.py
  - src/podcast_automate/studio_worker.py
  - src/podcast_automate/studio_settings.py
  - src/podcast_automate/studio_progress.py
  - src/podcast_automate/studio_messages.py
  - src/podcast_automate/studio_allowances.py
  - src/podcast_automate/studio_scripts.py
  - src/podcast_automate/studio_trash.py
  - src/podcast_automate/studio_text.py
  - src/podcast_automate/status_summary.py
  - src/podcast_automate/production_report.py
  - src/podcast_automate/model_trace.py
  - src/podcast_automate/execution.py
  - src/podcast_automate/attachments.py
  - src/podcast_automate/provided_works.py
  - src/podcast_automate/web/app.js
  - src/podcast_automate/web/index.html
---

# Studio

The Studio is the local browser interface of podcast-automate. It guides one project from the brief through research,
table of contents, script work and reading to the recording, and shows what each job is doing and why it stopped. Its
rules live in [Business logic](BUSINESS_LOGIC.md) and the stage docs [Research](RESEARCH.md), [Scripts](SCRIPTS.md),
[Teaching](TEACHING.md) and [Audio](AUDIO.md). The Studio speaks German or English
([Interface language](#interface-language)); this doc quotes its labels in German with an English gloss on first use.

## Starting the Studio

In the repository, double-click **`Podcast-Studio.cmd`** on Windows or **`Podcast-Studio.command`** on macOS; on Linux
run `sh Podcast-Studio.sh`. This starts the local server and opens `http://127.0.0.1:8765`; keep the server window open
while you work. It uses the installed controller environment `.venv` and finds FFmpeg under `tools/ffmpeg/bin` or on the
`PATH`. After a fresh checkout, first complete [Install on Windows 11](OPERATIONS.md#install-on-windows-11) or
[Install on macOS and Linux](OPERATIONS.md#install-on-macos-and-linux).

Or start it with one command:

```powershell
.\.venv\Scripts\pla.exe studio
```

`--port 8766` chooses another port, `--no-browser` keeps the browser closed. A second double-click opens the running
Studio. **„Studio beenden“** (stop the Studio) at the bottom left stops the local server and pauses its active jobs,
which stay resumable at the next start. By default only this computer can reach the server (see
[Studio access](SECURITY.md#studio-access)).

If the Studio started by double-click on Windows does not find Codex, see
[Check the subscriptions](OPERATIONS.md#check-the-subscriptions). Once that is fixed, **„Fortsetzen“** (resume)
continues the saved job; a table-of-contents draft still waits for your review before the scripts are written.

## Using it from a phone

On Windows, **`Podcast-Studio-WLAN.cmd`** (or `pla studio --lan`) makes the Studio reachable from your home network,
for example from a phone on the Wi-Fi. The server window and the bottom left of the Studio show the address, for
example `http://192.168.178.75:8765`; open it in the phone's browser. Who can connect and what the Studio checks:
[Studio access](SECURITY.md#studio-access).

On the first start, Windows Firewall asks whether Python may be reachable on the network; allow only private networks.
If the Studio already runs for this computer only, the Wi-Fi launch file does not open it again but asks you to close it
first with „Studio beenden“ once no job is running, because a running job would be paused.

## Interface language

The Studio shows its pages and its own messages in German or English, the same for every project of the workspace
(why: D-152). The choice is automatic, following the browser's language, or fixed to German or English; a workspace
that had projects before the choice existed stays German until you change it. Where the choice is stored and how
„automatic“ decides: [Interface language](CONFIGURATION.md#interface-language); which texts follow it:
[Languages](ARCHITECTURE.md#languages).

- **What follows it.** The pages and the server's own messages; the editorial chat, which answers in the language of
  your latest message and otherwise in the interface language; and the [status briefs](#status-briefs). A job keeps
  the language it started in: its brief and the messages its worker writes stay in it, an automatic resume takes the
  stopped job's language, and a queued recording the language it was approved in.
- **What does not.** The pipeline's own messages, such as a stop reason a run writes, are German and never translated
  ([Stop reasons](#stop-reasons)). Everything a podcast's listeners get, downloads included, follows the project's
  content language ([Downloads](#downloads), [Exports and listening sheet](AUDIO.md#exports-and-listening-sheet)), and
  the command line is English ([Commands](PRODUCT.md#commands)).
- **For the page.** `GET /locale.js` delivers the catalog of the active language over the English one, and
  `/api/bootstrap` names the setting and the resolved language (`ui_language`). `POST /api/ui-language` with
  `{"ui_language": "auto" | "de" | "en"}` saves the choice and answers with it and the resolved language; it is not a
  save of the settings page, and any other value is refused with `invalid_request`. Error answers and a job's stop
  name the language of their message (`message_language`).

## The guided flow

A project runs through six steps, each a page in the navigation:

1. **„Auftrag“** (brief): a single editorial partner asks in the chat for what it needs and proposes
   a brief. It asks early what the series is for (**„Ziel der Serie“** (series goal): „Verstehen“, „Bewerten“,
   „Anwenden“ (understand, evaluate, apply), each weighted 0–3) and, for fast-moving fields such as AI practice, how
   current the sources must be (**„Aktualität der Quellen“** (source recency): the last N months); both appear in the
   summary. It treats attachments and posts as pointers to names, works and tools, not as claims the episodes must
   check. You state topic, prior knowledge, depth and language in your own words; text model, audio provider, voices
   and execution are set on the [Settings page](#settings-page), not in the chat. **„Diese Auswahl übernehmen“**
   (apply this selection) saves the reviewed summary. The chat grants no plan or audio approval. Stored voice samples
   stay reachable through the collapsible voice library. Credentials belong only in the key field of the settings page.
   The partner's replies are shown with their formatting (bold labels, lists); your own messages stay plain text. Once
   a project has research or later work, the page leads with the saved brief (a waiting proposal with its
   „Diese Auswahl übernehmen“) and folds the conversation under **„Gespräch mit der Redaktion“** (conversation with the
   editorial team); it opens by itself while a message waits for an answer (why: D-161). The input box stays pinned at
   the foot of the conversation only while its attachment panel is closed, and never on a phone.
2. **„Recherche“** (research): searching, downloading and evaluating sources and checking the dossier run automatically
   after the start; the result is readable here. Then the table of contents is drafted; an existing plan can be opened
   directly. A new research is marked as a restart of its own. The dossier is shown by its structure: an overview,
   **„Leitfragen und Abdeckung“** (guiding questions and coverage) with each question's state and number of findings,
   short lists such as the open questions, then **„Befunde nach Leitfrage“** (findings by guiding question), each
   question folded with its findings titled by their first sentence and kind, and long lists such as the sources folded
   at the end. Finding ids, source-section ids, claim-type values and the run id are left out (the run id stands under
   „Technische Angaben“); the dossier file itself is unchanged (why: D-162). A dossier without finding headings is shown
   as one document.
3. **„Inhaltsverzeichnis“** (table of contents): review episodes, chapters, guiding questions and explanation steps and
   have them revised if needed. **„Plan freigeben & Skripte schreiben“** (approve plan & write scripts) approves exactly
   this plan state and starts the script work; an already approved plan leads to the running script work instead. A
   table of contents appears only when its draft is finished: while a new draft or a revision runs, the previous plan is
   hidden, and a stopped draft shows only its hold card (why: D-100). Only a finished draft can be revised or approved;
   after a stopped draft, continue with **„Neues Inhaltsverzeichnis entwerfen“** (draft a new table of contents).
4. **„Ausarbeitung“** (script work): teaching plan, script draft, dialogue polishing, quality review and publishing are
   automatic phases of one job, with no extra click between them. The page shows the current episode, finished results
   and concrete open points; reviewed teaching plans can be read while work continues. Supplementary research and
   internal corrections belong here.
5. **„Skripte lesen“** (read scripts): each episode becomes readable once its complete draft is saved. Its review state
   reads „Entwurf“ (draft), „Dialog überarbeitet“ (dialogue revised) or „Prüfungen bestanden“ (reviews passed), after
   publishing „Fertig zur Durchsicht“ (ready for review). Further episodes appear in the selection automatically. An
   opened version stays in place while you read; when a newer text or review state exists, **„Aktuellen Stand laden“**
   (load current state) loads it. Previews get no audio approval. After the script work, the published versions can be
   commented on and then explicitly approved for audio. The reader bar holds **„‹ Vorherige“** and **„Nächste ›“**
   around the episode picker, which names each episode in full and shows a state only for previews. **„Als gelesen
   markieren“** (mark as read) marks the published script state; a revised episode reads as unread again. The marks are
   kept in the project (`studio/reader_state.json`), so they follow you to the phone (why: D-163). The margin starts
   with **„Weiter zur Audio-Freigabe →“** and the feedback field. Speakers carry their host names, or without names the
   voice each host speaks with in this episode, such as „Erinome · Host A“.
6. **„Vertonung“** (recording): the checkbox confirms the script state you read, with the provider and voices shown,
   and **„Audio erzeugen“** (generate audio) starts the recording. Finished takes are listed under **„Alle fertigen
   Folgen anhören“** (listen to all finished episodes), which **„Podcast anhören“** (listen to the podcast) on the
   overview leads to. The page, including the size estimate for Gemini: [Recording flow](AUDIO.md#recording-flow).
   A recording made for an earlier state stays playable and says what changed since („geändert: Sprechtempo“, from the
   server's `audio_stale`: script, voices, provider, model, pauses, pace, styles, alternate roles); the page names it
   once above the approval (why: D-160).

The pronunciation check, the panels for spoken forms, host names and editorial notes, re-rendering one segment and the
listening review are described in [Spoken forms and pronunciation](AUDIO.md#spoken-forms-and-pronunciation),
[Re-rendering one segment](AUDIO.md#re-rendering-one-segment) and
[Exports and listening sheet](AUDIO.md#exports-and-listening-sheet).

Which approval each step needs and what it is bound to: [Human approvals](BUSINESS_LOGIC.md#human-approvals). Older
projects show their existing research, scripts and published audio files; **„Inhaltsverzeichnis entwerfen“** (draft
table of contents) creates a separate plan to review.

**Trial project.** Under the message box of a new project's brief page, the checkbox **„Probelauf: kleines Thema,
eine kurze Folge“** (trial: a narrow topic, one short episode; never preset) creates a trial project, with a short note
on what to expect built from the trial's limits (`/api/bootstrap` → `trial`). With it ticked, an empty message is
allowed: the project then takes the sample topic of its language. The request that creates the project
(`POST /api/projects`) carries `"trial": true` (`trial.trial_brief`), and its research and script runs keep to the
trial's small limits ([Trial project](BUSINESS_LOGIC.md#trial-project); why: D-157). The editorial partner of a trial
steers toward a narrow topic. A „Probelauf“ chip marks the project on its overview card and every page head. It then
runs through the same six steps as every project.

Automated tests cover plan approvals, stale text and voice states, assistant proposals without automatic project
changes, resume, the local HTTP access barriers, key handover, audio downloads with seek positions and the UI states
(see [Automated checks and early audio test](QUALITY.md#automated-checks-and-early-audio-test)). Automated content reviews
do not guarantee excellent narration; your reading stays deliberately part of the flow. A full run with a new topic and
a listening test remain the practical acceptance (see V-18).

## Attachments and provided works

### Attachments

At **„Neues Projekt“** (new project) → „Auftrag“ you can attach several **.md**, **.txt** or **.docx** files
below the message box (**„Dateien anhängen“** (attach files)), describe how to use them if needed, and click
**„Senden“** (send). Without accompanying text, the partner proposes a project from the files, takes the wishes they
contain into account and asks for what is missing. You still review the summary before „Diese Auswahl übernehmen“.

Active attachments are visible after reloading and under „Recherche“. **„Entfernen“** (remove) takes a file out of the
active inputs; earlier model calls and research snapshots stay traceable. After attachments change, the partner must
update its summary before you can apply it. Uploading the same file again creates no second active copy. Changes are
locked while jobs run.

Limits (`attachments.py`): up to **10 attachments per project** (`MAX_FILES`), text files up to **256 KiB** each
(`MAX_FILE_BYTES`), DOCX up to **2 MiB** (`MAX_DOCX_BYTES`), up to **1 MiB of extracted text** in total
(`MAX_TOTAL_BYTES`), and up to 4 MiB of files per send (`MAX_TRANSFER_BYTES`). TXT and MD must be UTF-8 (with or without
BOM) or UTF-16 with BOM. DOCX is read locally without Word, as main text including tables; images, layout, headers,
footers and footnotes are dropped. Save password-protected files as a normal DOCX or TXT first.

The server stores UTF-8 text copies under `inputs/uploads/` with short, self-generated names, keeps the original names
in the index `inputs/attachments.json` and registers the copies in `local_sources`; the original files stay unchanged.
Which `local_sources` entries the Studio accepts and which files a research run reads:
[Local files](SECURITY.md#local-files).

All attachments flow into the setup context; above 60,000 characters together (`attachments.CONTEXT_CHARS`) the partner
uses explicitly marked excerpts. The research reads the full text copies and, as with other sources, selects the
relevant sections for evaluation. Very short notes suit the brief but may fall below the minimum length of source
extraction. Claims from notes or desired results must be supported by independent sources or treated as open.

„Senden“ sends the text to the selected text model. An upload alone starts neither web research nor recording.
Credentials do not belong in files; detected keys are rejected ([Secrets and keys](SECURITY.md#secrets-and-keys)).

### Missing works

On the „Recherche“ page, **„Fehlende Werke“** (missing works) lists the books and articles that blocked sub-questions
(or sub-questions in a new attempt after a block) need as the original work and that no free source provided, each
with the questions that need it. Obtain the work through a library, interlibrary loan, subito or purchase and upload it
there as PDF, saved web page or text file (up to 150 MiB, `provided_works.MAX_WORK_BYTES`; books up to 2000 pages,
`pdf_text.BOOK_PAGES`). Under **„Anderes Werk hochladen“** (upload another work) you enter author, title and year
yourself.

An uploaded work is stored under `inputs/works/` with its reference in `inputs/works.json`, next to the brief, so a
running run stays resumable. Unlike an attachment it counts as **evidence**, not as a note: the research reads it at
the next step as a primary work (provenance: „vom Herausgeber bereitgestellte Kopie“ (copy provided by the editor)) and
retries the questions it was uploaded for; a paused run does so when resumed. Quotes are checked verbatim against the
file as always. Shadow libraries are not an admissible source.

## Settings page

The **„Einstellungen“** (settings) page sits at the top next to **„Übersicht“** (overview). Its values apply to all
projects:

| Section | What you set there |
| --- | --- |
| **„Textmodell“** (text model) | One of the presets; see [Choosing the text model](#choosing-the-text-model). Below them a line names the measured money per call of Claude Sonnet 5.5 and Opus 5.5 in research and script work and that a whole series took 1,400 to 1,800 calls ([Money limit](BUSINESS_LOGIC.md#money-limit)). |
| **„Websuche“** (web search) | **„Über das Textmodell“** (through the text model: Claude and Codex search with their own tools, the default) or **„Über Perplexity“** (through Perplexity: the Studio searches itself and the text model chooses among the results). Perplexity lets every text model research, an OpenRouter model included, needs the Perplexity key and a money limit, and costs about 0.005 USD per search request. New research and script runs take the choice; running ones keep theirs ([Research runs and their web search](BUSINESS_LOGIC.md#research-runs-and-their-web-search)). |
| **„Audio“** | Provider (Qwen, Gemini through Google, Gemini through OpenRouter), speech model (Gemini), the voices of host A and host B, the three pauses ([Pause minimums](AUDIO.md#pause-minimums)), **„Sprechtempo Deutsch“** and **„Sprechtempo Englisch“** (speaking tempo per language, [Speaking pace per language](AUDIO.md#speaking-pace-per-language)); for Gemini whether expression tags are set before recording. For Google also the style of each role with its presets, **„… ohne Eile sprechen“** (speak unhurried) per language, **„Rollen von Folge zu Folge tauschen“** (swap roles from episode to episode) and **„▶ Gesprächsprobe“** (conversation sample) of exactly this selection ([Style and roles](AUDIO.md#style-and-roles)). |
| **„Ausführung“** (execution) | See [Sequential or parallel](#sequential-or-parallel). |
| **„Ohne Rückfrage“** (without asking) | See [Pre-approvals](#pre-approvals). |
| **„Limits“** | Model calls per run, sources and search rounds per research, the time limit of one model call, and **„Kostengrenze je Lauf in USD“** (money limit per run in USD), required for a text model billed to a key and for the web search through Perplexity: saving either without it is refused with `cost_limit_required` ([Money limit](BUSINESS_LOGIC.md#money-limit)). |
| **„Claude“** | The switch for bought extra usage ([Studio settings](CONFIGURATION.md#studio-settings)). |
| **„OpenRouter-Key“** | The key for OpenRouter text, Gemini audio through OpenRouter and Jev: **„Key hinterlegen“** (store key), **„Key entfernen“** (remove key). Every key panel says where its key comes from (the credential store, this session only, or an environment variable of the server), and one note above them names the store; handling in [Keys in the credential store](SECURITY.md#keys-in-the-credential-store). |
| **„Google-Key“** | The key for Gemini audio through Google and the conversation samples, with the same two buttons; handling in [Google key in the Studio](SECURITY.md#google-key-in-the-studio). |
| **„Anthropic-Key“** | The key for Claude on your Anthropic API key (`claude_api`), with the same two buttons; every call is billed to your Anthropic account. Handling in [Anthropic key in the Studio](SECURITY.md#anthropic-key-in-the-studio). |
| **„Perplexity-Key“** | The key for the web search through Perplexity, with the same two buttons; only the search queries go to Perplexity, and every request is billed to your Perplexity account. Handling in [Perplexity key in the Studio](SECURITY.md#perplexity-key-in-the-studio). |
| **„CORE-Key“** | CORE's free key for free copies of blocked sources, with the same two buttons; only the title or DOI of the work goes to CORE. Handling in [CORE key in the Studio](SECURITY.md#core-key-in-the-studio). |

Below the title, links jump to each section, and **„Keys“** shows whether the OpenRouter, the Google, the Anthropic
and the Perplexity key are there („✓ hinterlegt“ or „fehlt“), entered in the Studio or taken from the server's
environment; the same mark stands on each key's panel. The key itself never reaches the page. The text models are
grouped by how a call is paid: **„Über deine Abos · keine API-Kosten“** (through your subscriptions),
**„Über deinen Anthropic-API-Key · pro Aufruf bezahlt“** and **„Über OpenRouter · pro Aufruf bezahlt“**; a paid group
names its missing key („Anthropic-Key fehlt“). What each costs folds under **„Was kostet welches Textmodell?“**
(why: D-161).

**„Einstellungen für alle Projekte speichern“** (save settings for all projects) saves the page from a bar pinned at
its foot, which reads **„Ungespeicherte Änderungen“** (unsaved changes) after an edit and **„Alles gespeichert“**
(everything saved) otherwise; leaving the page with unsaved edits asks first. Storing or removing a key acts at once
and is no unsaved edit. On the settings page and the overview, the sidebar lists the projects instead of a project's
steps, and the project picker reads **„Projekt wählen …“** (choose a project). The chip at the top
reads **„Gilt für alle Projekte“** (applies to all projects) once the workspace settings are saved, **„Noch je
Projekt“** (still per project) before. Storage, the values shown before the first save, and when running jobs pick up a
change: [Studio settings](CONFIGURATION.md#studio-settings) (why: D-108). The project page only shows the settings,
with **„Einstellungen öffnen“** (open settings).

## Choosing the text model

Text and audio providers are chosen independently; asked about them, the editorial partner points to „Einstellungen“.

The „Textmodell“ section offers the presets of `text_settings.TEXT_PRESETS`;
[Providers and models](PRODUCT.md#providers-and-models) lists them with their models, reasoning levels and the
minimum Claude Code versions. New Studio projects start with **„Automatisch · Claude, sonst
Codex“** (automatic · Claude, otherwise Codex). A saved choice that matches no preset stays selectable as „Bisher: …“
(so far: …). For OpenRouter presets, **„Höchstens Ausgabe-Tokens je Aufruf (nur OpenRouter)“** (at most output tokens
per call, OpenRouter only) caps the answer length. Other model IDs can be given on the command line
([Choosing the provider on the command line](SCRIPTS.md#choosing-the-provider-on-the-command-line)).

With „Automatisch“ the model cannot be set, and the reasoning level only jointly for both subscriptions (`low` to
`xhigh`, `text_settings.SHARED_EFFORTS`; for example „Automatisch · Claude, sonst Codex · high“); to set them
individually, choose a fixed provider. How the automatic choice picks a subscription per call, when it switches and
which provider runs the web research:
[Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection). An older Claude Code stops
a job with a fixed Claude choice with the hold card **„Claude Code zu alt“** (Claude Code too old).

The choice applies to the editorial chat, table of contents, teaching plan, script, dialogue polishing, quality
reviews and the [status briefs](#status-briefs). How each provider is called: [Text provider adapters](ARCHITECTURE.md#text-provider-adapters).

**„Verbindungen prüfen“** (check connections) on „Auftrag“ shows both subscription logins and the quota
state; a job is ready to start as soon as one subscription or Claude on the API key is usable (the same checks as
[`pla doctor`](OPERATIONS.md#quota-and-installation-check); here the `claude_api` and `perplexity_search` checks also see
a key entered on the settings page, while `pla doctor` reads only the environment). **„Websuche über Perplexity“** (web
search through Perplexity) only says whether the Perplexity key is there and never makes a job unready. What it checks
with Gemini audio:
[Providers and models](PRODUCT.md#providers-and-models).

With a text model billed to a key (OpenRouter, Claude on the Anthropic API key), the Studio refuses a billed action
(chat, research on the API key, table of contents, scripts, revision, expression tags, companion kit) with
`cost_limit_required` before any worker starts while no money limit is set; research with OpenRouter text searches on
the subscriptions and needs none ([Money limit](BUSINESS_LOGIC.md#money-limit)). With the web search „Über Perplexity“
the same refusal holds for research, table of contents, scripts and revision on any text model, and research with
OpenRouter text runs on the OpenRouter model instead of the subscriptions.

The job status shows the choice the run saved, even if other settings were saved for new jobs since; below it,
**„Aktueller Anbieter“** (current provider) shows the quota state and reset of both subscriptions from the last model
call. Older jobs without an explicit choice appear as „nicht festgelegt“ (not set) and get no new defaults when resumed.
Where a run stores its choice: [Run folder and manifest](ARCHITECTURE.md#run-folder-and-manifest).

### Continuing with another provider

Every script and research job can continue with another text provider at any time: below the job's saved text choice,
**„Weiter mit …“** (continue with …) offers „Claude, sonst Astra (xhigh)“, „Astra (xhigh), sonst Claude“, „Nur
Claude“, „Nur Astra (xhigh)“, „OpenRouter · bezahlt pro Aufruf“ (OpenRouter · paid per call) with a model selection,
and „Claude über den Anthropic-API-Key · bezahlt pro Aufruf“ (Claude on the Anthropic API key · paid per call). „Nur
Claude“ and the API key show a **„Claude-Modell“** (Claude model) list, preselected with the run's own Claude model; a
chosen model works at its preset's level, Sonnet 5.5 at high (D-170). The hold card's „Mit Claude fortsetzen“ keeps the
catalog default. Both
paid choices show a field **„Kostengrenze USD“** (money limit in USD) for this run, which the switch needs unless the
run already has one; **„Übernehmen“** (apply) saves the choice. If a job with a fixed provider stops because its subscription is
exhausted, the hold card offers the other one: **„Mit Astra (xhigh) fortsetzen“** (continue with Astra) for Claude,
**„Mit Claude fortsetzen“** (continue with Claude) for Codex. When the switch applies and what it keeps: [Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection).

## Overview, navigation and hold cards

The overview lists each project once, as a card: those waiting for you first, then running ones, then the rest. A
card holds the bar of the six steps, whose segments name their step and state (a legend above the cards: erledigt,
läuft, wartet auf dich, angehalten, braucht Hilfe), one line of state (for a research or script run a second line with its numbers: „49 von 74 geprüft · 6 in Arbeit ·
6 blockiert, 4 davon nur wegen Vorfragen · Aufrufe 1119 von 1500“, or the segments done; `cardProgress`), and its next
action as the one filled button,
such as „Skripte lesen“ or „Plan freigeben“; otherwise **„Öffnen“** (open). A stopped project's card carries the
step its page recommends as that button, done in one click on the overview: **„Fortsetzen“** where the header offers
it, **„Mit neuen Anläufen fortsetzen“** where the server would accept fresh attempts, **„Empfehlungen übernehmen und
fortsetzen“** or **„Limits anheben und Empfehlungen übernehmen“** for the advisor's retry advice (with each
question's hint and the decision card's sized limits), the raise of the call limit the advice needs, and the call or
search limit a stop names as its way on. „Ansehen“ or „Entscheiden“ stays beside it; a key, a money limit, a note, a
dispute or a choice per question still opens the page (why: D-169). Clicking the title or the card opens the
project where its work waits. Once takes are finished, „Podcast anhören“ and **„Podcast herunterladen“** (download
podcast, ZIP) follow; **„Projekt löschen“** sits in the card's „⋯“ menu (why: D-161).

The card, the navigation and the header take a project's state from the same facts (why: D-160). Recordings made for
an earlier script, voice or audio setting stay playable and ask for nothing: the card reads „Podcast verfügbar · 19
Folgen · 19 von einem früheren Stand“ and the step „Vertonung“ „Aufnahmen vorhanden · 19 älter“. Only a published
episode without any recording asks for an audio approval („1 Folge hat noch keine Aufnahme.“, ▲ on „Vertonung“).

The navigation shows each area's state: present, to approve (▲), in progress or pending. The running step shows its
elapsed time; a research plan waiting for approval shows the projection in hours. The header names the job or its stop
reason in one line, with stop, resume (only where it can help) and the jump to the page where something is to be
decided; running recordings are listed beside it. The header keeps one line on every page. Where the hold card
recommends fresh attempts, the header offers no plain „Fortsetzen“ but the jump to the card. After a finished job of
its own (a chat, a check, a companion kit, recordings) the header names the project's state instead, such as „Podcast
verfügbar · 14 Folgen“. While a job runs, the line adds its time since the start, the calls
used of the limit („Aufrufe 120 von 750“) and, for a run billed to a key, **„Kosten X von Y USD“** (cost X of Y USD:
money spent of the money limit, from the progress fields `cost_spent_usd` and `cost_limit_usd`; `cost_estimated_usd`
and `cost_unpriced_attempts` hold the part counted without a reported cost). Stopped recordings that share one reason
name it there, as in „14 angehalten: OpenRouter verweigert den Zugriff“, with **„Alle 14 fortsetzen“** (resume all) and, for a key stop,
a button to the „OpenRouter-Key“ panel; until 2026-10-04 only the job list on the recording page named the reason. The browser tab shows ● for a running job, ▲ for a decision and ! for
a stop; the overview shows the number of waiting projects.

The collapsed **„Maschinenraum“** (engine room) at the bottom holds only telemetry, in this order (why: D-164):
**„Kurzbericht“** (status brief), the live output, the calls and money of the run, the times of the current call,
the run's text model with **„Anderen Anbieter für diesen Auftrag wählen“** (choose another provider for this job)
folded, and for research and script runs the **„Produktionsbericht“** (production report: calls, model time and share
per stage, the same numbers per prompt version, providers, stops per stage and approvals; `production_report.py`; a
research run's one stage broken down by step from its prompt versions, such as „Quellen lesen“, „Websuche“,
„Antworten prüfen“). For a provider billed to a key it adds the USD billed, counted from every attempt's row in
`billing.json`, and the **„Versuche ohne Kostenangabe“** (attempts without a reported cost). A running chat, check,
voice sample, expression or companion kit shows its progress there; finished, it leaves nothing. After recordings the
engine room shows the running ones' progress and the project's last research or script run; the recordings
themselves, their buttons, the stop reason and the run time stand on their pages and in the header. Its head is its
one toggle, and it stays closed when there is nothing to show. A connection check answers under „Verbindungen“ beside
its button and a new voice sample under **„Neue Hörprobe“** (new voice sample) on „Auftrag“. Each step shows its own
job details: the research its question status with the plan approval, the script work its progress and teaching
plans, the recording the running and stopped recordings per episode.

Opening an existing project leads to the actual work step, also for a resumed job. After a step starts, the view
follows the flow; a page you open yourself stays open when the background work finishes. Project and page are kept in
the address, so back and forward in the browser switch between visited pages.
Navigation and reloading start no model calls and grant no approvals.

### Stop reasons

When a job stops, its page shows a **hold card**: what happened, whether „Fortsetzen“ can help and which button leads
on. The research shows it above the question status, the table of contents and the script work at the top of their
page, the recording in the episode's card, and the conversation as a chat reply. There are five kinds:

- **„Angehalten“** (stopped): „Fortsetzen“ repeats the step and everything finished stays saved (time limit,
  connection error, your own stop; transient stops of research and script runs resume by themselves, see
  [Automatic resume](#automatic-resume)). When a step has used up its automatic corrections, including the series
  review's correction (`series_review_failed`), the card offers **„Mit neuen Anläufen fortsetzen“** (continue with
  fresh attempts), but only when the run would accept them (`run_budget.fresh_attempts_available`) (why: D-101).
  The web search through Perplexity stops here with **„Websuche nicht erreichbar“** (web search unreachable:
  Perplexity failed or returned nothing) and **„Suchauswahl abgewiesen“** (search selection rejected: the model kept
  naming sources the search had not found; if it stops again, another text model helps); neither resumes by itself.
- **„Wartet auf Kontingent“** (waiting for quota): after the reset, „Fortsetzen“ or the automatic resume. On the
  Anthropic key this is **„Ratenlimit der Anthropic-API“** (rate limit of the Anthropic API), for the web search
  **„Ratenlimit von Perplexity“** (rate limit of Perplexity); the Studio resumes both by itself after a pause.
- **„Braucht Einrichtung“** (needs setup): first fix something outside the Studio (login, FFmpeg, OpenRouter credit),
  then „Fortsetzen“. If an OpenRouter or Anthropic key is missing (**„Anthropic-Key fehlt“**, Anthropic key missing)
  or refused (**„Anthropic-Key abgelehnt“**, Anthropic key refused), the card holds the input field and resumes once
  the key is stored. **„Anthropic-Guthaben erschöpft“** (Anthropic credit used up) asks you to top up the account in
  the Anthropic Console and is never resumed automatically; **„Claude lief nicht über den API-Key“** (Claude did not
  run on the API key) asks for `claude update`. The web search through Perplexity has the same cards: **„Perplexity-Key
  fehlt“** (Perplexity key missing) and **„Perplexity-Key abgelehnt“** (Perplexity key refused) hold the input field,
  **„Perplexity-Guthaben erschöpft“** (Perplexity credit used up) asks you to top up the Perplexity account. With „Automatisch“, an expired login or a too old Claude CLI stops the
  job only when the other subscription cannot continue either. Where the fix is another text model or audio provider,
  the card offers **„Einstellungen öffnen“** (open settings).
- **„Deine Entscheidung“** (your decision): research plan, blocked sub-questions or a higher call limit, for research
  and script runs alike; „… erhöhen und fortsetzen“ (raise … and resume) approves and resumes in one click. A budget
  stop names the limit it reached, model calls or search rounds (`job.stop.limit`, from the run's
  [manifest](ARCHITECTURE.md#run-folder-and-manifest)), and a sub-question whose web search ended on a run limit names
  whether the search rounds or the sources ran out (`block_cause`, [Run limits](RESEARCH.md#run-limits)), so the card
  offers the matching raise in either interface language; older runs carry neither, and the page then reads the
  German reason. A run
  billed to a key stops here with **„Kostengrenze fehlt“** (money limit missing) or **„Kostengrenze erreicht“** (money
  limit reached): the card shows the money spent, a USD field with a suggested limit
  ([Raising a limit](BUSINESS_LOGIC.md#raising-a-limit)) and **„Kostengrenze festlegen und fortsetzen“** (set money
  limit and resume) or **„Kostengrenze erhöhen und fortsetzen“** (raise money limit and resume); the limit applies to
  this run only.
- **„Neustart nötig“** (restart needed): this run cannot continue, for example after an unsupported review objection, a
  checkpoint that no longer fits, changed inputs, a permanently contradictory table of contents or a teaching plan
  still incomplete after the automatic corrections. The card offers no „Fortsetzen“ but the way forward (research anew,
  new table of contents, redraft with a note, approve again) and says what stays readable. For the teaching plan this
  is **„Mit den offenen Punkten neu entwerfen“** (redraft with the open points): the note arrives prefilled with the open
  points of the stop and stays editable; it goes into a new draft of this one episode with new repair rounds, the run
  resumes right away, and the approved table of contents stays.

Messages appear in the [interface language](#interface-language). The Studio's own messages come from its catalogs;
in a pipeline message it replaces command-line hints, local paths and internal identifiers and, in a German interface,
translates the review texts the model gets in English. The pipeline's messages are German and are never translated
(why: D-152): a message in the other language than the interface stands as the original under **„Technische
Details“** (technical details), where the stop code and, wherever the Studio rewrote a message, its original wording
are too; a code without its own card appears as „Angehalten“ with its code. For
unexpected program errors and the log files see [Logs and diagnosis](OPERATIONS.md#logs-and-diagnosis).

A paused run stays visible when a conversation, a connection check, a voice sample or a run of another kind runs
afterwards; only a new run of the same kind replaces it. The Studio sets each kind aside in its own file,
`studio/paused_<kind>.json` with `research`, `script` or `audio` (why: D-102); the single `studio/paused_job.json` of
older Studios is still read. Automatic resumes and pre-approvals also apply to runs set aside. If a resume comes to
nothing, the paused run stays on the page with its decisions.

Saving the brief, host names, editorial notes or spoken forms while a run that depends on them rests (also a table of
contents awaiting approval) first shows a warning and a confirmation question, because that run cannot be resumed
afterwards ([Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)).

If no answer arrives in the conversation, the chat names the reason and offers **„Erneut senden“** (send again); the
unanswered message is replaced, not repeated. When the conversation has used up its own call limit
([Budgets](BUSINESS_LOGIC.md#budgets)), **„Gesprächslimit auf … erhöhen“** (raise the chat limit to …) raises it.

### Deleting projects

**„Projekt löschen“** (delete project) on an overview card moves a resting project to `.studio/trash/`; the overview's
**„Papierkorb“** (trash) restores it with **„Wiederherstellen“** (restore). Running projects cannot be deleted.

### New versions

**„Neue Version anlegen“** (start a new version) in an overview card's **⋯** menu makes a podcast again from its
inputs with the current pipeline (`pla new-version <project>` from the command line; why: D-168). After a confirmation a
new project opens beside the old one, with the old one's brief, attachments, provided works (their old questions
cleared), other local sources, style notes, spoken forms and text, audio, execution and Jev choices; a German project
without a Jev choice gets today's default. Runs, research, scripts, recordings, exports, the setup conversation and the
project's own pre-approvals stay behind. The old version stays as it was and may keep running.

Versions are numbered per podcast in the project's `version.json`; from version 2 on the number stands beside the topic
in the overview, the sidebar and the project picker (**„Version 2“**). The newest completed research run's sources come
along: until the new version has research of its own, its research page offers them as a starting library
(**„Die … Quellen aus Version 1 als Startbibliothek anbieten“**, preselected); see
[Starting library](RESEARCH.md#starting-library).

### WebMCP

When the browser offers WebMCP (`document.modelContext`), the page registers two optional tools:
`read_podcast_workspace` reads the selected project's topic, current step and job status, and `navigate_podcast_step`
shows one of the six steps. They cannot grant an audio or plan approval. They were checked in a test context, not in a
supported live browser (see V-23).

## Stopping and resuming

### Stopping a job

**„Auftrag anhalten“** (stop job) stops the worker process the Studio started, with its child processes and any model
processes started in parallel, on Windows, macOS and Linux; other jobs keep running. A worker that an earlier Studio
started and that survived its restart appears as „läuft außerhalb dieses Studios“ (running outside this Studio) instead
of interrupted; the Studio recognises it by process ID and process start time and can stop it too (why: D-103).
Without this record, the page says that it ends by itself.

While a worker runs, it keeps the computer awake: on Windows through `SetThreadExecutionState` (the display may turn
off), on macOS through `caffeinate` (why: D-104); `PLA_KEEP_AWAKE=0` switches this off (see
[Environment variables](CONFIGURATION.md#environment-variables)).

What a stop keeps and what „Fortsetzen“ reuses is in
[Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding). Closing the browser ends no job.
After the worker ends, the running stage is saved as interrupted and resumable too. Complete script drafts and polished
versions stay available under „Skripte lesen“, and the display claims no model call still running. Budget counters,
approvals and completed intermediate results are kept; opening the Studio does not restart the job.

If a worker reports nothing new for more than five minutes, the job's page says so; Qwen counts each spoken segment,
and loading its speech model may take 15 minutes. If a worker ends without a result, for example after a crash, the
Studio sets its run back to resumable and shows the last lines of its error output (`<project>/studio/stderr/<job>.log`)
under „Technische Details“, filtered for credentials (see
[Credentials in traces, diagnostics and logs](SECURITY.md#credentials-in-traces-diagnostics-and-logs)).

### Automatic resume

The open Studio server resumes some stops by itself, at most three times in a row without progress
(`studio.MAX_AUTO_RESUMES`): the count starts again with your own „Fortsetzen“ and, since 2026-10-07, once a resumed
job got an answer from its model (why: D-155).

- **Quota.** A research, script or Qwen job paused by a subscription limit resumes at the reset of the subscription
  that ran out; without a known reset, after the waiting times in [Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection).
  A rate limit of the Anthropic API (`anthropic_rate_limit`) or of Perplexity (`perplexity_rate_limit`) names no
  reset and waits these times too. Exhausted credit (`openrouter_credits`, `anthropic_credits`, `perplexity_credits`)
  and a refused key are never resumed by themselves (`studio.NO_AUTO_RESUME`): only a top-up or a new key helps.
- **Transient technical stops.** Research and script runs stopped by a transient technical error (time limit, call
  without output, failed Claude or Codex call, Claude answer in the wrong format, OpenRouter unreachable or
  unavailable, the Perplexity search unreachable; since 2026-10-07 also a research call without an observable web
  search, `search_not_observed`, which the call itself repeats once first, and an answer without a readable result,
  `invalid_model_output`; `studio.TRANSIENT_STOPS`) resume 10, 30 and 90 minutes after the stop
  (`studio.TRANSIENT_BACKOFF_MINUTES`) (why: D-105, D-155). A correction loop that spent its attempts on answers it
  rejected is not transient: it takes fresh attempts ([Pre-approvals](#pre-approvals)).
- **After a code update.** A research or script run stopped on a saved state the code no longer reads
  (`invalid_research_checkpoint`) or on an unexpected program error (`processing_failed`) resumes once as soon as the
  Studio's code has changed after the stop (`studio.CODE_UPDATE_STOPS`); a stop after that resume waits for the next
  update (why: D-155).
- **Login.** A research or script run stopped for an expired subscription login (`authentication_required`) resumes
  once the login works again: while the project could start, the Studio checks the run's subscription logins at most
  every five minutes (`studio.LOGIN_CHECK_SECONDS`) with the CLIs' own status commands, without a model call. Logging
  in stays yours ([Check the subscriptions](OPERATIONS.md#check-the-subscriptions); why: D-155).

The hold card names the time and the attempt and says when the attempts are used up. Exhausted OpenRouter, Anthropic
or Perplexity credit and a refused key do not come back by waiting (`studio.NO_AUTO_RESUME`); the Studio never resumes
these, decisions or limits by itself, a money limit included, and an expired login only after the check above. Fresh
attempts and a higher call limit without asking come only through the [pre-approvals](#pre-approvals). No automatic resume passes the research plan approval
([Human approvals](BUSINESS_LOGIC.md#human-approvals)). Conversations, voice samples and Gemini episodes are not
resumed automatically, and the Studio announces nothing there; for missing voice samples see
[Gemini via OpenRouter](AUDIO.md#gemini-via-openrouter).

### Pre-approvals

Under „Ohne Rückfrage“ on the settings page, **„Neue Anläufe je Lauf“** (fresh attempts per run) and **„Aufruflimit
erhöhen je Lauf“** (raise the call limit per run) set what the Studio may give a stopped run by itself. The job summary
on „Auftrag“ shows them; a hold card announces when one is about to be used. A new workspace, without saved settings
and without a project, shows 2 fresh attempts and 250 extra calls there and gives them to its first project; existing
workspaces keep theirs (why: D-155). The choices, when the scheduler applies them and which decisions always stay
yours: [Pre-approvals](BUSINESS_LOGIC.md#pre-approvals) and [Budgets](BUSINESS_LOGIC.md#budgets).

### Research decisions and limits

- **Research plan.** Every Studio research job waits before the first sub-question until you click
  **„Plan freigeben und starten“** (approve plan and start). The card says in one sentence what the plan needs in
  sub-questions, hours, calls, sources and search rounds; when its limits do not suffice, the button is **„Plan
  freigeben und Limits anheben“** (approve plan and raise limits) and raises only the limits that fall short, then
  approves and starts (why: D-155); see
  [Scope check and plan approval](RESEARCH.md#scope-check-and-plan-approval) and, for the gate rule,
  [Human approvals](BUSINESS_LOGIC.md#human-approvals).
- **Blocked sub-questions.** The „Recherche“ page shows each guiding question's review state and offers the decisions a
  blocked sub-question needs (the advisor's recommendation, **„Noch einmal versuchen“** (try again), accepting a gap,
  raising an exhausted limit, disputes in the overall review, access gaps, finishing with remaining objections); see
  [Blocked sub-questions and decisions](RESEARCH.md#blocked-sub-questions-and-decisions).
- **One sentence and a recommended button.** Each decision card (advice, access gap, dispute, finishing with remaining
  objections, teaching redesign, table of contents, audio approval) opens with one plain sentence on what the choice
  means and highlights the recommended button; the alternative stays beside it, nothing is preselected and nothing runs
  on a timer, so the decision stays yours (why: D-155). An access gap recommends **„Werk hochladen“** (upload the work)
  when the work is on the list of missing works.
- **Raising limits for blocked questions.** When the source limit is reached or the search rounds run out, the card
  proposes a raise sized to the open questions: open questions times the higher of the plan's and the run's measured
  sources or rounds per sub-question (the measured rate from three verified questions on), a quarter more, at least one
  above the limit; without rates it offers +40 sources and +6 rounds. With retry advice the button **„Limits anheben und
  Empfehlungen übernehmen“** (raise limits and adopt recommendations) raises sources and rounds (and calls, if the
  advice would not fit), adopts the retries and resumes; without advice **„Limits auf … anheben“** (raise limits to …)
  only raises.
- **Jev.** Under „Lückenprobe“ (gap probe) in the job summary on „Auftrag“, **„Jev dazunehmen“** (add Jev)
  and **„Jev ausschalten“** (switch Jev off) switch Jev in this project's gap probe on and off; see
  [Gap probe](RESEARCH.md#gap-probe).
- **Starting library.** „Recherche neu beginnen“ (start research anew) preselects offering the previous research's
  sources as a starting library, and so does the first research start of a [new version](#new-versions) for the old
  version's sources; see [Starting and resuming](RESEARCH.md#starting-and-resuming).

### Table-of-contents corrections

The Studio corrects a contradictory table of contents automatically up to three times
(`script_checks.MAX_PLAN_REPAIRS`), working on the existing draft. The correction gets concrete details about swapped
foundations and scenes; the source check and the order stay binding. Draft and correction state are saved, so a resume
after an interruption picks up from there without resetting the correction limit.

### Restart after an update

The page reloads `app.js` on every visit, but the server and its scheduler keep the code they started with. If the code
has changed since, the top of the page shows „Das Studio hat neuen Code“ (the Studio has new code) with **„Neu starten,
sobald nichts läuft“** (restart as soon as nothing is running). The Studio then waits until no job and no recording
runs, accepts no new ones meanwhile, ends itself and restarts in the background with the same working folder and port,
without a browser window. The new server takes over queued recordings and scheduled resumes; the page reconnects by
itself.

The new server loads the keys from the credential store (D-167). Only on a system without one, an OpenRouter, Google, Anthropic or Perplexity key stored in the Studio lived only in the old server's memory and must be entered again
([Times and connection](#times-and-connection)); until then queued Gemini episodes show „wartet auf den Google-Key“
or „wartet auf den OpenRouter-Key“ (waiting for the key of their route) instead of waiting for a free slot.

While a key is missing but something needs it, every page shows **„Google-Key fehlt“**, **„OpenRouter-Key fehlt“**,
**„Anthropic-Key fehlt“** or **„Perplexity-Key fehlt“** (key missing) at the top, also on a page loaded fresh after a
restart (`Studio.key_reminder`, `studio.key_needs`): Gemini through Google (Google key), Gemini through OpenRouter, Jev
in the gap probe or an OpenRouter text model (OpenRouter key), Claude on the API key (Anthropic key, need
`anthropic_text`), or the web search „Über Perplexity“ (Perplexity key, need `perplexity_search`), each with its
projects and what happens without the key (Gemini recordings wait; new script runs search gaps by words only, or stop
where Jev was switched on by hand; OpenRouter jobs and jobs with Claude on the API key stop; research and
supplementary research stop as soon as they search). The notes fold into one line, such as „2 Keys fehlen ·
Google-Key (Gemini-Vertonung über Google) · OpenRouter-Key (Jev in der Lückenprobe)“; opened, each names its projects
and what waits and holds a field for its key; on the settings page it points to the key's panel (why: D-161). On the
recording page the only key field is the one in the approval card; the batch approval and the queue point to it. A
stored key ends its note at once. If the new server
fails before its
own log starts, the reason is in `.studio/relaunch.log` (why: D-117). The old server's console window can then be
closed. See also [Update the Studio](OPERATIONS.md#update-the-studio).

## Progress and telemetry

### Times and connection

During the script work the header shows the run time since the start or resume, and the engine room the duration
of the current model call and the age of the last saved model result. Ages beyond an hour read in hours, beyond two
days in days. „Letzte Änderung im Lauf“ (last change in the run)
says when the run itself last saved something.

If the Studio server does not answer for more than 30 seconds, the Studio marks the display as the state of a given
time, the header shows „Keine Verbindung“ (no connection) and the run indicator stops pulsing; this does not mean the
model crashed. If the running server rejects a request, its message appears instead of a connection notice. Every
answer names the running server instance: after a server restart the page reads session and key state again at the
next poll or click and says when an OpenRouter, Google, Anthropic or Perplexity key stored earlier was lost with the
old server.

The page never polls twice at the same time: a project page every 2.5 seconds, the overview every 10 seconds and a
hidden tab once a minute (the last two are `POLL_MS` in `web/app.js`); a tab that becomes visible again polls at once
(why: D-106). A project page polls `/api/projects/<id>/status`: the page without research, outline, episodes and
script previews, its jobs without the stages' output hashes, plus `content_version`, a fingerprint of those parts. Only
when the fingerprint or a job changes does it load the whole project (why: D-159); measured on 2026-10-07, 37 to
84 KB per poll instead of 2.5 to 3.7 MB. The overview builds each project card from a few cached file reads instead of
the full project page, with the running and stopped recordings only (why: D-107, D-159). Short file access errors are
retried at the next poll.

During a research, its page shows **„Nächster Schritt“** (next step): that there is nothing to do, with the current
step and its usual duration. The review loop reports each step in the activity line (review part, verdict,
assignment part, incorporation block, evidence correction, repeated attempt), and from the overall review on, the
research card names the review round and the number of reopened sub-questions.

### Status briefs

During research and script work the run writes short summaries of the logged activities and saved results under
`runs/<run_id>/status_reports/`; the engine room shows the newest as „Kurzbericht“. The run checks for changes every
three minutes (`status_summary.INTERVAL_SECONDS`); without new data a notice appears instead of another model call.
Each provider writes the brief with its own cheaper model at `low` (`status_summary.STATUS_MODELS`, listed under
[Providers and models](PRODUCT.md#providers-and-models)); the main model stays unchanged. The brief names no invented
remaining times and treats drafts explicitly as unreviewed.

These small extra calls use the subscription or the OpenRouter credit. They have their own visible counter (at most 100
per run, `status_summary.MAX_CALLS`), a time limit of 90 seconds (`status_summary.SUMMARY_TIMEOUT`), and pause after
three consecutive failures; the production budget stays unchanged. The brief is written in the
[interface language](#interface-language) its job started in (prompt version `studio_status.v2-ui-language`), while
the facts the model reads stay German; a brief in another language than its job's is written anew at the next check
(why: D-152). A run on the Anthropic API key gets no brief: the
„Kurzbericht“ reads „aus, weil dieser Lauf über den Anthropic-API-Key abrechnet“ (off, because this run bills the
Anthropic API key; state `off`, reason `billed_text`), since its calls would bill the key outside the run's money
limit (why: D-149). A failure of the status model does not stop the
job. When the job is stopped or ended, its status process ends too; older reports stay readable, and a stopped run's
brief never reads „wird gerade erstellt“ (being written). The brief stands first in the engine room; the small model's
name shows on pointing at its age.

### Current research task

Next to the brief, **„Aktueller Rechercheauftrag“** (current research task) shows the concrete question, the work order
given to the model, the last saved reading result and existing review objections. **„Material und Prüfpunkte“**
(material and checkpoints) lists the text passages provided, with source titles and pages, and the completion criteria.
Search hits are listed separately, because a hit is not yet read evidence. The view comes from each call's
`calls/call_*/work_context.json`; for calls started without one, it is reconstructed from the saved question status
and marked as such.

### Activity ages

The display distinguishes the age of the last content message from the model, of the last connection event and of the
last saved answer; reloading the progress data does not count as model activity. After three minutes without a content
message the waiting time is highlighted, without claiming a crash. Logged connection problems and retries, and
completed work steps without new evidence, are named separately. This local evaluation makes no extra model calls and
changes no research results or budgets.

### Live output

Under **„Live-Ausgabe · letzte 20 Meldungen“** (live output · last 20 messages) the engine room shows up to 20 readable
messages, expanded while the job runs; a stopped run folds them under **„Letzte Arbeitsschritte“** (last work steps).
Consecutive lines of one call at one moment share one head with time and sub-question or episode; plain live text
carries no kind label, a reasoning summary, a work step or a technical note does (why: D-164). Structured answers are reduced to their content as they arrive, for example „Vorhandene
Quellen durchsuchen“ (search existing sources), „Suchbegriff: …“ (search term: …) or „Einordnung: …“
(classification: …); empty fields, brackets, internal IDs and technical parameters are hidden. From Codex only public
reasoning summaries are shown, no raw or encrypted internal reasoning; which stream events each adapter delivers:
see [Text provider adapters](ARCHITECTURE.md#text-provider-adapters).

The display follows at the next Studio status poll, normally within a few seconds. Streaming does not guarantee
immediate output: while the model sends no visible text, the last confirmed state stays on display. The complete
answer is validated and saved only after the final event. A dropped connection takes over no partial answer and starts
no hidden retry.

The shared ring buffer `runs/<run_id>/model_trace.json` holds at most 20 lines in total, also with parallel calls
(`model_trace.MAX_LINES`). Older lines are replaced and long ones shortened; credentials are removed before saving
([Secrets and keys](SECURITY.md#secrets-and-keys)). Finished, validated research and script results are saved as
usual. Intermediate states in the live window are unreviewed and grant no approval.

Receiving and readable progress are shown separately. When fragments keep arriving but no new readable text has
appeared for at least a minute, the Studio says so explicitly. Hidden JSON fields and format data do not count as new
content. This can hide an output loop; an active connection alone does not prove that the research progresses. New
calls also log character and whitespace counts, without saving the raw fragments.

### Diagnostics

Each call keeps cleaned technical diagnostics that survive a timeout or abort, and an answer the response contract
rejected next to its failure record ([Run folder and manifest](ARCHITECTURE.md#run-folder-and-manifest)). Calls started
before this recording existed have none of these records; earlier traces cannot be reconstructed. Log files and
failure tracebacks: [Logs and diagnosis](OPERATIONS.md#logs-and-diagnosis).

## Sequential or parallel

Under „Ausführung“ on the settings page, text work („Textausarbeitung“) and recording („Vertonung“) are set to
sequential or parallel separately. Parallel text means at most five episodes at a time in script writing, dialogue
polishing or quality review (also across the subscriptions) and at most five independent research sub-questions at a
time (`execution.MAX_PARALLEL_TEXT`); within one episode's review, the first-time reader and the editorial review ask
at the same time, and the series review's correction revises all affected episodes of a round at once.

How sub-questions wait for their prerequisites: [Independent sub-questions in
parallel](RESEARCH.md#independent-sub-questions-in-parallel). The teaching plan stays in order, so presupposed examples
stay consistent. The mode is stored when a job starts, and resuming keeps it; the default is sequential. Provider limits
and the approved model-call budget still apply.

The job status names all running sub-questions (for example „5 Teilfragen in Arbeit: …“ (5 sub-questions in
progress: …)) and marks them in the question list. When several model calls run at once, the page names each with its
sub-question and duration, and every line of the live output carries its sub-question or episode. The script work
page shows all episodes in progress with their time. If one sub-question or episode stops, the others still finish
their running step; the page says so meanwhile, and only then does the job stop. After the stop no sub-question counts
as in progress; started ones continue on resume.

Parallel Gemini recording, its limits and the audio queue:
[Parallel recording and queue](AUDIO.md#parallel-recording-and-queue). A stopped episode stays visible in header,
navigation and overview while the others keep recording, and can be resumed in a free slot beside them. A stopped text
job instead waits until the recording is finished; the page says so instead of offering a button that would fail. The
recording page adds finished takes without rebuilding a running player.

Text jobs run one at a time per project, and at most three projects work at the same time
(`studio.MAX_PROJECT_JOBS`). Qwen records one episode at a time, and only in one project at a time, because it needs
the local graphics card. The locks that protect a recording and what an episode's stop button ends:
[Parallel recording and queue](AUDIO.md#parallel-recording-and-queue). Project and job states are stored under the
respective project.

## Review notes on the reading page

The reading page of a published episode has a collapsible field **„Hinweise der Prüfungen“** (review notes). It shows
what the reviews said but did not block: the limitations each review gives its own verdict, the explanation gaps the
reviewers classified as not necessary, with their reasoning, and the deterministic notes about repeated definitions,
repeated references to invented examples, a long cold open and excess length. None of this prevents publishing; it is
reading material for your review.

## Downloads

On the „Vertonung“ page, under „Alle fertigen Folgen anhören“, **„Gesamten Podcast herunterladen“** (download the
whole podcast) offers a ZIP with the individual MP3s in episode order: the most recently published takes including all
parts, without new speech generation or conversion. The ZIP carries a shortened podcast title; inside, files are named
`Folge 01 - Episodentitel.mp3`, with several parts extended by `Teil 01 von 02`. The podcast title is not repeated
inside, so ZIP folder and file name together stay short when unpacking on Windows. Single downloads also get a short
podcast title. Every overview project card offers the same ZIP as „Podcast herunterladen“ once one episode is
recorded. Long titles are shortened at word boundaries where possible; umlauts are kept, and no ellipsis is appended.

The names follow the project's content language, not the interface language (why: D-153). A German project's names
are the ones above and end the ZIP's name in `Alle Folgen` or `2 von 6 Folgen`; an English project's files read
`Episode 01 - Title.mp3` and `Part 01 of 02`, and its ZIP ends in `All episodes` or `2 of 6 episodes`. The server
sends these names with every download, and the project page and overview cards carry them for their links
(`download_names` per episode, `download_zip`; the page builds German names itself only for an older server). Where an
English word is longer, that much comes off the title, so a name keeps its length
([Languages](ARCHITECTURE.md#languages)).

An episode whose companion kit was made for exactly the recording in the ZIP also brings its kit as
`Folge 01 - Begleitmaterial/` (`Episode 01 - Companion kit/` in English) with `description_short.txt`,
`description.txt` and `sources.md`. The whole podcast's kit, while it covers exactly the published episodes and their
latest recordings, comes as `Begleitmaterial Podcast/` (`Podcast companion kit/`) with `description_short.txt`,
`description.txt`, `sources.md` and `transcript.md`, the transcript of every episode.

**„Begleitmaterial“** (companion kit) on the „Vertonung“ page shows, per episode, the short description and the
episode description for Spotify and Apple Podcasts, each with **„Kopieren“** (copy), the character count against the
4,000 allowed and how many sources fit into the text. **„Begleitmaterial erstellen“** (create) makes it, **„Neu
zusammenstellen“** (rebuild) takes the newest recording's chapters without a new model call, **„Neu formulieren“**
(reword) asks the text model again, **„Für alle Folgen“** does every episode. How the kit is made:
[Exports and listening sheet](AUDIO.md#exports-and-listening-sheet) (why: D-139).

**„Begleitmaterial für den Podcast“** (the podcast's companion kit) below it does the same for the whole podcast:
the short description and the podcast description for the show page, each with **„Kopieren“**, how many episodes the
transcript covers and how many of them are recorded, and how many sources the list holds; transcript and sources lie
in `publish/` and in the ZIP. **„Transkript herunterladen“** and **„Quellen herunterladen“** fetch `transcript.md` and
`sources.md` on their own, without the MP3s of the ZIP (`/download/<project>/kit/<file>`, named
`Podcasttitel - transcript.md`). The route serves only the four files the ZIP carries and only while the kit is
current; otherwise it refuses with `missing_kit`. **„Begleitmaterial für den Podcast erstellen“** makes it with one text-model call,
**„Neu zusammenstellen“** takes the newest scripts and recordings without a new call, **„Neu formulieren“** asks
again. After a new recording or script the panel says the kit is outdated and offers the rebuild (why: D-165).

For an incomplete series the link reads **„Fertige Folgen herunterladen · ZIP · 2 von 6 Folgen“** (download finished
episodes · ZIP · 2 of 6 episodes). Older script or voice states stay marked as such. If a file of a published episode
is missing, the ZIP download aborts with an understandable message instead of silently leaving parts out.

The ZIP is built only for the download and then removed from temporary storage; meanwhile the top of the page shows
„Das ZIP wird zusammengestellt“ (the ZIP is being assembled), and a refusal appears as a message. Research and script
work write no takes, so the ZIP and single downloads stay available during such a Studio job; only during a Qwen
recording or a command-line run does the download wait.
