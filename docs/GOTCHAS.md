---
title: Gotchas
doc_type: gotchas
status: current
last_reviewed: 2026-10-06
---

# Gotchas

Traps that cost time: what you see, why, and what to do. ✓ = handled in the repo, ⚠ = watch out. Behaviour is in the
stage docs, unknowns in the verification list of the [MVP acceptance plan](specs/2026-09-10-mvp-acceptance-plan.md#verification-list).

## Setup and update

- ✓ **After a code update the Studio still behaves the old way, and a browser reload does not help:** the server, its
  scheduler and running workers keep the code they started with; only `app.js` is reloaded. Click
  **„Neu starten, sobald nichts läuft“** (restart when idle); the banner „Das Studio hat neuen Code“ says so
  (`studio.py`). ([Restart after an update](STUDIO.md#restart-after-an-update))
- ⚠ **`pip install -e .` fails on Windows because `.venv\Scripts\pla.exe` cannot be replaced:** Windows locks an
  executable while a process runs it (a CLI run in another window, a Studio started with `pla.exe studio`). Stop every
  `pla` process first; `Podcast-Studio.cmd` starts the Studio through `python.exe -m podcast_automate`.
  ([Update the Studio](OPERATIONS.md#update-the-studio))
- ⚠ **`ffmpeg` is "not found" in a new PowerShell window:** `scripts/setup-ffmpeg.ps1` installs into `tools/ffmpeg/bin`
  without changing the system `PATH`. Run the two `PATH` lines in each window; `pla` and the Studio add
  `tools/ffmpeg/bin` themselves (`platforms.configure_path`). ([FFmpeg](OPERATIONS.md#ffmpeg))
- ⚠ **After a fresh checkout, audio assembly fails because FFmpeg is missing:** `tools/ffmpeg/` is git-ignored. Run
  `scripts/setup-ffmpeg.ps1` again. ([FFmpeg](OPERATIONS.md#ffmpeg))
- ✓ **Setup stops with „Die vorhandene .venv passt nicht zu diesem System …“ (or the same for `.venv-tts`):** virtual
  environments copied from another computer are system-specific. Do not copy them; set them up on the new machine
  (`scripts/setup.sh` and `scripts/setup-qwen.py` detect the copy). ([Move to another computer](OPERATIONS.md#move-to-another-computer))
- ⚠ **An old running job fails or misbehaves after a move to another computer:** runs contain absolute paths and
  approvals bound to their inputs. Run `setup-qwen.py --project …` or `setup-qwen.ps1 -ProjectDir …` again and approve
  a new audio run instead of resuming. ([Move to another computer](OPERATIONS.md#move-to-another-computer))
- ⚠ **A running Lemonade server does not offer Qwen3-TTS:** Lemonade 10.6.0 has only Kokoro as TTS backend. Use
  `scripts/setup-qwen.ps1`, which installs the application's own Qwen worker in `.venv-tts`.
  ([Set up local Qwen on Windows](OPERATIONS.md#set-up-local-qwen-on-windows))

## Subscriptions and providers

- ✓ **The Studio, started by double-click, reports „Codex wurde nicht gefunden“ (`codex_missing`) although `codex`
  works in a terminal:** Explorer does not inherit the `PATH` of a shell or IDE. The Studio also looks in
  `~/.local/bin/codex.exe` and the newest OpenAI extension of VS Code or VS Code Insiders (`codex.py`); otherwise set
  `runtime.codex_executable`, then „Verbindungen prüfen“ and „Fortsetzen“. ([Where the CLIs are found](OPERATIONS.md#where-the-clis-are-found))
- ✓ **„Claude-Code-Shim nicht auflösbar“ (`unsupported_claude_launcher`):** `claude` on `PATH` is an npm `.cmd` or
  `.ps1` shim, but `node` or `node_modules/@anthropic-ai/claude-code/cli.js` is missing. Install Claude Code natively
  or complete the npm install; the adapter runs a shim through `node`, never through `cmd.exe` (`claude_code.py`).
  ([Claude Code](OPERATIONS.md#claude-code))
- ✓ **An `ANTHROPIC_API_KEY` or OpenAI key in your environment would bill an API account instead of the
  subscription:** such variables take precedence over the subscription login. Nothing to do;
  `codex.subscription_environment` removes them from every CLI call. ([Subscription logins](SECURITY.md#subscription-logins))
- ⚠ **The first Claude call after a window runs out fails and is charged:** Claude reports no quota in advance; the
  block is noted only after the first refusal. Under „Automatisch“ Codex takes over; a fixed Claude job pauses.
  ([Quota](BUSINESS_LOGIC.md#quota-what-counts-as-limit-reached))
- ✓ **„Kein Abo hat gerade Kontingent“ although you bought Claude extra usage:** a stored Claude block from a reported
  weekly or 5-hour limit is respected. Turn on „Zusatzkontingent gekauft: gespeicherte Claude-Sperren übergehen“ on the
  settings page (`subscriptions.claude_extra_usage`). ([Settings page](STUDIO.md#settings-page))
- ⚠ **A call ends with `claude_budget_cap`, although `--max-budget-usd` did not stop its first model turn:** the CLI
  applies the cost cap (`claude_code.MAX_BUDGET_USD`, 12) only after the first model turn, so it is no hard per-call
  cap. The answer is not accepted (`claude_code.py`); check the scope of the call.
- ✓ **`invalid_output_schema`: „Das Antwortschema ist zu groß für die Claude-Code-Befehlszeile“:** Windows allows
  32,767 characters per command line, and the escaped schema is longer. A new login does not help; the adapter refuses
  the call before start (`claude_code.MAX_SCHEMA_CHARS`), and only a smaller schema in code fixes it.
  ([Claude Code adapter](ARCHITECTURE.md#claude-code))
- ✓ **Claude calls lose the subscription login when run with `--bare`:** `--bare` disables the subscription login
  entirely (API key only). The adapter isolates calls with `--setting-sources ""` and a fixed `--system-prompt`
  instead (`claude_code.py`).
- ✓ **A script run stops with `openrouter_truncated`:** `--max-output-tokens` (default 32,768) or the model's own
  output limit is too small for a full episode. Start a new `script` run with a higher `--max-output-tokens` or another
  model; a cut answer is never accepted (`openrouter.py`). ([OpenRouter for a script run](SCRIPTS.md#openrouter-for-a-script-run))
- ⚠ **`metadata.json` of an OpenRouter call shows no cost:** OpenRouter reported none. Treat the cost as unknown, not
  as free, and check OpenRouter's billing.

## Studio

- ✓ **After a Studio restart OpenRouter jobs stop for a missing key, and queued Gemini episodes show „wartet auf den
  OpenRouter-Key“:** a key entered in the Studio lives only in the old server's memory. Enter it again on the settings
  page, or start the server with `OPENROUTER_API_KEY` set; the page says so (`studio.py`).
  ([OpenRouter key in the Studio](SECURITY.md#openrouter-key-in-the-studio))
- ⚠ **After the first save on the settings page, a project's own text model, audio or limits seem ignored:** once
  `projects/.studio-settings.json` exists, its sections replace every project's `studio/*.json`, `research_limits` and
  `runtime.text_timeout_seconds`; the first save takes the values of the most recently changed project. Change them on
  the settings page. ([Studio settings](CONFIGURATION.md#studio-settings))
- ⚠ **The header shows „Keine Verbindung“ and the run indicator stops pulsing:** the Studio server has not answered
  polls for more than 30 seconds. Wait; this is no evidence that the model call crashed.
  ([Times and connection](STUDIO.md#times-and-connection))
- ⚠ **Live output keeps arriving, but no readable text appears for a minute or more:** the model may be in an output
  loop; hidden JSON fields do not count as content. The Studio points it out; an active connection is no proof of
  progress, so stop and resume if it persists. ([Live output](STUDIO.md#live-output))
- ✓ **A resting run can no longer be resumed after you edited host names, the brief, voices, editorial notes or
  spoken forms:** content inputs are bound to the run by hash. The Studio warns before saving; start a new run.
  ([Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding))

## Research

- ✓ **A run from an older version still shows „Zuschnitt der Teilfragen ungeklärt“ (`question_scope_unresolved`):**
  that stop no longer exists; the scope check now decides in its second pass. Press „Fortsetzen“ or „Mit neuen
  Anläufen fortsetzen“ (`studio_allowances.RESEARCH_FRESH_CODES`). ([Scope check](RESEARCH.md#scope-check))
- ✓ **The gap probe reports `no_hits` although an English source answers a gap of a German project:** the probe is a
  term-overlap search and cannot cross languages. New dossiers carry `gap_terms` in the source language, and the Jev
  probe finds such passages (`research_gap_probe.py`, `jev.py`); a dossier written before `gap_terms` needs a new
  research run. ([Gap probe](RESEARCH.md#gap-probe))
- ✓ **A source appears with empty content, for example Springer's „Client Challenge“:** the server returned a bot
  challenge page instead of the document. It is treated as blocked, and the import looks for a free copy (OpenAlex,
  Unpaywall, Semantic Scholar, Europe PMC, CORE), or you upload the work under „Fehlende Werke“ (`sources.py`).
  ([Blocked downloads](RESEARCH.md#blocked-downloads-free-copies-and-open-archives))
- ⚠ **CORE stops finding copies during the day:** all runs share one daily allowance (`PLA_CORE_DAILY_LIMIT`, default
  1000, counted per UTC day in `~/.podcast-automate/core_usage.json`). Wait for the next UTC day or raise the limit.
  ([Environment variables](CONFIGURATION.md#environment-variables))
- ⚠ **Unpaywall and CORE are never consulted:** `PLA_UNPAYWALL_EMAIL` or `PLA_CORE_API_KEY` is not set, or was set
  without restarting the Studio. Set the variables and restart the Studio.
  ([Environment variables](CONFIGURATION.md#environment-variables))
- ⚠ **A source fails as unreadable with "pages without a text layer":** it is a scanned PDF, and OCR never starts
  automatically. Supply a readable version as a `seed_urls` entry of a new research run, or upload the work under
  „Fehlende Werke“. ([Download and import limits](RESEARCH.md#download-and-import-limits))
- ✓ **A research run stops on a text-call timeout:** the call time limit (`runtime.text_timeout_seconds`, default
  1800) is too low for the call. Raise it on the settings page, or in `project.yaml` without workspace settings, and
  resume the same run; it does not bind the run (`storage.bound_brief`). ([Time limits and stalls](RESEARCH.md#time-limits-and-stalls))

## Scripts

- ⚠ **A resumed script run rewrites drafts it had already accepted (after 2026-10-06):** the teaching-design, writing
  and polishing checkpoints are bound to their prompt text, and the listenability change rewrote those prompts. A stage
  still open asks its calls again (more calls against `research_limits.model_calls`); finished stages and passed
  script reviews stay. To give a published series the new style, start a new `pla script` run or `--revise`.
  ([Existing projects](TEACHING.md#existing-projects))
- ✓ **Publishing a new table of contents stops with `episodes_locked`:** a file in an earlier series' episode folder is
  open (for example during a recording), so the folder cannot move to `episodes/archive/`. Close or finish what holds
  it, then resume (`script_artifacts.py`). ([Earlier series](SCRIPTS.md#earlier-series))
- ⚠ **Edits in `episodes/<ep>/script.md` have no effect or disappear:** `script.md` is a generated reading view;
  `script.yaml` is the canonical text. Edit `script.yaml`; `resume` detects the change instead of overwriting it, and
  the changed text needs a new subject review. ([Canonical script](SCRIPTS.md#canonical-script))
- ✓ **Resuming a stopped review or correction stops again at the same place:** verdicts and spent attempts are saved,
  and a resume buys no new attempts. Use „Mit neuen Anläufen fortsetzen“ or `pla approve <project> --fresh-attempts`
  (`run_budget.py`). ([Stopping and resuming](STUDIO.md#stopping-and-resuming))
- ⚠ **The series review of a large series fails or blocks:** all final texts are passed in full, without truncation,
  and can exceed a provider's context limit. Choose a model with a larger context or review a smaller series.
  ([Scope and limits of the series review](SCRIPTS.md#scope-and-limits-of-the-series-review))
- ✓ **`pla resume` after `pla series-review` continues the script run, and `pla resume --run-id <review run>` fails
  with `invalid_run`:** a review run is not resumable, and `runs/latest.json` keeps pointing at the last pipeline run.
  View it with `pla status <project> --run-id <id>`; start a new `pla series-review` for a new verdict (`cli.py`).
  ([Standalone `pla series-review`](SCRIPTS.md#standalone-pla-series-review))
- ⚠ **`scripts/audit-counts.py` counts no redefined terms on an old project:** teaching plans written before
  `Concept.terms` carry opaque concept IDs (`c01`, `c07_kv_cache`). Pass the spoken terms with
  `--terms "Token,Query,…"`; a regenerated series needs no flag.

## Audio

- ⚠ **A stopped Gemini recording asks for a new approval after an update:** its inputs bind the file hash of its
  engine, `speech.py` for OpenRouter and `google_speech.py` for Google (`worker_sha256` in `episode_audio`), so any
  change to that file ends the resumability of every recording of that route not yet finished (`inputs_changed`;
  Transformer ep_010, 2026-10-04). Approve the episode again: the new run takes every segment or passage already
  spoken from `cache/audio/gemini` or `cache/audio/google`, whose keys hold the text and the voices, not the code.
  Change an engine file only while no recording of its route is open.
- ✓ **Switching an episode from OpenRouter to Google records it completely again:** the two routes share no cache
  (one segment against a passage of several) and an approval names its provider. The old recording stays until the
  new one is published.
- ✓ **A wrong Google key comes back as HTTP 400, not 401:** Google names it `API_KEY_INVALID`; the recording stops
  with `google_authentication` and shows Google's message. Store the right key under „Google-Key“, then resume.
  ([Keys, errors and limits](AUDIO.md#keys-errors-and-limits))
- ✓ **Google stops a recording with `google_quota` at once instead of waiting:** the key's daily quota is used up,
  which waiting minutes cannot lift. Resume after the quota resets, or raise the tier in Google AI Studio.
  ([Keys, errors and limits](AUDIO.md#keys-errors-and-limits))
- ⚠ **A literal pipe in a spoken text becomes a listener reaction in a Google recording:** Google reads text
  between pipes (`|mhm|`) as the other host's reaction. The expression check admits only the listed reactions, but a
  pipe in the script itself or a spoken form would be taken the same way; write it out instead.
- ✓ **Every chapter of a Qwen recording fails as `invalid_audio` after the GPU work:** `runtime.tts_revision` was
  `main`, while the worker records the commit it actually loaded. `pla init` pins the known commit
  (`cli.pinned_revision`); when it knows none it says so, and you enter the commit in `project.yaml` before the first
  Qwen recording. ([Qwen](AUDIO.md#qwen))
- ✓ **A Qwen recording stops with `CUDA_UNAVAILABLE` or `MPS_UNAVAILABLE` after a driver change or on another
  machine:** an explicit `cuda:0` or `mps` never falls back; only `auto` does. Fix the driver, or set
  `runtime.tts_device` to `auto` or `cpu` and start a new run (`qwen_worker.select_device`).
  ([Device, model and first check](OPERATIONS.md#device-model-and-first-check))
- ✓ **After editing `language` or `voice_profile` in `project.yaml`, `resume` still uses the old voices:** a resume
  continues only runs with unchanged inputs. Start a new `audio-probe` run.
  ([Make a sample and resume it](OPERATIONS.md#make-a-sample-and-resume-it))
- ⚠ **An audio run started before a code update refuses to resume:** the run hashes its worker files (`speech.py`,
  `qwen_worker.py`), so a change to them makes its inputs differ. Start a fresh audio run; approvals stay valid, and
  cached segments whose spoken text equals their script text are reused.
- ✓ **A Gemini recording stops at once with `openrouter_privacy` (HTTP 404):** the OpenRouter account allows only Zero
  Data Retention providers, which excludes Google's speech model. Allow it at openrouter.ai/settings/privacy, then
  resume; the error names the setting (`speech.py`). ([Keys, errors and limits of the check](AUDIO.md#keys-errors-and-limits-of-the-check))
- ✓ **Gemini reads a style direction such as „Sag es fröhlich:“ aloud:** written directions are part of the input
  text. A Google recording sends each role's style in its own field; for OpenRouter use only the allowed inline tags,
  the expression check admits nothing else (`expression.py`). ([Style directions](AUDIO.md#style-directions))
- ⚠ **Recordings in another project seem to stall without an error:** a 429 in any project makes all Gemini
  recordings wait through `projects/.gemini_throttle.json`, for up to about three minutes. Wait; only a persisting
  limit stops an episode. ([Rate limits and transient errors](AUDIO.md#rate-limits-and-transient-errors))
- ✓ **The audio approval or a re-render is refused after „Ausdruck neu setzen“:** the approval is bound to the hash of
  the expression tags you read. Read the episode again with the current tags and approve again.
  ([Tags are part of reading the script](AUDIO.md#tags-are-part-of-reading-the-script))
- ✓ **An episode near the part length fails with `duration_exceeded` after paid synthesis:** parts were sized with the
  planned pause instead of the applied minimum. Partitioning and assembly now use the same `audio.applied_pause`
  (`episode_audio.segment_durations`). ([Pause minimums](AUDIO.md#pause-minimums))

## Logs and security

- ⚠ **A credential shows up in `<project>/studio/stderr/<job>.log`:** a worker that dies outside its own error
  handling leaves Python's raw traceback there; the log files and the Studio's view of that tail are filtered
  (`logs.scrub`), this file is not. The next job removes it once it is an hour old; don't share it before.
  ([Credentials in traces, diagnostics and logs](SECURITY.md#credentials-in-traces-diagnostics-and-logs))
- ✓ **`pla script … --api-key "KEY"` stops with `invalid_request`:** a key given as a value is refused unread, because
  it would stand in the process list and the shell history. Use `--api-key` without a value or set
  `OPENROUTER_API_KEY` (`cli.py`). ([OpenRouter key on the command line](SECURITY.md#openrouter-key-on-the-command-line))
- ✓ **„Keine verdeckte Key-Eingabe möglich“ in an IDE task or a pipe:** a non-interactive terminal cannot ask for the
  key hidden, and the prompt aborts instead of reading it visibly. Set `OPENROUTER_API_KEY` (`cli.py`).
  ([OpenRouter key on the command line](SECURITY.md#openrouter-key-on-the-command-line))
- ✓ **A research run stops with `local_source_outside`, or a script run with `invalid_request`:** a `local_sources`
  entry is absolute or leaves the project folder. Copy the file into the project folder or upload it in the Studio,
  then adjust `local_sources` (`research.py`, `scripting.py`). ([Local files](SECURITY.md#local-files))
- ⚠ **Windows asks on the first LAN start whether Python may use the network:** the LAN mode listens on all
  interfaces. Allow only „Private Netzwerke“ (private networks). ([Studio access](SECURITY.md#studio-access))

## Tests on Windows

- ⚠ **Temporary test directories cannot be removed on Windows:** a log file handle stays open for the life of the
  process, and Windows refuses to delete an open file. Tests that call `configure_logging` must call
  `release_logging` or close the handlers first. ([Rules for the tests themselves](../AGENTS.md#rules-for-the-tests-themselves))
- ✓ **Tests fail with "is not in the subpath of", or a job shows `interrupted`, when `TEMP` is a short path such as
  `C:\Users\ABCDEF~1\…`:** a test handed an unresolved root to code that expects the resolved one every entry point
  passes. The affected `setUp`s resolve their root. ([Rules for the tests themselves](../AGENTS.md#rules-for-the-tests-themselves))
- ⚠ **`test_research_parallel` fails about one run in three on Windows:** a raw `Path.read_text` on a file another
  research worker writes (`budget.json`, `research_questions.json`) races there. Read such files through
  `storage.read_text`. ([Rules for the tests themselves](../AGENTS.md#rules-for-the-tests-themselves))
- ⚠ **The Python suite is green, but the audio integration tests were skipped:** they run only when `ffmpeg` and
  `ffprobe` are on `PATH` (`skipUnless(shutil.which("ffmpeg") …)`), and `scripts/setup-ffmpeg.ps1` does not change the
  system `PATH`. Run the two `PATH` lines from [FFmpeg](OPERATIONS.md#ffmpeg) before the suite.
