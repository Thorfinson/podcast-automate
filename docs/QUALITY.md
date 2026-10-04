---
title: Quality
doc_type: quality
status: current
last_reviewed: 2026-10-04
covers:
  - src/podcast_automate/script_checks.py
  - src/podcast_automate/script_advisories.py
  - src/podcast_automate/series_review.py
  - scripts/evaluate-teaching.py
  - evals/
---

# Quality

How the application checks a series before and after recording, by which criteria a series is assessed, and how it is
accepted. The definition of done and the state of each acceptance item are in the
[MVP acceptance plan](specs/2026-09-10-mvp-acceptance-plan.md).

## Quality gates

The gate names are this document's labels, not code identifiers; the checks live in `script_checks.py` (outlines and
scripts), `series_review.py` and `script_advisories.py`.

| Gate | Blocking | Rule |
| --- | --- | --- |
| `schema_check` | Yes | Artefacts and all referenced IDs are valid. |
| `source_check` | Yes | Subject-matter statements in the script refer to matching knowledge-model entries. |
| `evidence_check` | Yes | Claims and subject-matter definitions can be traced back to sources that were actually read. |
| `factual_review` | Yes, on a finding | The evidence carries the statement; new or overstated claims go back to research. |
| `research_coverage_check` | Yes, on material gaps | Sub-questions and foundations are covered, or marked with justified consequences for the scope. |
| `depth_check` | Yes | Central questions are answered substantially through explanation steps, worked examples, evidence and limits. |
| `series_planning_check` | Yes | Questions, claims and prerequisites are assigned to episodes; deferred core content is not lost. |
| `series_script_check` | Yes, for new complete script runs | The final texts of all planned episodes are reviewed together for coverage, prerequisites, progression, deferred core questions, synthesis and arc, and, depending on the series goal, also for exposition and guidance (`series_review.CRITERIA`, `GOAL_CRITERIA`); older reports with five criteria stay valid. A finding that only describes a limitation of the sources (`source_limit`) becomes an advisory instead of a blocker. Partial jobs and older runs get no retroactive overall approval. How the review runs: [Series review](SCRIPTS.md#series-review). |
| `continuity_check` | Yes, on a break in understanding | Order and transitions work; terms are explained before they are needed. |
| `redundancy_check` | Warning | Unnecessary repetition within and between episodes is no substitute for going deeper. |
| `duration_check` | Yes | Planned and estimated running time stay within the maximum per episode, and every audio part stays within the maximum per part, measured before export ([limits](BUSINESS_LOGIC.md#episode-and-series-length)). |
| `rights_check` | Planned gate | Individual rights states and export blocks are not implemented yet. Currently only private use and the deterministic quote limits apply; see [Source rights and privacy](SECURITY.md#source-rights-and-privacy). |
| `audio_readiness_check` | Yes, before rendering | Speakers, spoken text, pauses and chapters are unambiguous. |
| `audio_output_check` | Yes, before the final audio export | All segments are present and technically valid; assembly, measured duration and chapters match. |
| `advisories` | No | Non-blocking review notes beside the gates: terms defined again, repeated reminders that an example is invented, a long cold open and a duration above the target (`script_advisories.py` with German and English patterns; other languages get only the cold-open and duration notes); series-review findings that only describe a source limitation or that, in the re-check of a repair round, newly concern an unchanged episode; and, in the research, findings from only one research group (`single_group_findings`). They are stored in `reports/script_quality.yaml` under `episodes.<ep>.advisories` and `series_review.advisories`, or in the research report, and the Studio shows them under **„Hinweise der Prüfungen“** (review notes, [on the reading page](STUDIO.md#review-notes-on-the-reading-page)). Nothing evaluates them automatically. |

ID and schema checks are deterministic. Depth of content, fit of the evidence and naturalness need an editorial
assessment; a source ID does not prove that a statement is factually correct. The report keeps automatic checks, model
assessments and human findings apart.

A report stores the hashes of the reviewed versions of sources, knowledge model, plan and script. A change to any of
these inputs invalidates the affected quality approvals (see
[Runs, resume and input binding](BUSINESS_LOGIC.md#runs-resume-and-input-binding)).

Blocking pre-checks prevent audio rendering. Findings on the generated audio allow targeted repair attempts but
prevent the final export. Internal artefacts and error reports stay available for correction. A report about a single
episode must never count as a review of the whole series.

## Assessment criteria

The prioritised main case is a source-bound podcast series whose scope follows from the topic and the desired depth.
Total duration and number of episodes are not fixed; the limits per episode and per recorded part are under
[Episode and series length](BUSINESS_LOGIC.md#episode-and-series-length). The assessment covers research, depth of
explanation, structure across several episodes and listening quality.

### State of the assessment

Version 0.1 covers the whole path from real topic research to the automatic assembly and MP3 export of approved
episodes (see [What it is for](PRODUCT.md#what-it-is-for)). The tests check simulated model responses and real FFmpeg
processing of test signals.

A complete series of six episodes with exported MP3s exists („Die Entwicklung der Transformer-Architektur“, produced
from 13 to 16 September 2026). The audit of 19 September 2026 assessed it: the individual verdicts are in the
[quality audit](specs/2026-09-19-quality-audit.md), the changes made from it in the
[status of its implementation plan](specs/2026-09-19-quality-audit-plan.md#status). Source and structure checks still
do not prove sufficient depth of explanation; that is what the editorial review against the desired standard is for.

The human listening acceptance of this series is outstanding (see V-1); every audio report carries
`human_listening_reviewed: false` until the review is recorded in the Studio.

Tutor pedagogy, quizzes, diagnosis of learning progress and repetition planning are outside this assessment (see
[Out of scope](PRODUCT.md#out-of-scope)). Understandable explanations and well-built foundations remain decisive.

### Criteria

| Area | What good quality looks like | Insufficient result |
| --- | --- | --- |
| Topic coverage | Prioritised questions have justified answers or visible limits. | A long script leaves out central parts of the guiding question. |
| Research | Concrete source sections carry the statements; origin and counter-positions are put in context. | Search snippets, source lists or well-known names replace checking the statements. |
| Depth of explanation | Terms, prerequisites and explanation steps lead to a connection the listener can follow. | Several definitions are given without explaining the how and the why. |
| Understandable without prior knowledge | Familiar images and small explanation steps make the idea understandable; the necessary terms follow afterwards. | Technical or mathematical language assumes knowledge that was never explained. |
| Mental images | A few connected metaphors explain a mechanism and name where the comparison ends. | An image replaces the explanation, misleads, or is presented as an actual research finding. |
| Examples | One concrete example is worked through step by step and tied to the explanation. | Examples stay short keywords or decorative anecdotes. |
| Evidence and limits | Findings, interpretation, hypotheses and uncertainty are kept apart. | A single perspective is presented as the settled overall state. |
| Series structure | Episodes answer different questions and build on foundations already introduced. | Every episode starts again with the same overview. |
| Series scope | The number of episodes covers the need for explanation and grows with additional content requirements. | Core content is cut because of a flat limit on total time or number of episodes. |
| Coherence | Deferred questions are taken up later; the last episode connects the results. | Episodes stand side by side or lose central open questions. |
| Dialogue | The hosts' follow-up questions lead to precision, derivation, criticism or a deeper example. | The speakers only alternate between short claims and agreement. |
| Listenability | Pace, pronunciation, pauses and chapters support the explanation. | A formally correct script is hard to follow when spoken. |
| Automatic production | Two stable voices, fitting transitions, loudness and finished files come about without manual editing. | The user has to sort, join or repair audio snippets in an editor. |
| Reliability | Subscription pauses and technical errors can be resumed with the results kept. | An interruption forces the series to be recomputed, or an unrequested switch to an API. |
| Running time | The duration planned from the content and the actually measured duration are shown; every episode and every recorded part stays within its limit. | A fixed total number of hours counts as proof of quality, or word count is equated with depth. |

## Depth check

For every central explanatory question, the editors check as a whole:

1. Are the terms needed clear before they are used decisively?
2. Is it explained how and why a connection comes about, as far as the sources allow?
3. Does a worked example contribute to understanding?
4. Are prerequisites, limits and relevant other interpretations dealt with?
5. Is the answer to the episode question substantially richer at the end than at the beginning?

The mere presence of the five elements is not enough. The assessment names concrete successful or missing script
passages and refers to the associated sources or knowledge-model entries. An open subject-matter question may stay open
if the limits of the available explanation are clear.

Depth is not a simple count. Source IDs and word counts support the check but do not replace an assessment of the line
of thought and of how well the evidence fits.

## Across episodes

Before a series is accepted, these points are also checked:

- **Coverage:** every prioritised sub-question and every central claim has a justified place.
- **Prerequisites:** an episode assumes only knowledge that was already explained or explicitly stated.
- **Progression:** every episode extends understanding and does not consist mainly of repetition.
- **Recaps:** repetitions help listeners connect and do not crowd out new explanations.
- **Open questions:** deferred content is dealt with later or taken out of the scope with a reason.
- **Synthesis:** the last episode answers the overarching guiding question from the results built up before.

A good single script is not enough proof of a good series. The model-run part of this check is the
[series review](SCRIPTS.md#series-review) behind the `series_script_check` gate.

## Subject-matter pilots

The user's examples provide two possible pilots:

- **Energy-based models in machine learning:** checks whether terms, prerequisites, explanation steps and concrete
  examples carry a longer series. Statements about Yann LeCun and Alfredo Canziani must be attributed to concrete works.
- **Blood values:** checks whether foundations and different kinds of subject-matter statements are kept apart in a
  traceable way, and whether person-centred starting sources are placed in a broader body of sources. An unclarified
  reference to a person is never silently attributed to a person.

These pilots are research assignments, not checked subject-matter results; concrete sources must be researched for
them first. After an early technical audio test, one central explanatory episode of the first topic is the
subject-matter quality benchmark.

## Automated checks and early audio test

Before the full research pipeline was built, a German Qwen voice sample was generated and assembled automatically on
Windows 11 with the Radeon RX 9070 XT. It contains both voices, longer explanatory passages, speaker changes, technical
terms, numbers and units. The technical execution is proven; the editorial assessment of its suitability is outstanding
(see V-2).

The test documents model and runtime versions, memory use, generation time, intelligibility, voice consistency and
pronunciation. A short voice sample serves the initial choice of model and voices; it is not a recurring manual
editing step.

In the production run, missing or damaged segments, empty or unexpected output and the measured duration are checked
automatically; Gemini takes also pass a plausibility check that Qwen takes do not. Remaining findings block the
affected final export with a concrete error message. Chapters and running time are checked against the audio data
actually assembled. Loudness and peak level of the finished MP3 are measured and recorded in the audio report but not
assessed as a gate. Checks and error codes: [Recording flow](AUDIO.md#recording-flow) and
[Take checks, pauses and loudness](AUDIO.md#take-checks-pauses-and-loudness); automated audio tests:
[Verification status](AUDIO.md#verification-status).

A supplementary local back-transcription is assessed against known omissions and repetitions and must never count on
its own as proof of correct pronunciation (its state: [Back-transcription](AUDIO.md#back-transcription)). Automatic
error detection cannot guarantee error-free or consistently natural speech output; the listening check of the pilot
remains an acceptance criterion of its own.

## Acceptance procedure

1. Create an evidenced dossier from actual research and review its core content and gaps editorially.
2. Review an overall plan and one central episode before the complete series is produced.
3. Correct the gaps found in the knowledge model, the plan and the script.
4. Review the complete script series against sources, coverage and coherence.
5. After approval, generate and assemble the audio automatically; check pronunciation, actual running times and how
   well the episodes can be followed by ear.
6. Assess the complete pilot series and document the remaining defects concretely.
7. Deliberately try out an interruption, a subscription pause and a defective segment; finished results must be kept.

This editorial acceptance assesses the MVP; the normal production run needs no manual editing of each episode.
Blocking findings must be fixed before the final export ([Quality gates](#quality-gates)). Step status:
[MVP acceptance plan](specs/2026-09-10-mvp-acceptance-plan.md).

## Evals

The fixture projects originally planned for evaluation (`fixtures/simple_topic`, `fixtures/mechanism_series` and
`fixtures/conflicting_perspectives`) were never created. The evaluation basis instead is:

1. the fixture modules of the test suite under `tests/*_fixtures.py` (research, questions, teaching plan, script,
   polishing, series) with simulated model responses that run through the complete flow including resume, budget and
   approvals; which suite to run when is in [AGENTS.md](../AGENTS.md);
2. the cases under `evals/` (list below) with frozen corpora, expected verdicts, deliberately faulty cases and clean
   controls, which run by hand, partly with real model calls and partly as offline replay;
3. the sample series of September 2026 and the rejected pilot (`evals/teaching_quality/pilot_rejected.json`) as
   regression cases.

The product's main cases ([Subject-matter pilots](#subject-matter-pilots)) are candidates for later pilots.

The cases under `evals/`, each with a README on how to run it:

- [`dialogue_polishing/`](../evals/dialogue_polishing/README.md): whether the production dialogue comparison keeps
  meaning, completeness, speaker roles and spoken language; one subscription call per case with `--live`.
- [`jev_decisions/`](../evals/jev_decisions/README.md): whether Jev (`typesafe/jev-1.13` on OpenRouter) can take over
  two narrow yes/no checks (`claim`, `section`), labelled by the pipeline's own verdicts, not by humans; paid
  OpenRouter requests, capped by `--max-usd`.
- [`research_evidence/`](../evals/research_evidence/README.md): an offline contract check of the evidence gate on 14
  fixed, fictional cases (eight with seeded defects, six clean controls), labelled by the implementation agent (see
  V-5).
- [`research_refinement/`](../evals/research_refinement/README.md): targeted research and resume, through regression
  tests of the regular suite and a read-only check of a saved source set; no model calls.
- [`teaching_quality/`](../evals/teaching_quality/README.md): teaching-quality controls through `assess_teaching`,
  run by `scripts/evaluate-teaching.py`; subscription calls with `--live`.

No real-model run of the teaching, dialogue-polishing and research-evidence evals is recorded after the prompt changes
of 19 September 2026 (see V-3).
