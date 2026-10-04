---
title: Studio
doc_type: frontend
status: current
last_reviewed: 2026-10-04
covers:
  - src/podcast_automate/studio.py
  - src/podcast_automate/studio_worker.py
  - src/podcast_automate/studio_settings.py
  - src/podcast_automate/studio_progress.py
  - src/podcast_automate/studio_messages.py
  - src/podcast_automate/studio_allowances.py
  - src/podcast_automate/studio_scripts.py
  - src/podcast_automate/studio_trash.py
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
[Teaching](TEACHING.md) and [Audio](AUDIO.md). The UI is German; labels are quoted in German with an English gloss on
first use.

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

## The guided flow

A project runs through six steps, each a page in the navigation:

1. **„Auftrag & Stimmen“** (brief & voices): a single editorial partner asks in the chat for what it needs and proposes
   a brief. It asks early what the series is for (**„Ziel der Serie“** (series goal): „Verstehen“, „Bewerten“,
   „Anwenden“ (understand, evaluate, apply), each weighted 0–3) and, for fast-moving fields such as AI practice, how
   current the sources must be (**„Aktualität der Quellen“** (source recency): the last N months); both appear in the
   summary. It treats attachments and posts as pointers to names, works and tools, not as claims the episodes must
   check. You state topic, prior knowledge, depth and language in your own words; text model, audio provider, voices
   and execution are set on the [Settings page](#settings-page), not in the chat. **„Diese Auswahl übernehmen“**
   (apply this selection) saves the reviewed summary. The chat grants no plan or audio approval. Stored voice samples
   stay reachable through the collapsible voice library. Credentials belong only in the key field of the settings page.
2. **„Recherche“** (research): searching, downloading and evaluating sources and checking the dossier run automatically
   after the start; the result is readable here. Then the table of contents is drafted; an existing plan can be opened
   directly. A new research is marked as a restart of its own.
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
   commented on and then explicitly approved for audio.
6. **„Vertonung“** (recording): the checkbox confirms the script state you read, with the provider and voices shown,
   and **„Audio erzeugen“** (generate audio) starts the recording. Finished takes are listed under **„Alle fertigen
   Folgen anhören“** (listen to all finished episodes), which **„Podcast anhören“** (listen to the podcast) on the
   overview leads to. The page, including the size estimate for Gemini: [Recording flow](AUDIO.md#recording-flow).

The pronunciation check, the panels for spoken forms, host names and editorial notes, re-rendering one segment and the
listening review are described in [Spoken forms and pronunciation](AUDIO.md#spoken-forms-and-pronunciation),
[Re-rendering one segment](AUDIO.md#re-rendering-one-segment) and
[Exports and listening sheet](AUDIO.md#exports-and-listening-sheet).

Which approval each step needs and what it is bound to: [Human approvals](BUSINESS_LOGIC.md#human-approvals). Older
projects show their existing research, scripts and published audio files; **„Inhaltsverzeichnis entwerfen“** (draft
table of contents) creates a separate plan to review.

Automated tests cover plan approvals, stale text and voice states, assistant proposals without automatic project
changes, resume, the local HTTP access barriers, key handover, audio downloads with seek positions and the UI states
(see [Automated checks and early audio test](QUALITY.md#automated-checks-and-early-audio-test)). Automated content reviews
do not guarantee excellent narration; your reading stays deliberately part of the flow. A full run with a new topic and
a listening test remain the practical acceptance (see V-18).

## Attachments and provided works

### Attachments

At **„Neues Projekt“** (new project) → „Auftrag & Stimmen“ you can attach several **.md**, **.txt** or **.docx** files
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
| **„Textmodell“** (text model) | One of the presets; see [Choosing the text model](#choosing-the-text-model). |
| **„Audio“** | Provider, speech model (Gemini), the voices of host A and host B, the three pauses ([Pause minimums](AUDIO.md#pause-minimums)); for Gemini whether expression tags are set before recording. |
| **„Ausführung“** (execution) | See [Sequential or parallel](#sequential-or-parallel). |
| **„Ohne Rückfrage“** (without asking) | See [Pre-approvals](#pre-approvals). |
| **„Limits“** | Model calls per run, sources and search rounds per research, the time limit of one model call. |
| **„Claude“** | The switch for bought extra usage ([Studio settings](CONFIGURATION.md#studio-settings)). |
| **„OpenRouter-Key“** | The key for OpenRouter text, Gemini audio and Jev: **„Key hinterlegen“** (store key), **„Sitzungs-Key entfernen“** (remove session key); handling in [Secrets and keys](SECURITY.md#secrets-and-keys). |

**„Einstellungen für alle Projekte speichern“** (save settings for all projects) saves the page. The chip at the top
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

**„Verbindungen prüfen“** (check connections) on „Auftrag & Stimmen“ shows both subscription logins and the quota
state; a job is ready to start as soon as one subscription is usable. What it checks with Gemini audio:
[Providers and models](PRODUCT.md#providers-and-models).

The job status shows the choice the run saved, even if other settings were saved for new jobs since; below it,
**„Aktueller Anbieter“** (current provider) shows the quota state and reset of both subscriptions from the last model
call. Older jobs without an explicit choice appear as „nicht festgelegt“ (not set) and get no new defaults when resumed.
Where a run stores its choice: [Run folder and manifest](ARCHITECTURE.md#run-folder-and-manifest).

### Continuing with another provider

Every script and research job can continue with another text provider at any time: below the job's saved text choice,
**„Weiter mit …“** (continue with …) offers „Claude, sonst Astra (xhigh)“, „Astra (xhigh), sonst Claude“, „Nur
Claude“, „Nur Astra (xhigh)“ and „OpenRouter · bezahlt pro Aufruf“ (OpenRouter · paid per call), the last with a model
selection; **„Übernehmen“** (apply) saves the choice. If a job with a fixed provider stops because its subscription is
exhausted, the hold card offers the other one: **„Mit Astra (xhigh) fortsetzen“** (continue with Astra) for Claude,
**„Mit Claude fortsetzen“** (continue with Claude) for Codex. When the switch applies and what it keeps: [Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection).

## Overview, navigation and hold cards

The overview starts with **„Wartet auf dich“** (waiting for you: approvals and paused jobs) and **„Läuft gerade“**
(running now). Below, each project has a row with the bar of the six steps, **„Projekt öffnen“** (open project) and,
once takes are finished, „Podcast anhören“ and **„Podcast herunterladen“** (download podcast, ZIP).

The navigation shows each area's state: present, to approve (▲), in progress or pending. The running step shows its
elapsed time; a research plan waiting for approval shows the projection in hours. The header names the job or its stop
reason in one line, with stop, resume (only where it can help) and the jump to the page where something is to be
decided; running recordings are listed beside it. Stopped recordings that share one reason name it there, as in
„14 angehalten: OpenRouter verweigert den Zugriff“, with **„Alle 14 fortsetzen“** (resume all) and, for a key stop,
a button to the „OpenRouter-Key“ panel; until 2026-10-04 only the job list on the recording page named the reason. The browser tab shows ● for a running job, ▲ for a decision and ! for
a stop; the overview shows the number of waiting projects.

The collapsed **„Maschinenraum“** (engine room) at the bottom holds only telemetry: **„Kurzbericht“** (status brief),
live output, times, budget, model choice and, for research and script runs, the **„Produktionsbericht“** (production
report: calls, model time and share per stage, the same numbers per prompt version, providers, stops per stage and
approvals; `production_report.py`). It displaces no page and holds no action that is not also on the page. Each step
shows its own job details: the research its question status with the plan approval, the script work its progress and
teaching plans, the recording the jobs per episode.

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
- **„Wartet auf Kontingent“** (waiting for quota): after the reset, „Fortsetzen“ or the automatic resume.
- **„Braucht Einrichtung“** (needs setup): first fix something outside the Studio (login, FFmpeg, OpenRouter credit),
  then „Fortsetzen“. If an OpenRouter key is missing, the card holds the input field and resumes once the key is
  stored. With „Automatisch“, an expired login or a too old Claude CLI stops the job only when the other subscription
  cannot continue either. Where the fix is another text model or audio provider, the card offers
  **„Einstellungen öffnen“** (open settings).
- **„Deine Entscheidung“** (your decision): research plan, blocked sub-questions or a higher call limit, for research
  and script runs alike; „… erhöhen und fortsetzen“ (raise … and resume) approves and resumes in one click.
- **„Neustart nötig“** (restart needed): this run cannot continue, for example after an unsupported review objection, a
  checkpoint that no longer fits, changed inputs, a permanently contradictory table of contents or a teaching plan
  still incomplete after the automatic corrections. The card offers no „Fortsetzen“ but the way forward (research anew,
  new table of contents, redraft with a note, approve again) and says what stays readable. For the teaching plan this
  is **„Lehrkonzept mit Hinweis neu entwerfen“** (redraft teaching plan with a note): your note goes into a new draft of
  this one episode with new repair rounds, the run resumes right away, and the approved table of contents stays.

Messages appear in German: the Studio translates the pipeline's review texts, which the model gets in English, and
replaces command-line hints, local paths and internal identifiers. The original wording and the stop code are under
**„Technische Details“** (technical details); a code without its own card appears as „Angehalten“ with its code. For
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

The open Studio server resumes some stops by itself, at most three times in a row (`studio.MAX_AUTO_RESUMES`; your own
„Fortsetzen“ starts the count again):

- **Quota.** A research, script or Qwen job paused by a subscription limit resumes at the reset of the subscription
  that ran out; without a known reset, after the waiting times in [Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection).
- **Transient technical stops.** Research and script runs stopped by a transient technical error (time limit, call
  without output, failed Claude or Codex call, Claude answer in the wrong format, OpenRouter unreachable or
  unavailable; `studio.TRANSIENT_STOPS`) resume 10, 30 and 90 minutes after the stop
  (`studio.TRANSIENT_BACKOFF_MINUTES`) (why: D-105).

The hold card names the time and the attempt and says when the attempts are used up. An expired login and exhausted
OpenRouter credit do not come back by waiting (`studio.NO_AUTO_RESUME`); the Studio never resumes these, decisions or
limits by itself. Fresh attempts and a higher call limit without asking come only through the
[pre-approvals](#pre-approvals). No automatic resume passes the research plan approval
([Human approvals](BUSINESS_LOGIC.md#human-approvals)). Conversations, voice samples and Gemini episodes are not
resumed automatically, and the Studio announces nothing there; for missing voice samples see
[Gemini via OpenRouter](AUDIO.md#gemini-via-openrouter).

### Pre-approvals

Under „Ohne Rückfrage“ on the settings page, **„Neue Anläufe je Lauf“** (fresh attempts per run) and **„Aufruflimit
erhöhen je Lauf“** (raise the call limit per run) set what the Studio may give a stopped run by itself. The job summary
on „Auftrag & Stimmen“ shows them; a hold card announces when one is about to be used. The choices, when the scheduler
applies them and which decisions always stay yours: [Budgets](BUSINESS_LOGIC.md#budgets).

### Research decisions and limits

- **Research plan.** Every Studio research job waits before the first sub-question until you click
  **„Rechercheplan freigeben und starten“** (approve research plan and start); see
  [Scope check and plan approval](RESEARCH.md#scope-check-and-plan-approval) and, for the gate rule,
  [Human approvals](BUSINESS_LOGIC.md#human-approvals).
- **Blocked sub-questions.** The „Recherche“ page shows each guiding question's review state and offers the decisions a
  blocked sub-question needs (the advisor's recommendation, **„Noch einmal versuchen“** (try again), accepting a gap,
  raising an exhausted limit, disputes in the overall review, access gaps, finishing with remaining objections); see
  [Blocked sub-questions and decisions](RESEARCH.md#blocked-sub-questions-and-decisions).
- **Jev.** Under „Lückenprobe“ (gap probe) in the job summary on „Auftrag & Stimmen“, **„Jev dazunehmen“** (add Jev)
  and **„Jev ausschalten“** (switch Jev off) switch Jev in this project's gap probe on and off; see
  [Gap probe](RESEARCH.md#gap-probe).
- **Starting library.** „Recherche neu beginnen“ (start research anew) preselects offering the previous research's
  sources as a starting library; see [Starting and resuming](RESEARCH.md#starting-and-resuming).

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

An OpenRouter key stored in the Studio lived only in the old server's memory and must be entered again
([Times and connection](#times-and-connection)); until then queued Gemini episodes show „wartet auf den
OpenRouter-Key“ (waiting for the OpenRouter key) instead of waiting for a free slot.

While no key is available but something needs one, every page shows **„OpenRouter-Key fehlt“** (OpenRouter key
missing) at the top, also on a page loaded fresh after a restart (`Studio.key_reminder`, `studio.key_needs`): Gemini as
the audio provider, Jev in the gap probe, or an OpenRouter text model, each with its projects and what happens without
the key (Gemini recordings wait; new script runs search gaps by words only, or stop where Jev was switched on by hand;
OpenRouter jobs stop). The note holds a key field; on the settings page it points to the „OpenRouter-Key“ panel. A
stored key ends it at once. If the new server fails before its
own log starts, the reason is in `.studio/relaunch.log` (why: D-117). The old server's console window can then be
closed. See also [Update the Studio](OPERATIONS.md#update-the-studio).

## Progress and telemetry

### Times and connection

During the script work the engine room shows separately the total run time since the start or resume, the duration of
the current model call and the age of the last saved model result. „Letzte Änderung im Lauf“ (last change in the run)
says when the run itself last saved something.

If the Studio server does not answer for more than 30 seconds, the Studio marks the display as the state of a given
time, the header shows „Keine Verbindung“ (no connection) and the run indicator stops pulsing; this does not mean the
model crashed. If the running server rejects a request, its message appears instead of a connection notice. Every
answer names the running server instance: after a server restart the page reads session and key state again at the
next poll or click and says when an OpenRouter key stored earlier was lost with the old server.

The page never polls twice at the same time: a project page every 2.5 seconds, the overview every 10 seconds and a
hidden tab once a minute (the last two are `POLL_MS` in `web/app.js`); a tab that becomes visible again polls at once
(why: D-106). The overview builds each project card from a few cached file reads instead of the full project page
(why: D-107). Short file access errors are retried at the next poll.

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
three consecutive failures; the production budget stays unchanged. A failure of the status model does not stop the
job. When the job is stopped or ended, its status process ends too; older reports stay readable.

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
messages, expanded at first. Structured answers are reduced to their content as they arrive, for example „Vorhandene
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

For an incomplete series the link reads **„Fertige Folgen herunterladen · ZIP · 2 von 6 Folgen“** (download finished
episodes · ZIP · 2 of 6 episodes). Older script or voice states stay marked as such. If a file of a published episode
is missing, the ZIP download aborts with an understandable message instead of silently leaving parts out.

The ZIP is built only for the download and then removed from temporary storage; meanwhile the top of the page shows
„Das ZIP wird zusammengestellt“ (the ZIP is being assembled), and a refusal appears as a message. Research and script
work write no takes, so the ZIP and single downloads stay available during such a Studio job; only during a Qwen
recording or a command-line run does the download wait.
