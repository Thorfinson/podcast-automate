---
title: Research
doc_type: business-logic
status: current
last_reviewed: 2026-10-04
covers:
  - src/podcast_automate/research.py
  - src/podcast_automate/question_research.py
  - src/podcast_automate/question_answering.py
  - src/podcast_automate/question_scope.py
  - src/podcast_automate/question_synthesis.py
  - src/podcast_automate/question_dependencies.py
  - src/podcast_automate/question_budget.py
  - src/podcast_automate/question_ownership.py
  - src/podcast_automate/question_sources.py
  - src/podcast_automate/research_gap_probe.py
  - src/podcast_automate/research_advisor.py
  - src/podcast_automate/research_evidence.py
  - src/podcast_automate/research_dates.py
  - src/podcast_automate/research_models.py
  - src/podcast_automate/research_patches.py
  - src/podcast_automate/research_quality.py
  - src/podcast_automate/research_reader.py
  - src/podcast_automate/research_retrieval.py
  - src/podcast_automate/evidence_models.py
  - src/podcast_automate/jev.py
  - src/podcast_automate/sources.py
  - src/podcast_automate/pdf_text.py
  - src/podcast_automate/provided_works.py
---

# Research

`pla research <project-dir>` turns a topic into a verified dossier: **topic → live search → retrieved sources → verified dossier**. The evidence contracts (`evidence.v1`) check each finding, source roles and independence, cross-source comparisons, and that a claim keeps its scope and strength all the way into the script ([Evidence contracts](#evidence-contracts)).

## Starting and resuming

The topic is in `project.yaml`; prepared source files are not required. Optional `seed_urls`, `seed_people`, `focus_questions` and `local_sources` add to the job; local source paths are relative to the project folder ([Local files](SECURITY.md#local-files)).

The controller environment needs the current project dependencies including `pypdf[fonts]`. Install only while no other `pla` process uses the Windows executable:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\pla.exe research .\projects\windows-pilot
.\.venv\Scripts\pla.exe status .\projects\windows-pilot
.\.venv\Scripts\pla.exe resume .\projects\windows-pilot
```

- `--json` gives machine-readable output; `resume --run-id <run_id>` picks an existing run.
- Content changes to `project.yaml` or to local source files need a new research run; operational fields do not bind a resumed run ([Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)).
- Research needs neither FFmpeg nor Qwen; nothing is spoken at this stage.

### Reusing verified sources

If only the explanation style or the audience changes, the verified sources can serve a new dossier:

```powershell
.\.venv\Scripts\pla.exe research .\projects\windows-pilot --reuse-sources <run_id>
```

The new run takes a verified copy of the sources and documents the original search run with its retrieval date. Dossier and quality review are made anew; if the adopted sources miss a quality criterion, the run researches further. Changed research questions, damaged files, changed local sources or a mismatched parser version prevent the reuse.

### Starting library

A new research run can take the stored sources of an earlier research run as a starting library (`pla research <project> --seed-corpus RUN_ID`; preselected in the Studio when starting research again, see [Research decisions and limits](STUDIO.md#research-decisions-and-limits)).

- The source search sees the library with title, address, type and date. What it or a later web search picks from it is copied into the new run instead of downloaded again (`sources.load_library`); a document with a changed raw file or an older parser is fetched again, and user material never joins the library.
- The run still searches anew, including for newer sources, and records the library in `research_request.json` and its inputs; a resume keeps it.

## Pipeline

### What a research run does

A research run works through six steps:

1. Derive sub-questions, required foundations and search terms from the topic brief.
2. Attribute named people and concrete works; where an identity is unclear, leave the attribution open.
3. Search for primary sources and subject overviews and check them for recency, relevance, method and conflicts of interest.
4. Search for relevant independent assessments, counter-positions and limits.
5. Connect statements across sources and explain existing contradictions.
6. Document coverage and remaining gaps. When the budget is exhausted, end with visible gaps.

Every prioritised sub-question needs viable material or a documented finding why it cannot be answered. Source count alone is no proof of quality; missing independent confirmation is made visible. Essential unresolved foundations block the episodes that depend on them.

No dossier is composed, closed or published while a sub-question has neither a verified answer nor a block (`research_questions_open`). A blocked sub-question stops the run itself (`research_questions_blocked`) until it is decided editorially or accepted as a gap ([Blocked sub-questions and decisions](#blocked-sub-questions-and-decisions)). (why: D-056)

### Stages

1. **Discovery:** The text model derives questions and search queries and searches live for accessible primary sources. The application checks actual search events in the CLI log; a mere claim by the model is not enough. Research always runs on a subscription ([Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection)); how its calls invoke the CLI: [Text provider adapters](ARCHITECTURE.md#text-provider-adapters).
2. **Retrieval:** The application downloads HTML, PDF or text itself, stores the raw files and extracts sections with stable IDs. PDFs keep page references. Duplicate URLs and identical texts are detected; access errors stay in the report.
3. **Fixed research questions:** A work plan breaks the original guiding questions into separately checkable sub-questions with completion criteria. Every original guiding question and every evidence gap already stored must be assigned. Definitions and empirical confirmation are handled separately; looking up a term demands no invented proof of effectiveness. Before reading, a new plan gets a separate [scope check](#scope-check-and-plan-approval).
4. **Reading and answering:** Each sub-question gets matching original sections. The model can search the whole stored corpus or within one source, request further result pages and read neighbouring sections; only when material is missing does it search the web for new sources. Its own notes remain hints and cannot support an answer on their own.
5. **Individual review:** A separate model call checks the answer against its fixed criteria and the evidence actually read, on two levels:
   - An unmet criterion, a refuted finding or one not checkable without context, an unsuitable source, a changed claim profile or unproven independence rejects the answer.
   - Anything else (a partly supported subordinate clause, a wording note, a blanket "not fully supported") lets it pass and is stored at the sub-question as a **review limitation** with finding and clause, in `research_questions.json` and `research_quality.md`. A written dossier of older runs carries it as `review_limitations` into the limitations of the affected finding.

   Every reason is one sentence of at most about 300 characters. Passed answers are stored with source and answer checksums and survive an interruption.

   The review of a reworked answer sees its earlier rejecting verdict (failed criteria, blocking findings, source suitability). Before the call, code fixes what may still fail (`review_scope`, stored at the sub-question): the points objected to earlier, new or changed findings, and every criterion resting on one of them or on another set of findings than before (for example after the rework removed one). Anything the review newly objects to in unchanged, previously passed material keeps the earlier verdict and is listed as a note among the limitations.
6. **Dossier and overall review:** The dossier is built only from the verified answers. For runs of prompt generation 3 it is assembled without a model call from every finding of every verified answer, unchanged, plus each answer's summary and limits ([Assembled dossier](#assembled-dossier)). An independent assessment of all original guiding questions remains required. Concrete objections go to the affected sub-questions; answers that together serve one guiding question are not reopened wholesale.
7. **Export:** Dossier and companion files become the current project state only after the individual and overall reviews passed, never with an open sub-question ([Revalidation instead of a stop](#revalidation-instead-of-a-stop)). Research artefacts and raw sources stay local and are excluded from Git.

### Assessment of the guiding questions

The first search takes the breadth of the original guiding questions into account. An independent model assessment then checks each guiding question against five mandatory criteria: a complete answer; an explained mechanism with foundations and an example; evidence actually read; a suitable independent cross-check; limits and justified connections.

Missing questions, unreadable texts and bare tables of contents fail this check. Scientifically open questions may be answered with competing explanations that are each supported and a clear account of the state of knowledge; missing research must not be recast as scientific uncertainty.

### Series goal, task aims and source types

(why: D-045)

- **Series goal** (`series_goal`, weights 0–3, [Project brief](CONFIGURATION.md#project-brief)): *understand* (explain ideas and theories in their own logic), *evaluate* (check what holds), *apply* (show how it is done today). Without a value the checking standard applies.
- **Task aim** (`aim` per research question):
  - *explain* names in `primary_works` the works the theory is explained from and asks for the basic question, assumptions, view of people or society, mechanism and terms; tests of the theory are separate *evaluate* questions.
  - *build* asks for steps, options and when to choose which, current tools, pitfalls and effort.
  - The plan is rejected when an explain question names no works or a goal weighted 2 or 3 has no matching questions (`question_research.check_aims`). The profiles of the answer review follow the aim (`research_evidence.AIM_PROFILES`).
- **Prerequisites** (`depends_on` per research question) only where a question needs another's verified answer in substance: checking needs the explanation of the same work, a synthesis its building blocks. The episode plan sets reading or teaching order later.
  - A blocked prerequisite blocks everything behind it, so a plan is requested again when a question other than a synthesis holds up more than a third of the questions, directly or through others (at least five are always allowed; `question_dependencies.gatekeepers`). The last permitted response still goes to approval instead of stopping the run. (why: D-046)
  - Reader and review of a dependent question see each verified prerequisite as a digest: question, summary, outcome type, limits and, per finding, ID, statement, claim relation and re-readable evidence references (`question_answering.prerequisite_digests`). The review still binds the full answers (`prerequisite_hashes`). (why: D-054)
- **Syntheses despite an accepted gap:** A synthesis summarises without a prerequisite accepted as a gap; reader and review receive it as `prerequisite_gaps` and name it in the answer's limits. Every other dependent question stays blocked and needs its own decision.
- **Source type** (`source_type` on candidate and document): `primary_work` (an author's own work), `study`, `critique`, `overview`, `practice` (documentation, maintained repository, practice report: shows how it is done, not whether it works), `standard` and `idea`. The web search accepts every type except idea sources.
- **Idea sources** (attachments without an address and anything typed `idea`, for example LLM-written notes or posts) supply names, works and tools as research targets and are never evidence (`research_models.is_idea`). Source search and plan do not turn them into a fact check unless the brief asks for it.
- **Recency** (`recency_months` in the brief): practice, tool and benchmark sources should be at most that many months older than the run date (`research_date`, from the run ID, the same on every resume); foundations and standards may be older, and the web search of an *explain* question ignores the rule. Every source carries its date and its origin (`date_basis`: the document, for PDFs the creation date, a provided work's citation, or the search result); the models see type and date of every source.
- The research quality review weights by goal: where understanding leads, a named competing approach or a limit suffices as cross-check; where applying leads, current practice counts and outdated practice counts as missing research.

### Reading and answering in detail

**Section search.** It separates downloaded original texts from the user's own materials and prefers concrete technical terms and explanatory passages over author names and link lists. There is no fixed restriction to the first two hits. A read step returns up to 36,000 characters including requested neighbouring sections; sections that no longer fit are reported explicitly as still pending. Only passages actually read may support an answer; a hit alone closes no question.

**What the reader sees.** Each step shows the most recently read excerpt in full plus the sections read earlier for this sub-question, newest first, together up to 80,000 characters (`question_answering.VIEW_CHARS`). (why: D-043) A section that dropped out of this view and is read again counts as new evidence.

**Read decisions.** A read decision that fills another field besides its action's (for example `searches` besides `windows`) is accepted, and only the action's field counts; one without that field is rejected.

**Immediate re-request.** An answer failing its fixed checks is requested again in the same step, naming the defects, without using a read step: for example a quote that is not verbatim in the section (with an explicit note when it was shortened with "…") or a section that was not read. Only the last permitted answer takes the normal path and becomes the feedback for the next step. (why: D-044)

**Verbatim quotes.** Verbatim means the same letters in the same order; spaces and hyphenation do not count, because the text layer of scanned books tears words apart ("human p otential"). Typographic variants count as equal (`research_evidence.TYPOGRAPHY`): ligatures, hyphens and dashes, soft hyphens and all quotation marks, including the guillemets » « › ‹ that German books quote with while the model types straight quotes.

**Missing evidence type as a result.** If a criterion demands a particular kind of evidence (independent, from the recency window, several studies, measured figures) and the web search finds none either, the reader answers with what the read passages show and names the missing evidence type in the answer's limits. After a web search of this sub-question (`web_attempts` ≥ 1, checked in code), the review rates such a criterion as met as long as the answer claims nothing beyond the passages (`question_answering.EVIDENCE_ABSENCE_REVIEW`); explanation, mechanism and definition are reviewed as usual. (why: D-042)

### Finding ownership

Merged findings keep their assignment to the original sub-questions. A reopened empirical question cannot change a definition that stays closed, even when both answer the same higher-level guiding question. Shared findings, or findings without an unambiguous owner, stay protected unless all their sub-questions are worked on again; targeted evidence corrections may not cross this boundary either.

### Independent sub-questions in parallel

If text execution is `parallel` ([Sequential or parallel](STUDIO.md#sequential-or-parallel)), the run works on up to five sub-questions at once (`execution.MAX_PARALLEL_TEXT`), as the script stages do with their episodes.

- A sub-question starts once all its prerequisites are verified and closed and waits while one is being worked on; if a prerequisite ends without a verified answer (also as an accepted gap, except for a synthesis), it is blocked with `prerequisite_block`.
- Work is handed out in plan order, so the sequential mode (the default, also for runs without a stored mode) takes one sub-question after the other. The mode is recorded at start in `research_request.json`; a resume keeps it.
- Each sub-question writes only its own row of the question state. Everything shared (saving the question state, call timings, gap probes, source attempts, progress messages) runs under a lock that is released only during a model call and while a web search downloads and imports its sources. (why: D-058)
- Reserving an address stays under the lock, so two sub-questions never fetch the same address. If another sub-question extended the source index meanwhile, the search builds on it, and its download receipt lists only what it added itself.
- Receipts per call lie in the sub-question's folder; the budget's call numbers are assigned under a file lock and stay unique.
- `research_questions.json` and `.md` keep the plan order. `active_tasks` names the running sub-questions (`active_task` stays as the first of them for older views), and the Studio shows, for example, „5 Teilfragen in Arbeit: …“ (5 sub-questions in progress).
- If one sub-question fails (quota pause, exhausted budget, concrete block), the others finish their current model call and stop at the next step; the state is saved and the first error reported. A resume picks up each sub-question at its saved step without repeating answered calls.

## Scope check and plan approval

### Scope check

Before reading, a new question plan gets a separate scope check:

- Every task needs a delimited subject; its completion criteria check the same answer.
- Independent methods, mechanisms or empirical comparisons are split, even for the same author or guiding question. A coherent mechanism, and design, results and limits of a single study, stay together.
- A split keeps all previous criteria and source assignments.
- The check has at most two passes and uses the selected text model and the normal job budget.
- The second pass decides finally: its splits are adopted while the plan stays within the task cap, else the plan stays as after the first pass. (why: D-059)
- If the second pass still splits more than a tenth of the tasks (at least two), a note says so in the question state (`scope_unresolved`), in the projection (`scope_note`) and in the plan-approval message, where the cut can be checked and a cap set.

Smaller tasks do not widen the topic scope but may need more individual model calls.

How planning stays within the approved call limit and what the minimum need after the scope check means: [Budgets](BUSINESS_LOGIC.md#budgets).

### The plan approval hold

After planning and scope check, before the first call for a sub-question, the run stops and presents its projection. It uses no further model call until approval; resuming without approval stops at the same point. No automatic resume skips this gate ([Human approvals](BUSINESS_LOGIC.md#human-approvals)).

- The projection is stored in `runs/<run_id>/question_research/plan_projection.json` with `tasks`, `tasks_pending`, `expected_calls_per_task` and its origin (`expected_calls_source`), `closing_reserve`, `closing_calls`, `projected_calls`, `approved_limit`, `seconds_per_call` and its origin, `projected_hours` and `plan_hash`.
- The stage reports `research_plan_review` (status `blocked`), the question state the phase `awaiting_plan_approval`.
- The message states the figures in one sentence, for example „18 Teilfragen, voraussichtlich 290 Aufrufe, etwa 22 Stunden bei 4,5 Minuten je Aufruf (16 Aufrufe je Teilfrage, Standardwert; genehmigtes Limit 750 Aufrufe, 3 verbraucht)“, plus the scope check's note (`scope_note`) if its second pass still split.
- In the Studio, every research job shows „Wartet auf Freigabe des Rechercheplans“ (waiting for research plan approval) in the header and at the top of the **„Recherche“** (Research) page, with the projection: number of sub-questions, expected calls, estimated hours, minutes per call and the origin of the experience values (measured in this run, project experience value or default value). If the projection exceeds the limit, the card next to it offers the increase.
- From six sub-questions on (`question_budget.LARGE_RUN_TASKS`) the plan approval warns that the dossier no longer fits one model window and that every review round reviews the whole dossier again, about 1.4 review parts per sub-question (`question_budget.REVIEW_PARTS_PER_TASK`; see [Written dossier of older runs](#written-dossier-of-older-runs)).

### Approval receipt

Who writes the receipt `runs/<run_id>/plan_approval.json`, and how `--approve-plan` waives the stop: [Human approvals](BUSINESS_LOGIC.md#human-approvals). The receipt is bound to the run, the input hash and `plan_hash`, and protected by a checksum.

- A changed or copied receipt, or one of another run, is rejected (`invalid_plan_approval`); a receipt for an earlier plan of the same run is no approval.
- The Studio button continues the job with the sub-questions as soon as it has written the receipt.
- A receipt the run writes itself under `--approve-plan` carries `source: auto`, the run records this in `research_request.json`, and the projection is stored anyway.

### Re-cutting the plan to N sub-questions

With `--max-tasks N` (Studio: number field **„Höchstens N Teilfragen“**, at most N sub-questions) below the planned number, the next run plans once more:

- Planning gets `max_tasks: N` as a cap, bundles commitments and is requested again up to twice if it exceeds the cap; the scope check follows with the same cap.
- The scope check can only split tasks, never merge them, so the cut goes through planning and costs those planning calls.
- The new plan gets a new projection and waits for approval again, so the approved plan is always the one that runs.
- The first plan's receipts stay under `plan.json` and `scope_*.json`; the cut writes `plan_capN.json`, `scope_*_capN.json` and `planning_budget_capN.json`, and the question state lists the caps under `plan_caps` and `plan_revisions`.
- If planning cannot keep the cap without dropping commitments, the same cap is not tried again: the run presents the existing plan with this note, and the operator approves it without a cap or starts a new research run.

## Blocked sub-questions and decisions

### What the research page shows

Open points of the [guiding-question assessment](#assessment-of-the-guiding-questions) trigger further search and review automatically. On the **„Recherche“** page, met and open guiding questions expand with their reasons.

- A guiding question that is not met, whose defect is a limit of the sources or whose objection is already noted, decided or accepted as a gap while its sub-questions have not changed since, appears as „als Grenze vermerkt“ (noted as a limit) and is not reassessed in every review round.
- If the review passes with such limits, the overall verdict reads „bestanden mit vermerkten Grenzen“ (passed with noted limits): the limits are in the quality report; that is not an accepted gap.
- Every sub-question states how often it was reopened with an objection and how often it was rechecked.
- Only the passed review releases planning. A run that reaches a limit stays saved with concrete gaps ([Budgets](BUSINESS_LOGIC.md#budgets)).
- Every blocked sub-question, expanded, names its reason, the web searches actually run and any run limit reached.

### Advisor

Before the run stops for blocked sub-questions, each gets an advisor call (`research_advisor`, prompt `block_advice`, version `block_advice.v2`) at the subscription's deepest level (Opus 5.5 on xhigh, `research_advisor.ADVISOR_MODEL` and `ADVISOR_EFFORT`, when the run uses the Claude subscription). In parallel mode up to five questions are advised at once.

- It reads the unmet criteria, the sources read, the failed downloads, the limits, and every earlier advice on this question with its outcome: which new attempt followed, how it ended and how many new sections it read (`advice_history`).
- It may search the web itself while search rounds are free. If a question advised at the same time takes the last search round, it advises on without its own search instead of stopping the run for an approval (`search_fallback` on the advice).
- It names the cause and recommends a new attempt, a gap or a higher limit, pointing to concrete works and free locations, and does not send the question down a path that already produced nothing.
- The card **„Wartet auf dich“** (waiting for you) shows its diagnosis, its recommendation and the sources it found as links; its hint is already in the field for the new attempt.
- The run starts a recommended new attempt itself, at most five times per sub-question (`research_advisor.MAX_AUTO_RETRIES`) and never after an automatic attempt that read no new section (`auto_stop`: `limit` or `no_progress`). Every other decision stays with the human, who sees the advice next to the question.
- The advice is search help, not evidence: what the new attempt finds goes through the same independent review.
- Without room in the call budget for the advice and a new attempt, it is skipped.

### Try again

With **„Noch einmal versuchen“** (try again), a blocked sub-question gets the allowance of a new question at the next resume: ten steps and two web searches on top of those used, plus your optional hint as feedback to the model. Sub-questions that only waited for it start again afterwards. Each request covers exactly one new attempt; if the question blocks again, you decide again. Command line: `pla approve <project> --retry <task_id> [--hint "…"]`.

### Accepting a gap

A blocked sub-question can be accepted explicitly as a gap (button on the **„Recherche“** page or `pla approve <project> --accept-gap <task_id> [--reason "…"]`; the optional reason appears in the quality report next to the gap).

- The next run closes the dossier without it and lists the gap in the quality report, in `research/open_questions.md` and in `reports/research_quality.json` (`complete_topic_coverage: false`).
- Review objections that concern only accepted gaps become documented remaining objections instead of new research; objections against verified answers still reopen them.

### Raising limits

If a limit is not enough, the **„Recherche“** page offers the increase as a button, also for an exhausted search-round or source limit; on the command line `pla approve <project> --model-calls N --search-rounds M --sources Q` (or the buttons in the job status) raises them per run. Default limits: [Budgets](BUSINESS_LOGIC.md#budgets).

### Accepting an access gap for one criterion

If a sub-question fails only on a criterion whose source the publisher refuses, this one criterion can be accepted as an access gap: card **„Kriterium als Zugangslücke akzeptieren“** (accept criterion as an access gap) under **„Wartet auf dich“**, choosing criterion and blocked source, or `pla approve <project> --access-gap <task_id> <criterion> --blocked-source <url> [--reason "…"]` (criteria counted from 0).

- Only an address whose download was demonstrably blocked in this run qualifies: HTTP 401, 402, 403 or 451, or a bot challenge page instead of the document (`blocked_sources` in `research_questions.json`).
- The approval is stored in `runs/<run_id>/criterion_gaps.json`. The next resume reopens the sub-question; if this was the last open decision, the job continues at once.
- Answer and review then see the criterion with the blocked source and its proof (`accepted_access_gaps`), and the review rates the rest of the criterion. The previously rejected answer may be reviewed once more unchanged, because the criterion changed.
- The verified answer carries the gap as the limitation `accepted_access_gap` into the dossier and the quality report.
- Unlike an accepted sub-question, the question keeps its verified parts; all other prompts stay unchanged.

### Finishing with remaining objections

Every review round reviews the whole dossier again and often finds new details; otherwise the loop ends only when every sub-question has used its two reworks. From the first overall review on it can be ended explicitly: **„Nach der nächsten Gesamtprüfung mit Resteinwänden abschließen“** (finish with remaining objections after the next overall review) on the **„Recherche“** page, or `pla approve <project> --finish-with-residuals [--reason "…"]`, stored in `runs/<run_id>/residual_finish.json`.

- The next overall review runs as always; instead of reopening sub-questions, the run then closes, without a further rework round.
- Its open objections appear under „Verbliebene Prüfeinwände“ (remaining review objections) in the quality report and in `accepted_gaps.json`, in the objection register with status `residual`, and the publication carries `model_review: residual_objections_remaining`.
- Objections that an earlier round of an assembled dossier closed stay closed (`closed_objections`).
- Sub-questions blocked only because their reworks are used up (`audit_block`) keep their last verified answer; one reopened in the last rework that now only waits for such prerequisites gets its last verified answer back.
- A running session adopts the approval at its next decision after an overall review.
- The advisor does not advise on questions whose reworks are used up and does not reopen them; the overall review's objections are their diagnosis, and the editor decides.

### Dispute in the overall review

If the overall review contradicts an earlier objection (`review_disagreement`, for example because a passage read meanwhile supports the statement objected to), the run stops and stores verdict and objection in `synthesis/audit_NN/review_disagreement.json`. The editor decides: on the **„Recherche“** page (card **„Streitfall in der Gesamtprüfung“**, dispute in the overall review, showing both positions) or with `pla approve <project> --dispute <objection_id> reviewer|objection [--reason "…"]`, stored in `runs/<run_id>/dispute_decisions.json`.

- `reviewer` (**„Dem Prüfer folgen“**, follow the reviewer) closes the objection; the dispute appears with both positions and the note under „Strittige Prüfeinwände“ (disputed review objections) in the quality report.
- `objection` (**„Einwand aufrechterhalten“**, uphold the objection) sends the objection back to its sub-question in its original wording, without a routing call; the sub-question reworks it like any objection, and the next review round checks it again.
- The job continues immediately. Resuming uses the stored review parts, so the decision costs no call.
- A newly raised objection that the review cannot anchor in read passages still ends the run; it is recorded once in `review_disagreements`, even when a resume replays the same review.

### Blocked downloads, free copies and open archives

If an address refuses the download (HTTP 401, 402, 403, 451), answers with a bot challenge or login page instead of the document (for example Springer's „Client Challenge“: short text with a script notice or a challenge page title), or its server cannot be reached, the import treats it as blocked; a bot challenge page would otherwise be imported as a source with empty content. The same applies to a scan without a text layer.

- The import then asks OpenAlex, Unpaywall, Semantic Scholar, Europe PMC and CORE in turn for the same work until a copy reads (`sources.open_access_copy`). OpenAlex is asked via the DOI in the address, otherwise only via an exactly identical title. The first readable copy is read, PDF files before full-text pages of PubMed Central or Europe PMC; landing pages with only an abstract stay out.
- The copy keeps the original address as its identity; `final_url` and the reliability note name the service and where it was read. It takes no further place in the source limit.
- Europe PMC supplies full texts of free articles as JATS XML, even where PubMed Central shows a captcha.
- Unpaywall is asked only with a contact address (`PLA_UNPAYWALL_EMAIL`), CORE only with a free key from core.ac.uk (`PLA_CORE_API_KEY`); set both as environment variables and restart the Studio ([Environment variables](CONFIGURATION.md#environment-variables)).
- CORE allows about 1,000 requests a day with the free key: all runs count their CORE searches together in `~/.podcast-automate/core_usage.json` (UTC day, limit `PLA_CORE_DAILY_LIMIT`, default 1000, `sources.CORE_DAILY_LIMIT`), and CORE is skipped for the rest of a used-up day (`sources.reserve_core_call`). The research page shows the status under „Fehlende Werke“ (missing works).
- The first search and every sub-question's web searches also name open archives per field (`prompts/open_archives.txt`: arXiv, OpenReview, ACL Anthology, PsyArXiv, SocArXiv, OSF, RePEc, HAL, Zenodo, CORE, BASE, Europe PMC, OAPEN, DOAB, PhilArchive, Internet Archive Scholar, Project Gutenberg, data portals) and exclude shadow libraries.
- A work no source releases freely can be obtained through a library, interlibrary loan, subito or purchase and uploaded under „Fehlende Werke“ (`provided_works`, [Missing works](STUDIO.md#missing-works)). With its reference (`citation`) it counts as a primary work, not a note, also as independent evidence of an empirical test. It is read with the book limits ([Download and import limits](#download-and-import-limits)) and imported at the next step (`QuestionResearch.adopt_provided_works`), and the questions it was uploaded for are tried again.

## Assembled dossier

### Composition

A run started on or after 1 October 2026 (prompt generation 3, `prompt_generation` in the question state) assembles its dossier from the verified answers instead of having a model write it (`question_synthesis.assemble_dossier`). (why: D-048)

- It holds every finding of every verified answer unchanged. The finding ID is joined with the sub-question (`<task_id>__<finding>`), because IDs are unique only within one answer.
- Under `answers` each sub-question lists question, summary, limits and its finding IDs.
- Coverage per guiding question follows from the sub-questions serving it; accepted gaps stay coverage gaps.
- The dossier carries `assembled: true`.

### Overall review of an assembled dossier

The overall review does not check the findings against their sources again: every finding keeps the evidence of its answer's independent review, under its dossier ID (`complete_research/source_review.json`).

- The assessment of all guiding questions reads answers; findings with type, statement, cited sources and evidence verdict; the coverage; and one identity line per source, without quote excerpts (about 450,000 characters in the runs of 1 October, one call in the 1,000,000-token window of Sonnet and Opus 5.5).
- An objection goes back to its sub-question via routing, never into the dossier text; the answer is reworked and reviewed again, and the next dossier is again assembled from the answers.
- A review round checks no closure conditions: the next assessment judges the whole again; an earlier objection it does not raise again counts as closed, one raised again is open again.
- The limit of two reworks per sub-question applies unchanged.

### Rules that end the review loop

Three rules make the review loop of an assembled dossier end on its own (why: D-050):

- **Met criteria pass.** A guiding question whose five criteria the assessment rates as met and which has independently retrieved evidence passes. What the assessment also lists as "still missing" appears as „Als Grenze vermerkt“ at the guiding question in the quality report (`noted` in `research_quality_gate.json`) and triggers no research.
- **After two reworks, objections are only noted.** An objection against a sub-question whose two reworks are used up no longer reopens it or stops the run: the last verified answer stays, and the objection appears under „Einwände nach zwei Nachbesserungen“ (objections after two reworks) in the quality report (`noted_objections` with `basis: reworks_spent`, gate key `noted_after_reworks`). The run closes once no sub-question can be reworked any more.
- **An accepted gap with a verified answer keeps the answer.** A sub-question blocked because its reworks were used up (`audit_block`), or accepted as a gap in this state, gets its last verified answer back on resume (`question_synthesis.keep_spent_answers`); its objection is noted as above. A sub-question without a verified answer stays a gap. For such sub-questions the Studio offers **„Fortsetzen“** (resume) without a decision of their own (`keeps_spent_answers` in the question state).

**A rework that ends blocked returns the verified answer at once.** If a sub-question's rework finds nothing that closes its objection and ends with an evidence, search or access block (even one already accepted as a gap), the sub-question gets its previously verified answer back in the same pass instead of stopping the run. The objection appears as a limit under „Einwände, die eine Nachbesserung nicht schließen konnte“ (objections a rework could not close) in the quality report (`noted_objections` with `basis: rework_blocked`), and the next follow-up assessment reads it as noted, not as reworked. A sub-question that only waits for a prerequisite is decided together with it.

### Follow-up assessment from the second round

From the second round of an assembled dossier, the assessment no longer judges everything anew (`question_synthesis.follow_up_assessment`, prompt `research_assessment_followup`, call `assessment_followup`). (why: D-051)

- Code fixes the scope before the call and stores it (`audit_NN/assessment_scope.json`), so a resume asks the same question. Only guiding questions are reassessed that a changed answer serves, whose cited findings changed, or that last failed with a fixable gap; all others keep their verdict.
- So does a failed guiding question where nothing changed and whose objection, in the last routing, ended at every sub-question as noted, disputed, accepted as a gap or blocked: it is neither reassessed nor routed again and appears as „Als Grenze vermerkt“ in the quality report (`recorded_limit`, `recorded_limits` in `assessment_merged.json`) until one of its answers changes. (why: D-052)
- The assessment sees its previous verdicts, its objections and what became of each (reworked, noted as a limit, disputed, accepted).
- For each guiding question and objection it says whether new research can close it (`research`) or whether it is a limit of the available sources (`limit`: abstract only, third-party copy, not freely available, manufacturer information only, independence unproven).
- Only a fixable objection about a changed answer stops a run. Source limits and points about unchanged answers appear under „Hinweise fürs Skript“ (notes for the script) in the quality report (`script_notes`); a guiding question failing only on a source limit appears there as „Grenze der verfügbaren Quellen“ (limit of the available sources, `source_limit`) and opens no question.
- Every round stores its verdicts and the answers it judged (`audit_NN/assessment_merged.json`, `assessed_answers` in the question state).

The same principle applies to the [script review](SCRIPTS.md#script-review), the [teaching-plan review](TEACHING.md#three-editorial-reviews) and the source review of [written dossiers](#written-dossier-of-older-runs).

### Routing with a fixed scope

Before the objections of an assembled dossier are routed, code fixes for each objection which sub-questions it may open (`routing_scope` in the review report, `objection_scopes` in the prompt). (why: D-053)

- For an unmet guiding question: the sub-questions serving it and those whose findings its verdict cites; in a follow-up assessment, for a guiding question that passed before, only its changed sub-questions.
- For an objection of the follow-up assessment: the changed sub-questions it names.
- A routing beyond that is requested again, naming the defect (`invalid_question_routing`).
- The routing material lists each answer's findings with their statements, so an anchor hits the finding at issue.
- If a round's assessment exists (`audit_NN/assessment_merged.json`) and the answers it judged are unchanged, a resume continues the round with it, even after an assessment prompt changed.
- A routing receipt that answered an earlier prompt is set aside as `<name>_superseded_receipt_NN.json` and requested again.

### Completeness objections as limits

An overall-review objection that a firmly agreed criterion is not entirely met (`rule: criterion`) does not open its sub-question when the verified answer already addresses this criterion with findings (`question_synthesis.criterion_covered`). It is noted as a limit (`noted_objections` in the ledger, `noted_objections.json` in the result) and appears in the quality report under „Als Grenzen vermerkte Vollständigkeitseinwände“ (completeness objections noted as limits). (why: D-049)

- A criterion without any finding is still researched further; all other objections (evidence, source, changed claim, synthesis, scope) open their question.
- The follow-up assessment's verdict decides first: if it says research can close the objection (`remedy: research`), the objection is researched further whatever anchor the routing chooses, and what it classifies as a source limit never reaches the routing ([Follow-up assessment](#follow-up-assessment-from-the-second-round)). This section's rule applies only without such a verdict.

### Quotes and word limits

An assembled dossier has no word limits per source; the verbatim-quote rule applies instead to what is broadcast, per source and episode ([Source rights and privacy](SECURITY.md#source-rights-and-privacy)). Supplements of the teaching research then count their word limits only for what they add themselves.

### Rebuilding a written run

A run started earlier keeps its written dossier and its prompts. On explicit request it is reassembled from its verified answers at the next resume: `pla approve <project> --run-id <run_id> --rebuild-dossier` (stored in `runs/<run_id>/dossier_rebuild.json`).

- No sub-question is researched again.
- The written dossier and the objections of its review rounds are set aside in `question_research/synthesis/superseded_audit_NN.json`; the previous round's receipts stay unchanged in their folder, and the next review round gets a folder of its own.
- Used reworks keep counting.
- Only an unfinished research run can be rebuilt.

## Evidence contracts

The evidence contracts (`evidence.v1`) extend the existing question controller and model calls. They do not install external research skills, copy their prompts, introduce a review panel, or change research/audio approval rules. (why: D-039)

| Finding | Implemented behavior |
| --- | --- |
| Finding-level verification | Answer and dossier reviews must assess every finding exactly once. Receipts identify all cited passages, support verdict, unsupported clauses, source suitability, claim preservation and empirical-test status. Empty issue lists cannot replace coverage. Unknown, unread or missing references fail validation, and the error names the finding or source ids concerned; duplicate entries collapse to their most conservative receipt. On the last permitted attempt such shape defects are settled instead (see below). Partial support requires correction or further research. |
| Source roles and independence | Reviewers record source roles, original work/version, shared study or dataset, method, group, population, geography, period and limitations, grounded in supplied passages. Unknown properties remain explicit. DOI aliases, duplicate text and user notes cannot count as independent tests; a provided copy of a published work named in its `citation` is that work, not a note, and can count ([provided works](#blocked-downloads-free-copies-and-open-archives)). There is no universal source-count requirement for passing a finding; definitions can pass without a DOI or an empirical test. |
| Cross-source synthesis | A bounded relation map records findings, comparison dimension, conditions, comparability, evidence, interpretation and resolution. Different settings or time scales cannot be labeled a direct contradiction. Valid unresolved scientific disagreements remain visible through knowledge models, teaching plans and scripts; they are not automatically missing research. |
| Claim preservation | Findings carry basis, relation strength, scope, qualifications and normalized quantities. Targeted patches record before/after changes. Final dossier review receives verified answer baselines. Script review must account for every segment and quote its actual text, including nonfactual framing. Drift creates a blocking correction issue; a segment that follows its source where the finding misstates it is not drift ([Script review](SCRIPTS.md#script-review)). Equivalent spoken numbers, translations and unit conversions are allowed. Supplementary teaching research has the same evidence checks and retains additional claim contracts. |
| Anchored objections | Objections identify a fixed criterion or existing quality rule, affected findings, evidence or a specific missing-evidence statement, correction and closure condition. Routes require task-specific anchors, respect finding ownership and keep unresolved objections stable across paraphrasing; for an assembled dossier, code fixes which tasks each objection may reopen ([routing scope](#routing-with-a-fixed-scope)). Existing objections of a written dossier receive explicit closure checks. Unsupported demands stop as review disagreements rather than starting more searches. |
| Prerequisites and outcomes | Optional task dependencies are validated and scheduled as a DAG. Dependent readers and reviewers receive digests of the verified prerequisite answers, while verification binds the full answers by hash ([prerequisites](#series-goal-task-aims-and-source-types)). Reopened or revalidated prerequisites send only their transitive dependents back for revalidation ([revalidation](#revalidation-instead-of-a-stop)). Evidence profiles distinguish definitions, theory, mechanisms, empirical work, examples, boundaries and synthesis. Outcomes distinguish supported answers, supported uncertainty, access/extraction/evidence/search/budget blocks and blocked prerequisites. |

Search receipts extend the existing task ledger and download receipts. They retain local queries, candidate results, web selection/exclusion rationales and counterevidence outcomes. Tool-observed web queries and model-reported queries are separate fields: model JSON is not proof that a query ran. Initial discovery also has a selection receipt. The existing live-search event requirement still applies.

The last permitted attempt of an answer review settles receipt shape defects that have a conservative reading instead of refusing them (`research_evidence.settle_receipts`):

- Receipts for unknown findings and assessments of sources no finding cites are dropped, as are references outside the supplied passages or outside the finding.
- Independence claimed without a study or dataset identity becomes unknown.
- A finding without a receipt, or with one that covers none of its passages, gets an `insufficient_context` receipt, so it blocks the answer rather than passing unchecked.
- A cited source without an assessment gets unknown roles and independence and is recorded as a limitation of the answer.
- A finding that cites a passage never supplied is not settled: that is ledger corruption and still fails.
- A well-formed review comes back unchanged.

Corpus concentration is descriptive: each dimension reports known and unknown source counts and the largest share among known values. It is an advisory, not a quota or automatic rejection. New PDF imports record total pages, pages with text, empty/suspected image pages and pages with table/equation signals; these are heuristics, not a visual verification of extracted data. Oversized or unreadable imports still fail ([Download and import limits](#download-and-import-limits)).

### Artifacts and presentation

The final run stores `evidence_report.json`, `search_receipts.json` and `objections.json` in `complete_research/` and publishes copies under `research/`. `research/discovery_receipt.json` records initial discovery. The dossier contains source assessments and synthesis relations. The question ledger exposes typed outcomes and support summaries; Studio distinguishes available passages, automated semantic checking and documented independent empirical tests. The final quality report retains concentration advisories and unresolved scientific comparisons.

`evidence.v1` identifies the stronger contracts. Model/schema/prompt changes invalidate affected call receipts; they do not convert old aggregate scores into finding-level reviews. During an unfinished question-run upgrade, the old ledger is retained in `legacy_evidence_state.json`, existing answers become candidates for rechecking, downloads remain reusable and consumed budget remains consumed. Pre-ledger planning receipts remain intact and get a separate plan under the new schema. Completed historical artifacts remain readable without acquiring new verification claims. Script snapshots accept added empty/default schema fields only after semantic equality checks, preserving the exact approved serialization and repair allowance.

The new fields enlarge prompts and responses but add no mandatory model calls to the ordinary successful workflow. Existing step, search, source, reopening and model-call limits still apply; a prerequisite recheck or a rejected review can consume additional calls within them.

### Validation and limits of the contracts

The [frozen offline evaluation](../evals/research_evidence/README.md) contains definition, empirical and synthesis examples, development/heldout partitions, deliberate defects and clean controls. It records corpus, prompt and schema hashes, rejection/false-block counts, missing explanations, unjustified reopenings, semantic drift and call/correction-time fields. The first replay rejects eight defective cases and accepts six clean cases. These are synthetic, agent-authored annotations; independent human adjudication and real-model accuracy measurements are still outstanding (see V-3, V-5).

Regression tests exercise controller replay, budget preservation, receipt coverage, source independence, dependency scheduling/invalidation, legacy upgrades, supplements, script drift and UI labels. Semantic support and methodological suitability remain judgements by a fallible model. A valid receipt makes those judgements inspectable and enforces coverage; it cannot guarantee their scientific correctness. The evaluation runner can also score separately captured reviews, but those scores should not be presented as an independently validated literature benchmark.

Main implementation modules: `evidence_models.py`, `research_evidence.py`, `question_dependencies.py`, `question_research.py`, `script_evidence.py`, and the existing research, patching, source, teaching and scripting modules.

## Gap probe

### Corpus probe of reported gaps

Every reported gap is checked without a model call against the sections already stored (`research_gap_probe`), with the same lexical search as re-reading:

- Key terms are formed from the gap text; a section is a hit when at least two different key terms occur in it as whole words (`research_gap_probe.MIN_KEY_TERMS`).
- Only whole words count, not substrings: "rule" in "overruled" or "load" in "download" does not, although the ranking of the read search still counts such occurrences.
- Common German function words (for example „sie“, „beim“, „unter“, „dabei“) are not key terms.
- The results are in `runs/<run_id>/question_research/gap_probes.json` and in the quality report under „Korpusprobe der Lücken“ (corpus probe of the gaps).

### Gap terms

The probe compares words. If the gap is German and the source English, the gap text alone does not find the matching section; `no_hits` then means "no lexical overlap", not "not present". So every unanswered coverage row of a written dossier carries `gap_terms`: three to eight search words in the language of the stored sources that a section answering the gap would contain.

- The model fills them when writing and changing the dossier; the probe counts them as key terms next to those of the gap text.
- Older dossiers without this field still load, and an assembled dossier, which no model writes, has none; their gaps are probed via the text only.
- The probe skips attached own materials (sources without an address), because the read search leaves them out.

### States

A hit does not refute the gap. A term search over hundreds of sections almost always hits something; a hit therefore names a section that has to be read.

| State | Meaning |
| --- | --- |
| `no_hits` | No matching section. |
| `hits_unread` | Hits, not yet read. |
| `hits_read_confirmed` | Read; the gap remains. |
| `resolved` | Answered in the sources. |
| `hits_unowned` | Script run only: hits that lie in no episode's sources (see below). |

Only `hits_unread` blocks: the quality review then names the section, and `open_questions.md` carries the status after every open question. Hits are also queued as the first read windows of the responsible sub-question, at no extra call.

### In the script run

In the script run the same probe runs once per run during planning over the uncertainties of the knowledge model, with the dossier's `gap_terms`, and writes `runs/<run_id>/gap_probes.json`. The uncertainties are the dossier's open questions and coverage gaps, its unresolved syntheses and, since 2026-10-04, the limits the research noted for the script ([Research limits in the script](SCRIPTS.md#research-limits-in-the-script)). An assembled dossier has no open questions and, with every sub-question answered, no coverage gap, so its limits are its only gaps; until then its probe searched nothing, and Jev never ran on it. (why: D-131)

- Every row names in `owner_episodes` the episodes whose sources contain a hit.
- Hits the research sub-questions have already read count as read, as in the research probe (`research_read`): if all are read, the row is `hits_read_confirmed` with `settled_by: research`; otherwise it keeps only the unread hits (`unread_references`) and the episodes whose sources contain them. A run file saved before this rule is reconciled the same way on resume; a second reconciliation changes nothing.
- Before each teaching plan, the unread hits in this episode's sources go into the existing supplementary research, whose source context contains the unread hit sections.
  - If the supplement answers the question, the row becomes `resolved`.
  - If it names the question in `remaining_gaps`, the row becomes `hits_read_confirmed`, but only if all hit sections were in the supplement's source context. The question must be there verbatim; quotation marks around it and an appended reason are tolerated, and the reason then appears as `confirmation_note` in the row.
- `settled_by` records the episode that decided it; a later episode with the same sources spends no further supplement round on it.
- The script review of an episode waits for exactly the rows it should have read itself: unread hits in its own sources.
- Hits that lie in no episode carry the state `hits_unowned`; they block nothing and stay in the run file for review.

### Jev as a second finder

A new script run can also check the gaps with Jev, TypeSafe's decision model via OpenRouter (`jev.py`, `jev.JEV_MODEL`).

**Switching it on.** The Studio switch **„Jev dazunehmen“** (add Jev) turns it on per project ([Research decisions and limits](STUDIO.md#research-decisions-and-limits); `studio/jev_probe.json`, see [Where settings live](CONFIGURATION.md#where-settings-live)); on the command line `pla script <project> --jev-probe`. The key comes from the Studio or from `OPENROUTER_API_KEY`; the Studio passes it only to runs that use the probe ([Secrets and keys](SECURITY.md#secrets-and-keys)).

- New German-language projects have Jev on from the start, because their gaps are worded in German and the sources are mostly English. If the key is missing then, the gap probe runs with the word search only (`runs/<run_id>/jev_probe.json`: `status: skipped`) instead of stopping the run. Whoever switches Jev on themselves still gets a stop without a key.
- The switch changes no adopted selection, and running jobs keep their gap probes, because new hits would reopen finished episodes.

**How it works.**

- **Scan:** For every section of at least 200 characters that is not a bibliography (`jev.MIN_SECTION_CHARS`) and every gap, Jev answers whether the section fills what the gap misses. Eight gaps go into one request (`jev.QUESTIONS_PER_REQUEST`).
- **Hits:** Sections with a probability of at least 0.3 (`jev.THRESHOLD`) join the word hits, at most five per gap (`jev.JEV_HITS`), marked with `"via": "jev"` and their probability.
- **Reading stays mandatory:** Jev decides nothing; a Jev hit has to be read like any other before the gap may stand. Jev finds the matching passages even when gap and source are in different languages; the text model still reads and confirms them.
- **Safe to interrupt:** The answers are stored in `runs/<run_id>/jev_scan.jsonl`, where an interrupted scan continues; `runs/<run_id>/jev_probe.json` reports progress, requests and cost.
- **Effort:** With the source corpora of Asimov and Ontologies about 20,000 requests and 0.60 USD per run.
- **Basis:** The evaluation of 29 September 2026 (`evals/jev_decisions`) showed that Jev separates cited sections from random ones well (AUC 0.95 to 0.975), including German questions against English sources; there the word search reported 15 of 23 Asimov gaps as `no_hits`. Jev does not reliably judge whether individual statements are faithful to findings and is not used for that. (why: D-041)
- **Errors:** A rejected key, missing credit or a privacy setting that excludes TypeSafe stop the run with the respective reason.

## Explanation in the dossier

The general explanation standard (no prior knowledge assumed, everyday language first, terms after the explanation) is in [Editorial standard](TEACHING.md#editorial-standard). In the dossier:

- The commission is stored permanently in the project's `audience_level`, `prior_knowledge` and `depth_request`; the dossier and review instructions apply it.
- At university level the dossier also records the justifying intermediate steps that connect foundations and advanced mechanisms. Necessary concepts such as gradient and normalisation are explained, not excluded wholesale.
- An intuitive derivation does not replace a formal proof; evidence actually missing stays visible as a gap.
- An invented image stands apart from the supported finding in `illustration`, always with `illustration_limit`: where does the comparison stop helping? A lone half of the pair is dropped. The readable dossier shows both as „Bild zum Mitdenken“ (image to think along) and „Grenze des Bildes“ (limit of the image).
- The review also checks for understandable language and misleading comparisons.
- Original titles and short evidence quotes stay in the source section; they are not spoken podcast text.

## Limits and resume

### Run limits

The project budgets apply too; default limits, what counts against them and which calls are not charged: [Budgets](BUSINESS_LOGIC.md#budgets).

- Local re-reading stays possible when the web or source budget is exhausted.
- A written dossier holds at most 120 findings (`research_models.MAX_FINDINGS`), an assembled one all verified findings.
- Every web search loads new sources. When the source limit, the run's search rounds or the sub-question's web attempts are used up, it ends before it starts and the sub-question names the limit as its reason (`budget_block`), also when a parallel sub-question took the last search round.
- The source limit also counts failed and duplicate-content downloads. A persistent register reserves every new canonical address before the download. Interrupted downloads continue their reservation; finished downloads are restored from their receipts even when the limit is exhausted.
- A quota limit pauses the run; an exhausted configured research budget blocks further model calls of this run.

### Step limits per sub-question

Question work has fixed limits:

- at most **10 read, search or answer decisions per working pass on a sub-question (also after reopening)** (`question_research.MAX_STEPS`);
- at most **2 additional web searches per sub-question** (`question_research.MAX_WEB_ATTEMPTS`);
- at most **2 reopenings after concrete objections of the overall review** (`question_research.MAX_REOPENINGS`).

**Recovery steps.** Two steps without new hits, read sections or a passed independent answer review trigger two recovery steps in turn: first, still unread stored sections are read; then the web is searched once, if the sub-question has a web attempt and the run a search round left. So no sub-question is blocked for missing evidence before the web was searched, and a block's reason names only what actually happened. Sub-questions an earlier run blocked without any web attempt make up the web search at the next resume; dependent sub-questions are then decided again.

**Answer lock.** After a rejected review, answering is locked: `allowed_actions` offers the reader only reading, searching and blocking, so it must read or find new sections first; the lock lifts only with new evidence.

- The web search (`search_web`) is offered only while the sub-question has web attempts, the run search rounds and the source limit room (`web_open`). (why: D-057)
- An answer given anyway is not reviewed and counts as a step without progress.
- If the lock is active, the web was already searched in this working pass, and a step brings no new evidence, the sub-question is blocked at once with `evidence_block` and the unmet criteria.
- If progress fails to come otherwise, the specific question is blocked with a reason; other questions continue.
- A resume alone neither resets these limits nor starts a new loop. Deciding a blocked sub-question: [Blocked sub-questions and decisions](#blocked-sub-questions-and-decisions).

### Reopening after objections

Overall-review objections that the sections already read can fix (resolution `revise`, for example wording that is too broad) open their sub-question in correction mode: the reader receives the previous answer, the cited sections and the objections and may only deliver a corrected answer, which is reviewed independently like every answer. If the review rejects the correction, it becomes an ordinary reopening with reading and search. A sub-question that also has an objection demanding new evidence (`research`) is reopened fully.

During routing, an objection counts as already known only with the same closure condition; a second defect of the same finding becomes an objection of its own.

### Invalid and rejected model responses

Formally invalid model responses (for example a search without logged search queries, an answer review missing a criterion, a read decision whose payload does not match the chosen action, or a plan that omits a guiding question) are not stored as checkpoints but requested again up to twice with the concrete objection.

- Small form errors cost no call: a criterion rated twice or a duplicate finding receipt is merged into one entry, keeping the stricter verdict; an evidence receipt that checks only part of the cited sections is accepted, and the unchecked sections are noted as a limitation.
- Only a missing or unknown criterion and an unread section are still rejected, and the message names the affected criteria, findings or source IDs.
- On the last permitted attempt a form error of the answer review is settled conservatively instead (`research_evidence.settle_receipts`): an unknown criterion is dropped, a missing one counts as not met, and receipt defects are settled as listed under [Evidence contracts](#evidence-contracts). If the review thus left only parts of the answer unjudged, the same answer is reviewed once more in the next step instead of being locked.
- Likewise, on the last attempt a sub-question's web search is trimmed like the first source search instead of rejected: candidates without a permitted source type drop out, whatever exceeds the permitted number is not imported, and both appear in the search's limits (`question_answering.settled_search`).
- Evidence that cites a whole source, an unknown section or a section not read for this sub-question is returned with the reference objected to and the fitting next step.
- The same applies to a readable response that breaks a cross-field rule of its response contract (`rejected_output`), for example an objection without evidence or a partly supported finding without a named clause: the adapter reports the fields objected to, the call stays charged, and the task is requested again with the named defect. Only a missing or unreadable response remains `invalid_model_output`.
- A finding reported as `supported` that lists `unsupported_clauses` counts as `partially_supported`; provenance gaps such as a year known only from metadata belong in the review's limitations.

**Attempts.** Rejected responses are stored as `*_rejected_NN.json` next to the valid receipt. Only the third rejection blocks the run; resuming does not repeat such calls. A receipt stored earlier that fails today's check is set aside the same way and requested again.

- If a stored rejected response passes today's check after a rule change, the most recent one becomes the receipt on resume (`adopted_rejection`) instead of costing a new call or stopping the run again. Each is checked as the attempt it answered (an earlier one like a first attempt, the last one under today's rules for the last attempt), so a call that stopped after its corrections were used up closes on resume as that attempt would close today.
- If a step has used its three attempts, the run stops, and a resume stops at the same place again. The stop code is the rejecting check's code, and `rejected_output` where it rejected a readable response as `invalid_model_output` (`research_patches.exhausted_code`); `invalid_model_output` as a stop code means only that no readable response came, and a resume then asks again.
- **„Mit neuen Anläufen fortsetzen“** (resume with fresh attempts, stop card in the Studio) or `pla approve <project> --fresh-attempts` sets aside the stored rejections of every such call (`<name>_superseded_NN_MM.json`, logged in `fresh_attempts.json`) and gives it three fresh attempts; only for a stopped run.
- Rejected responses count only against the prompt they answered; if the prompt changes, they move to `<name>_superseded_NN_MM.json`, and the corrected prompt gets its attempts anew.
- Every step of the review loop reports its state in the activity line (review part, assessment, routing part, incorporation block, evidence correction).

### Revalidation instead of a stop

If a verified prerequisite is reworked or reopened, every verified answer building on it, directly or through others, is reviewed again (`question_dependencies.invalidate_dependents`), also on resume and for an answer kept after its reworks. (why: D-056)

- A stored verified answer that no longer passes a rule tightened since (its fixed checks, its stored review verdict or its evidence receipts) is reviewed again with the named points, together with everything building on it, instead of stopping the resume. If the round had reached the dossier or the overall review, a new review round follows.
- A revalidation is no rework: it counts against no limit, the answer may come back unchanged (`resubmit`), and a lock from an earlier rejection does not apply.
- It is still counted: `revalidations` per sub-question in `research_questions.json`, „N Mal erneut geprüft“ (rechecked N times) in the Studio, „Nachprüfungen nach geänderten Voraussetzungen“ (revalidations after changed prerequisites) in the quality report of an assembled dossier.
- A stored answer or source that no longer matches its checksum still stops the run with `invalid_research_checkpoint`.
- Before assembling and before completion, every sub-question must be verified or blocked (blocked ones first stop for a decision, except accepted gaps); otherwise the run stops with `research_questions_open`, and a resume continues working on them. Publication checks the same again, and a run stored as completed with open sub-questions answers them on resume in a new review round.

### Download and import limits

- Downloads are limited to 20 MiB (`sources.MAX_BYTES`) and bounded waiting times; name resolution waits at most 10 seconds (`sources.DNS_TIMEOUT_SECONDS`).
- A download carrying a service key (for example CORE's) follows a redirect only on the same server and never from https to http; otherwise it aborts, so the key does not go to a foreign server.
- PDF texts are extracted in a separate process with a 120-second time limit (`sources.PDF_TIMEOUT_SECONDS`), so a broken file cannot stop the job.
- At most 300 PDF pages and one million extracted text characters are imported per source (`pdf_text.MAX_PAGES`, `MAX_TEXT`).
- Books are read with the book limits: at most 2,000 pages and 6 million characters (`pdf_text.BOOK_PAGES`, `BOOK_TEXT`; for HTML and text `sources.MAX_BOOK_TEXT`) within 900 seconds (`sources.BOOK_TIMEOUT_SECONDS`). A book is a provided work, every source typed as a primary work (`source_type: primary_work`), and every address at OAPEN, DOAB, Project Gutenberg, archive.org, OpenEdition or Open Book Publishers or with a path such as `/book/`, `/books/` or `/monograph/` (`sources.book_candidate`). (why: D-055)
- The 20 MiB still apply to every download; only a work uploaded in the Studio may be larger (`provided_works.MAX_WORK_BYTES`, 150 MiB).
- PDFs locked only with an owner password against copying or printing are read; a user password remains an access problem, as do image PDFs without text, login pages and unsupported formats.
- Automatic text extraction may capture formulas, tables and figures incompletely ([pypdf text extraction and its limits](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)).
- Every failed download appears with its cause in the download report and as a row of the source index: error class of the PDF reader, encrypted file, page or size limit, pages without a text layer (probably scanned) or HTTP status, each with a fixed `code` such as `source_unreadable` or `source_download_failed`. Text recognition (OCR) does not start automatically; a readable version of the same source can be supplied as a `seed_urls` entry, which needs a new research run.

The source stays fully stored within the import limits. For each sub-question the model sees specifically requested sections; the overall review receives all evidence passages of the merged answers and adopted findings, without a read limit. If a synthesis, review or routing still lacks a cited passage, the run stops with `research_context_incomplete` instead of judging without it; a prompt that is too large is rejected by the model adapter with its own limit. The local search is lexical, not a general translation or semantic search; English technical terms help with English sources. The written dossier of older runs limits quotes and paraphrases per source ([Source rights and privacy](SECURITY.md#source-rights-and-privacy)); the assembled dossier holds all verified answers ([Quotes and word limits](#quotes-and-word-limits)).

### Time limits and stalls

- Individual text model calls have **30 minutes** by default (`runtime.text_timeout_seconds: 1800`), independent of the call budget (`ResearchLimits.model_calls`); existing explicit time limits are kept. Codex stores the actual time limit in the call metadata or in the error report.
- After a timeout, raise this time limit (on the Studio's settings page, or in `project.yaml` without workspace settings: [Studio settings](CONFIGURATION.md#studio-settings)) and resume the same run: finished search and sources are kept, only the interrupted call is repeated, and the aborted call is not charged. Topic and model choice stay binding.
- The adapters repeat once, uncharged, a streaming call (Codex app server, Claude Code) with **10 minutes without any output** (`stall_retry.json`), and repeat once a Claude response the CLI could not bring into the required format (`claude_structured_output`, `format_retry.json`); only a second such failure stops the run. A prompt too large for Claude's window is refused before the start, uncharged (`prompt_too_large`), and goes to Codex under the automatic subscription choice. Details: [Text provider adapters](ARCHITECTURE.md#text-provider-adapters).

### Resuming interrupted and older runs

- Valid completed stages are skipped on resume. Successful model responses and individual downloads (with checksum checkpoints) are stored, so an interruption between search, reading and review repeats no finished work.
- After a parser update, stored raw files can be processed again and dependent dossier and review stages are renewed. Damaged raw files are downloaded again.
- Stopped older runs adopt, at the next **„Fortsetzen“**, the retrieved sources and the last formally valid draft of the stored rounds; old assessments become hints, not automatically passed answers. Raw files, section references and checksums are checked before reuse, and consumption in the run budget is kept. Fully completed runs are not recalculated.
- The open Studio server resumes a job paused by a subscription limit or stopped by a transient technical error by itself ([Automatic resume](STUDIO.md#automatic-resume)); new attempts and a higher call limit without asking come only from the pre-approvals ([Budgets](BUSINESS_LOGIC.md#budgets)).
- At the next resume the new Studio worker loads updated research code without a server restart; reloading the browser page loads the new display.

### Written dossier of older runs

Runs started before 1 October 2026 (prompt generation 2 or earlier) keep their written dossier. This section applies only to them; an assembled dossier needs neither incorporation in blocks nor a source review in parts.

**Window limit.** Characters per Claude model (`claude_code.prompt_limit`; research prompts measured 2.1 to 2.4 characters per token) and the output cap of a call: [Text provider adapters](ARCHITECTURE.md#text-provider-adapters).

- Synthesis keeps every call below 240,000 characters of material (`question_synthesis.PROMPT_BUDGET_CHARS`).
- A call's dossier is about as long as the verified answers it incorporates, so a composing call takes at most 80,000 characters of verified answers (`question_synthesis.ANSWER_BUDGET_CHARS`) even if the prompt could hold more; a response cut off anyway ends as `claude_output_limit` and is not adopted.

**Composition in batches.** If the verified answers and their source passages do not fit one window, or the answers exceed this measure, the dossier is built in portions:

- The first answers that fit form the start (`dossier_batch_000.json`); every further answer is incorporated as a patch with only its own passages (`dossier_batch_NNN.json`, `dossier_batches.json`), as a resumed run incorporates newly closed questions into a stored dossier.
- A block correction fixes only evidence errors in its own findings and defers the source-wide quote and paraphrase limits that only the sum of the blocks can violate (`dossier_batch_NNN_references_deferred.json`); after the last block a dossier-wide pass corrects them (`dossier_batches_references.json`), as with a single call.
- After reopened questions, the incorporation block size follows the same budgets, at most four questions per block (`question_synthesis.SEED_BATCH_SIZE`).

**Overall review in parts** (`grounding_0_part_NNN.json`):

- Each part sees the dossier frame, its own findings with their passages and the objections concerning these findings; the parts are merged into one review receipt (`grounding_0_merged.json`) and checked as a whole.
- If the whole fails although every part passed (typically because the merged receipt keeps, per source, the assessment with the lowest independence), every part is checked again under the other parts' source assessments and, on a defect, requested again with that defect; only a defect that no part can be assigned stops the run.
- Each part repeats frame, other findings, sub-questions and verified answers. If these alone leave less than 80,000 characters of the budget free, a part still takes that much own material and then needs the larger window of Opus 5.5, instead of having each finding reviewed separately.
- Passages cited only by an objection are under `objection_sources`: they serve the objection check, and only the sources cited by the part's findings are rated.
- The assessment then receives the sources only as identity and pages; if even that is too much, it receives the review receipts without reasoning texts, the findings without the already checked quote excerpts, the review parts' source assessments combined into one line per source and finally the sources only as ID, title and address.
- The same limit binds the routing of objections (questions, criteria, answer summaries, finding statements and objection anchors instead of the evidence) and a correction round (objections, closing checks and the corrected findings' evidence verdicts instead of the whole review).
- More than 25 objections are routed in parts (`routes_part_NNN.json`, `routes_merged.json`, `question_synthesis.ROUTING_PART_SIZE`), because the response grows with the objections.
- The plan approval warns about this effort from six sub-questions on ([The plan approval hold](#the-plan-approval-hold)).

**Targeted overall review from the second round.** The first overall review reads the whole dossier; from the second round it checks only what changed or is still disputed: changed findings, findings with an objection of the last round and findings named by an objection still to be checked.

- An unchanged finding whose evidence passed in the last round keeps this evidence; a closed objection whose findings are unchanged stays closed.
- Adopted and new receipts are merged into one review receipt and checked as a whole (`grounding_0_merged.json`, field `targeted`). New objections thus arise only where something changed, and with the limit of two reworks per sub-question the loop ends.
- The selection is fixed at the start of the round (`grounding_0_targeted.json`), so a resume checks the same parts; a round that started as a full review runs to the end that way.

**Prompts.** Runs from prompt generation 2 on (`prompt_generation` in the ledger) also get the writing rule that a synthesis comparison names read evidence for each side; older runs keep their prompts and thus their stored receipts. Small runs still take the single-call path; their stored receipts stay valid.

## Outputs

| File | Content |
| --- | --- |
| `research/research_plan.yaml` | Research questions, search queries, scope and limits |
| `research/source_candidates.yaml` | URLs found, selection reasons and download problems |
| `sources/raw/<run_id>/` | Source files actually downloaded or imported |
| `sources/processed/<run_id>/` | Extracted texts, sections, pages, metadata and hashes |
| `models/source_index.yaml` | Structured index of the imported sources |
| `research/dossier.yaml` | Findings, evidence and question coverage |
| `research/research_briefing.md` | Readable dossier with quotes and source links |
| `research/open_questions.md` | Open questions and gaps |
| `research/questions.md` and `questions.json` | Verified individual answers, fixed completion criteria and sources |
| `runs/<run_id>/research_questions.json` and `.md` | Running state of every sub-question, readable even after an interruption |
| `runs/<run_id>/question_research/` | Work plan, read steps, answer reviews, download receipts and resume data |
| `research/quality.md` | Quality criteria and reasoned assessment of every original guiding question |
| `reports/research_quality.json` | Review status, limits, source count and run budget used |
| `runs/<run_id>/` | Manifest, checkpoints, search events, model metadata and repair findings |

The evidence artefacts (`evidence_report.json`, `search_receipts.json`, `objections.json`) are listed under [Artifacts and presentation](#artifacts-and-presentation).

A source candidate is not yet evidence. Only imported sections the writing model actually had in front of it may be quoted. Publication details may partly come from search results and are marked as metadata still to be checked.

### What a completed research run means

Every sub-question has a verified answer or is explicitly accepted as a gap, the answers have valid source references, and the overall review passed or ended with documented limits. `model_review` in `reports/research_quality.json` names what remained open:

| `model_review` | Meaning |
| --- | --- |
| `residual_objections_remaining` | Closed with remaining objections ([Finishing with remaining objections](#finishing-with-remaining-objections)). |
| `accepted_gaps_remaining` | Only with an actually accepted gap (`passed_with_accepted_gaps`). |
| `noted_limits_remaining` | Passed with noted limits (`passed_with_noted_limits`): a guiding question not met as a source limit or noted limit, a note next to a met one, notes for the script, objections noted as limits, or other remaining review objections, disputed ones included. |
| `no_remaining_issues` | Nothing remained open. |

`complete_topic_coverage` is true only when all guiding questions meet all quality criteria, no gap is accepted and no review objection remains, disputed ones included; it refers explicitly to `coverage_scope: agreed_brief`. This is an automated acceptance of the agreed research scope, not a claim of definitive knowledge about a field. Review details are in `research/quality.md` and `reports/research_quality.json`; during the research the Studio shows the current state per guiding question.

### Hand-off to planning

Planning adopts only the finally verified artefacts from `complete_research/`. Older dossiers without this quality level must be researched again. When a new research run starts, the old table of contents leaves the current selection; its run and the previous files are kept. While the new research is open, an older dossier cannot release a new planning as a substitute. See [Series plan](SCRIPTS.md#series-plan).

### In the Studio

The Studio shows **verified sub-questions / total**, the working state, fixed completion criteria, sections read, finished answers with source links and concrete blocks. The overall assessment of the original guiding questions stays separate, so that an outdated overall score does not hide ongoing progress. Opened answers stay expanded when the page refreshes.
