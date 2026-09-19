# Testing rules for podcast-automate

Which tests to run when, for agents and contributors. Claude Code loads this file through `CLAUDE.md`.

## The two suites

| Suite | Command | Size | Needs |
| --- | --- | --- | --- |
| Python | `python -m unittest discover -s tests` | about 500 tests, about 150 s | `ffmpeg` and `ffprobe` on PATH |
| Browser logic | `node --test tests/studio_ui.test.cjs` | 70 tests, under 1 s | Node 22 |

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
| Editing `logs.py` or anything that keeps a file open for the life of the process | `test_logs` and `test_cli`, then confirm on Windows. Open handles break temp-directory cleanup there and nowhere else. |
| Editing `claude_code.py`, `subscriptions.py` or `provider_pool.py` | Their mapped tests plus `test_scripting`, `test_research`, `test_status_summary`, `test_studio` and `test_text_selection`. Every run goes through the pool; the fixed-provider path must stay free of quota queries. |
| Before committing or opening a pull request | Both full suites, green. |
| CI (`.github/workflows/tests.yml`) | Both suites on Linux, macOS and Windows against the installed package. All three legs must pass. Superseded runs of the same ref are cancelled automatically. |
| Before tagging a release | Nothing beyond CI. There is no deployment pipeline. |

A green subset is not evidence that the suite passes. Tests patch functions where they are looked up,
for example `patch("podcast_automate.script_pipeline.research_foundations")`. Moving a function between
modules breaks such a patch silently, and only the full suite shows it.

## Source module to test modules

Derived from imports and patch targets in `tests/`. Regenerate with the snippet at the end when modules move.

- `attachments` → `test_attachments`
- `audio`, `episode_audio` → `test_audio`, `test_episode_audio`, `test_parallel`, `test_polishing`, `test_series_review`, `test_speech`, `test_studio`, `test_teaching`
- `claude_code` → `test_claude_code`, `test_provider_pool`
- `cli`, `doctor` → `test_cli`, `test_provider_pool`; resume through the CLI is also exercised by `test_episode_audio`, `test_openrouter`, `test_research`, `test_scripting`
- `codex`, `codex_stream`, `call_activity`, `model_trace`, `process` → `test_codex`, `test_codex_stream`, `test_setup_schema`, `test_model_trace`, `test_status_summary`, `test_studio_progress`, `test_research_resilience`; the app-server quota RPC in `codex_stream` is covered by `test_subscriptions`, the Claude stream observer in `call_activity` by `test_claude_code`
- `editorial`, `prompts` → `test_prompts`, `test_teaching`, `test_episode_framing`
- `execution`, `parallel_speech` → `test_parallel`
- `logs` → `test_logs`, `test_cli`
- `openrouter` → `test_openrouter`, `test_setup_schema`
- `polishing` → `test_polishing`, `test_scripting`
- `provider_pool` → `test_provider_pool`; `text_generation_settings` lives here and is exercised by `test_scripting` and `test_text_selection`
- `question_research`, `question_answering`, `question_synthesis`, `question_scope`, `question_budget`, `question_dependencies` → `test_question_research`, `test_research_invariants`, `test_evidence_contracts`, `test_model_trace`
- `research` and every `research_*` module → `test_research`, `test_research_invariants`, `test_research_migration`, `test_research_quality`, `test_research_refinement`, `test_research_resilience`, `test_research_status`, `test_evidence_contracts`, `test_question_research`, `test_attachments`, `test_parallel`, `test_run_budget`, `test_prompts`
- `run_budget`, `script_budget`, `script_checkpoints` → `test_run_budget`, `test_script_budget`, `test_question_research`, `test_research_resilience`, `test_studio_progress`, `test_teaching_research`
- `provider_pool`, `subscriptions`, `claude_code` → `test_provider_pool`, `test_subscriptions`, `test_claude_code`, `test_research_resilience`, `test_studio`
- `scripting`, `script_pipeline`, `script_checks`, `script_artifacts`, `script_models`, `script_evidence` → `test_scripting`, `test_teaching`, `test_teaching_research`, `test_polishing`, `test_parallel`, `test_planning`, `test_provider_pool`, `test_run_budget`, `test_series_review`, `test_episode_framing`, `test_openrouter`, `test_speech`, `test_studio`, `test_studio_scripts`, `test_text_selection`, `test_evidence_contracts`
- `series_review` → `test_series_review`, `test_scripting`
- `sources`, `pdf_text`, `downloads` → `test_downloads`, `test_attachments`, `test_evidence_contracts`, `test_question_research`, `test_research`, `test_research_migration`, `test_research_resilience`, `test_teaching_research`
- `speech`, `voice_samples`, `qwen_worker`, `platforms` → `test_speech`, `test_voice_samples`, `test_parallel`, `test_setup_schema`, `test_studio`, `test_platforms`, `test_worker_cache`
- `status_summary`, `research_status` → `test_status_summary`, `test_research_status`, `test_provider_pool`
- `subscriptions` → `test_subscriptions`, `test_provider_pool`
- `studio`, `studio_worker`, `studio_progress`, `studio_scripts` → `test_studio`, `test_studio_progress`, `test_studio_scripts`, `test_studio_trash`, `test_attachments`, `test_parallel`, `test_platforms`, `test_provider_pool`, `test_research_resilience`, `test_setup_schema`, `test_speech`, `test_text_selection`, `test_question_research`, `test_run_budget`
- `teaching`, `teaching_research` → `test_teaching`, `test_teaching_research`, `test_polishing`, `test_scripting`
- `text_settings` → `test_text_selection`, and `test_cli` for the doctor catalog line

