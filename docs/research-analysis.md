# Research pipeline analysis — 17 September 2026

All four implementation findings below are fixed. Historical reproductions are retained to explain the failures; the acceptance checks now require the corrected behavior. Existing production runs, approvals and budgets were not modified. Research quality still depends on the evidence and independent model reviews described under remaining limits.

## What the implementation does

1. `run_research` binds a run to the project, local-source hashes, selected model and research policy. It records live discovery and retrieves the chosen documents itself.
2. `sources.py` imports HTML, PDF or text, preserves original bytes and creates stable section references. Current limits are 20 MiB, 300 PDF pages and one million extracted characters per source; scanned images and omitted equations remain limitations.
3. `QuestionResearch.initialise` creates fixed tasks covering the original requirements, discovery questions and inherited gaps. A separate scope review can split overly broad tasks before reading starts.
4. Each task uses local lexical search and explicit reading windows. Candidate previews are not evidence. A model can request additional local reading or bounded web search, then submit an answer against every acceptance criterion.
5. `answer_errors` checks references and verbatim anchors. A separate model call checks factual support and source suitability. Verified answers, read sections, objections and counters are saved in a checksummed ledger.
6. Verified answers are assembled into a dossier. A source review and an assessment of all original requirements gate completion. Concrete objections are assigned to tasks for bounded reopening.
7. Publication writes the checked dossier and reports. Script planning accepts only a completed, hash-valid research run for the current brief.

The default limits are 150 production calls, 12 search rounds and 60 source candidates. Each task has at most 10 reader decisions per attempt, two additional web searches in total, and two reopenings. Verification calls also consume the production budget. Local search and reading do not call a model. Optional progress summaries have a separate allowance, so the production budget is not a total subscription/API usage limit.

## Fixed findings and original reproductions

### 1. Duplicate downloads can evade the source-attempt limit

Location: [`QuestionResearch.web_search`](../src/podcast_automate/question_research.py), especially construction of `attempted` and deduplication by `text_hash`.

The remaining allowance is reconstructed from retained source URLs and failures. A newly downloaded URL with identical text is discarded without adding an entry to either collection. The next task or step consequently sees the same remaining allowance and can download another duplicate. Per-step download receipts exist, but they are not used in the global attempt count.

Offline reproduction: with a source limit of **2**, one initial source plus two new URLs returning identical text produced **3 downloads**, **1 retained source**, and **0 recorded failures**. The configured source-candidate bound was exceeded.

Implemented: a checksummed attempt ledger survives deduplication, failure and interruption. Canonical identities are reserved before downloads; completed receipts are restored even after the last source slot has been used. Known original/final URLs are reused, and old duplicate-download receipts reconstruct missing attempts without changing saved source-index hashes. The reproduction now produces **2 downloads, 1 retained source and 1 duplicate report**, within the source limit.

### 2. A targeted dossier update can modify an unrelated verified answer

Location: [`QuestionResearch.compose`](../src/podcast_automate/question_research.py), where `targets` is extended with all findings attached to the dirty task's discovery-question IDs.

Several focused tasks can legitimately share one broad discovery question. Reopening one task then admits *all* findings attached to that question as patch targets. The instruction to preserve unrelated findings is therefore broader than the deterministic protection enforced by the patch operation.

Offline reproduction: definition and empirical tasks shared `q_energy`. Only the empirical task was dirty, but the patch was permitted to update both **`f_empirical` and `f_definition`**. This proves the edit boundary is too broad; it does not prove a particular live model already changed the definition. The final dossier audit remains active, but it does not restore the intended protection of unrelated verified work.

Implemented: composition saves a task-to-finding ownership map and uses it for later patches. Explicit inherited finding assignments take precedence; generated findings are matched against answer content and evidence. Ambiguous mappings are conservatively shared. A shared finding is editable only when all its owners are dirty. Coverage permissions remain separate. Reference repairs are constrained to the same edit boundary, and hashes of unrelated findings are checked afterwards. Compatible older patch receipts replay without another call; receipts that actually change protected findings are blocked. The reproduction now permits only **`f_empirical`**.

### 3. Long task IDs can collide when tasks are split

