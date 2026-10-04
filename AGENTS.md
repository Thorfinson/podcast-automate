# Agent instructions for podcast-automate

Commands, layout and conventions for coding agents and contributors, then the testing rules: which tests to run
when. Claude Code loads this file through `CLAUDE.md`. What the product does and how the docs are organised is in the
[README](README.md).

## Commands

Windows (PowerShell); macOS and Linux use `.venv/bin/…` and `sh scripts/setup.sh`.

```powershell
.\.venv\Scripts\python.exe -m pip install -e .        # install or update the controller (no pla process may be running)
.\scripts\setup-ffmpeg.ps1                             # once: FFmpeg into tools/ffmpeg/bin
.\.venv\Scripts\pla.exe studio                         # run the Studio on http://127.0.0.1:8765
.\.venv\Scripts\pla.exe doctor --skip-tts              # check the installation without local Qwen
.\.venv\Scripts\python.exe scripts\test_map.py         # regenerate the raw source-to-test map
```

The test commands are under [The two suites](#the-two-suites). Installing and updating in detail:
[OPERATIONS](docs/OPERATIONS.md).

## Layout

| Path | Content |
| --- | --- |
| `src/podcast_automate/cli.py` | `pla` entry point; one subcommand per run kind |
| `src/podcast_automate/research*.py`, `question_*.py`, `sources.py`, `jev.py` | Research run: sub-questions, reading, answer review, dossier |
| `src/podcast_automate/scripting.py`, `script_*.py`, `polishing.py`, `teaching*.py`, `series_review.py`, `review_authority.py` | Script run: plan, teaching plans, writing, polishing, reviews and who settles their points |
| `src/podcast_automate/episode_audio.py`, `audio.py`, `speech.py`, `parallel_speech.py`, `qwen_worker.py` | Audio run: synthesis, assembly, export |
| `src/podcast_automate/claude_code.py`, `codex*.py`, `openrouter.py`, `provider_pool.py`, `subscriptions.py` | Text provider adapters and the per-call provider choice |
| `src/podcast_automate/studio*.py`, `web/` | Local Studio server, worker processes and the browser UI |
| `src/podcast_automate/models.py`, `storage.py`, `errors.py`, `runner.py` | Shared contracts, file I/O, errors and the run manifest |
| `src/podcast_automate/prompts/` | Every model instruction as a text file ([prompts README](src/podcast_automate/prompts/README.md)) |
| `tests/` | Python suite (`test_*.py`, fixtures in `*_fixtures.py`) and the browser suite `studio_ui.test.cjs` |

The other top-level folders are in the [README](README.md#layout). `projects/`, `runs/`, `.studio/`, `tools/ffmpeg/`,
`.venv/` and `.venv-tts/` are local and git-ignored.

## Conventions

- Raise `errors.AppError` with a stable `code`; the Studio and the manifest show the code, so renaming one is a
  behaviour change.
- Write project files through `storage` (`atomic_text`, `write_json`, `write_yaml`); read files that another worker
  may be writing through `storage.read_text`.
- A model call carries a `prompt_version` tag. Bump it when the meaning of its prompt changes; checkpoints are bound
  to the prompt hash, so a changed prompt re-runs only the calls it affects.
- Code, comments and docs are English. Text the Studio shows (`web/app.js`, `studio_messages.py`) is German.
- Docs: every fact has one home; the doc index is in the [README](README.md#docs). When you change a documented fact,
  update its owner doc in the same change and bump its `last_reviewed`. Unverified items go into the verification
  list of the active plan in `docs/specs/`; decisions into [DECISIONS](docs/DECISIONS.md); traps into
  [GOTCHAS](docs/GOTCHAS.md); user-visible changes into [CHANGELOG](CHANGELOG.md).

## Before you change…

| If you touch | Read first |
| --- | --- |
| Provider choice, quotas, budgets, approvals, resume | [BUSINESS_LOGIC](docs/BUSINESS_LOGIC.md) |
| Research modules | [RESEARCH](docs/RESEARCH.md) |
| Script, polishing, teaching or series review modules | [SCRIPTS](docs/SCRIPTS.md), [TEACHING](docs/TEACHING.md) |
| Audio, speech, spoken forms | [AUDIO](docs/AUDIO.md) |
| Studio server or `web/` | [STUDIO](docs/STUDIO.md) |
| Text provider adapters | [ARCHITECTURE](docs/ARCHITECTURE.md#text-provider-adapters), [SECURITY](docs/SECURITY.md) |
| Keys, local file access, the Studio's network access | [SECURITY](docs/SECURITY.md) |
| Prompts | [prompts README](src/podcast_automate/prompts/README.md) and the prompt row under [When to run what](#when-to-run-what) |

## The two suites

| Suite | Command | Size | Needs |
| --- | --- | --- | --- |
| Python | `python -m unittest discover -s tests` | about 1,070 tests, 6 to 8 minutes | `ffmpeg` and `ffprobe` on PATH |
| Browser logic | `node --test tests/studio_ui.test.cjs` | 165 tests, under 1 s | Node 22 |

Model calls, downloads and speech synthesis are simulated in both suites; FFmpeg assembly is real.
No account, API key, GPU or network is needed. Never add a test that performs a real model call.
Real-model checks live under `evals/` and are run by hand.

Windows setup (PowerShell), after `scripts/setup-ffmpeg.ps1` has run once:

```powershell
$env:PATH = "$PWD\tools\ffmpeg\bin;$env:PATH"
.\.venv\Scripts\python.exe -m unittest discover -s tests
node --test tests/studio_ui.test.cjs
```

macOS and Linux use `.venv/bin/python` after `sh scripts/setup.sh`. Leave `.venv-tts` alone: it holds
several gigabytes of PyTorch and any scan of it takes minutes.

## When to run what

| Situation | Run |
| --- | --- |
| Editing one source module | The test modules mapped to it below, plus those mapped to any module you moved a function into or out of. |
| Editing `web/app.js` or `web/*.html` | The browser suite. |
| Editing `models.py`, `storage.py`, `errors.py`, `runner.py` or any `tests/*_fixtures.py` | The full Python suite. Nearly every test builds on these. |
| Editing `prompts/*.txt` or `prompts.py` | `test_prompts` plus the tests mapped to every module that composes the changed text. If the meaning changed, bump the `prompt_version` tag at the call site and run the real-model evals under `evals/teaching_quality/` by hand. |
| Editing `logs.py` or anything that keeps a file open for the life of the process | `test_logs`, `test_cli` and `test_core`, then confirm on Windows. Open handles break temp-directory cleanup there and nowhere else. |
| Editing `claude_code.py`, `subscriptions.py` or `provider_pool.py` | Their mapped tests plus `test_scripting`, `test_research`, `test_status_summary`, `test_studio` and `test_text_selection`. Every run goes through the pool; the fixed-provider path must stay free of quota queries. |
| Before committing or opening a pull request | Both full suites, green. |
| CI (`.github/workflows/tests.yml`) | Both suites on Linux, macOS and Windows, each with Python 3.12 and 3.13, against the installed package. All six legs must pass. Superseded runs of the same ref are cancelled automatically. |
| Before tagging a release | Nothing beyond CI. There is no deployment pipeline. |

A green subset is not evidence that the suite passes. Tests patch functions where they are looked up,
for example `patch("podcast_automate.script_pipeline.research_foundations")`. Moving a function between
modules breaks such a patch silently, and only the full suite shows it.

## Choosing what to run

Agents run tests only as this policy selects them. It is the standard the suites are reviewed against:
meaningful protection per unit of cost, not test count, lines or coverage.

- Select by the mapping below, including transitive dependents. A module that a function moved into or
  out of, a shared helper, a fixture, a schema and a prompt all count as changed. Filename proximity, a
  test's name or a judgement call alone never justify leaving a mapped test out; when the mapping looks
  unreliable, broaden the selection instead of narrowing it.
- During implementation, run the focused technical tests and the affected business-rule tests and keep
  a small, cheap baseline green. Before a commit or pull request, run both suites in full. Nothing more
  exists before a release, because there is no deployment pipeline.
- Broader or expensive checks (the real-model evals under `evals/`, a mutation or timing pass) run by
  hand or on a named trigger, never silently skipped. A deferral states why it is acceptable, what
  triggers the run, who owns it and which risk remains until then.
- A passing subset is not evidence that the suite passes, and an unchanged business rule is no reason
  to skip its test: helpers, schemas, rounding, time, configuration and wiring change outcomes too.
- Never make a run green by retrying, skipping or relaxing an assertion. A failure is a finding until it
  is explained; a quarantine names the lost protection, an owner, a deadline and a compensating check.
- Report honestly: what ran, how long it took, what was not run and why. Fewer test functions are not
  less compute, one green run says nothing about flakiness, and a proposed command is not an executed one.

## Source module to test modules

Derived from imports and patch targets in `tests/test_*.py`, then curated: a line also names the test
modules a change reaches through transitive dependents. Regenerate the raw list with
`scripts/test_map.py` (see the end of this section) when modules move, and fold its additions in here.

- `attachments` → `test_attachments`
- `audio`, `episode_audio` → `test_audio`, `test_episode_audio`, `test_expression`, `test_parallel`, `test_polishing`, `test_series_review`, `test_speech`, `test_studio`, `test_teaching`; both compose `spoken_forms`, and `assemble` needs real FFmpeg for the chapter and pause checks; the montage progress the Studio shows is asserted in `test_audio` and read back in `test_studio`. `speech` imports `audio.silent_runs` and `audio.PAUSE_TAGS` for the Gemini health gate, so a change to either also selects the `speech` line
- `claude_code` → `test_claude_code`, `test_provider_pool`
- `cli`, `doctor` → `test_cli`, `test_provider_pool`, `test_research_plan_gate` (`--approve-plan` and `pla approve --research-plan`), `test_research_resilience`; resume through the CLI is also exercised by `test_episode_audio`, `test_openrouter`, `test_research`, `test_scripting`
- `codex`, `codex_stream`, `call_activity`, `model_trace`, `structured_trace`, `process` → `test_codex`, `test_codex_stream`, `test_setup_schema`, `test_model_trace`, `test_status_summary`, `test_studio_progress`, `test_research_resilience`, `test_platforms`, `test_evidence_contracts`, `test_scripting`, `test_teaching`; the app-server quota RPC in `codex_stream` is covered by `test_subscriptions` and reached through `subscriptions` by `test_provider_pool` and `test_studio`, the Claude stream observer in `call_activity` and the trace view of `model_trace` by `test_claude_code`, and the call subject a parallel script stage sets by `test_parallel`. No test imports `codex_stream` or `structured_trace` directly; they are reached through `codex` and `subscriptions`, and through `model_trace`
- `editorial`, `prompts` → `test_prompts`, `test_teaching`, `test_episode_framing`, `test_scripting`, `test_series_arc`, `test_teaching_research`, `test_research_audit`
- `evidence_models` → `test_evidence_contracts`, `test_question_research`, `test_research_audit`, `test_research_quality`; it is a schema that the `question_*`, `research_*`, `script_models`, `script_pipeline` and `teaching_research` modules embed, so their lines apply as well
- `jev` → `test_jev`, `test_teaching_research` (a German gap only Jev finds, end to end), `test_studio` (the switch and the key handover); `evals/jev_decisions/run.py` measures it against real runs by hand
- `expression` → `test_expression`, `test_prompts`; the recording tests there run `episode_audio` with a Gemini choice and a patched `episode_audio.AdapterPool`, `tag_episode` for reading and `studio_worker.express_published` after a script run; `test_studio` covers the approval bound to the tags read
- `execution`, `parallel_speech` → `test_parallel`, `test_research_parallel`, `test_speech`; the Jev default of a new German project is asserted in `test_studio` and its keyless fallback in `test_teaching_research`. `parallel_speech` delegates each segment to `speech.run_gemini_tts` with the shared cache locked, so a change there selects the `speech` line, which already holds `test_parallel`
- `logs` → `test_logs`, `test_cli`, `test_core`
- `models`, `errors`, `runner` → the full Python suite (see the table above); `test_core` covers the manifest, probe and status paths of `runner` directly
- `openrouter` → `test_openrouter`, `test_setup_schema`
- `polishing` → `test_polishing`, `test_scripting`, `test_episode_framing`; `compare_dialogue` is also driven by `evals/dialogue_polishing/run.py`
- `provider_pool` → `test_provider_pool`; `text_generation_settings` lives here and is exercised by `test_scripting` and `test_text_selection`
- `question_research`, `question_answering`, `question_synthesis`, `question_scope`, `question_budget`, `question_dependencies`, `question_ownership`, `question_sources` → `test_question_research`, `test_question_answering`, `test_research_audit`, `test_research_parallel`, `test_research_plan_gate`, `test_research_resilience`, `test_research_invariants`, `test_evidence_contracts`, `test_model_trace`; `question_ownership` is reached only through `question_synthesis`
- `script_advisories` → `test_script_advisories`; the report field is asserted by `test_scripting`
- `review_authority` → `test_review_authority`, `test_polishing`, `test_scripting`, `test_teaching`; the review loops of `polishing`, `script_pipeline` and `teaching` use it, so their lines apply as well. `scripts/call-baseline.py` → `test_review_authority`, which loads the script from its file and reaches `production_report` through it
- `research_gap_probe` → `test_gap_probe`, `test_jev`, `test_question_research`, `test_research`, `test_research_quality`, `test_scripting`, `test_teaching_research`
- `provided_works` → `test_provided_works`; the run reading a provided work and retrying its question in `test_question_research`, the raw upload route in `test_studio`, the book limits of `pdf_text` in `test_research`; the research page's list of missing works in the browser suite
- `research_dates`, source types, idea sources and task aims (`research_models`, `research_tasks`, `research_evidence`, `question_research.check_aims`, `sources.import_source`, `pdf_text`) → `test_source_types` in addition to the research list below; the starting library (`sources.load_library`) → `test_research`
- `research` and every `research_*` module → `test_source_types`, `test_research`, `test_research_audit`, `test_research_parallel`, `test_research_plan_gate`, `test_research_invariants`, `test_research_migration`, `test_research_quality`, `test_research_refinement`, `test_research_resilience`, `test_research_status`, `test_evidence_contracts`, `test_question_research`, `test_question_answering`, `test_provided_works`, `test_gap_probe`, `test_attachments`, `test_parallel`, `test_run_budget`, `test_prompts`, `test_provider_pool`, `test_model_trace`; `research_ledger` is also read by `test_studio`, `test_studio_progress` and `test_teaching_research`, and `research_models` by `test_planning`, `test_scripting` and `test_teaching_research`
- `run_budget`, `script_budget`, `script_checkpoints` → `test_run_budget`, `test_script_budget`, `test_question_research`, `test_research`, `test_research_parallel`, `test_research_plan_gate` (plan approvals and their receipts), `test_research_resilience`, `test_studio`, `test_studio_progress`, `test_studio_automation`, `test_teaching_research`, `test_provider_pool`, `test_cli`, `test_scripting`, `test_series_review`, `test_teaching`, `test_polishing`
- `provider_pool`, `subscriptions`, `claude_code` → `test_provider_pool`, `test_subscriptions`, `test_claude_code`, `test_research_resilience`, `test_studio`
- `scripting`, `script_pipeline`, `script_checks`, `script_artifacts`, `script_models`, `script_evidence` → `test_series_arc`, `test_scripting`, `test_cli`, `test_teaching`, `test_teaching_research`, `test_polishing`, `test_parallel`, `test_planning`, `test_provider_pool`, `test_run_budget`, `test_series_review`, `test_episode_framing`, `test_openrouter`, `test_speech`, `test_studio`, `test_studio_scripts`, `test_text_selection`, `test_evidence_contracts`, `test_episode_audio`, `test_expression`, `test_research_quality`, `test_script_advisories`
- `series_review` → `test_series_review`, `test_series_arc`, `test_scripting`, `test_cli` for the standalone `pla series-review`. `test_series_review` also stops a run with a series correction at each of its model calls and resumes it (about 25 s); it guards every checkpoint of `script_pipeline`, `teaching` and `series_review`
- `sources`, `pdf_text`, `downloads` → `test_downloads`, `test_attachments`, `test_evidence_contracts`, `test_question_research`, `test_research`, `test_research_migration`, `test_research_parallel`, `test_research_resilience`, `test_teaching_research`
- `spoken_forms` → `test_spoken_forms`, `test_audio`, `test_episode_audio`, `test_speech`; the cache-key rule shared with `qwen_worker` is pinned by `test_worker_cache`
- `storage` → `test_storage` for the `replace_file` and `read_text` retries; every other test module builds on the rest of it, so the full suite is the gate (see the table above). A run file that one research worker writes while another may read it (`budget.json`, `research_questions.json`) is read through `storage.read_text`; a raw `Path.read_text` there is a Windows race that `test_research_parallel` hits in about one run of three
- `transcription_check` → `test_transcription_check`; no recogniser is loaded by either suite
- `speech`, `voice_samples`, `qwen_worker`, `platforms` → `test_expression`, `test_speech`, `test_voice_samples`, `test_parallel`, `test_setup_schema`, `test_studio`, `test_platforms`, `test_worker_cache`, `test_episode_audio`, `test_audio`
- `status_summary`, `research_status` → `test_status_summary`, `test_research_status`, `test_studio_progress`, `test_provider_pool`, `test_research_parallel`
- `subscriptions` → `test_subscriptions`, `test_provider_pool`
- `studio_settings` → `test_studio` (`WorkspaceSettingsTests`), `test_text_selection`; `storage.load_project` reads it, so the `storage` line (the full suite) applies, and `speech`, `execution` and `studio_allowances` read their sections; the settings page is in the browser suite
- `studio_allowances`, `production_report`, and the restart when idle in `studio` → `test_studio_automation`; the page side is in the browser suite. No test imports `production_report` directly; it is reached through `studio`, whose line applies as well
- `studio`, `studio_worker`, `studio_progress`, `studio_scripts`, `studio_messages`, `studio_trash` → `test_expression`, `test_studio`, `test_studio_automation`, `test_studio_progress`, `test_studio_scripts`, `test_studio_trash`, `test_attachments`, `test_cli`, `test_core`, `test_parallel`, `test_platforms`, `test_provider_pool`, `test_research_parallel`, `test_research_plan_gate` (the plan approval route and the gated Studio resume), `test_research_resilience`, `test_setup_schema`, `test_speech`, `test_teaching`, `test_text_selection`, `test_question_research`, `test_run_budget`; `test_studio_trash` reaches `studio_trash` through the `studio` routes
- `teaching`, `teaching_research` → `test_teaching`, `test_teaching_research`, `test_episode_framing`, `test_polishing`, `test_scripting`, `test_run_budget`
- `text_settings` → `test_review_authority` (`call_role`, the A1 level), `test_text_selection`, `test_research_resilience`, `test_teaching_research`, `test_provider_pool` (defaults and the stage levels of `stage_effort`), and `test_cli` for the doctor catalog line
- `scripts/setup-qwen.py` → `test_qwen_setup`, which loads the script from its file, so the regenerated list does not show it

Regenerate the raw list from the repository root (Windows: `.\.venv\Scripts\python.exe scripts\test_map.py`):

```
python scripts/test_map.py
```

It reads `tests/test_*.py` with `ast` and counts a test module for a source module when it imports it
(`from podcast_automate.X import …`, `from podcast_automate import X`, `import podcast_automate.X`, also
inside a function) or patches it (`patch("podcast_automate.X.…")`). It prints one `module → tests` line
per module, then lists each module no test names with the importers that lead to tested modules and the
tests reached through them. The fixture modules are not read; editing one already selects the full suite.

## Rules for the tests themselves

- Expectations must be independently justified. Budget arithmetic is checked against what the fixture
  pipeline actually spends in `test_run_budget`, not against the constants in `script_budget.py`. When a
  stage gains a model call, update `STAGE_CALLS`; that test fails until you do.
- Business rules keep their tests through refactors. Do not delete a test because it is slow or rarely
  fails. When replacing one, name the retained protection in the commit message.
- Run-folder invariant: on a resume, only `budget.json`, `budget_projection.json` and the status record
  `run_manifest.yaml` (the runner stamps `updated_at`) may change. Every other file under `runs/<run_id>/`
  must stay byte-identical; `test_research_plan_gate` hashes the whole folder to check it.
- A research run started now assembles its dossier from the verified answers (prompt generation 3; finding ids
  `task_definition__f_energy`). Tests written for the composed dossier of older runs, and the fixtures the
  script, teaching and audio tests build on, pin generation 2 with `research_fixtures.composed_generation()`;
  a test of the default path leaves it unpinned.
- Model responses come from `patch("podcast_automate.<module>.CodexAdapter.structured")` and the helpers
  in `tests/*_fixtures.py`. The script fixture asserts `search=False`; supplementary research is patched at
  `script_pipeline.research_foundations`.
- Tests that call `configure_logging` must call `release_logging` or close the handlers before their
  temporary directory is removed.
- Every entry point resolves the project root once (`run_script`, `run_research`, `Studio`, `studio_worker.main`),
  and inner functions rely on it. A test that calls an inner function directly, or looks up Studio workers and jobs,
  resolves its temporary root in `setUp` (`Path(...).resolve()`); an 8.3 short `TEMP` on Windows otherwise breaks
  `relative_to` and the Studio's lookups.
- No sleeps, no network, no real model or speech calls, no GPU. Retrieval is patched at
  `podcast_automate.sources.download`.
- No real `codex` or `claude` process and nothing under `~/.podcast-automate`. Tests that reach the
  subscription rule set `PLA_SUBSCRIPTIONS_STORE` to a temporary path and patch
  `podcast_automate.subscriptions.codex_quota` and `claude_quota` (see `QuotaFakes` in
  `test_provider_pool`); adapter tests use the fake CLIs in `test_claude_code` and `test_subscriptions`.
- Never weaken a gate to get green: no retries, no skipped assertions, no relaxed expectations without a
  justified behaviour change.
