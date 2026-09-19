# Frozen evidence gate evaluation

This is an offline contract evaluation, not a scientific-quality benchmark. All source passages are fictional. Labels and scripted review receipts were authored by the implementation agent; independent human annotation is pending. The heldout partition is separated from the development cases, but is not an independently curated holdout.

The 14 fixed cases cover definitions, empirical assertions and cross-source synthesis. Eight contain seeded defects: an unsupported extra clause, repeated study/version presented as independent, correlation promoted to causation, lost population/time scope, an incomparable result labeled a contradiction, a missing explanation, and an unjustified reopening. Six controls include valid definitions, independent studies, supported uncertainty, qualified comparisons and equivalent spoken numbers.

Run from the repository root with the project environment:

```powershell
.\.venv\Scripts\python.exe evals/research_evidence/run.py --split development
.\.venv\Scripts\python.exe evals/research_evidence/run.py --split heldout
.\.venv\Scripts\python.exe evals/research_evidence/run.py --split all --output evals/research_evidence/results/offline-v1.json
```

The default mode replays scripted receipts through the production validators. Its baseline is the previous issue-only final acceptance rule, replayed against the same receipts. It is a reconstructed gate comparison, **not a before/after experiment with real models**. The seeded receipts deliberately have optimistic aggregate issue lists while their structured details expose the defect. The first replay accepts eight defective cases under the old rule and none under the new rule, with no rejection of the six valid controls.

The report records corpus, prompt and schema hashes; per-split unsupported acceptances, valid false blocks, missing explanations, unjustified reopenings and accepted semantic drift; model identity and calls; and human correction time. Model identity and human correction time are `null` in offline replay, and model calls are zero. These are not inferred improvements in model comprehension, research latency or human effort.

For separately authorized model experiments, `--export-prompts PATH` exports source-bound review prompts and schemas without making calls. `--responses PATH` scores captured reviews. The file must contain `corpus_hash`, `model`, and a `cases` object keyed by every selected case ID, with each value a `SourceReview` or `ScriptReview` response. The scorer checks the corpus binding and keeps development and heldout metrics separate. Fixed synthesis candidates and objection-routing challenges still exercise deterministic gates; this interface is not an end-to-end discovery or routing benchmark. Record actual generation calls and timings alongside captured responses. Obtain independent annotations before making research-quality claims.

Keep the corpus unchanged when comparing policies. If an annotation needs correction, version the corpus and explain the correction; do not silently retune heldout labels to a model's answers.

## The false gap of 19 September 2026

The audit found a declared gap whose answer stood verbatim in a stored section: ep_003 told the
listener that V3's routing rule was missing while `src_79bf6b4435bc1b72#sec_6891d807643ea0ef`
contains "decrease the bias term by γ if overloaded, increase it if underloaded".

That case is not in `corpus.json`, because this corpus drives model review verdicts and the gap
probe makes no model call. It lives instead in `tests/test_gap_probe.py`, which runs in CI on every
change and pins three facts. The German gap text of finding f13 alone reports `no_hits` against the
English corpus: a term-overlap probe cannot cross languages, and that limit stays recorded so a
later change cannot quietly claim to have removed it. The same gap with its coverage row's
`gap_terms` (`expert`, `load`, `balancing`, `bias`, `rule`, `overloaded`, the words a composing
model is asked to supply in the sources' language) puts the section holding the rule at rank one
with three whole-word matches, so the case is caught whenever the terms are present. And the count
is of whole tokens: a section containing only "overruled" and "download" is not a hit for "rule"
and "load". On the sample project's full index the German text still returns no candidate, while
the gap terms rank `src_79bf6b4435bc1b72#sec_6891d807643ea0ef` first of 158.