Location: [`scoped_plan`](../src/podcast_automate/question_scope.py), which derives children with `task.id[:29]` plus a short suffix.

Two valid, distinct parent IDs sharing their first 29 characters generate identical child IDs. This can also occur with long sibling IDs produced by an earlier split. The duplicate check correctly prevents corrupted state, but rejects an otherwise valid split as `invalid_question_scope`.

Offline reproduction: two valid 31-character parent IDs ending in `_a` and `_b` were each split into two parts. The derived IDs collided and the plan was blocked.

Implemented: short collision-free IDs retain their readable suffixes. Long or conflicting IDs use a deterministic suffix derived from the complete parent identity, child index and collision counter. Existing identities are reserved and allocation is independent of plan order. Parent mappings and ownership survive refinement. Long siblings and repeated splits now yield unique IDs of at most 32 characters.

### 4. Task planning does not account for the minimum call budget

Locations: [`QuestionResearch.initialise`, `research_task`, `verify`, `compose`](../src/podcast_automate/question_research.py) and [`reserve_call`](../src/podcast_automate/research.py).

A fresh run with N tasks needs at least **2N + 6 calls** in the ideal case: discovery, task planning, one scope review, an answer and independent verification for each task, dossier synthesis, source review and requirements assessment. This excludes every extra reading decision, search, repair, second scope review and reopening. Thus **73 tasks already exceed the default 150-call allowance**, even with ideal responses.

The saved production snapshot inspected during this analysis had **77 tasks**, with 1 verified, 75 pending and 1 researching; 33 production calls and 4 search rounds had been consumed. Its explicit run-bound approval raises the effective call limit to **250**, so it is incorrect to diagnose that run using the default 150 alone.

That run also has an inherited dossier and 77 dirty tasks. Its first composition uses batches of four, requiring 20 patch calls before source and requirement review. Under the current saved plan, an ideal remaining path is approximately **152 answer/verification calls + 20 composition calls + 2 final reviews = 174 calls**, against 217 remaining. That leaves about **43 calls** for further reading, searching, failed reviews and repairs. This is a lower-bound planning estimate, not a prediction of completion time or quality.

Implemented: new planning prompts include a stable snapshot of the approved allowance and per-task overhead. After scope review, the ledger, Markdown report and Studio show the minimum remaining calls, closing-stage reserve, available allowance and shortfall. Every uncached call is checked against this projection; optional searches, repairs and routing are charged in addition when needed. Pending answers and reusable synthesis/review receipts reduce the estimate. An infeasible plan blocks with `research_budget_insufficient` before answering; the agreed scope, saved answers and approvals remain intact. The existing run-bound approval mechanism supplies the effective limit on every check. Estimates are lower bounds, not a completion guarantee.

## Evidence quality and remaining limits

- **What is deterministically established:** an anchor occurs in a known section, that section was read, references and acceptance-criterion indices are valid, and saved evidence has not changed.
- **What still depends on model judgment:** whether the source supports the full claim, whether studies are independent, whether a causal explanation is sufficient, and whether a requirement is substantively answered. An external URL is a provenance check, not proof of an independent scientific result.
- The reader is lexical, with term normalization, limited prefix matching, pagination and neighboring passages. It is not general cross-language or semantic retrieval. Good source-language queries remain important.
- The dossier has a maximum of 120 findings and strict per-source quotation/paraphrase bounds. Many individually verified answers can therefore require substantial synthesis and compression; successful individual tasks do not guarantee effortless final composition.
- A blocked task is intentionally not reset by ordinary resume. This prevents endless loops, but the application does not yet offer a narrowly scoped, explicitly authorized retry after a changed external condition while keeping other verified answers.
- The whole-dossier audit and objection router can disagree with task reviews. Reopening is bounded, but separate calls can still share the same model's blind spots. No numerical reliability or speedup claim follows from the unit tests.

## Verification performed

After the fixes, the complete offline Python suite ran **392 tests: 391 passed, 1 platform-specific skip**. All **66 Studio UI tests passed**. The focused research suite includes 40 tests, with 17 new invariants covering these findings. The full-suite runner blocked real provider processes and nonlocal network connections.

