---
title: Overviews and Evidence Levels Plan
doc_type: plan
status: active
date: 2026-10-21
---

# Overviews and Evidence Levels Plan

Goal: short overviews of 15 to 30 minutes next to the deep-dive series, and a choice of how strictly a project is bound
to sources. Scope: the brief, a new kind of research run built from the user's own material, the import of finished
projects, series planning for one episode, the evidence checks of the script run, the exports' transparency note and
the Studio. Recording stays as it is; the audiobook is a later plan ([Not in this plan](#14-not-in-this-plan)).

Written for the operator and the agents who implement it. Every code reference was checked against the working tree
on 21 October 2026. Rules that already hold are owned by [BUSINESS_LOGIC](../BUSINESS_LOGIC.md),
[RESEARCH](../RESEARCH.md) and [SCRIPTS](../SCRIPTS.md); this plan links to them and states only what would change.

## Status

**Planned; nothing implemented.**

The operator decided on 21 October 2026:

1. **The overview is a format of its own:** one episode of 15 to 30 minutes, made from a topic, from own material,
   from finished projects, or from any mix of these. A series project is one kind of material, not a precondition.
   (why: D-TODO(overview-format))
2. **Three evidence levels per project**, chosen in the brief and independent of the format
   ([§2.2](#22-evidence-levels)): „Universitär“ (sourced, as today), „Eigenes Material“ (the material plus the
   model's general knowledge, no research) and „Material + Recherche“ (research allowed, source rules relaxed).
   (why: D-TODO(evidence-levels))
3. **Where general knowledge ends** ([§2.4](#24-the-general-knowledge-boundary)): at levels 2 and 3 the model may
   explain terms, concepts and relationships from its own knowledge. Concrete facts (numbers, dates, versions, names,
   and anything about the subject itself, such as a tool's buttons and functions) come only from the material or, at
   level 3, from research. (why: D-TODO(general-knowledge-boundary))

Open decisions: O-6 to O-10 in [DECISIONS](../DECISIONS.md#open-questions), worked out in [§11](#11-open-decisions).

## 1. Problem

- **One format.** The product is the deep-dive series. Orlagau has 15 episodes and 762 planned minutes (about 13
  hours), built on 414 findings, 300 of them episode core; its research took 1,488 model calls. Few listeners take
  13 hours.
- **No short form.** The planner sizes length by content: „not a fixed total runtime: target_minutes between 15 and
  60 per episode“ (`prompts/series_plan.txt:2-4`). A requested total is only a wish that the plan weighs against the
  content ([Series length follows the content](../BUSINESS_LOGIC.md#series-length-follows-the-content)). At Orlagau's
  density of about 0.4 core findings per minute, 20 minutes carry 8 to 10 core findings. The planner would ask for
  more episodes instead.
- **One evidence level.** Every project is researched on the web with full source rules:
  - A script run refuses to start without a completed research run (`research_required`, `scripting.py:57-63`). The
    audio run checks the same research (`episode_audio.py:69-71`).
  - The web search cannot be switched off: `--web-search` takes only `model` and `perplexity` (`cli.py:100-102`).
  - User material without an address is never evidence (`research_models.is_idea`, `research_models.py:198-204`).
    Attachments enter research as `source_type="idea"` (`research.py:756-759`), and the prompts call them
    „unverified starting material, not independently verified evidence“ (`prompts/material_rules.txt:1`).
- **Work use.** An overview of how one of the operator's tools works has no sources beyond a description and a few
  screenshots. Strict research would block on missing sources. A free model would invent buttons.
- **No images.** No adapter passes an image:
  - Claude Code gets the prompt on stdin and either no tools or only the search tools (`claude_code.py:392-395`).
  - The Codex app server sends only a `text` input item (`codex_stream.py:151`).
  - OpenRouter sends the prompt as a plain string (`openrouter.py:276`).
  - Attachments accept `.md`, `.txt` and `.docx` only (`attachments.py:105`).
- **One series per project.** A run that publishes a different table of contents moves the earlier episodes to
  `episodes/archive/` ([Earlier series](../SCRIPTS.md#earlier-series)). An overview inside the Orlagau project would
  push its 15 episodes out of the Studio.

## 2. Concepts

### 2.1 Format

| Format | Brief | Episodes | Length |
| --- | --- | --- | --- |
| Series (today) | `format` unset | as many as the content needs | 15 to 60 minutes each, total from the content |
| Overview (new) | `format: overview` | exactly one | 15 to 30 minutes, binding |

An overview is a project of its own, like every series. A new overview of the same material is a new project or a
[new version](../STUDIO.md#new-versions).

### 2.2 Evidence levels

| Level (Studio label) | Brief value | Basis | Web research | General knowledge | Transparency note |
| --- | --- | --- | --- | --- | --- |
| 1 „Universitär“ (sourced) | `evidence_level` unset (`sourced`) | verified sources, as today | full question research | not as a basis | today's wording |
| 2 „Eigenes Material“ (own material) | `material` | the operator's material and imported projects | none | explanations only ([§2.4](#24-the-general-knowledge-boundary)) | own wording ([§7](#7-exports)) |
| 3 „Material + Recherche“ (material and research) | `material_research` | as level 2, plus looked-up web sources | for gaps only, relaxed ([§4](#4-level-3-lookup-with-relaxed-rules)) | explanations only | own wording |

The level is chosen with the brief and bound to the project like every content field. Changing it later means a new
version, because every run is bound to the project hash.

### 2.3 Material

| Kind | Today | Plan |
| --- | --- | --- |
| Text: MD, TXT, DOCX | attachments: 10 files, 256 KiB each, 1 MiB of text per project (`attachments.py:18-22`); idea sources | one material area at the brief; at levels 2 and 3 the basis of the overview |
| Documents: PDF, saved web page | provided works on the research page, with a citation, up to 150 MiB (`provided_works.py:25`, `:61-63`); evidence | in the same material area; the citation stays required for a third-party work |
| Web links | `seed_urls` in the brief; fetched by research | at level 2 fetched once as material, without search |
| Screenshots: PNG, JPG | not accepted anywhere | described once in text, the description checked by the operator ([§3.2](#32-screenshots)) |
| Finished projects | only as a starting library of a new version ([Starting library](../RESEARCH.md#starting-library)) | their verified findings imported with their sources ([§5.2](#52-finished-projects-as-material)) |

At level 1 own material keeps today's status: attachments point the way, provided works are evidence. Whether an
operator may mark a single file as evidence at level 1 is O-6.

### 2.4 The general-knowledge boundary

At levels 2 and 3:

- **Allowed from the model's knowledge:** terms, concepts, relationships and general explanations, for example what a
  webhook is or why a cache speeds things up. Such a segment cites no finding.
- **Only from the material or, at level 3, from a looked-up source:** numbers, dates, versions, names, quotations, and
  every statement about the subject itself. For a tool, that covers its screens, buttons, functions, limits and
  behaviour.
- **Not answered by the material:** at level 2 the point goes to the operator ([§3.4](#34-open-points)); at level 3 it
  is looked up first.
- **Audible origin.** The script says where a statement comes from: „laut eurer Beschreibung“ (according to your
  description), „auf dem Screenshot“ (on the screenshot), „laut <Seite>“ (according to <page>), or a general
  explanation. This extends today's rule ([Role, recap and theory first](../SCRIPTS.md#role-recap-and-theory-first);
  `prompts/write_episode.txt:49-51`).

The review checks the boundary ([§6.3](#63-review-verdict-for-general-knowledge)).

## 3. The material run (levels 2 and 3)

The script run and the audio run read research through `scripting.load_research` (`scripting.py:57-102`). The
material run therefore writes the same contract instead of bypassing it:

- `research/latest.json`;
- a run of kind `research` with a `completeness` stage;
- `complete_research/discovery.json`, `source_index.json`, `source_context.json` and `dossier.json`
  (`research_quality.load_complete_research`, `research_quality.py:287-292`);
- `research_quality_gate.json`.

Everything after it works unchanged: the knowledge model, built deterministically (`script_pipeline.py:291-303`),
planning, writing, the reviews, recording, show notes and kits.

The run records its mode (`material`) in its manifest and inputs. Its stages:

### 3.1 Import

- **One material area.** Every file goes under `inputs/` as today. The attachments' 1 MiB text limit does not apply to
  material; the provided works' 150 MiB and book limits do.
- **Material sources.** Each file becomes a `SourceDocument` with sections, typed with a new
  `source_type: material`. `is_idea` stays as it is for level 1. A material run admits `material` as evidence and
  labels it `user_material` to the models, as `research_reader.py:26` and `:92` already do.

### 3.2 Screenshots

- **One description call per image.** A vision-capable model describes what is visible: the screen, its labels, the
  fields and the order of steps. The result is a text source whose sections the findings cite.
- **The operator checks.** The Studio shows every description next to its image before any finding is extracted. A
  correction is the operator's text and replaces the description.
- **Which provider reads images** is open (V-51). Each adapter needs an image path of its own: an image input item for
  the Codex app server, an image input for Claude Code, image content parts for OpenRouter.
- **Key detection.** Attachments refuse texts that contain API keys (`attachments.py:125-130`). The description runs
  through the same check; the image itself has already gone to the vision model by then ([§9](#9-security-and-privacy)).

### 3.3 Findings from the material

- **One extraction call per material source** (or per section group for a long one). It yields findings of the
  existing kinds (definition, claim, mechanism, example, limitation). Each carries evidence `reference` and verbatim
  `excerpt` (`research_models.py:98-111`).
- **`research.validate_dossier` checks the excerpts** against the material, as it does today (`research.py:302-339`).
  This check is code and costs nothing; it stops a finding that cites something the material does not say.
- **Word limits.** The 25 quoted and 150 paraphrased words per source protect third-party rights. They apply to a
  third-party work in the material, but not to the operator's own notes and descriptions.
- **Questions.** The discovery questions are the brief's central question and focus questions. Coverage holds one row
  per question, as `validate_dossier` requires.

### 3.4 Open points

A question the material does not answer becomes a coverage gap:

- **Level 2:** the Studio lists the gaps. The operator answers a gap, and the answer becomes a material source of its
  own („Angabe des Auftraggebers“, provider's statement, dated). Or the operator accepts the gap, and it becomes a
  research limit that the script names once ([Research limits in the script](../SCRIPTS.md#research-limits-in-the-script)).
  Whether the run asks or only names the gaps is O-7.
- **Level 3:** a lookup comes first ([§4](#4-level-3-lookup-with-relaxed-rules)), then the same choice for what
  remains.

### 3.5 Quality gate

`research_quality_gate.json` gets a version of its own (`material_quality.v1`). It passes when:

- the dossier validates;
- every gap is answered or accepted.

The requirement of evidence from non-idea sources (`research_quality.py:138-152`) does not apply. The limits are
written in the shape `script_checks.research_limits` reads (`script_checks.py:334-368`). `scripting.load_research`
accepts the material version for a brief at levels 2 and 3, and only for those.

## 4. Level 3: lookup with relaxed rules

- **Before planning:** a lookup stage of the material run searches the web for the gaps of [§3.4](#34-open-points). It
  is built from the parts of the supplementary research (`teaching_research.py`): source discovery, the supplement
  check and the source review.
- **During the script run:** the supplementary research of the teaching plan stays available
  ([Supplementary research for the teaching plan](../TEACHING.md#supplementary-research-for-the-teaching-plan)), with
  the same relaxations.

What is relaxed and what is kept:

| Dropped at level 3 | Kept at level 3 (code only, no extra calls) |
| --- | --- |
| The original-work rule: today the search asks for „publicly readable PRIMARY sources“ (`prompts/foundation_discovery.txt:3`). Documentation, overviews, reference works and practice reports count. | Every statement from the web names the page it was read from. |
| The question research with its independent answer review, repair rounds, advisor and plan approval | The verbatim excerpt check against the fetched page |
| The block when a work is out of reach: the gap is named as a limit instead | The quote word limits for third-party sources |
| The independence requirements (`single_group_findings`) | Idea sources are never evidence. |
| The 74-question scale: a fixed cap of searches and sources per run, measured in V-55 | The review blocks only for a critical error (factual error, claim beyond the passage, contradiction), as the supplement review does today (`teaching_research.py`, `FoundationReview`). |

## 5. The overview format

### 5.1 Brief and planning

- **The brief:**
  - `format: overview`;
  - `target_total_minutes` required, between 15 and 30;
  - an overview from a topic at level 1 runs the question research with a cap of its own (`OVERVIEW_SUB_QUESTIONS`,
    10 to start, measured in V-54).
- **How the cap is applied.** Trial projects already do this (`trial.plan_cap`, `trial.py:58-63`, applied in
  `QuestionResearch.planning_allowance`, `question_research.py:830-847`). At the measured 16.4 to 23.4 calls per
  sub-question, 10 sub-questions cost about 165 to 235 calls, plus about 15 for discovery and the closing steps.
- **A plan prompt of its own.** `prompts/overview_plan.txt` with its own `prompt_version` (`overview_plan.v1`), since
  the series prompt's first lines contradict a fixed length. The prompt plans one episode:
  - the length as the brief says;
  - core findings chosen by how much they contribute to the central question;
  - study details as supporting findings.
- **Omissions completed by code.** `validate_plan` needs every finding placed or omitted (`script_checks.py:77-82`).
  With 414 findings, an overview omits about 400. The model names only what it places; code adds every other finding
  as an omission with the reason „nicht Teil des Überblicks“ (not part of the overview). The readable outline lists
  the selection per research question.
- **Plan check.** Exactly one episode with 15 ≤ `target_minutes` ≤ 30. Today a single episode needs a
  `worked_example` scene, because the finale exemption needs more than one episode (`script_checks.py:63-67`). For an
  overview, a synthesis scene that follows a case counts as well.

### 5.2 Finished projects as material

- **An import copies a finished project's newest completed research run into the overview.** That covers its
  findings, sources, source context, coverage and discovery questions. Every id is namespaced per project (an
  `Identifier` is `^[a-z][a-z0-9_]*$`), and the references `source_id#section_id` are rewritten.
  `validate_dossier` must pass on the union.
- **The binding is new.** `load_research` compares the run's snapshot with the brief on topic, central question,
  focus questions and further content fields (`scripting.py:75-79`). The quality gate binds the brief's hash
  (`scripting.py:84-89`). An overview with a question of its own fails both against a copied run. So the import is a
  research run of the overview project itself (mode `import`): it records the source project, the run id and the
  hashes of the copied files, and writes its own gate for the overview's brief.
- **Levels.** At level 1 the imported findings stay verified, so an Orlagau overview from the import is
  „Universitär“. At levels 2 and 3 they join the material run's dossier with their sources and count as sourced.
- **Planning help from a series.** If the source is a series, the planner may also read its series plan and each
  teaching plan's `big_idea` and `destination`. The overview then follows the series. Whether it points to the
  series' episodes („ausführlich in Folge 7“, in detail in episode 7) is O-9.
- **Mixing at level 1.** A topic research and imports in the same overview at level 1 would need a merge of two
  verified research runs. That is not in this plan; level 3 covers the mix.

### 5.3 Framing, writing and reviews

- **Framing.** The framing prompt already handles a standalone episode: „For a standalone episode, one integrated
  introduction and conclusion suffice“ (`prompts/episode_framing.txt:28-29`; `series_plan.txt:32-33`). The overview
  adds that the episode introduces itself as an overview of its subject.
- **The writer's length.** It comes from the word budget as today (`script_checks.word_budget`), with the floor of 85 %
  of the planned minutes (`script_checks.MIN_DURATION_SHARE`).
- **Risk: segments that sum up.** A segment that sums up several findings is where claim drift arises, and claim drift
  drove most review repairs in the 2026-10-04 evaluation. The evidence review keeps its rules. A summary sentence
  cites the findings it restates, as a synthesis relation does (`evidence_models.SynthesisRelation`, 2 to 6
  findings). V-53 measures the repairs per overview against those per series episode.

## 6. The level in every stage

### 6.1 Stage table

| Stage | Level 1 | Level 2 | Level 3 |
| --- | --- | --- | --- |
| Research | question research as today | material run ([§3](#3-the-material-run-levels-2-and-3)) | material run with lookup ([§4](#4-level-3-lookup-with-relaxed-rules)) |
| Gap probe and Jev (`ScriptRun.run_probes`, `script_pipeline.py:440-476`) | on | off: the gaps are settled in the material run | off |
| Research gap in the teaching design (`teaching_research_required`, `teaching.py:627-637`) | supplementary research | a stop card with the question for the operator; the answer is stored as that episode's supplement with the answer as its source | supplementary research with the relaxations of [§4](#4-level-3-lookup-with-relaxed-rules) |
| Spot check against the source (`script_evidence.py:73-79`) | on | on, against the material sections | on |
| Prompts that forbid model knowledge (`prompts/write_episode_opening.txt:2-3`, `prompts/dialogue_polish.txt:4-5`, `prompts/script_review.txt:57-58`) | unchanged | a level block allows explanations within [§2.4](#24-the-general-knowledge-boundary) | the same block |
| Origin labels (`prompts/write_episode.txt:49-51`) | unchanged | adds material, the operator's answer and general explanation | adds the looked-up page |
| Transparency note | unchanged | wording of level 2 | wording of level 3 |

### 6.2 Level blocks in the prompts

One prompt block per level (`prompts/evidence_level_material.txt`, `prompts/evidence_level_research.txt`) is composed
into writing, polishing and its comparison, the script review and its repair, and the teaching design and its review.
At level 1 no block is added, so these prompts and their hashes stay byte-identical. At levels 2 and 3 the stage tags
get a suffix (`+material`, `+material_research`), as a follow-up review gets `+followup` today
(`script_pipeline.py:935`).

### 6.3 Review verdict for general knowledge

A segment's receipt (`evidence_models.SegmentClaimCheck`, verdicts `preserved`, `drift`, `no_research_claim`,
`source_corrected`) gets a new verdict at levels 2 and 3: `general_knowledge`. The review sets it for an explanation
that cites no finding. A concrete fact in such a segment is a `grounding` issue, as an unsupported claim is today. The
review prompt gets the boundary of [§2.4](#24-the-general-knowledge-boundary) word for word.

## 7. Exports

- **Transparency note.** It is defined in `content_text.py` (German `:240-251`, English `:448-456`) and written into the
  show notes, the listening sheet and both kits ([AI marking](../AUDIO.md#ai-marking)). Levels 2 and 3 get their own
  wording; level 1 keeps today's bytes, which the German goldens in `test_content_text` pin. Drafts for O-10:
  - Level 2: „Dieser Output fasst bereitgestelltes Material zusammen und ergänzt allgemeine Erklärungen. Er ist nicht
    anhand unabhängiger Quellen geprüft.“ (This output summarises provided material and adds general explanations. It
    has not been checked against independent sources.)
  - Level 3: „Dieser Output beruht auf bereitgestelltem Material und ergänzender Webrecherche. Die Quellen sind
    genannt, aber nicht mit wissenschaftlicher Strenge geprüft.“ (This output is based on provided material and
    supplementary web research. Its sources are named but not checked with scientific rigour.)
- **AI marking.** The MP3 tags (`audio.ai_marking`, `audio.py:45-48`) stay as they are.
- **Source lists.**
  - The show notes of a script run link a local source by its path (`"../../" + source.raw_path`,
    `script_artifacts.py:124`). Material is listed by its original name instead.
  - The companion kit leaves idea sources and local paths out today (`publish_kit.py:279-280`). It lists material as
    „Eigenes Material: <Name>“ (own material: <name>), without a path.
  - With no source at all, the kits already write `kit_no_sources` (`content_text.py:40`); nothing fails.

## 8. Studio

- **New project.** Format („Serie“ or „Überblick“) and level (the three labels of [§2.2](#22-evidence-levels)) are
  chosen next to the trial option (`trialOption`, `web/app.js:623-629`). The new project's POST carries them like
  `trial` (`studio.py:1846-1883`). The default stays a series at level 1, so nothing changes for the operator's
  current way of working.
- **The setup conversation** knows format and level. Its `BriefProposal` (`studio.py:539-557`) gains neither field;
  they are the operator's choice, not the model's.
- **One material area at the brief** for every kind of [§2.3](#23-material). At level 1 it shows today's two
  statuses: lead and work with citation.
- **Step 2 at levels 2 and 3.** The step keeps its key `research` (`web/app.js:42`) and is labelled „Material“. It
  shows the material, the screenshot descriptions to check, the open points to answer or accept and, at level 3, the
  looked-up sources.
- **„Überblick erstellen“** (create an overview) in a finished project's card menu creates a new overview project
  with that project as material, built like a new version (`project_versions.py`).
- **Chips** for format and level on the overview cards, beside the trial chip (`web/app.js:1946`).
- **Interface text** comes from `locales/de.json` and `locales/en.json` with the same keys.

## 9. Security and privacy

- **What leaves the computer.** The trust-boundary table names the text of attachments and provided works going to the
  text model ([Trust boundaries](../SECURITY.md#trust-boundaries)). Screenshots to the vision model are a new row.
  So is internal work material, which at levels 2 and 3 is the main content of a prompt. The script still goes to
  the speech provider; only Qwen speaks locally.
- **A hint at upload.** The material area says once per project that the material is sent to the text model.
  Whether a company allows that for internal tools is the operator's call.
- **Screenshots can show secrets** (a token, a customer's name). The key detection sees only the description, after
  the image has gone out. The Studio's hint says so.
- **Local paths stay inside the project**, as today ([Local files](../SECURITY.md#local-files)).

## 10. Steps

Each step is checkpointable and leaves every existing project byte-identical. Its tests follow the mapping in
[AGENTS](../../AGENTS.md#source-module-to-test-modules).

**Phase A: contracts, no change in behaviour**

1. **`TopicBrief.format` and `TopicBrief.evidence_level`.**
   - Both are left out of every dump at their defaults (the `trial` pattern, `models.py:153-166`), so every existing
     `project.yaml` and project hash stays as it is.
   - `Contract` forbids unknown keys (`models.py:59`), so an older version of the code refuses a brief that carries
     either field. That matters only for a downgrade.
   - The Studio's choice and chips are part of this step, and so are the decision entries.

**Phase B: overview at level 1**

2. **The overview plan.** `overview_plan.txt`, the plan check, the omissions completed by code, the synthesis scene
   counting for an overview, `OVERVIEW_SUB_QUESTIONS` and the framing line.
   - First real test: an overview of a narrow topic, measured for V-54.

**Phase C: finished projects as material**

3. **Import.** The import run with namespaced ids, its binding and its gate, the planning help from a series, and
   „Überblick erstellen“.
   - First real test: the Orlagau overview, measured for V-53.

**Phase D: level 2 with text material**

4. **The material run.** The material source type, extraction, open points, the material gate and
   `load_research` accepting it.
5. **Prompts and exports.** The level blocks, the review verdict `general_knowledge`, the gap probe and Jev off,
   teaching gaps to the operator, the transparency wording per level, and the material area and step 2 in the
   Studio.
   - First real test: an overview of one of the operator's tools from a text description, checked with the
     fidelity eval of [§12](#12-testing-and-evals).

**Phase E: screenshots**

6. **Image description.** After V-51: the description step with the operator's check, the key detection on the
   descriptions, and the SECURITY rows.

**Phase F: level 3**

7. **Lookup.** The lookup stage in the material run and the relaxed supplementary research in the script run.
   - Measured for V-55.

Phases C and D do not depend on each other and can swap.

### Acceptance criteria

- **Phase A.** Every existing project loads, hashes and resumes as before; a new project stores both fields only when
  they are set.
- **Phase B.** An overview plan has one episode within 15 to 30 minutes; every finding is placed or omitted; the
  script passes every check of a series episode.
- **Phase C.** An Orlagau overview is written from the import without a new research call, and its findings carry
  the original evidence.
- **Phase D.** A level-2 overview of a tool from a description:
  - states no concrete fact the material lacks (fidelity eval);
  - names the origin of its statements;
  - ends with the level-2 note.
- **Phase E.** Every screenshot's description is checked before a finding cites it.
- **Phase F.** A level-3 overview names a page for every looked-up statement; no run stops because an original work
  is out of reach.

## 11. Open decisions

| ID | Question | Recommendation |
| --- | --- | --- |
| O-6 | May own material count as evidence at level 1, set per file, reversing the rule of 2026-09-30 („LLM-Notizen sind Landkarten“, LLM notes are maps) for that file? | Per file. A published work with a citation is evidence by default, a note a lead by default. |
| O-7 | Open points the material does not answer: ask the operator before writing, or only name them as open? | Ask; accepting a point turns it into a stated limit. |
| O-8 | Material kinds beyond text, PDF, links and screenshots: slides (PPTX), transcripts of talks or videos, e-mails? | Decide before Phase D; each kind is its own import step. |
| O-9 | Does an overview made from a series point to the series' episodes? | Yes when the source is a series of the same podcast; switchable per overview. |
| O-10 | The wording of the transparency note at levels 2 and 3 | The drafts in [§7](#7-exports), to be approved by the operator. |

## 12. Testing and evals

The changes touch high-fanout modules. Select tests by the mapping in [AGENTS](../../AGENTS.md#when-to-run-what):

- `models.py` and `storage.py` select the full Python suite.
- The prompts select `test_prompts` plus the tests of every module that composes them.
- `script_checks` and `script_pipeline` select the scripting line.
- The research modules select the research line.
- `content_text` selects `test_content_text`; the German goldens must stay byte-identical at level 1.
- `studio` and `web/` select the Studio line and the browser suite.
- When a stage gains a model call, update `STAGE_CALLS` and check `test_run_budget` against what the fixture pipeline
  spends.

New tests:

- the brief fields omitted at their defaults, byte for byte (as `tests/test_trial.py:57-68` does for `trial`);
- the overview plan check and the omissions completed by code;
- the material run's dossier passing `validate_dossier` and `load_research`;
- refusal of the material gate for a level-1 brief;
- the import's namespacing and binding;
- the verdict `general_knowledge`, and a concrete fact without a finding as a `grounding` issue;
- the transparency wording per level;
- the gap probe and Jev off at levels 2 and 3, with no quota query on the fixed-provider path.

Evals, run by hand under `evals/`:

- **`evals/material_fidelity/` (new).** A tool description with a few facts deliberately left out, plus three
  screenshots. The eval counts concrete facts in the overview's script that the material does not contain, and checks
  the origin labels. This is the main risk at levels 2 and 3.
- **`evals/teaching_quality/`.** Rerun when the meaning of a teaching prompt changes, as AGENTS requires.

## 13. Verification list

| ID | Item | How to verify | Needed for | Status |
| --- | --- | --- | --- | --- |
| V-51 | Which providers accept an image with a structured answer: an image input item in the Codex app server, an image input in Claude Code (the prompt goes on stdin today), image content parts on OpenRouter | One probe call per adapter with a screenshot and the description schema | Phase E | open |
| V-52 | How faithfully a description renders a screen: labels, fields, order of steps | The operator reads the descriptions of ten screenshots of one tool against the images | Phase E | open |
| V-53 | Review repairs of an overview script against a series episode, since summing up drives claim drift | Count the repairs per category for the Orlagau overview and compare them with its episodes | Phase C | open |
| V-54 | Calls, duration and plan size of a level-1 overview from a topic with `OVERVIEW_SUB_QUESTIONS` = 10 | Count `calls/*` of the first overview's research and script runs | Phase B | open |
| V-55 | Searches, sources and calls per gap at level 3, and how often a secondary source is wrong | Count per gap; the operator checks a sample of looked-up statements | Phase F | open |
| V-56 | Whether the overview plan with about 400 omitted findings fits the output cap when the model names only what it places | The plan answer's size for Orlagau's 414 findings | Phase B | open |
| V-57 | Whether the transparency wording of levels 2 and 3 meets the AI disclosure that V-43 checks for level 1 | The same check as V-43 | Phase D | open |

## 14. Not in this plan

- **Audiobooks.** One narrator, prose and chapter files. Findings of 2026-10-21 for that plan:
  - the dialogue is fixed in the contract (`Segment.speaker_id: Literal["host_a", "host_b"]`, `models.py:187`; two
    different voices required, `models.py:174-180`);
  - it is fixed in about ten prompts, in `script_advisories.dialogue_shape` and in the dramaturgy's
    `partner_stance`;
  - ElevenLabs records through Text to Dialogue (`elevenlabs_speech.py:42`);
  - an audiobook would be one M4B file with chapter marks.
- **Several productions in one project.** That would touch every path of the Studio, the exports, the downloads and
  the kits. One project holds one series or one overview.
- **Mixing a topic research with imports at level 1** ([§5.2](#52-finished-projects-as-material)).
- **Publishing overviews.** Export stays private ([Source rights and privacy](../SECURITY.md#source-rights-and-privacy)).

## Sources

- Code as of 2026-10-21:
  - brief and storage: `models.py`, `storage.py`, `trial.py`;
  - research: `research.py`, `research_models.py`, `research_quality.py`, `research_reader.py`,
    `question_research.py`, `question_budget.py`;
  - material: `attachments.py`, `provided_works.py`;
  - script: `scripting.py`, `script_pipeline.py`, `script_checks.py`, `script_evidence.py`, `script_models.py`,
    `teaching.py`, `teaching_research.py`;
  - audio and exports: `episode_audio.py`, `content_text.py`, `script_artifacts.py`, `publish_kit.py`, `audio.py`;
  - adapters: `claude_code.py`, `codex.py`, `codex_stream.py`, `openrouter.py`, `elevenlabs_speech.py`;
  - Studio: `studio.py`, `web/app.js`;
  - prompts: `prompts/series_plan.txt`, `prompts/episode_framing.txt`, `prompts/write_episode.txt`,
    `prompts/material_rules.txt`, `prompts/foundation_discovery.txt`
- The Orlagau project `projects/orlagau-und-oberland-899-bis-um-1400-her-72b065`: `models/series_plan.yaml` and
  `models/knowledge_model.yaml`
- [BUSINESS_LOGIC: Episode and series length](../BUSINESS_LOGIC.md#episode-and-series-length)
- [RESEARCH: Series goal, task aims and source types](../RESEARCH.md#series-goal-task-aims-and-source-types)
- [SCRIPTS: Series plan](../SCRIPTS.md#series-plan)
- [STUDIO: Attachments and provided works](../STUDIO.md#attachments-and-provided-works)
- [SECURITY: Trust boundaries](../SECURITY.md#trust-boundaries)