Regenerate the mapping:

```python
import re, pathlib, collections
mapping = collections.defaultdict(set)
for path in sorted(pathlib.Path("tests").glob("test_*.py")):
    text = path.read_text(encoding="utf-8")
    mods = set(re.findall(r'from podcast_automate\.(\w+) import', text))
    mods |= set(re.findall(r'patch\(["\']podcast_automate\.(\w+)\.', text))
    for m in mods:
        mapping[m].add(path.stem)
for mod in sorted(mapping):
    print(f"{mod}: {', '.join(sorted(mapping[mod]))}")
```

## Rules for the tests themselves

- Expectations must be independently justified. Budget arithmetic is checked against what the fixture
  pipeline actually spends in `test_run_budget`, not against the constants in `script_budget.py`. When a
  stage gains a model call, update `STAGE_CALLS`; that test fails until you do.
- Business rules keep their tests through refactors. Do not delete a test because it is slow or rarely
  fails. When replacing one, name the retained protection in the commit message.
- Run-folder invariant: on a resume, only `budget.json` and `budget_projection.json` may change. Every
  other file under `runs/<run_id>/` must stay byte-identical.
- Model responses come from `patch("podcast_automate.<module>.CodexAdapter.structured")` and the helpers
  in `tests/*_fixtures.py`. The script fixture asserts `search=False`; supplementary research is patched at
  `script_pipeline.research_foundations`.
- Tests that call `configure_logging` must call `release_logging` or close the handlers before their
  temporary directory is removed.
- No sleeps, no network, no real model or speech calls, no GPU. Retrieval is patched at
  `podcast_automate.sources.download`.
- No real `codex` or `claude` process and nothing under `~/.podcast-automate`. Tests that reach the
  subscription rule set `PLA_SUBSCRIPTIONS_STORE` to a temporary path and patch
  `podcast_automate.subscriptions.codex_quota` and `claude_quota` (see `QuotaFakes` in
  `test_provider_pool`); adapter tests use the fake CLIs in `test_claude_code` and `test_subscriptions`.
- Never weaken a gate to get green: no retries, no skipped assertions, no relaxed expectations without a
  justified behaviour change.
