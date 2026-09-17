# Research pipeline comparison with Academic Research Skills

Historical design assessment, written before the evidence-contract implementation. Several proposed changes below are now implemented; use [the research workflow documentation](research.md) for current behavior. The comparison and its original verification limits are retained as design rationale.

Assessment date: 17 September 2026. Compared the project working tree, including uncommitted changes, with Academic Research Skills (ARS) v3.22.0 at commit [`3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e`](https://github.com/Imbad0202/academic-research-skills/tree/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e). This records the comparison before implementation, not a measured comparison of research quality. The recommendations have since been implemented; see [implementation, artifacts and validation limits](research-evidence.md).

**Keep our question-based controller. Adapt selected ARS evidence and review contracts into it.** Our application already supplies real source retrieval, exact passage checks, persistent answers, bounded recovery, input-bound checkpoints and enforced completion gates. ARS is more explicit about the meaning and strength of evidence, relationships between sources, review coverage and preservation of scientific qualifications. Those are the useful additions for this project.

| Area | Current project | ARS contribution | Decision |
| --- | --- | --- | --- |
| Source retrieval and locators | Downloads originals, retains raw/text hashes and page/section IDs, checks anchors against passages read for the task | Typed provenance and access states | Keep our retrieval; enrich its records selectively |
| Task scope and completion | Fixed acceptance criteria, separate scope review, exact criterion coverage, bounded reopening | Precommitted review criteria and field-specific evidence expectations | Extend the existing contract |
| Source suitability | Selection rationale and model review; URL-backed material distinguished from local notes | Source method, limitations, bibliographic identity and fitness for the particular claim | High-value addition |
| Claim support | Verbatim anchors plus semantic model review; aggregate support booleans | Per-claim judgments, partial support, evidence rows and coverage receipts | First implementation priority |
| Cross-source synthesis | Synthesis tasks and final dossier composition; connections expressed in prose | Explicit evidence matrix and scoped contradiction inventory | High value for the current multi-author topic |
| Revision | Protected unrelated findings; edits scoped by ownership; later grounding review | Claim-strength and qualification preservation inside edited text | Extend current protection |
| Orchestration | Python controller, durable state, real budgets, automatic internal progress | Prompt-driven stages and frequent human checkpoints | Retain ours |
| Evaluation | Mocked workflow tests and a saved-source retrieval check | Held-out defect tests and explicit capability/evidence ceilings | Adopt the evaluation discipline, not its numerical claims |

**What is already strong, including recent fixes**

[`question_research.py`](../src/podcast_automate/question_research.py) checks that a finding cites a known section read for that question and that its anchor occurs in that section. A separate call evaluates semantic support. Verified answers carry hashes and are revalidated on resume. Failed individual questions or final reviews prevent completion. These controls already implement much of the useful substance behind ARS's Material Passport and citation anchors.

The current tree also contains source-attempt accounting, finding ownership, collision-resistant split IDs and a minimum-remaining-call projection. The diagnostic from the earlier analysis now reports two downloads under a two-source limit, only the empirical finding editable when that task is dirty, and no long-ID collision. The old findings in `research-analysis.md` should therefore be treated as historical reproductions, not restated as current defects. The budget projection is present in code; this comparison does not establish that every production workload estimate is accurate.

The inspected saved run still has 77 tasks: 10 definitions, 25 mechanisms, 40 empirical tasks, one example and one synthesis task. One task is verified, one is researching and 75 are pending. It has consumed 33 production calls and has an explicit allowance of 250. This demonstrates the scale the design must handle, not that the plan is necessarily over-split or that a current run has failed. No production state was changed.

**1. Require explicit evidence judgments for every finding — highest priority**

Current gap: [`AnswerReview`](../src/podcast_automate/research_tasks.py) enumerates acceptance criteria but has only answer-wide `supported` and `source_adequacy` booleans. [`SourceReview`](../src/podcast_automate/research_review.py) returns issues and limitations. An empty issues list can close the source-review stage without a machine-checkable record that each finding was considered. The prompts do request full support checks; the missing part is the result contract.

An exact quotation can support one clause while the rest of the sentence adds an unsupported cause, prediction or generalization. The existing model review may catch that, but its output does not expose which clause was assessed against which passage.

ARS makes [claim-audit judgments](https://github.com/Imbad0202/academic-research-skills/blob/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e/shared/contracts/passport/claim_audit_result.schema.json) and [evidence provenance](https://github.com/Imbad0202/academic-research-skills/blob/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e/shared/references/evidence_row_protocol.md) explicit. Its partial-support representation is especially relevant.

Proposed change: extend the existing verification calls with one compact result per finding: finding ID, evidence references, support verdict, justification, and any unsupported clause. Distinguish supported, partially supported, contradicted and insufficient context. Keep retrieval or extraction failure separate from a semantic judgment. Validate exact finding-ID coverage and reference membership before accepting a review. A partial claim needs an evidence-backed correction that still satisfies the original task, or additional research.

Reuse the implementation pattern already in [`series_review.py`](../src/podcast_automate/series_review.py): it requires complete review coverage and supporting passages for successful checks. This needs a larger structured response, not a new agent or another mandatory call. Coverage validation makes omissions visible; it cannot prove that the model's judgments are correct.

**2. Record source roles and independence, rather than treating another URL as another test**

Current gap: [`SourceDocument`](../src/podcast_automate/research_models.py) has useful provenance but no structured work identity, study design, evidence family or relationship to a theory's proponents. [`import_source`](../src/podcast_automate/sources.py) retains selection rationale and generic extraction warnings. The deterministic external-source check establishes that a document was fetched from the web; independence and source adequacy remain model judgments.

Consequently, an author's book, their institutional summary and a preprint of the same study can look like three documents without representing three independent results. Text-hash deduplication cannot identify all such relationships. This is a design exposure, not an observed misclassification in the saved run.

ARS's [bibliography workflow](https://github.com/Imbad0202/academic-research-skills/blob/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e/deep-research/agents/bibliography_agent.md) and [source-quality framework](https://github.com/Imbad0202/academic-research-skills/blob/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e/deep-research/references/source_quality_hierarchy.md) offer a clearer approach: identify the work, describe the method and limits, and assess fitness for the claim.

Proposed change: add source assessments with a role such as original definition, theory exposition, empirical test, replication, critical comparison or synthesis; record method, scope and known limitations when visible in the source. Group known versions of the same work and shared datasets. Mark unknown independence explicitly. Bibliographic IDs can help identify versions, but absence from an index must not reject legitimate books or regional sources.

Use claim-specific evidence expectations: an original definition can establish what an author means; a causal or predictive claim needs evidence appropriate to that assertion. For contested claims, save the counterevidence search attempted and its result. Our discovery prompt already asks for independent tests and competing accounts; this makes that work inspectable. Do not introduce a universal minimum number of sources or copy a medical evidence hierarchy into historical and social theory research.

**3. Make cross-source synthesis an explicit artifact**

Current gap: [`ResearchDossier`](../src/podcast_automate/research_models.py) contains findings, question coverage and open questions, but no typed relationships between findings. `compose()` receives the checked answers and requests connections in prose. The downstream [`KnowledgeModel`](../src/podcast_automate/script_models.py) has explanation dependencies and a list of limitation IDs; it does not add a structured disagreement or evidence-strength map.

This matters directly to the saved task comparing Morris, Piketty, Dalio and Turchin. Accurate individual summaries do not establish whether two accounts contradict each other, concern different scales or periods, or merely use similar language. A fluent combined narrative could hide those distinctions.

ARS's [synthesis agent](https://github.com/Imbad0202/academic-research-skills/blob/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e/deep-research/agents/synthesis_agent.md) specifies candidate source pairs, evidence pointers, conflict type and resolution state. It distinguishes contradiction, conditional difference, no material conflict and insufficient overlap, and explicitly limits its coverage claim.

Proposed change: generate a bounded synthesis map in the existing synthesis/composition call. Each important connection should name the participating findings, comparison dimension, relevant conditions, relation, evidence and explanation. Mark our own synthesis as an interpretation derived from named premises, rather than implying that a source directly tested the combined theory. Carry unresolved substantive disagreements into the dossier and teaching plan. Do not require every possible source pair to be compared or invent counterarguments for settled definitions.

**4. Preserve claim strength through composition and podcast simplification**

Current gap: findings carry a statement and evidence but no explicit claim-strength or qualification record. Scoped patches protect untouched findings, yet an authorized edit can still change the meaning inside a finding. Our polishing and script-review prompts already prohibit altered numbers, unsupported causality and stronger claims; those protections are primarily semantic instructions and model review.

ARS's [claim-strength ladder](https://github.com/Imbad0202/academic-research-skills/blob/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e/shared/references/claim_strength_ladder.md) makes the distinction precise. Association, prediction and causation are different assertions; dropping a population limit, negation or exploratory qualifier can change the claim even when the citation remains unchanged.

Proposed change: retain a small claim contract alongside important findings: asserted relationship, scope, essential qualifications, and whether the basis is a source's theory, an empirical result or our synthesis. Compare changed findings and later script segments against it. Preserve normalized quantities, units and direction where applicable; use semantic review for wording and qualification changes. Do not freeze every numeric token, since spoken language and translations can legitimately change representation.

A hypothetical example: “X predicts unrest in the observed sample” must not become “X causes social collapse.” The citation and all nouns could remain correct while the assertion changes substantially. This extension belongs across research, composition and polishing; another final citation-presence check would not address it.

**5. Give review objections stable criteria and a concrete closure condition**

Current gap: the task reviewer uses fixed criterion indices, which is good. Final objections and reopening reasons are mostly strings, however, and their connection to the original acceptance contract is explained by another model. Correct routing to an existing task does not by itself establish that the objection is necessary, supported or within scope.

ARS's [review report contract](https://github.com/Imbad0202/academic-research-skills/blob/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e/academic-paper-reviewer/templates/peer_review_report_template.md) requires anchored findings, and its [reviewer workflow](https://github.com/Imbad0202/academic-research-skills/blob/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e/academic-paper-reviewer/SKILL.md) separates committed standards, evidence evaluation and revision verification.

Proposed change: keep our separate review calls and frozen criteria. Give each blocking objection a stable ID, criterion or existing quality-rule reference, affected findings, source evidence or an explicit description of the missing evidence, the requested correction and a closure condition. Preserve the current `research` versus `revise` distinction. Newly noticed genuine defects remain valid; a new optional topic must not silently become a completion requirement. An inadequately supported objection should become a review disagreement to resolve, rather than immediately triggering another search.

This is more useful than copying a five-person review panel. Separate calls to the same model can share blind spots; persona names do not establish independent error processes.

**6. Reuse checked prerequisites and give different question types appropriate stopping rules**

Current gap: tasks already have kinds and acceptance criteria. However, [`QuestionTask`](../src/podcast_automate/research_tasks.py) has no prerequisite task IDs. The reader prompt supplies the current task, sources, task-group aliases and local history, not a package of previously verified prerequisite answers. Ordering a synthesis task after other tasks is currently a planning instruction, not an explicit dependency contract. Full dossier composition does receive all checked answers.

ARS's staged handoffs and methodology blueprint motivate a clearer separation of prerequisite research and synthesis. A task dependency graph is our proposed adaptation, not a claim that ARS provides a directly reusable scheduler.

Add optional `depends_on` references for genuine dependencies, validate that they form a DAG, and supply the dependent task with compact verified prerequisite answers plus source locators. A new cross-task inference must still be checked. When a prerequisite materially changes, mark dependent answers for targeted revalidation; avoid silently invalidating everything in the same broad question.

Formalize small evidence profiles using the existing task kinds: definition, mechanism/theory, empirical test and synthesis. Record whether a completion represents a supported answer or a supported account of unresolved science. Distinguish missing access, extraction failure, search exhaustion and budget exhaustion operationally. The current prompts already permit supported uncertainty; typed outcomes should make that distinction visible without relaxing completion requirements or shrinking scope.

**Secondary improvements**

- Add a compact search and selection receipt linking executed query, task, returned candidates, inclusion/exclusion reasons and counterevidence attempt. Existing search events, candidate rationale and attempt logs are useful foundations; they are not absent. Extend them rather than storing another competing history.
- Assess known corpus concentration by research group, dataset, method, geography or period. Show unknown denominators and treat concentration as an advisory whose relevance depends on the question.
- Record PDF extraction coverage and suspected missing tables, equations or image-only pages. Current extraction has size/page bounds and generic warnings; those do not establish that a particular statistical table was read correctly. A claim requiring missing content should stay unresolved or use another accessible source. No blanket OCR requirement is needed.
- Distinguish “passage exists,” “automated support review passed” and “independently tested” in research outputs. The existing completion explanation already limits its claim to the agreed brief; retain that honesty at finding level as well.

**Implementation order and evaluation**

First build a small frozen evaluation set and record the current baseline, then implement the claim-review receipts and anchored objection contracts together. They make the existing controller's decisions inspectable without adding review rounds. Next add source roles/independence and the bounded synthesis map. Then add claim-preservation checks and prerequisite reuse, measuring both correctness and resource use.

Use a definition-heavy topic, a contested empirical topic and a multi-author synthesis topic. Include independently annotated defects: a valid quotation attached to an unsupported extra clause, same-study sources presented as independent, correlation promoted to causation, a lost population qualifier, a false contradiction caused by different time scales, and a genuine unresolved question incorrectly treated as failed research. Include clean controls so a more aggressive reviewer is not rewarded for inventing defects. Freeze held-out cases separately from prompt-development cases.

Measure unsupported claims accepted, valid claims falsely blocked, missing required explanation steps, unjustified reopenings, semantic drift, call consumption and human correction time. Structural tests should additionally reject omitted review rows and stale evidence hashes. Test that scientific uncertainty can close a properly evidenced question while failed retrieval cannot. Report model, prompt/schema versions and corpus hashes; do not infer research accuracy from mocked tests.

ARS is useful here as an evaluation-design reference, but its own [capability matrix](https://github.com/Imbad0202/academic-research-skills/blob/3c546bc08c56f79e0068f1ea4f0acedf5bf69b5e/docs/STAGE_CAPABILITY_MATRIX.md) records unmeasured citation/claim catch rates and limited reviewer evidence. Its measurements do not establish that importing its prompts would improve our outputs.

**What to leave out**

The full academic orchestrator, journal-fit personas, publication formatting, PRISMA paperwork for every topic, frequent internal approval prompts and broad multi-agent expansion do not fit this application's core purpose. Our existing hashes and ledgers already serve the useful passport function. Four-index bibliographic lookup is secondary to verifying the actual downloaded passages we use. Installing the full suite would also introduce its runtime assumptions and a CC BY-NC 4.0 licensing dependency; this report proposes original, project-specific implementation of the ideas, not copying its code or prompts.

**Verification for this comparison**

The existing offline diagnostic was rerun using only temporary synthetic projects. It confirmed the three earlier defect reproductions no longer reproduce. The selected suites `tests.test_question_research`, `tests.test_research_quality` and `tests.test_research_refinement` ran **57 tests successfully**. Models and downloads were mocked; this is not an end-to-end research-quality evaluation.

The project's Python 3.13 launcher was inaccessible in the sandbox. Successful checks used the bundled Python 3.12.14 runtime, Pydantic 2.13.5 and bundled pypdf 6.10.0, with missing pure-Python dependencies available from the existing environment. No dependencies were installed. The saved production run was inspected read-only. No live model calls, live research, budget changes, skill installation or production-run mutations were performed. Only this comparison document was added.