The three original defect reproductions are covered by `tests/test_research_invariants.py`, using temporary synthetic projects and mocked downloads. This suite is included in normal test discovery; the separate acceptance script was removed because it repeated those checks:

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_research_invariants -v
```

The existing saved-reader evaluation was also run read-only against the documented production source snapshot. It inspected **37 sources and 4,275 sections**, successfully restored an **86-finding** dossier, and found the expected original definition at **rank 1**, with three sections available in the reading window. This confirms the retrieval of that particular saved passage, not the quality of all answers or a completed live run.

Regression tests additionally cover source exhaustion during resume, failed and equivalent URLs, ownership of newly composed findings, shared ownership, legacy patch replay, unchanged finding hashes, recursive ID splits, exact budget exhaustion and approved budget increases. No live research, paid model request, production audio generation, budget change or production-run mutation was performed. Complete-run quality on held-out topics still requires human assessment.

## Update, 19 September 2026: dead ends, stalls and growth

A second analysis asked whether the research step can explode, get stuck or fail to finish. It cannot loop forever: every model call passes the run budget, and each inner loop has a counter. It could, however, reach dead ends that no resume leaves, hang for the full deadline on a silent provider, and grow its ledger with every step. The pilot run `run_20260916_121103` showed all three: 77 tasks after splitting, one reader call silent for 30 minutes before the deadline, and the same step started three times by hand with each attempt charged. Findings and the implemented changes:

1. **A blocked task ended the run for good.** `question_research.run` raised on every resume. Now a blocked task can be accepted as a gap (`run_budget.approve_research_gap`, `pla approve --accept-gap`, Studio button). The workflow finishes without it, records the gap in the quality report, `open_questions.md` and `reports/research_quality.json` (`complete_topic_coverage: false`), and objections that only concern accepted gaps are recorded as residual objections instead of reopening anything. Objections against verified tasks still reopen them. The search-round limit can be raised by the same run-bound approval.
2. **Poisoned receipts.** `cached_call` stored an output before its deterministic checks and replayed the defect on every resume. Receipts are now written only after validation; a rejected output is kept as `<name>_rejected_NN.json` and the call repeats with the rejection named, at most twice, before an explicit block. A stored receipt that fails today's check is retired the same way. Plan, scope, search, review, grounding, assessment, routing and patch calls all validate this way.
3. **Task-count explosion.** Planning and scope review receive `max_tasks` from the approved allowance at about 5 calls per task (`question_budget.affordable_tasks`); an oversized plan or a split beyond the cap is re-asked, the final attempt is accepted and the two-call minimum projection decides. The projection reports the expected remaining calls beside the minimum.
4. **Stalls.** Streaming transports stop a call after 10 minutes without output (`process.STALL_TIMEOUT_SECONDS`), the pool repeats it once (`stall_retry.json`), and the app-server stdin is written from its own thread so a server that stops reading cannot bypass the deadline. Calls without a model response (timeout, stall, quota pause, stop, prompt too large) are refunded (`research.refund_call`); a resume also refunds calls a killed worker never finished (`research.reconcile_budget`). Call directories keep unique numbers through a separate sequence.
5. **Prompt size.** Claude refuses a prompt above `claude_code.PROMPT_LIMIT_CHARS` before starting, and the automatic subscription rule excludes Claude for such a call (`prompt_size.json`).
6. **Quota pauses.** The worker records `retry_at`; the Studio scheduler resumes a paused job after that time, at most three times per chain (`Studio.due_resumes`, `resume_due`).
7. **Unbounded local work.** PDF parsing runs in a child process with a 120-second limit (`pdf_text`); name resolution waits at most 10 seconds; the Studio shows how long a running worker has been silent.
8. **State growth.** Local search receipts keep query and references, not previews; index copies store document metadata plus a reference to the processed text (`research_ledger.save_index`), verified by text and section hashes; download receipts store only the added documents; the public ledger no longer embeds search receipts.

Verification: `tests/test_research_resilience.py` covers each item offline; the remaining research, provider and Studio modules were run green alongside it. No live model call was made.
