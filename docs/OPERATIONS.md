---
title: Operations
doc_type: operations
status: current
last_reviewed: 2026-10-04
covers:
  - scripts/setup.sh
  - scripts/setup-ffmpeg.ps1
  - scripts/setup-qwen.ps1
  - scripts/setup-qwen.py
  - scripts/generate-voice-samples.py
  - src/podcast_automate/doctor.py
  - src/podcast_automate/logs.py
  - src/podcast_automate/platforms.py
  - src/podcast_automate/cli.py
  - src/podcast_automate/codex.py
  - src/podcast_automate/claude_code.py
  - src/podcast_automate/qwen_worker.py
  - src/podcast_automate/runner.py
  - src/podcast_automate/studio.py
  - src/podcast_automate/studio_worker.py
  - pyproject.toml
  - requirements-tts-windows.txt
  - requirements-tts.txt
  - Podcast-Studio.cmd
  - Podcast-Studio.sh
  - Podcast-Studio.command
---

# Operations

Runbooks for this repository. Rules are in [BUSINESS_LOGIC](BUSINESS_LOGIC.md), traps in [GOTCHAS](GOTCHAS.md).

## Tasks

Commands run from the repository root. `pla` below stands for `.\.venv\Scripts\pla.exe` on Windows and `.venv/bin/pla`
on macOS and Linux. All commands: [Commands](PRODUCT.md#commands); in daily use the Studio guides through the steps.

| Task | How |
| --- | --- |
| Install | [Windows 11](#install-on-windows-11), [macOS and Linux](#install-on-macos-and-linux) |
| Start the Studio | `Podcast-Studio.cmd` (Windows), `Podcast-Studio.command` (macOS) or `sh Podcast-Studio.sh`; [Starting the Studio](STUDIO.md#starting-the-studio) |
| Update | [Update the Studio](#update-the-studio) |
| Check subscriptions and quota | `pla doctor --skip-tts`, `pla quota`; [Check the subscriptions](#check-the-subscriptions) |
| Set the OpenRouter key | Studio key field, `--api-key` or `OPENROUTER_API_KEY`; [Secrets and keys](SECURITY.md#secrets-and-keys) |
| Watch progress | `pla status <project>` in a second terminal; [Logs and diagnosis](#logs-and-diagnosis) |
| Resume a run | **„Fortsetzen“** (resume) or `pla resume <project> [--run-id <run_id>]`; [Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding) |
| Raise a run's call limit | The hold card's offer or `pla approve <project> --model-calls N`; [Budgets](BUSINESS_LOGIC.md#budgets) |
| Accept a gap or retry a blocked sub-question | [Blocked sub-questions and decisions](RESEARCH.md#blocked-sub-questions-and-decisions) |
| Re-render one segment | [Re-rendering one segment](AUDIO.md#re-rendering-one-segment) |
| Restore a deleted project | **„Papierkorb“** (trash) on the overview; [Overview, navigation and hold cards](STUDIO.md#overview-navigation-and-hold-cards) |
| Set up local Qwen | `scripts/setup-qwen.ps1` on [Windows](#set-up-local-qwen-on-windows), `scripts/setup-qwen.py` on [macOS and Linux](#set-up-local-qwen-on-macos-and-linux) |
| Make a voice sample | `pla audio-probe <project> --approve-audio`; [Voice samples](#voice-samples) |
| Read logs | [Logs and diagnosis](#logs-and-diagnosis) |
| Run the tests | [AGENTS.md](../AGENTS.md) |

## Install on Windows 11

Prerequisites: Python 3.12 and the downloaded repository. Run the commands in PowerShell in the repository folder; the
virtual environment need not be activated.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\pla.exe --help
.\.venv\Scripts\pla.exe init .\projects\energy-models --topic "Energiebasierte Modelle gründlich verstehen" --tts-python .\.venv-tts\Scripts\python.exe
```

`.venv-tts` is set up later ([Set up local Qwen on Windows](#set-up-local-qwen-on-windows)); the project already
records its planned Python path. Running `init` again never overwrites an existing project (`project_exists`). Then set
up FFmpeg (below) and log in to at least one subscription ([Check the subscriptions](#check-the-subscriptions)).

### FFmpeg

FFmpeg and ffprobe assemble the audio. `scripts/setup-ffmpeg.ps1` installs the project-local Windows x64 build 9.0.1
(gyan.dev's build, from its GitHub mirror `GyanD/codexffmpeg`; why: D-120) and checks its SHA-256:

```powershell
powershell -NoProfile -File .\scripts\setup-ffmpeg.ps1
$ffmpegBin = (Resolve-Path .\tools\ffmpeg\bin).Path
$env:PATH = "$ffmpegBin;$env:PATH"
ffmpeg -version
ffprobe -version
```

- The binaries live in `tools/ffmpeg/bin/` and are excluded from Git, so a fresh checkout needs the setup again; a
  second run over the same verified build downloads nothing.
- No system-wide PATH change is needed: `pla` and the Studio prepend `tools/ffmpeg/bin` of the current folder (the
  Studio: of its workspace) to their own PATH (`platforms.configure_path`).
- Run the two PATH lines in each new PowerShell window before you call `ffmpeg` yourself, run the tests or
  `scripts/generate-voice-samples.py`, or start `pla` from another folder. Without FFmpeg on PATH the audio
  integration tests are skipped, not failed ([AGENTS.md](../AGENTS.md)).
- Download source and checksum: [scripts/setup-ffmpeg.ps1](../scripts/setup-ffmpeg.ps1).

## Check the subscriptions

Web research and every text call that does not go to OpenRouter need at least one logged-in subscription: Codex CLI
with a ChatGPT subscription or Claude Code with a Claude Max subscription. Choice between them and quota pauses:
[Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection); login rules:
[Subscription logins](SECURITY.md#subscription-logins).

### Codex

Install the Codex CLI and log in with your ChatGPT account ([Codex login](https://learn.chatgpt.com/docs/auth)).

```powershell
codex login
.\.venv\Scripts\pla.exe text-probe .\projects\energy-models
.\.venv\Scripts\pla.exe status .\projects\energy-models
```

The text probe hands the topic to Codex (default `--backend codex_cli`) and expects validated JSON with possible
follow-up questions. It tests the connection, uses subscription quota and does no research. Result and usage metadata
go to `probes/text/<run_id>/` (`result.json`, `metadata.json`). When the quota is exhausted, the run is saved as
`waiting_for_quota` (exit code 2).

### Claude Code

Log in to Claude Code through claude.ai with `claude auth login`; `claude auth status --json` must show
`authMethod: "claude.ai"`. An API-key login is refused (why: D-109).

```powershell
claude auth login
claude auth status --json
.\.venv\Scripts\pla.exe quota
.\.venv\Scripts\pla.exe text-probe .\projects\energy-models --backend claude_code
```

The adapter is verified against Claude Code 2.1.92 (2026-09-19), 2.1.283 (2026-09-26) and 2.1.284 (2026-09-29)
(`claude_code.py`). Minimum CLI version per model: [Providers and models](PRODUCT.md#providers-and-models).

### Quota and installation check

`pla quota` makes no model call. It shows the Codex window in percent with its reset time and the Claude state (login,
and a noted block if there is one).

`pla doctor [<project>] [--skip-tts]` checks without a model call:

- Python and operating system,
- the age of the model and voice catalogs, with their verification date (informational; a stale catalog never blocks
  work),
- `ffmpeg` and `ffprobe` on PATH,
- the Codex and Claude logins and the quota of both subscriptions,
- without `--skip-tts`, the Qwen environment set in the project (default settings without a project; see
  [Set up local Qwen on Windows](#set-up-local-qwen-on-windows)).

The installation is ready when every technical check passes and at least one subscription login is usable
(`doctor.readiness`). `--skip-tts` skips only local Qwen, for example when you record with Gemini. `doctor` produces no
audio and does not check an OpenRouter key; research needs a subscription whatever the audio provider.

### Where the CLIs are found

`codex` and `claude` are looked up on `PATH`, then in `~/.local/bin`; on Windows, Codex also in the newest OpenAI
extension of VS Code or VS Code Insiders ([Text provider adapters](ARCHITECTURE.md#text-provider-adapters)). A path in
`runtime.codex_executable` always wins. So a Codex that works in a terminal but is missing from the `PATH` of a
double-clicked Studio needs no reinstall; its ChatGPT login is checked separately. After a detection error is fixed,
**„Fortsetzen“** continues the saved job; a draft table of contents still waits for your review before the scripts are
written.

On macOS and Linux, the Studio starter and `scripts/setup.sh` also add `~/.local/bin`, `/opt/homebrew/bin` and
`/usr/local/bin` to `PATH`. One usable subscription is enough.

## Install on macOS and Linux

The browser Studio uses the same flow and project files on every platform; Python, FFmpeg and Codex or Claude Code are
installed on each computer. Gemini audio needs only the OpenRouter key in the protected key field, not local Qwen.

The controller needs Python **3.12 or newer** and `ffmpeg` and `ffprobe` on PATH. Run the commands in the repository
folder.

On macOS with Homebrew:

```sh
brew install python@3.12 ffmpeg
sh scripts/setup.sh
sh Podcast-Studio.sh
```

Afterwards **Podcast-Studio.command** can be double-clicked in the Finder; the setup sets the execute permissions, and
the starter covers the Homebrew paths of Apple Silicon and Intel Macs.

On Ubuntu 24.04 or a comparable distribution with Python 3.12 or newer:

```sh
sudo apt update
sudo apt install python3 python3-venv ffmpeg
sh scripts/setup.sh
sh Podcast-Studio.sh
```

On other distributions, install Python, the venv module and FFmpeg with the package manager. Choose a specific Python
with, for example, `PYTHON=python3.13 sh scripts/setup.sh`. The setup never changes an existing virtual environment
that belongs to another system; it stops with a message.

`sh Podcast-Studio.sh` passes options such as `--port 8766` or `--no-browser` on to the Studio
([Starting the Studio](STUDIO.md#starting-the-studio)); the server stays bound to this computer
([Studio access](SECURITY.md#studio-access)).

A check without a local Qwen worker ([what it checks](#quota-and-installation-check)):

```sh
.venv/bin/pla doctor --skip-tts
```

CI runs the tests on Windows, Ubuntu and macOS ([AGENTS.md](../AGENTS.md)), including the Unix starters, platform
paths, device selection, process trees and trash and restore (see V-22).

## Set up local Qwen on Windows

The application runs its own Qwen process in `.venv-tts`. The Lemonade installation 10.6.0, checked on 2026-09-13,
offers Kokoro as its TTS backend but has no Qwen3-TTS entry in its model catalog or recipes, so a running Lemonade
server does not provide this Qwen environment; the setup below uses the project's own worker (why: D-115).

Prerequisites: the controller installed in `.venv`, a project created with `pla init`, and a Radeon driver suitable for
AMD PyTorch. The target computer has Windows 11 Pro, an RX 9070 XT and driver 32.0.31041.1004 of 2026-08-17.
[AMD installation guide and driver requirements](https://rocm.docs.amd.com/projects/radeon-ryzen/en/latest/docs/install/installrad/windows/install-pytorch.html)

```powershell
powershell -NoProfile -File .\scripts\setup-qwen.ps1 -ProjectDir .\projects\windows-pilot
```

The setup:

- installs Python 3.12.14 with `uv` under `tools/python/` when needed and creates `.venv-tts`,
- installs the AMD wheels and Qwen from `requirements-tts-windows.txt`,
- checks package dependencies (`pip check`), the Qwen import and a BF16 calculation on the GPU,
- downloads `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` with its speech tokenizer into the normal Hugging Face cache under
  `%USERPROFILE%/.cache/huggingface/hub/`,
- sets the TTS Python, the model, the pinned model revision, `cuda:0` and `eager` in the chosen `project.yaml`,
- keeps the original project brief as `reports/project-before-qwen.yaml` and writes the environment report
  (`reports/qwen_environment.json`) and the installed package versions (`reports/tts-requirements-installed.txt`).

The downloads come to several gigabytes; binaries, virtual environments and model weights do not belong in the Git
repository. The setup starts no audio generation and keeps existing voices and other topic settings; old text
runs and their artefacts stay saved.

The separate Python 3.12 environment follows the
[Qwen environment recommendation](https://github.com/QwenLM/Qwen3-TTS#environment-setup) (why: D-114).

### Pinned versions

| Component | Version / setting |
| --- | --- |
| TTS Python | 3.12.14 |
| PyTorch / Torchaudio | 2.9.1+rocm7.2.1 |
| ROCm SDK | 7.2.1 |
| Qwen package | qwen-tts 0.1.1 |
| Transformers | 4.57.3 |
| Model | Qwen3-TTS-12Hz-0.6B-CustomVoice |
| Model revision | `85e237c12c027371202489a0ec509ded67b5e4b5` |
| Voices in the pilot | Ryan and Serena, language German |
| Device / attention | `cuda:0` / `eager` |

Other pins (torchvision, accelerate, huggingface-hub, gradio): `requirements-tts-windows.txt`. The model and its calls
follow the [Qwen documentation](https://github.com/QwenLM/Qwen3-TTS). No FlashAttention installation is required. With
this AMD PyTorch build, the `cuda` device name is the interface used.

### Device, model and first check

The model and the voices Ryan and Serena from the table are preset for the first sample; the listening test decides
whether they fit. With `cuda:0`, a missing GPU is an error; only `auto` falls back to MPS or CPU
(`qwen_worker.select_device`). After a successful first test, the 1.7B-CustomVoice variant can be chosen in
`runtime.tts_model` for a quality comparison. Model, Python and voices are set in `project.yaml`
([Project brief](CONFIGURATION.md#project-brief)); a changed model variant, voice or other input starts a new probe
instead of quietly continuing an old run ([Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)).

Then check the environment:

```powershell
.\.venv\Scripts\pla.exe doctor .\projects\windows-pilot --json
```

`doctor` shows the packages of the TTS environment, the GPU PyTorch detects and, where present, the HIP version. It
loads no model weights and generates no speech. It also checks the subscription logins, which the audio probe itself
does not need.

## Set up local Qwen on macOS and Linux

Qwen gets its own **Python 3.12 environment** in `.venv-tts` (why: D-114), taken from `--python`, from `python3.12` on
PATH, or from the running Python if it is 3.12. The setup installs pinned package versions, downloads the pinned model
revision and checks a small calculation on the chosen device. It produces no voice sample.

On an Apple Silicon Mac:

```sh
brew install sox
.venv/bin/python scripts/setup-qwen.py --device mps
```

On MPS the worker uses Float32 and no CUDA calls. This path is implemented but still needs a real Qwen listening test on
Apple hardware (see V-20). If there are problems, choose `--device cpu` explicitly; recording on the CPU can take much
longer. For Intel Macs, Gemini is the intended audio path; the Qwen/PyTorch package set pinned here is not validated
for them.

Linux without a GPU:

```sh
sudo apt install sox libsndfile1
.venv/bin/python scripts/setup-qwen.py --device cpu --torch-index-url https://download.pytorch.org/whl/cpu
```

Linux with a suitable NVIDIA GPU and a driver for CUDA 12.8:

```sh
.venv/bin/python scripts/setup-qwen.py --device cuda:0 --torch-index-url https://download.pytorch.org/whl/cu128
```

Linux with an AMD GPU supported by ROCm 6.4:

```sh
.venv/bin/python scripts/setup-qwen.py --device cuda:0 --torch-index-url https://download.pytorch.org/whl/rocm6.4
```

The wheel index must match the hardware and the driver. These examples use PyTorch and torchaudio 2.9.1 according to the
[official version overview](https://pytorch.org/get-started/previous-versions/#v291); the shared Qwen packages are pinned
in `requirements-tts.txt`. Real Qwen listening tests on Linux are still outstanding (see V-21).

`--device auto` (the default) picks CUDA/ROCm, otherwise MPS, otherwise CPU. When an explicitly chosen GPU is missing,
the setup stops; it never quietly switches to the CPU.

The setting is saved locally in `.studio/tts-runtime.json` (installed package versions in
`.studio/tts-requirements-installed.txt`) and used for new Studio projects. For an existing project under `projects/`:

```sh
.venv/bin/python scripts/setup-qwen.py --device mps --project projects/mein-projekt
.venv/bin/pla doctor projects/mein-projekt
```

The previous project configuration is saved first (`reports/project-before-qwen.yaml`). A project with a running job
stays locked. Speech generation starts only after your approval in the Studio.

## Voice samples

A voice sample (`audio-probe`) is a technical check of the local Qwen setup, not a reviewed podcast episode. It speaks
the bundled test dialogue, assembles the speaker segments and pauses, normalises the loudness and exports an MP3. Gemini
voices: [Voice library](AUDIO.md#voice-library).

### Make a sample and resume it

With FFmpeg set up ([FFmpeg](#ffmpeg)), in each new PowerShell window:

```powershell
$ffmpegBin = (Resolve-Path .\tools\ffmpeg\bin).Path
$env:PATH = "$ffmpegBin;$env:PATH"
.\.venv\Scripts\pla.exe doctor .\projects\windows-pilot --json
.\.venv\Scripts\pla.exe audio-probe .\projects\windows-pilot --approve-audio
.\.venv\Scripts\pla.exe status .\projects\windows-pilot
```

After an interruption of the same unchanged audio run:

```powershell
.\.venv\Scripts\pla.exe resume .\projects\windows-pilot
```

`resume` keeps the approval of an unchanged audio probe and reuses finished stages and audio segments
([Human approvals](BUSINESS_LOGIC.md#human-approvals), [Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)).

The command writes:

| File under `probes/audio/<run_id>/` | Content |
| --- | --- |
| `audio.mp3` | Automatically assembled voice sample |
| `chapters.json` | Chapters with measured time positions |
| `timeline.json` | Mapping between script segments and audio times |
| `transcript.md` | Spoken text of the test dialogue |
| `audio_report.json` | Technical audio measurements and target parameters |

The TTS report `runs/<run_id>/tts_report.json` holds the model revision, package versions, GPU data, load time and the
render time of each segment, and shows which segments came from the cache.

Judge pronunciation, naturalness and consistent voices from the MP3; no manual editing is needed. The technical reports
never claim a passed listening review; that stays a human task (other limits: [Out of scope](PRODUCT.md#out-of-scope)).
The measurements of the pilot run of 2026-09-13 (model load time, GPU memory used, synthesis time per segment) are in
the Git history of the removed `docs/windows-pilot.md`.

### Compare German and English

`language` in `project.yaml` chooses both the bundled dialogue and the Qwen language for `audio-probe`: `de-DE` for
German (default), `en-US` for English. Transcript and chapter titles follow it, and it is part of the audio cache key
([Qwen](AUDIO.md#qwen)).

The voices currently chosen for the English variant:

```yaml
language: en-US
voice_profile:
  host_a: Aiden
  host_b: Vivian
```

For a direct comparison there are two local projects: `projects/windows-pilot/` in German and
`projects/windows-pilot-en/` with the English translation. Both currently use Aiden as `host_a` and Vivian as `host_b`,
the same model revision, seed and pauses; the original dialogue samples were spoken by Ryan and Serena. The texts cover
the same content; speaking pace and duration can differ by language.

```powershell
.\.venv\Scripts\pla.exe audio-probe .\projects\windows-pilot-en --approve-audio
```

A changed language or voice needs a new `audio-probe` run. The language choice affects the voice sample only, not
the Codex connection probe.

### Listen to all nine voices

The installed CustomVoice model provides `Ryan`, `Serena`, `Aiden`, `Vivian`, `Uncle_Fu`, `Ono_Anna`, `Sohee`, `Eric` and
`Dylan` (`VOICES` in `scripts/generate-voice-samples.py`; catalog: [Providers and models](PRODUCT.md#providers-and-models)).
A separate script speaks the same short text in English and German for every voice. It takes the Qwen settings of the
given project and does not change its voice configuration.

With FFmpeg on the current PATH:

```powershell
.\.venv\Scripts\python.exe .\scripts\generate-voice-samples.py --project-dir .\projects\windows-pilot
```

The results go to `projects/voice-samples/`: a generated listening index with the single files and time marks
(`README.md`), 18 single MP3 files and one continuous comparison file per language, all local and excluded from Git.
The continuous files follow the voice list above; chapter files and transcripts map the sections to the voices.

- `--languages en-US` or `--languages de-DE` limits a new call to one language; `--output-dir` chooses another output
  folder.
- Running it again with the same settings reuses valid Qwen segments from the cache; the assembly runs again.
- To pick a voice, copy its name into `voice_profile.host_a` or `voice_profile.host_b` and start a new probe run. The
  two hosts need different voices.

## Move to another computer

Copy the repository and the folders you want under `projects/`. Do not copy `.venv`, `.venv-tts` or Windows binaries;
set them up again locally. Personal projects are excluded from Git, so a checkout alone does not carry them over.

Finished MP3 files and scripts stay readable. Before a new local recording, configure the project for this computer with
`setup-qwen.py --project …` (Windows: `setup-qwen.ps1 -ProjectDir …`). Old running jobs partly contain absolute paths and
approvals bound to their inputs; resuming them on another computer is not guaranteed. After a configuration change,
approve a new audio run. Adjust locally imported sources ([Local files](SECURITY.md#local-files)) and explicitly set
program paths such as `runtime.codex_executable` where needed.

## Update the Studio

1. Get the new code, for example with `git pull`. The controller is installed in editable mode (`pip install -e .`), so
   the code itself is read from `src/`.
2. If the update changed the dependencies in `pyproject.toml` (the controller needs them current, including
   `pypdf[fonts]`), reinstall. On Windows, only when no other `pla` process uses `.venv\Scripts\pla.exe`, for example
   a CLI run in another window:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -e .
   ```

   On macOS and Linux, `sh scripts/setup.sh` does the same.
3. Restart the Studio: a running server and its scheduler keep the code they started with, and reloading the browser
   page is not enough. When the Studio notices new code it shows „Das Studio hat neuen Code“ with
   **„Neu starten, sobald nichts läuft“** (restart once nothing is running); see
   [Stopping and resuming](STUDIO.md#stopping-and-resuming). Alternatively, once the running job has finished, click
   **„Studio beenden“** (stop the Studio) and start it again.
4. Enter an OpenRouter key that was kept only in the Studio again; it lived in the old server's memory
   ([Secrets and keys](SECURITY.md#secrets-and-keys)). Until then, queued Gemini episodes show
   „wartet auf den OpenRouter-Key“.

A failed restart leaves its reason in `.studio/relaunch.log` ([Log files](#log-files)).

## Logs and diagnosis

### Log files

| File | Written by |
| --- | --- |
| `.studio/studio.log` | The Studio server |
| `<project>/studio/worker.log` | Each job process the Studio starts |
| `<project>/logs/pla.log` | Single CLI commands that are given a project folder |
| `.studio/relaunch.log` | A restart of the Studio; holds the reason when the new server fails before its own log starts (why: D-117) |
| `<project>/studio/stderr/<job>.log` | Error output of a job process |
| `runs/<run_id>/failures/<stage>_<attempt>_<time>.txt` | Cleaned traceback of a stage that stopped |
| `<project>/studio/failures/worker_<time>.txt` | Cleaned traceback of a job process that failed unexpectedly |

`.studio/` is in the Studio's workspace, normally the repository folder. `studio.log`, `worker.log` and `pla.log` rotate
at 2 MB (`logs.MAX_BYTES`) and keep three older files (`logs.BACKUPS`). `relaunch.log` starts afresh once it reaches
1 MB. Error-output files of finished jobs older than an hour are removed when the next job process starts.

Log files and terminal output are filtered for credentials, tracebacks included (`logs.scrub`), and so are the
traceback files ([Credentials in traces, diagnostics and logs](SECURITY.md#credentials-in-traces-diagnostics-and-logs)).

### When a step fails

- **Unexpected program error.** The Studio message stays short; the hold card opens the cleaned traceback as text
  under **„Technische Details“** (technical details), and `pla status` lists the same files („Technische
  Fehlerprotokolle“).
- **Stop with a domain reason** (quota, missing evidence, review objections). The log gets one line without traceback
  and the message names no traceback file, but the stop is still recorded under `runs/<run_id>/failures/`, like every
  stage stop (`runner.execute_stages`).
- **Job process ended without a result**, for example after a crash. The Studio sets its run back to resumable and shows
  the last lines of `<project>/studio/stderr/<job>.log`, filtered for credentials, under **„Technische Details“**.
- **Per-call records** (provider choice, diagnostics, failures, rejected output) are in `runs/<run_id>/calls/`
  ([Run folder and manifest](ARCHITECTURE.md#run-folder-and-manifest)).

### Exit codes

Every `pla` command returns `0` for success, `1` for blocked or failed, `2` for a quota pause and `130` for an
interruption. `pla doctor` returns `1` when the installation is not ready; `pla quota` returns `2` when a subscription is
usable but none has quota left, and `1` when none is usable. `--json` gives machine-readable results
([Commands](PRODUCT.md#commands)).
