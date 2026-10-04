---
title: Audio
doc_type: business-logic
status: current
last_reviewed: 2026-10-04
covers:
  - src/podcast_automate/audio.py
  - src/podcast_automate/speech.py
  - src/podcast_automate/parallel_speech.py
  - src/podcast_automate/episode_audio.py
  - src/podcast_automate/expression.py
  - src/podcast_automate/spoken_forms.py
  - src/podcast_automate/qwen_worker.py
  - src/podcast_automate/voice_samples.py
  - src/podcast_automate/transcription_check.py
  - src/podcast_automate/prompts/audio_expression.txt
  - src/podcast_automate/execution.py
  - src/podcast_automate/studio.py
  - src/podcast_automate/studio_worker.py
  - src/podcast_automate/cli.py
  - scripts/gemini-tags-test.py
---

# Audio

How an approved script becomes an MP3 episode with chapters, transcript and show notes, recorded either locally
with Qwen or with Gemini via OpenRouter.

## Recording flow

Audio is produced only after an explicit approval and passed blocking checks; what the approval is bound to and
which checks must pass: see [Human approvals](BUSINESS_LOGIC.md#human-approvals).

### In the Studio

The guided flow ends on the page **„Vertonung“** (recording):

- The checkbox confirms the script state you have read, with the provider and the voices shown.
- For Gemini, the approval card, the batch approval and the queue show the scope beforehand: the minutes estimated
  at the planning rate ([Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length)) and the characters
  of the spoken text, without a price, because the price cannot be checked offline.
- Only **„Audio erzeugen“** (generate audio) starts the recording; when the episode has to wait for a free place, the
  button reads **„Freigeben und einreihen“** (approve and queue, see [Queue](#queue)).
- For Gemini, progress, stopping and resuming are separate per episode.
- All finished recordings are listed under **„Alle fertigen Folgen anhören“** (listen to all finished episodes) with
  a player and an MP3 download, also while further episodes are being recorded; **„Podcast anhören“** (listen to the
  podcast) on the overview leads there.
- Earlier recordings stay available and are marked as an earlier version when text, provider or voices have changed
  since.

The two hosts need two different voices. Switching the provider does not carry an earlier Qwen approval over to paid
Gemini calls. A resumed audio run keeps the model, voices and text it started with; only the OpenRouter key may be
renewed (see [Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)).

On the command line, `pla audio` records one reviewed episode with Qwen (see [Commands](PRODUCT.md#commands)).

### Segments, cache and assembly

- The chosen provider renders each stored speaker segment separately; the whole episode is never produced in a
  single TTS call. A longer turn can be split into several segments of the same speaker.
- The structured direction controls speaker assignment, pauses and chapters; assembly orders the segments, sets the
  pauses and builds the episode automatically, so nobody has to operate an audio editor.
- FFmpeg and ffprobe do the assembly and the measurement. Unintended silence at the edges may be corrected; speech
  sounds and planned thinking pauses must be kept (tagged pauses: see [Pause trimming](#pause-trimming)).
- The cache key covers provider, model revision, voice, spoken segment text, pronunciation and TTS settings,
  including any seeds used. Only changed or missing segments are rendered again. Failed episodes can be resumed one
  by one.
- Real speaker overlaps, scene-wide stage directions and ElevenLabs TTS are not implemented; music and other audio
  extras: see [Out of scope](PRODUCT.md#out-of-scope).

### Errors

Missing or damaged files, empty output, conspicuous silence, level errors and an implausible duration lead to a
targeted check and a limited number of repair attempts. An error that persists is reported with the segment id and
blocks the affected final export:

- For Gemini, every take passes the [plausibility check](#plausibility-check-of-gemini-takes).
- A segment file that is missing or silent, or whose hash, voice or text does not match the job, stops the run with
  `invalid_audio`; a missing or silent one is named by its segment id.
- A failed loudness measurement stops with `loudness_failed`, an episode or part longer than allowed with
  `duration_exceeded`.

### Output format and chapters

- The standard format is MP3 (192 kbit/s), 44.1 kHz, stereo, loudness-normalised to -16 LUFS (see
  [Loudness](#loudness)).
- Chapter marks are produced from the actual audio timeline and embedded in the MP3.
- An episode too long for one audio part ([part length](BUSINESS_LOGIC.md#episode-and-series-length)) is split
  automatically into the fewest and most evenly sized consecutive parts. Chapters stay together unless one is longer
  than a part; a single segment longer than a part stops with `duration_exceeded`.

### Production estimate and logging

Before production, the scope is shown and, as far as the pilot allows deriving them, render time and storage needs
(the Studio's estimate: see [In the Studio](#in-the-studio)). The run logs the actual render time, cache use and the
subscription usage data that is available; remaining quota that is unknown is shown as unknown. Estimated API dollar
values reported by a CLI are not subscription costs that were actually charged.

## Qwen

- Qwen3-TTS runs locally in a separate environment (`.venv-tts`) as its own worker process (`qwen_worker.py`).
- Model variant, model revision, voices and the AMD runtime were fixed and tested on the target computer on
  2026-09-13. Setup and pinned versions: [Set up local Qwen on Windows](OPERATIONS.md#set-up-local-qwen-on-windows)
  and [on macOS and Linux](OPERATIONS.md#set-up-local-qwen-on-macos-and-linux).
- `pla init` writes the Qwen revision this computer already uses into `runtime.tts_revision` instead of leaving
  `main` (why: D-099). When none is known, it says so, and the commit of the loaded model has to be entered in
  `project.yaml` before the first Qwen recording.
- The installed CustomVoice model has nine built-in voices (`speech.QWEN_VOICES`, see
  [Providers and models](PRODUCT.md#providers-and-models)).
- The language is part of a Qwen segment's cache key, as are the worker and package versions. Run again with the same
  settings, valid segments come from the cache and only the assembly runs again.

## Gemini via OpenRouter

In the Studio, choose **„Gemini TTS · OpenRouter“** and both voices in the **„Audio“** section of the
[Settings page](STUDIO.md#settings-page); the chat does not set them. The audio choice is a workspace setting
([Studio settings](CONFIGURATION.md#studio-settings)), saved separately from the text model (which can still be Codex
or Claude), and changes neither the reviewed script nor its research. **„Stimmen anhören“** (listen to voices) on the
project page compares the voices; the key goes into the **„OpenRouter-Key“** section of the settings page.

Gemini preselects an editable voice for the expert and one for the curious conversation partner. Models and the 30
voices (`speech.GEMINI_MODELS`, `speech.GEMINI_VOICES`): see [Providers and models](PRODUCT.md#providers-and-models).

### The speech request

- The adapter uses the documented binary endpoint `POST https://openrouter.ai/api/v1/audio/speech`
  (`speech.SPEECH_ENDPOINT`) with the chosen `model`, the approved text as `input`, the selected `voice` and
  `response_format: pcm`; no chat or JSON-schema call.
- The PCM is stored, as the model specification states, as 24 kHz, 16-bit mono WAV and exported as MP3 by the FFmpeg
  assembly. OpenRouter accepts only `mp3` and `pcm` for speech
  ([OpenRouter TTS documentation](https://openrouter.ai/docs/guides/overview/multimodal/tts),
  [model description](https://openrouter.ai/google/gemini-3.8-flash-tts)).
- The OpenRouter speech call takes one voice per request, so the recording renders the speaker segments, each with
  its own Gemini voice, and assembles them in script order. **Native two-speaker requests within a single Gemini
  call are not connected:** Google's own API supports them, but its separate request format is not carried over to
  OpenRouter without verification
  ([Google's multi-speaker documentation](https://ai.google.dev/gemini-api/docs/speech-generation#multi-speaker)).
- There is no editorial rule of 30 to 90 seconds and no forced change of speaker. Very large segments are split at
  sentence or word boundaries only to keep within the request size; every character and the speaker assignment are
  kept, and coherent monologues stay monologues.
- Text, language, voice, model and adapter version (`speech.SPEECH_VERSION`) determine the cache entry. Interrupted
  requests never land in the cache as finished audio.

### Keys, errors and limits of the check

- Key handling and redirects: see [Secrets and keys](SECURITY.md#secrets-and-keys).
- Error responses, empty audio, unexpected formats and damaged cache files are caught. A rejected request (400, 404,
  413, 422) stops with `openrouter_speech_request`; rate limits and transient errors are retried (see
  [Rate limits and transient errors](#rate-limits-and-transient-errors)).
- If the OpenRouter privacy setting (Zero Data Retention) excludes Google as a provider, the endpoint answers 404;
  the recording stops with `openrouter_privacy`, and the Studio names the setting at openrouter.ai/settings/privacy.
- The technical audio check cannot prove that every word was spoken correctly: a raw audio stream has no reliable
  word timestamps and no complete transcript check. Listening, pronunciation and possible omissions therefore stay
  part of the acceptance.

### Voice library

- **„Gemini-Stimmen zum Vergleichen“** (Gemini voices to compare) lists all 30 voices. **„Fehlende Hörproben
  erzeugen · API“** (generate missing voice samples · API) creates one short recording for each missing voice, in
  German or English according to the selected language; all voices read the same comparison text. The progress
  shows how many samples are stored.
- After an interruption, finished voices are kept; the same button continues with the missing ones and skips every
  complete recording. After a provider error, no further voices are requested automatically. New recordings use
  OpenRouter credit.
- **„▶ Play“** next to each finished voice plays only the stored MP3, without a model call or API key; another click
  pauses, and the player below has a timeline. Voice samples survive project switches and Studio restarts. A single
  missing voice can be created with **„Hörprobe erzeugen · API“** (create voice sample · API) next to the role
  selection. Loading the page and switching language or voice create no recordings.
- The shared library lies under `projects/voice-samples/gemini/`, excluded by the gitignore rule for `projects/`,
  with separate German and English libraries. Language, voice, model, comparison text and adapter version determine
  a recording; file checksums prevent reuse of damaged files. Matching WAV recordings already in individual projects
  are adopted without a new API call.
- Qwen voice samples are made by a script; see [Voice samples](OPERATIONS.md#voice-samples).

### Verification status

- On 2026-09-13 the German Gemini library with all 30 voices was created through the local Studio server: the
  existing Sadaltager recording came from its verified cache, the other 29 voices were generated over OpenRouter at
  the user's request. All 30 MP3 files were decoded and checked for valid durations.
- Automated tests run the real FFmpeg assembly on simulated API audio. They cover the speech call and its voices, WAV
  creation, splitting without text loss, cache reuse, API and format errors, the take check, retries after gateway
  errors and the shared rate-limit wait (`tests/test_speech.py`), loudness and pause trimming (`tests/test_audio.py`),
  unchanged scripts, separate provider choice, approvals, resuming after an API limit, voice-sample reuse without an
  API key, continuing after an error and **„▶ Play“** without a generation job. Qwen calls are blocked in the Gemini
  integration tests; the expression stage's text call is simulated (`tests/test_expression.py`).
- No speed comparison between Gemini and local Qwen is recorded: Gemini avoids the local GPU recording, but a
  concrete speed-up is not established (see V-19).
- A complete run with a new topic and a listening test remain the practical acceptance (see V-18); listening to the
  generated voices and episodes stays a human task.

## Expression tags

Every Gemini recording gets the stage **„Ausdruck“** (expression) before synthesis unless it is switched off (see
below) (why: D-090). The text model of the script run chooses from twelve kinds of inline tags in angle brackets and
sets them sparingly between the words, in at most about every fourth segment: for example a chuckle at a surprising
turn, a breath before a summary or a short pause before a key statement.

- Only `<short pause>`, `<long pause>`, `<breath>`, `<exhales>`, `<sigh>`, `<phew>`, `<laugh>`, `<chuckle>`,
  `<giggle>`, `<gasp>`, `<tsk>` and `<throat-clearing>` are allowed (`expression.ALLOWED_TAGS`). Google documents
  more; this selection suits a factual podcast.
- On 2026-09-29 all twelve were listened to in German over OpenRouter (`scripts/gemini-tags-test.py`, second run
  with all lines): they are performed, not read aloud, and all twelve stay allowed.

### The check

The check in `expression.py` keeps the layer strict:

- Without its tags, every segment must be word for word the text that would be spoken anyway.
- At most two tags per segment (`expression.MAX_TAGS_PER_SEGMENT`); per episode at most as many tags as a quarter of
  its segments, but always at least three. No tag inside a word.
- A `<long pause>` never opens a segment, because assembly pauses there anyway (why: D-094). The check removes it
  there without a correction call, and a segment left without a tag drops out of the layer
  (`expression.without_opening_pause`, prompt version `audio_expression.v2`).
- A deviating answer goes back with the objection twice (`research_patches.MAX_REJECTIONS`). If it stays invalid,
  the episode is recorded without tags, with the reason in `expression.json`. Expression is a finish and never stops
  a recording.

### Tags are part of reading the script

The tags belong to the script reading (why: D-091).

- **When:** once a script run is finished and the project records with Gemini and expression, the run's text model
  places the tags of every published episode, the episodes in parallel, each with a small call allowance of its own
  (one answer, at most two corrections). A failure there never undoes the finished script run.
- **Where:** `episodes/<episode>/expression.json`, bound to the script hash, with the spoken text they were placed on.
- **Reading:** on **„Skripte lesen“** (read scripts) the tags are marked in the text; where a segment's spoken form
  differs from the written text, they appear below it as „Gesprochen mit Ausdruck: …“ (spoken with expression).
- **Placing again:** the side column offers **„Ausdruck setzen“** (set expression) or **„Ausdruck neu setzen“** (set
  expression again), also **„Für alle Folgen“** (for all episodes); this asks the text model again.
- **Approval:** the approval on the page Vertonung carries the hash of the tags you read. If they were placed again
  afterwards, the Studio rejects the approval until the episode has been read again.
- **Recording:** it speaks exactly these tags, without a new model call; a segment whose spoken form has changed
  since is recorded without a tag. If no tags are placed yet, the recording places them itself, and the card says
  that they are then unread.
- **Unchanged:** script, transcript and approval text. A recording without expression still counts as current. To
  record without expression, clear **„Ausdrucksmarken vor der Vertonung setzen“** (set expression tags before
  recording) in the audio panel of the settings page, or, in a project without workspace settings, set
  `"expression": false` in `studio/audio.json` (see [Studio settings](CONFIGURATION.md#studio-settings)).

### Style directions

Gemini reads a written style direction in the text, such as „Sag es fröhlich:“ (say it cheerfully), aloud; the
OpenRouter endpoint does not pass through a speaking style outside the text (`speech_metadata`).
`scripts/gemini-tags-test.py` makes short comparison recordings with and without tags for listening; with `--neu`
only the further tags from line 07 on.

## Take checks, pauses and loudness

### Plausibility check of Gemini takes

Every Gemini take passes a plausibility check (`speech.take_defect`) (why: D-095):

- A take without audible speech fails.
- Every silence over 2.5 seconds (`speech.HEALTH_SILENCE_SECONDS`) needs a pause tag in the spoken text.
- From 80 spoken characters on (`speech.HEALTH_MIN_CHARACTERS`; expression tags do not count), the speaking rate
  must lie between 8 and 25 characters per second (`speech.HEALTH_RATE`), measured from the first to the last
  audible sound without pauses over one second.
- Silence means 20 ms windows whose peak stays under -45 dBFS (`audio.SILENCE_WINDOW_SECONDS`,
  `audio.SILENCE_LEVEL`).

A failed take is requested once more. If the second one fails too, the recording stops with `invalid_speech` and
names the segment id; finished segments stay stored, and resuming records this segment again. A cached take that
fails the check is discarded and recorded again, never reused. Qwen takes do not go through this check.

### Pause trimming

In segments with a pause tag, assembly shortens silences over 1.5 seconds to 1.2 seconds
(`audio.TRIM_ABOVE_SECONDS`, `audio.TRIM_TO_SECONDS`) (why: D-096); other segments stay as recorded. The time
removed is in `audio_report.json` (`trimmed_silence_seconds`) and per segment in `timeline.json`.

### Pause minimums

The three pause values are minimum pauses for the same voice, for a change of voice and at a chapter boundary
(`speech.PausePolicy`, default 250, 450 and 900 ms); a longer planned pause is kept. They are set on the
[Settings page](STUDIO.md#settings-page), and the approval card shows them under „Pausen“ (pauses).

- Assembly and the split into parts compute with the same applied pauses, so an episode near the length limit of a
  part is split before assembly instead of failing only after the paid recording.
- Changed pauses are audible and therefore need a new audio approval.
- The default values are not stored in approvals and run inputs; only a deviating pause policy is stored there and
  changes the input hash.

### Loudness

Loudness is matched with linear gain to -16 LUFS and a true-peak limiter with a ceiling of -2 dBFS
(`audio.LIMITER_CEILING_DB`) (why: D-097). `audio_report.json` records the method
(`loudness_mode: linear_gain_limiter`), the gain (`gain_db`) and the loudness and peak measured on the finished MP3
(`output_loudness`).

## Spoken forms and pronunciation

Pronunciation and voice consistency are judged in the audio pilot, supported by the project's spoken-forms table
(`studio/spoken_forms.json`), per-segment overrides and [re-rendering single segments](#re-rendering-one-segment).

On the audio page, **„Aussprache prüfen“** (check pronunciation) sits directly above the approval checkbox; the two
collapsible panels **„Sprechformen und Hostnamen“** (spoken forms and host names) and **„Redaktionelle Notizen“**
(editorial notes) sit next to it in the side column. Host names and notes are part of the script work
([SCRIPTS](SCRIPTS.md#host-names-and-style-notes)).

### Pronunciation report

**„Aussprache prüfen“** lists the words a voice reads by its own rule: multi-digit numbers, abbreviations, version
and model names such as `V3.2` or `H800`, and words with foreign characters.

- The server computes the list without a model call from the published text, the spoken-forms table and the
  per-segment spoken forms, so it is ready before the first recording and should be read before the approval.
- The list describes the text actually spoken: an entry in the table makes the word disappear from it.
- After a recording, the same report is also stored in `reports/<episode>_audio.json`.

### Spoken-forms table

**„Sprechformen und Hostnamen“** stores one table per project, one line per entry in the form `written = spoken`
(in the UI: `geschrieben = gesprochen`).

- Every recording then gets two fields: `text` stays the reviewed script text for transcript, hash and approval;
  `spoken_text` is what the voice hears, present only where it differs from the text.
- A segment without a spoken form is therefore stored and found in the cache exactly as before spoken forms
  existed; no recording already made is paid for twice.
- A spoken form never changes the script or a script hash.

### Replacement rules

The replacement is case-exact and works on whole words.

- A hyphen, a dash and a slash separate words: the entry `KL` reaches „KL-Abweichung“, the entry `H800` also
  „H800-GPUs“.
- A dot between characters does not separate: the entry `V3` leaves „V3.2-Exp“ unchanged, the entry `1.000` leaves
  „1.000.000“ unchanged; the entries `V3.2` and `1.000.000` exist for that.
- The report splits the text by the same rule, so every reported word can be fixed with exactly one entry.
- When several entries match, the longest wins, and no replacement is replaced a second time.

### Per-segment spoken form

On the reading page, every spoken passage of a published episode with audio has its own **„Sprechform“** (spoken
form). The field starts with the text the table produces for this segment; saving it unchanged creates no override,
and the table keeps applying. A different text applies only to this segment and takes precedence over the table.
The overrides are stored under `spoken_overrides` in `episodes/<episode>/audio_review.yaml`.

### Back-transcription

A supplementary local back-transcription to detect omissions and repetitions is prepared as a deterministic
comparison (`transcription_check.py`) but not connected to any recognizer (see [Roadmap](PRODUCT.md#roadmap)). It
does not guarantee error-free pronunciation; its findings are a reason to listen to a segment, never a verdict, and
it never blocks.

## Re-rendering one segment

**„Nur diesen Abschnitt neu rendern“** (re-render only this segment) sends the job with the flag `rerender` instead
of a new approval:

- The Studio checks by the same rule as the recording whether a stored approval exists for exactly this script hash
  with the chosen provider and voices; otherwise it rejects the job with a pointer to the audio page
  (`audio_approval_required`).
- Script hash, reading view, job and audio choice are checked against the displayed state, as with every recording;
  for Gemini with expression also the tags you read (the hash of `expression.json`). If the expression was placed
  again since you read it, the Studio rejects the re-render and asks you to read and approve again.
- The run renders only the changed segment again; all others come from the cache.
- The run's approval receipt names the stored approval, not a new decision.
- The deviations then appear in the export report and in the show notes.

## Parallel recording and queue

With the [parallel mode](STUDIO.md#sequential-or-parallel) selected, Gemini audio starts every approved episode at
once as its own job, at most 30 at the same time across the Studio (`execution.MAX_PARALLEL`) (why: D-092); each
episode still needs its own script approval. In sequential mode, one recording per project runs at a time; Qwen always
records one at a time. Within an episode, the speech segments go to OpenRouter one after another. Parallel recordings
share the Gemini cache with a lock per segment, so no cache entry is generated twice.

### Rate limits and transient errors

- When the provider reports a request limit (HTTP 429), all running recordings wait together, also across projects
  (`projects/.gemini_throttle.json`, kept under a file lock by `speech.shared_throttle`) (why: D-093): as long as
  the provider asks with `Retry-After`, otherwise 5, 10, 20, 40 and twice 60 seconds (`speech.RATE_LIMIT_RETRIES`).
  A 429 in one project therefore also slows the parallel recordings and voice samples of the others. Only after
  about three minutes without success does the episode stop.
- A gateway or overload answer (502, 503, 504), a broken transfer or a reset connection is requested twice more,
  after 3 and 6 seconds (`speech.TRANSIENT_RETRIES`, `speech.TRANSIENT_BACKOFF_SECONDS`, `speech.TRANSIENT_CODES`),
  before the episode stops (why: D-098). A connection error that persists stops with `openrouter_connection`.
- When an error persists, the recording stops; finished segments stay stored, and the user resumes the run
  explicitly.

### Queue

- An approval for which no place is free (all places taken, or a text job of the project is running) is not refused
  but queued (`studio/audio_queue.json`).
- The Studio's scheduler starts queued episodes in the order of their approvals as soon as a place is free and the
  OpenRouter key is present; after a restart, an episode otherwise shows „wartet auf den OpenRouter-Key“ (waiting
  for the OpenRouter key).
- Before starting, the scheduler checks script, reading view, voices and expression again; if something has
  changed, the episode stays in the queue with the reason.
- The page Vertonung shows the queue with **„Entfernen“** (remove) and **„Alle gelesenen Folgen freigeben“**
  (approve all read episodes): one checkmark confirms that all listed scripts, including their expression, have been
  read, and approves them in one step. As many start at once as places are free; the rest is queued.
- The Studio must stay open for this.

### Locks and stopping

- The project lock prevents changes by other CLI jobs during a recording; the same episode is also locked against a
  double start (`episode_busy`).
- An episode's stop button ends only its own process. Closing the browser or stopping the server: see
  [Stopping and resuming](STUDIO.md#stopping-and-resuming).

## Exports and listening sheet

Each recording run writes its export to `exports/<episode>/<run_id>/`:

| File | Content |
| --- | --- |
| `audio.mp3` | The episode with embedded chapters (ID3v2.3) |
| `chapters.json` | Chapters measured at assembly, and those read back from the MP3 (`embedded`) |
| `timeline.json` | Start, speech end, end, pause and trimmed silence of every segment |
| `audio_report.json` | Duration, format, loudness and pause data; `speech_quality_verified: false` |
| `transcript.md` | The approved text with host labels, in script order |
| `README.md` | Links to the parts with their duration, the voices and any deviating spoken forms |
| `playlist.m3u` | The parts in order |
| `show_notes.md` | Chapters with timestamps from the measured assembly, the voices, and pronunciation notes for segments with a deviating spoken form |
| `listening_sheet.md` | The listening sheet, see below |

An episode recorded in parts has the first five files once per part in `part_01/`, `part_02/` and so on; the other
four cover all parts. The run also writes `reports/<episode>_audio.json`, `episodes/<episode>/audio_latest.json`
(status `awaiting_listening_review`, pronunciation report, spoken-form overrides, chapters and parts) and
`pronunciation.json` in the run folder.

The export never publishes anything (see [Source rights and privacy](SECURITY.md#source-rights-and-privacy)). The
technical reports do not claim a passed listening review: pronunciation, naturalness and voice consistency are
judged from the MP3. Downloads of single episodes or the whole podcast as a ZIP: see [Downloads](STUDIO.md#downloads).

### Listening review

After listening, you enter the **„Hörprüfung“** (listening review) with **„Hörprüfung eintragen“** (enter listening
review).

- `listening_sheet.md` lies in the export next to the MP3 and has columns for unclear points, lost attention and
  pronunciation. Each part's times start at 0:00, so for an episode recorded in parts the sheet names the part of
  every row in a column of its own.
- Only a human sets the listening review; no program step does. It is stored as `human_listening_reviewed` with
  `listening_note` in `episodes/<episode>/audio_review.yaml`; publishing a new recording of the episode clears an
  earlier listening review and its note.
- The listening review and the per-segment spoken form can be entered while other episodes are being recorded. Only
  while the same episode is being recorded does the Studio refuse the entry (`episode_busy`); it goes through once
  that recording has finished or has been stopped.
