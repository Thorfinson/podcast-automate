# Targeted research and resume

Production research uses fixed sub-questions, targeted re-reading, independent answer reviews and stored individual completions. The earlier round controller has been removed. Old research runs are still read in through `bootstrap_legacy` and continued with the current flow; sources, valid drafts and consumed calls are kept.

## Automated regressions

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_research tests.test_question_research tests.test_research_quality tests.test_research_refinement tests.test_research_invariants tests.test_research_migration -v
node --test tests/studio_ui.test.cjs
```

All Python cases are also part of the regular test suite. Using temporary projects and simulated models, they check in particular:

- Findability of stored sections, page references, neighbouring context and read limits.
- Protection of uninvolved findings and renewed review of changed evidence.
- Source and call limits, and resuming without repeated searches or downloads.
- Completeness of the original guiding questions, independent review and limited reopening.
- Import of historical draft and repair formats, unchanged sources and checksums, and resuming before and after the old review stage.

Migration tests create the historical files directly. A second executable research pipeline and tests of its now unused control logic are not needed for that. The current research flow is described in [docs/RESEARCH.md](../../docs/RESEARCH.md) and its [evidence contracts](../../docs/RESEARCH.md#evidence-contracts); the analysis of 17.09.2026 with the background of the original errors was removed on 19.09.2026 and remains in the Git history (`docs/research-analysis.md`). The separate acceptance script was merged into the invariant tests.

## Check with a stored source corpus

`check_saved_reader.py` works read-only: no model calls, downloads or changes to the research job.

```powershell
.\.venv\Scripts\python.exe evals/research_refinement/check_saved_reader.py projects/mir-gehts-um-die-inhalte-der-doumente-di-c85f45 run_20260916_121103_388868_a082fa2a --query "Max-Neef Human Scale Development singular satisfiers synergic satisfiers definitions" --key-term "singular satisfiers" --key-term "synergic satisfiers" --expect-reference "src_6622dc67b5fea004#sec_f1dc13c2699a5406"
```

The stored test case contains **37 sources and 4,275 sections**. The expected original definition on page 24 reaches **rank 1**; re-reading returns it with two neighbouring sections. The import adopts the last formally valid draft with **86 findings** from `round_003/dossier_patch_applied.json`.

This check demonstrates the findability of this passage and the readability of the historical corpus. Simulated tests and stored sources do not replace a full run with real models or the subject-matter acceptance of a finished series. No percentage runtime improvement or general research quality can be derived from it.
