---
title: Audio
doc_type: business-logic
status: current
last_reviewed: 2026-10-07
covers:
  - src/podcast_automate/audio.py
  - src/podcast_automate/speech.py
  - src/podcast_automate/google_speech.py
  - src/podcast_automate/parallel_speech.py
  - src/podcast_automate/episode_audio.py
  - src/podcast_automate/expression.py
  - src/podcast_automate/spoken_forms.py
  - src/podcast_automate/qwen_worker.py
  - src/podcast_automate/voice_samples.py
  - src/podcast_automate/content_text.py
  - src/podcast_automate/transcription_check.py
  - src/podcast_automate/prompts/audio_expression.txt
  - src/podcast_automate/prompts/audio_expression_backchannels.txt
  - src/podcast_automate/publish_kit.py
  - src/podcast_automate/prompts/publish_kit.txt
  - src/podcast_automate/prompts/podcast_kit.txt
  - src/podcast_automate/execution.py
  - src/podcast_automate/studio.py
  - src/podcast_automate/studio_worker.py
  - src/podcast_automate/cli.py
  - scripts/gemini-tags-test.py
---

# Audio

How an approved script becomes an MP3 episode with chapters, transcript and show notes, recorded locally with Qwen,
with Gemini through Google's own API (two hosts per request, the default for Gemini since 2026-10-06), or with
Gemini via OpenRouter (one segment per request).

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
- All finished recordings are listed under **„Alle fertigen Folgen anhören“** (listen to all finished episodes), one
  row per episode with its length, its state and an MP3 download, also while further episodes are being recorded. ▶
  plays it in one player docked at the foot of the page, with speed, previous and next; an episode in parts plays its
  parts in a row. Where each recording stopped, which were heard to the end and the speed are kept in the project
  (`studio/reader_state.json`), so listening goes on where it stopped, also on another device (why: D-163). The page
  says once what older recordings differ in. **„Podcast anhören“** (listen to the
  podcast) on the overview leads there.
- Earlier recordings stay available and are marked as an earlier version when text, provider or voices have changed
  since.

The two hosts need two different voices. Switching the provider does not carry an earlier approval over to another
provider's calls. A resumed audio run keeps the model, voices, style and text it started with; only the OpenRouter or
Google key may be renewed (see [Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)).

