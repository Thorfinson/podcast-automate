# Jev decision checks

Can [Jev](https://openrouter.ai/docs/guides/community/jev), TypeSafe's decision model on OpenRouter
(`typesafe/jev-1.13`, beta), take over two narrow checks of this pipeline? Jev generates no text: it
answers typed yes/no, choice or score questions about a passage of at most 32,000 tokens and returns a
probability. It costs 0.042 USD per million input tokens, and its output is free. This evaluation asks
only yes/no questions:

1. **claim**: does a spoken script sentence stay faithful to the findings and source passages it cites?
   Each case is one `claim_checks` row of the script reviews the pipeline already ran, with the reviewer's
   verdict as the label. `supported` means every review of that quote said `preserved`, `drift` means
   every one said `drift`, and `disputed` means the reviews disagreed. Every drift and disputed case is
   kept. At most 250 supported cases per project are sampled (`--max-supported`).
2. **section**: does a source section help answer a research question? The label is whether the reviewed
   dossier cites the section for that question. Each question's pool holds three kinds of section:
   - the top 30 of today's BM25 reader (`SourceReader.search` with the research's saved search query and
     the question),
   - every cited section,
   - 15 random sections.

Both projects are included. Asimov asks German questions of mostly English sources, so it also tests
language crossing, which the term-overlap gap probe cannot do (see `evals/research_evidence/README.md`).

## Running it

```powershell
.\.venv\Scripts\python.exe evals\jev_decisions\run.py build    # offline, writes .studio/jev-eval/v1/cases.json
.\.venv\Scripts\python.exe evals\jev_decisions\run.py run      # asks before sending, then asks for the key
.\.venv\Scripts\python.exe evals\jev_decisions\run.py score    # offline, rewrites report.json
```

- **The key:** it comes from `OPENROUTER_API_KEY` or is asked for without echo, and it is never written.
- **The first requests:** before the cases go out, one tiny request per variant finds the endpoint and the
  name of the yes/no type. On 2026-09-29 `https://openrouter.ai/api/alpha/decisions` answered with the type
  name `noul`; the doubled path from the reference returned 404.
- **Resuming:** answers are appended to `answers.jsonl`, and a new `run` sends only the missing ones.
- **What is sent:** script sentences, findings and source passages go to TypeSafe through OpenRouter, and
  TypeSafe's data policy is not documented. Passages of user-supplied documents, which have no URL (the
  Asimov manifests, the LinkedIn collection), are left out unless `--include-user-material` is given.
- **Cost:** the build of 2026-09-29 held 3,893 cases, about 2.2 million input tokens, so roughly 0.09 USD.
  `--max-usd` (default 1.0) stops a run whose estimate is higher.
- **Where the output goes:** everything is under `.studio/`, which git ignores, because the cases quote
  private project material.

## What the report says, and what it does not

- `claim`:
  - **AUC:** how well Jev's probability separates supported from drift cases.
  - **Agreement at 0.5:** how often Jev's yes/no at a 0.5 cut matches the reviewer.
  - **Drift caught / supported passed:** the two error directions separately.
  - **Calibration bins:** in each probability range, how often the reviewer said `preserved`.
  - **Disputed cases:** the mean probability Jev gives them.
- `section`: recall of the cited sections among the first k (5, 10, 20), divided by `min(k, cited)`, for
  three orderings:
  - `bm25`, today's reader;
  - `jev_rerank`, Jev reordering exactly the BM25 candidates, which is the realistic use;
  - `jev_pool`, Jev over a pool that contains every cited section. That is a measure of separation, not
    of retrieval, and it cannot be compared with `bm25`.

The labels are model verdicts and citations, not human annotation, so the report measures agreement with
the current reviewer and recall of the evidence the research happened to cite. A section the research
never cited can still answer the question, and a `preserved` verdict can be wrong. The BM25 baseline runs
one saved query per question, whereas the research's readers searched with several queries of their own.
A good result is a reason to try Jev as a cheap second signal. It does not show that Jev is right where
the reviewer is not, and it would not replace a review that has to give a repair its reason.