On the command line, `pla audio` records one reviewed episode with Qwen (see [Commands](PRODUCT.md#commands)).

### Segments, cache and assembly

- Qwen and Gemini via OpenRouter render each stored speaker segment separately; Gemini through Google renders a
  passage of several consecutive segments of both hosts in one request (see [Passages](#passages)). The whole
  episode is never produced in a single TTS call. A longer turn can be split into several segments of the same
  speaker.
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
- A failed loudness measurement stops with `loudness_failed`.

### Output format and chapters

- The standard format is MP3 (192 kbit/s), 44.1 kHz, stereo, loudness-normalised to -16 LUFS (see
  [Loudness](#loudness)).
- Chapter marks are produced from the actual audio timeline and embedded in the MP3.
- **One episode is one MP3**, whatever its length; the montage sets no length limit, because the script check bounds
  an episode before the recording ([Episode length](BUSINESS_LOGIC.md#episode-length)). Until 2026-10-04 an episode
  over 30 minutes was split into parts of at most 30 minutes; recordings made then keep their parts. (why: D-128)

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

## Gemini via Google

The listening rounds of 2026-10-06 (`.studio/hoerproben/make_hoerproben.py`, Ontologies episode 1) chose this route
(why: D-133): two hosts speaking in one request sounded markedly more like a conversation than segments recorded one
by one, and the user found the voices of the per-segment recordings too clean and the mood monotonous. In the Studio,
choose **„Gemini TTS · Google“** in the **„Audio“** section of the [Settings page](STUDIO.md#settings-page) and store
the **„Google-Key“** there; the OpenRouter key stays for OpenRouter text, Jev and the OpenRouter route.

### The speech request

- `POST https://generativelanguage.googleapis.com/v1beta/interactions` (`google_speech.GOOGLE_SPEECH_ENDPOINT`), the
  key only in the `x-goog-api-key` header, never in the URL. The model is the chosen `speech.GEMINI_MODELS` entry
  without OpenRouter's `google/` prefix (`gemini-3.8-flash-tts`).
- One `text` item per turn (consecutive segments of one host joined), each with `speech_metadata` naming its
  speaker and its role's style; `generation_config.speech_config` is `{"mode": "conversational", "speakers": [...]}`
  with the two voices. A passage where only one host speaks uses the single voice (`[{"voice": ...}]`) and leaves out
  the listener reactions, which need the other host.
- The answer carries a base64 WAV (24 kHz, mono, 16 bit) in the audio item of its `model_output` step; another
  format, a text-only answer or an empty one stops with `invalid_audio` and stores nothing.
- The speaker names in the request are `Alex` and `Robin` (`google_speech.SPEAKER_NAMES`), gender-neutral and never
  spoken, so no name hints at who explains.
- Verified live on 2026-10-06 with `gemini-3.8-flash-tts`: single voice, two speakers, the style field (two styles
  of the same sentence sound clearly different), listener reactions in pipes and a 3.7-minute passage of 20 turns in
  one answer. German was not part of the listening rounds (see the verification list of the active plan).

### Passages

A passage is a run of consecutive segments of one chapter and scene (`google_speech.plan_passages`), at most 3,600
characters of script text and 20 segments (`MAX_PASSAGE_CHARACTERS`, `MAX_PASSAGE_TURNS`) (why: D-134):

- A chapter is cut into as few passages as the bounds allow, at the segment boundaries nearest equal shares of its
  text, so no short remnant is left; a single segment over the bounds is a passage of its own.
- The cut is made on the script text only: a spoken form or newly placed tags never move a boundary, so a change
  records again only the passage that contains it.
- A passage never crosses a chapter, so chapter marks stay measured. Pauses ([Pause minimums](#pause-minimums)) go
  between passages by the rule of a passage's last segment; inside a passage Gemini paces the turns itself.
- The cache key (`google_speech.passage_settings`) holds each segment's script text and spoken form where it
  differs, the voices, styles and speaker names of the hosts who speak in it, the model, the language and the
  adapter version (`google_speech.GOOGLE_SPEECH_VERSION`). A run binds `google_speech.py` (`worker_sha256`) and the
  passage rule (`passages`) in its inputs. The cache lies in `cache/audio/google/`.
- The [plausibility check](#plausibility-check-of-gemini-takes) judges a passage as a whole; a failure names its
  first and last segment.

### Style and roles

- Each role has a short style in English, as Google recommends (`speech.RoleStyles`, at most 80 characters; a long
  direction makes the voice drift). The presets (`speech.STYLE_PRESETS`) are the listening rounds; **„Neugierig,
  gespannt auf das, was kommt“** (curious and eager for what comes next) is the default, the user's choice of
  2026-10-06 (why: D-135). The fields can also be overwritten with own words or left empty. Styles are Google's only.
- **„Rollen von Folge zu Folge tauschen“** (swap roles from episode to episode, `AudioChoice.alternate_roles`): in
  every even-numbered episode the two voices swap, so each voice is the expert in turn; voice A explains in episode 1
  (`AudioChoice.for_episode`). The roles in the script stay host_a explains and host_b asks; only the voices swap.
  The approval and the run inputs hold the choice itself, the report and the show notes the episode's voices.
- Default voices of the route: Erinome (explains in episode 1) and Sadachbia (asks).
- **„▶ Gesprächsprobe“** (conversation sample) on the settings page plays a short conversation of four turns with
  exactly the selected voices and styles and the pace of the sample's language
  ([Speaking pace per language](#speaking-pace-per-language)), with a pause tag and a listener reaction, in German or English;
  **„▶ Mit getauschten Rollen“** plays it with the roles swapped. A sample that does not exist yet is made after a
  confirmation with one short request on the Google key (`voice_samples.generate_pair`) and is then played from
  `projects/voice-samples/google/<language>/<fingerprint>/` without a further request.

### Keys, errors and limits

- Key handling: see [Secrets and keys](SECURITY.md#secrets-and-keys). Without a Google key the run stops before any
  request with `google_key_required`.
- Google answers a wrong key with 400 `API_KEY_INVALID`; it and 401/403 stop with `google_authentication`. Another
  rejected request (400, 404, 413, 422) stops with `google_speech_request`. Google's own short message is kept in
  both, without credentials: it names the voice, model or quota at fault.
- A 429 waits Google's `retryDelay` (else 5, 10, 20, 40, 60 seconds), shared by every recording of the workspace,
  up to eight times (`google_speech.RATE_LIMIT_RETRIES`); a per-day quota (`quotaId` with `PerDay`) does not wait
  and stops at once with `google_quota` (status `waiting_for_quota`). 500, 502, 503, 504, a cut transfer and a reset
  connection are requested twice more; a lasting connection error stops with `google_connection`.
- A passage of 20 turns hides a single skipped turn better from the plausibility check than a segment does; the
  listening review stays the acceptance.

## Gemini via OpenRouter

In the Studio, choose **„Gemini TTS · OpenRouter (Abschnitt für Abschnitt)“** and both voices in the **„Audio“** section of the
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
  its own Gemini voice, and assembles them in script order. Two hosts in one request are what the
  [Google route](#gemini-via-google) does.
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
- At most two tags per segment (`expression.MAX_TAGS_PER_SEGMENT`); per episode at most as many tags besides the
  pauses as a quarter of its segments, but always at least three. No tag inside a word.
- Pauses are counted apart from the other tags, at most a third of the segments (`expression.episode_pause_limit`),
  a ceiling, not a target. They go where the conversation invites one: after a question to think about, before a
  turn or a key result, after a dense stretch; the content sets the rhythm, never a schedule or a fixed pattern
  (the user: „immer organisch bleiben“) (why: D-144, prompt version `audio_expression.v3-pauses`).
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
  afterwards, the Studio rejects the approval until the episode has been read again. A reading placed for the other
  route (with or without listener reactions) or for an earlier script counts as none, on the reading page and in the
  approval alike (`episode_audio.reading_hash`); the recording then places its own tags.
- **Recording:** it speaks exactly these tags, without a new model call; a segment whose spoken form has changed
  since is recorded without a tag. If no tags are placed yet, the recording places them itself, and the card says
  that they are then unread.
- **Unchanged:** script, transcript and approval text. A recording without expression still counts as current. To
  record without expression, clear **„Ausdrucksmarken vor der Vertonung setzen“** (set expression tags before
  recording) in the audio panel of the settings page, or, in a project without workspace settings, set
  `"expression": false` in `studio/audio.json` (see [Studio settings](CONFIGURATION.md#studio-settings)).

### Listener reactions

A Google recording also gets the other host's short reactions (why: D-136): the expression stage places, from the
language's list (`expression.BACKCHANNELS`: for example `|mhm|`, `|right|`, `|aha|`), at most one reaction between
pipes inside a segment of at least 30 words (`expression.MIN_BACKCHANNEL_WORDS`), never at its start or end, and per
episode at most a quarter of its segments, at least two, counted apart from the tags
(`prompts/audio_expression_backchannels.txt`, prompt version `audio_expression.v3-backchannels`). Gemini speaks the
reaction in the other voice. The check removes nothing silently: without tags and reactions the text must be word for
word the spoken one. A reading placed with reactions says so in `expression.json` (`"backchannels": true`) and is
used only for a Google recording, a reading without them only for the other routes. The approval card and the
reading show the number of reactions. The OpenRouter route gets none: whether it passes pipes through is unverified.

### Style directions

Gemini reads a written style direction in the text, such as „Sag es fröhlich:“ (say it cheerfully), aloud. The
Google route sends each role's style in its own field (see [Style and roles](#style-and-roles)). That the OpenRouter
endpoint does not pass a speaking style outside the text (`speech_metadata`) was observed on 2026-09-29; the re-test of
2026-10-06 did not run (the test asked OpenRouter for MP3, which its Gemini speech refuses), so it stays unverified.
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

- Changed pauses are audible and therefore need a new audio approval.
- The default values are not stored in approvals and run inputs; only a deviating pause policy is stored there and
  changes the input hash.

### Speaking pace per language

Each language a project can speak has a pace of its own (`speech.LanguagePace` in `AudioChoice.pace`), set on the
[Settings page](STUDIO.md#settings-page) (why: D-147). The user's choice of 2026-10-07: English at 93 % and unhurried,
German as recorded.

- **„Sprechtempo“** (speaking tempo), 80 to 100 %: under 100 % assembly slows every recording with its pitch kept
  (FFmpeg `atempo`) before the pauses go in, so the pauses keep their length and the timeline and the chapters are
  measured on the slowed audio. `audio_report.json` names it as `tempo`. It holds on every route and never speeds a
  recording up.
- **„… ohne Eile sprechen“** (speak unhurried), Google only: both roles' styles are sent with „, unhurried“ added
  (`speech.UNHURRIED`, `AudioChoice.spoken_styles`); an empty style becomes „unhurried“. The takes are new requests.
  How much slower Gemini speaks with it is not measured yet (V-28).
- A recording binds only the pace of its project's language (`AudioChoice.for_language`): a pace set for English
  leaves the approvals, run inputs and the „already recorded“ mark of German projects as they were, and the reverse.
  A language at full tempo without the calm delivery is not stored, so choices and approvals made before the pace
  existed keep their hashes.
- A changed pace of the project's language is audible and needs a new audio approval. A new tempo alone reuses the
  recorded takes from the cache and repeats only the montage; a calm delivery records anew.
- The approval card shows the project language's pace under „Tempo“.

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
- The run renders only the changed segment again; all others come from the cache. With Gemini through Google that is
  the whole passage containing the segment: its other turns are spoken anew too and may sound different.
- The run's approval receipt names the stored approval, not a new decision.
- The deviations then appear in the export report and in the show notes.

## Parallel recording and queue

With the [parallel mode](STUDIO.md#sequential-or-parallel) selected, Gemini audio starts every approved episode at
once as its own job, at most 30 at the same time across the Studio (`execution.MAX_PARALLEL`) (why: D-092); each
episode still needs its own script approval. In sequential mode, one recording per project runs at a time; Qwen always
records one at a time. Within an episode, the segments (OpenRouter) or passages (Google) are requested one after
another. Parallel recordings share the Gemini cache with a lock per segment or passage, so no cache entry is generated
twice. The rate-limit rules of the Google route: see [Keys, errors and limits](#keys-errors-and-limits).

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
  key of the project's route (Google or OpenRouter) is present; after a restart, an episode otherwise shows „wartet
  auf den Google-Key“ or „wartet auf den OpenRouter-Key“ (waiting for the key).
- Before starting, the scheduler checks script, reading view, voices and expression again; if something has
  changed, the episode stays in the queue with the reason.
- The page Vertonung shows the queue with **„Entfernen“** (remove) and **„{n} gelesene Folgen freigeben“**
  (approve {n} read episodes), beside **„Zuerst die Skripte lesen“** (read the scripts first): one checkmark, never
  preset, confirms that all listed scripts, including their expression, have been read, and approves them in one
  step. As many start at once as places are free; the rest is queued.
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
| `audio.mp3` | The episode with embedded chapters (ID3v2.3) and the [AI marking](#ai-marking) in its tags |
| `chapters.json` | Chapters measured at assembly, and those read back from the MP3 (`embedded`) |
| `timeline.json` | Start, speech end, end, pause and trimmed silence of every segment; for a Google recording of every passage, with its `segment_ids` and `speaker_ids` (`schema_version` 1.1) |
| `audio_report.json` | Duration, format, loudness and pause data; `speech_quality_verified: false`; `ai_marking` (the tags written) and `ai_marking_embedded` (whether ffprobe reads all of them back from the MP3) |
| `transcript.md` | The approved text with host labels, in script order |
| `README.md` | Link to the MP3 with its duration, the voices and any deviating spoken forms |
| `playlist.m3u` | The MP3 |
| `show_notes.md` | Chapters with timestamps from the measured assembly, the voices, and pronunciation notes for segments with a deviating spoken form; ends with the transparency note |
| `listening_sheet.md` | The listening sheet, see below; ends with the transparency note |
| `publish/` | The companion kit, written later on request („Begleitmaterial“, `pla publish-kit`; why: D-139): `description_short.txt`, `description.txt` (at most 4,000 characters: the description, chapters as `00:00 Title`, as many sources as fit, and the transparency note), `sources.md` with every source, and `kit.json` bound to the script hash and this recording (`publish_kit.KIT_VERSION`, `publish_kit.v2`). Without a recording the kit lies in `episodes/<episode>/publish/`, chapters without times. |

The whole podcast gets a companion kit of its own in `publish/` of the project, written on request („Begleitmaterial
für den Podcast“, `pla publish-kit --podcast`; why: D-165):

| File | Content |
| --- | --- |
| `description_short.txt` | Two or three sentences on the podcast (80 to 300 characters) |
| `description.txt` | The podcast description for the show page of Spotify and Apple Podcasts (400 to 1,500 characters), then the transparency note and an AI notice that names the podcast's scripts |
| `sources.md` | Every source of the series once, in the order of first use, each with the episodes that use it („(in Folge 01, Folge 03)“) |
| `transcript.md` | The approved text of every published episode in script order with the hosts' labels, as each recording's `transcript.md` has it, under „## Folge 01: Title“ and „### 00:00 Chapter“ with the chapter's measured start; an episode without a recording of its current script says so once and has chapters without times; ends with the transparency note |
| `kit.json` | `publish_kit.PODCAST_KIT_VERSION` (`podcast_kit.v1`), per episode the script hash, the script run and the recording it covers, the sources, the idea sources left out and the files' hashes |
| `descriptions.json` | The two descriptions with the hash of their prompt, reused by a rebuild |

- One call of the newest script run's text model writes the two descriptions from what each episode is planned to
  cover (title, central question, role in the series) and its chapter titles, not from the transcripts; the checks are
  the episode kit's (length, language, no addresses, ids, Markdown, timestamps or exclamation marks), corrected at
  most twice. Its calls lie in `studio/publish_kit/podcast/`.
- The transcript and the sources are built from the published scripts, their recordings and their script runs, never
  by a model; one work two episodes cite under two copies is one source (the research's `work_id`, else the same
  title). Idea sources and local file paths never appear.
- The kit covers every episode with a published script, also one not recorded yet. It counts as current while every
  episode's script and latest recording are the ones it covers; after a new recording or script it is outdated, not
  shown and not zipped until it is rebuilt („Neu zusammenstellen“), which reuses the descriptions without a model call
  while no episode's plan or chapter titles changed.

An episode recorded in parts before 2026-10-04 has the first five files once per part in `part_01/`, `part_02/` and so
on; the other four cover all parts. The run also writes `reports/<episode>_audio.json`,
`episodes/<episode>/audio_latest.json` (status `awaiting_listening_review`, pronunciation report, spoken-form
overrides, chapters, and `parts` with the one MP3) and `pronunciation.json` in the run folder.

The export never publishes anything (see [Source rights and privacy](SECURITY.md#source-rights-and-privacy)). The
technical reports do not claim a passed listening review: pronunciation, naturalness and voice consistency are
judged from the MP3. Downloads of single episodes or the whole podcast as a ZIP: see [Downloads](STUDIO.md#downloads).

### Language of the exports

The fixed words of the export (the README, the transcript's notice, the show notes, the listening sheet, the companion
kit, the MP3's comment and the transparency note) are in the project's content language (`language` in the
[Project brief](CONFIGURATION.md#project-brief)), never in the Studio's interface language (why: D-153). German and
English have their own words (`content_text.py`); any other language reads German. A German project's export is the
same byte for byte as before 2026-10-07, apart from the transparency note. Which other files follow the content
language: [Languages](ARCHITECTURE.md#languages).

### AI marking

Every exported MP3 says in its own tags that it was made with AI (why: D-154), written by FFmpeg at encoding
(`audio.marking_args`, `audio.ai_marking`):

| Tag | Value |
| --- | --- |
| `title` | The episode title, as before |
| `comment` | An episode: „KI-generiert: Skript von einem Sprachmodell geschrieben, Sprache mit synthetischen Stimmen erzeugt.“ or "AI-generated: script written by a language model, speech made with synthetic voices."; a technical probe and a voice preview name the synthetic voices only („KI-generiert: Sprache mit synthetischen Stimmen erzeugt.“ or "AI-generated: speech made with synthetic voices.") |
| `DIGITAL_SOURCE_TYPE` | `http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia` (the IPTC digital source type for media a trained model generated) |
| `AI_GENERATED` | `true` |

- The tags stand in every episode MP3, every audio probe (`pla audio-probe`), the samples of the Gemini
  [voice library](#voice-library) and the Google [conversation samples](#style-and-roles); a sample's comment is in
  the language of the sample.
- FFmpeg 9 writes `comment` as a TXXX frame named "comment", not as a COMM frame, like the other two keys; a player that
  reads only COMM shows no comment (V-42). `audio_report.json` records the tags written (`ai_marking`) and whether
  all of them were read back from the MP3 (`ai_marking_embedded`). Whether podcast platforms keep the tags after an
  upload is open (V-41).
- `show_notes.md`, `listening_sheet.md`, the companion kit's `description.txt` and the podcast kit's
  `description.txt` and `transcript.md` end with a transparency note:
  the heading „Transparenzhinweis“ or "Transparency note", the source-bound synthesis note of the
  [Roadmap](PRODUCT.md#roadmap) („Dieser Output ist eine quellengebundene Synthese. …“ or "This output is a
  source-bound synthesis. …") and an AI notice („KI-Hinweis: Das Skript dieser Folge wurde von einem Sprachmodell
  geschrieben und wird von synthetischen Stimmen gesprochen.“ or "AI notice: the script of this episode was written by
  a language model and is spoken by synthetic voices."). The kit's description carries the note and the AI notice
  without the heading. The podcast kit's AI notice names the podcast's scripts („KI-Hinweis: Die Skripte dieses
  Podcasts wurden von einem Sprachmodell geschrieben …“ or "AI notice: the scripts of this podcast were written by a
  language model …").
- The companion kit counts the note against its 4,000 characters and never cuts it: sources give way first, and when
  the description, the chapters and the note alone do not fit, the kit stops with `description_too_long`. A kit made
  before the note (`publish_kit.v1`) is not shown on the recording page and not put into the ZIP until it is rebuilt
  („Neu zusammenstellen“ or `pla publish-kit`); the rebuild reuses its saved descriptions without a model call
  (`publish_kit.DESCRIPTIONS_VERSION` stays `publish_kit.v1`).
- The audio itself carries no spoken disclosure; that stays an open option, since it would change approved audio.
  Whether note and tags satisfy the EU AI Act's Art. 50 for an intended use is not checked legally (V-43).

### Listening review

After listening, you enter the **„Hörprüfung“** (listening review) with **„Hörprüfung eintragen“** (enter listening
review).

- `listening_sheet.md` lies in the export next to the MP3 and has columns for unclear points, lost attention and
  pronunciation. For an episode recorded in parts before 2026-10-04, each part's times start at 0:00, so the sheet
  names the part of every row in a column of its own.
- Only a human sets the listening review; no program step does. It is stored as `human_listening_reviewed` with
  `listening_note` in `episodes/<episode>/audio_review.yaml`; publishing a new recording of the episode clears an
  earlier listening review and its note.
- The listening review and the per-segment spoken form can be entered while other episodes are being recorded. Only
  while the same episode is being recorded does the Studio refuse the entry (`episode_busy`); it goes through once
  that recording has finished or has been stopped.
