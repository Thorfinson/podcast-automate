---
title: Teaching design
doc_type: business-logic
status: current
last_reviewed: 2026-10-04
covers:
  - src/podcast_automate/teaching.py
  - src/podcast_automate/teaching_research.py
  - src/podcast_automate/editorial.py
  - src/podcast_automate/script_advisories.py
  - src/podcast_automate/polishing.py
  - src/podcast_automate/prompts/
---

# Teaching design

Every series follows one editorial standard. A source list and a correct dossier do not make an understandable
teaching episode, so every new `pla script` run plans the lessons between research and dialogue, for every topic:
after the series plan come a teaching plan per episode with its own source review, a subject draft, dialogue
polishing with a before/after comparison, and separate source, reading and teaching reviews
([Scripts](SCRIPTS.md)).

## Editorial standard

### Required depth

Beyond extraction and an argument map, every series must deliver a synthesis: what connects with what and how, what
the statements rest on, how positions differ, and what stays open, regardless of the number of sources.

For each central explanatory question, plan and script must show:

- precise terms and the prerequisites needed,
- a comprehensible explanation of how and why, as far as the sources support it,
- at least one thoroughly worked example or case analysis; a qualitative case without numbers counts,
- evidence and the relevant limits, alternatives or uncertainties,
- an answer to the episode question and its contribution to the series question.

The elements must relate to one another in content; merely mentioning them does not pass the
[depth check](QUALITY.md#depth-check). A counterargument is not invented when the sources carry none; actual limits or
open questions are named instead.

Accessibility does not limit subject depth. With a university-level aim, the first episode leads from the initial
problem to the actual mechanism and its further consequences, each section answering a question the previous one
raised. A worked example, justified intermediate steps and counterexamples make the argument traceable. Technical
terms such as gradient or normalisation are allowed once their meaning is explained; a list of definitions or a long
analogy does not replace this development.

### Understandable without prior knowledge

By default, research texts and scripts address curious people without subject or mathematical prior knowledge; depth
comes from comprehensible connections and causes. Formulas, chains of abbreviations, unexplained jargon, source IDs
and stage directions do not belong in the spoken text. Rewriting a formula in words is not enough: the text explains
what a step does, why it helps and where it can fail.

A central explanation starts from a familiar situation or a clear mental image and shows step by step what changes,
why a step helps and where the limits are. The first episode builds its central question without prior knowledge and
leads through a worked example with connected mental images. A technical term follows the idea, when it helps
understanding or recognition. A few continuous images give orientation; frequent switches between unconnected
metaphors make listening harder.

The tone is clear, adult and at eye level, without oversimplification; missing specialist knowledge is no reason to
explain the obvious at length. Necessary terms are introduced once, briefly, then used as a matter of course.
Repeated definitions, lecturing preambles, announcing every small step, several summaries of the same idea and
multiple look-backs are cut in favour of a natural conversation. A good comparison may stand without being explained
again and again or flagged as invented.

Metaphors are explicitly illustrations that should open up a connection. The explanation names their important limit
once, where it matters, and separates invented everyday situations from evidenced experiments. The dossier stores
them as `illustration` and `illustration_limit`, and the source review checks that the image renders the evidenced
connection correctly. In the dialogue, the second voice may ask where someone without prior knowledge needs an
intermediate step.

### Subject perspectives

Person-centred research distinguishes an evidenced statement of this person, their interpretation, and the findings
of further sources. Technical terms are explained in their respective context.

For health topics, current professional primary sources and guidelines belong in the research. Reference ranges,
decision thresholds and claimed "optimal values" are kept apart in the source model and placed by their origin.
Personal diagnosis or treatment from individual lab results is not a main use case ([Out of scope](PRODUCT.md#out-of-scope)).

### Two hosts and storytelling

The default style profile `de_calm_deep` (`TopicBrief.style_profile_id`) uses German, a calm, thorough tone, medium
speaking pace, little humour and two hosts. Their roles are independent of the TTS voice (`polishing.HOST_ROLES`):

- `host_a` (Host A), the calm, precise expert, develops explanations and mechanisms, supplies relevant details,
  works through examples, connects findings and answers the partner's concrete objection.
- `host_b` (Host B), the curious conversation partner who thinks along, asks about mechanisms, voices obvious doubts,
  tests an assumption, raises justified objections, makes a testable prediction, marks unclear terms and asks about
  significance or consequences. They may draw conclusions themselves, need not feign ignorance, and must not mainly
  supply agreement or cues. A plausible question creates an occasion for the next explanation; a generic „Spannend,
  erzähl mehr“ ("Exciting, tell me more") does not.

Rules for the conversation:

- Speaker changes follow the line of thought and need a reason in the content: an objection, an addition, testing a
  guess, or a new perspective. Speakers need neither alternate constantly nor speak equally much.
- Longer monologues that develop a thought coherently are explicitly allowed. There is no fixed word count or
  duration (30–90 seconds) per turn, and a technical cut into several audio files needs no speaker change.
- Sentence length may vary; short reactions or self-corrections should serve the explanation. Forced ping-pong,
  mechanical alternation, forced interruptions, artificial enthusiasm and sprinkled filler words are not a quality
  goal.
- Questions open up the topic; there are no mandatory quiz or answer pauses for the listener.

An episode leads from a concrete question or case through context, explanation, evidence, complication and deepening
to a synthesis. The opening names the episode question within the first 90 seconds, and the ending takes it up again.
A foundations episode may have a different arc than a controversy episode; the dramaturgy should carry the content.
Intro, outro and series framing: [Writing and framing](SCRIPTS.md#writing-and-framing).

## What the application enforces

1. The research also searches for the foundations the chosen audience needs before the subject sources.
2. Source selection also uses the context around quoted passages and the stored full texts, so a short dossier
   excerpt does not create an artificial evidence gap. The `teaching` stage develops per episode learning objectives
   with check questions, concept dependencies, scene transitions, a worked example and a justified synthesis. It must
   teach the episode's core findings (`finding_ids`) and draws on supporting findings (`supporting_finding_ids`) only
   where the explanation gains ([Core and supporting findings](SCRIPTS.md#series-plan)). A further model call reviews
   this draft against the sources.
3. Missing necessary evidence creates `runs/<run_id>/teaching/<episode_id>/research_needed.md` and starts targeted
   [supplementary research](#supplementary-research-for-the-teaching-plan) automatically.
4. The `polishing` stage revises the draft for spoken language and the roles of expert and conversation partner; its
   evidenced before/after comparison keeps invented knowledge or lost explanation steps from passing as linguistic
   improvement ([Dialogue polishing](SCRIPTS.md#dialogue-polishing)).
5. After the source review, a fresh model call answers the learning questions from the revised dialogue, without
   model answers, dossier, teaching plan or series goal. A separate editorial review sees target group, prior
   knowledge, required depth, series goal (`series_goal`, if set), the spoken text and the approved series metadata
   (overall topic, order, planned outlook) to place the series introduction and the transition to the next episode;
   teaching plan, model answers and earlier verdicts stay hidden, and series metadata support no subject statements.
   It checks in particular the actual opening, the necessary transitions and the speakers' substantive reactions to
   each other, and its negative verdict blocks even when the other models judge positively.
6. The teaching review checks opening, structure, example, synthesis, dialogue, depth and listening comprehension
   (`teaching.CRITERIA`: `orientation`, `progression`, `worked_example`, `synthesis`, `dialogue`, `depth`,
   `spoken_clarity`) and every learning objective. A passed criterion needs verbatim evidence from existing speaker
   segments. Every gap the reader reported is classified explicitly as required or outside the learning objective,
   with a reason. Missing criteria, invented evidence, skipped gaps and missing required explanations allow no export.

### Series framing in the teaching plan

The teaching plan prepares the series framing ([Writing and framing](SCRIPTS.md#writing-and-framing)): episode 1
introduces the overall topic, its significance and the path of topics that build on each other.

- The final episode is the synthesis of the series ([Series plan](SCRIPTS.md#series-plan)), not a recap in the
  closing scene: its teaching plan designs the scenes as steps of a reasoned answer to the shared opening question,
  assembled from the earlier episodes' contributions (their `series_role` in the series context, and the predecessor
  context) and including the remaining limits; only the last scene closes the answer. Draft, editorial and teaching
  review demand this; a recap only in the closing paragraph fails the teaching review.
- Every episode has a spoken opening with a greeting and a clear ending with a goodbye, prepared in the first and last
  existing chapter and checked again on the actual text.
- The overall recap needs assigned evidence (`recap_finding_ids` in the plan). Missing greetings are editorial tasks
  and trigger no web research.

### Series goal and example form

The series goal from `project.yaml` (`series_goal`) reaches the teaching plan, draft review, editorial review and
teaching review (why: D-079).

- If it weights *understanding* with 2 or 3, the teaching plan first explains an idea or theory in its own logic,
  attributed to its originator and supported by findings from their own work, and gives this the larger share of
  scenes and learning objectives; tests, data and critique follow. A draft whose main work is studies and data is
  then an objection of the draft review, and such an episode fails `depth` in the editorial and teaching review.
- If it weights *applying* likewise, the closing scenes plan what to do in practice.
- The worked example may be a qualitative case, such as a situation from the author's work or a historical episode
  followed through the theory to what it explains or predicts. A study, measurement or calculation is needed only when
  the learning objective demands it.
- A misconception with its correction, and a limit of the example, are optional (`WorkedExample.misconception`,
  `correction`, `limits`) (why: D-080): misconception and correction come together or not at all, and no review
  demands them where the episode does not need them. Saved teaching plans with these fields read as before.

### Supplementary research for the teaching plan

The supplementary research uses web search: at most three primary sources per episode (fewer when
`research_limits.sources` is lower), evidenced additions and an independent source review. Like all research, it
runs on the subscriptions ([Text providers and model selection](BUSINESS_LOGIC.md#text-providers-and-model-selection)).

- On success, the teaching plan continues without another click; the approved series plan is kept.
- Additions lie with their original sources and checksums under `teaching/<episode_id>/supplement/`; writing, reviews
  and source notes use them, also after a resume. A changed operational field (`storage.OPERATIONAL_FIELDS`) does not
  invalidate them (`invalid_supplement`).
- Up to three rounds per episode, for different gaps (`teaching_research.MAX_SUPPLEMENTS`); results are reused after
  an interruption.
- **Word limits per source.** On top of the dossier's limits (25 quoted and 150 paraphrased words), an addition may
  add 10 quoted and 40 paraphrased words per source (`teaching_research.QUOTED_WORDS`,
  `teaching_research.PARAPHRASED_WORDS`), counting the findings that already cite the source, so even a source the
  dossier exhausted can carry a short correcting sentence. With an assembled dossier, its findings do not count, so
  the addition's own words have the whole 35 and 190 (`teaching_research.counted_findings`). The addition sees under
  `source_budget` what each source can still carry.
- **Quotes** follow the dossier's rule: end-of-line hyphenation, ligatures and typographic quotation marks from text
  extraction do not invalidate a verbatim quote.
- **Rejected additions.** If the review rejects an addition (for example a question with neither an answer nor a gap
  note, or a source too long), the run asks again up to twice with the rejected answer and the named defects
  (`evidence_rejected_NN.json`). Once these attempts are used up, the run stops, and resuming stops at the same place.
- **What blocks.** The independent review blocks only for a critical error of an explanation: a factual or mechanism
  error, a claim beyond the cited passages (including an omitted qualification of the source), or a contradiction to
  a supplied passage; each names its basis (`issue_basis`). Everything else (wording, a term to explain, the form of
  an attribution, an unused section) is a non-blocking advisory under `advisories` in the addition's `review.json`.
- **Corrections.** A critical point goes back to the addition as a correction (`review_rejected_NN.json`), and the
  next review sees what the previous one objected to. Corrections the review demands and corrections of formal errors
  such as the word limit each have their own two attempts (`teaching_research.MAX_SUPPLEMENT_REJECTIONS`). The review
  sees the same word limit (`source_budget`); for an exhausted source it demands no further quote but that the
  contradicting statement be deleted or narrowed.
- **Fresh attempts.** Once its corrections are used up, **„Mit neuen Anläufen fortsetzen“** (resume with fresh
  attempts) on the hold card, or `pla approve <project> --fresh-attempts`, gives it new ones. The spent corrections
  move unchanged to `<kind>_superseded_RR_NN.json`, and the points their reviews objected to stay known to the next
  review as `previous_issues`.
- If the same question, already handled, comes back without new evidence, the run stops instead of running in a paid
  circle. If a real evidence gap stays open or a different content scope would be needed, it stops with the concrete
  questions.

### Revisions and stops

- On criticism, the teaching plan is revised automatically up to twice. If explanation steps stay open, a focused
  correction round follows without another click: each point of criticism is answered with concrete passages from the
  revised plan, then a fresh call reviews the whole plan independently; the correction list is no proof of passing.
  Plans that stopped after two attempts can also use this round on resume.
- If points stay open after the focused correction, a final step at the role A3 makes one more focused correction and
  one more review ([Roles of review calls](BUSINESS_LOGIC.md#roles-of-review-calls)) (why: D-122). A correction that
  fails the plan check or does not answer every point is discarded and the plan before it stays; only then does the
  run stop with `teaching_design_failed`. Without quota the A3 call pauses the run; with no usable subscription for it
  the run stops right away, and a resume tries again. The checkpoint keeps the step (`a3`).
- The limits and the finished corrections, comparisons and reading reviews are kept on resume.
- With persisting defects, or an exhausted budget or quota, the results stay saved; merely resuming does not reset
  the attempt limits.

### Scoped review of the teaching plan

So that the review converges, it has a fixed scope per episode (`teaching/<episode>/review_scope.json`): every point
earlier reviews of this episode named, also across redesigns. From the second review on, a point blocks only if

- it is an earlier point still unresolved,
- the draft ignores the editor's hint ([Redesign with a hint](#redesign-with-a-hint)), or
- it is a new critical error: a factual or mechanism error, a claim beyond its evidence, a contradiction to a supplied
  source, or a missing step without which a learning objective cannot be reached (`teaching.CRITICAL_BASIS`).

Each such point names its basis (`issue_basis`); a verbatim repeat of an earlier point needs none. Code, not the
review, decides which points block (`teaching.scoped_review`) (why: D-068): only a point that contains the text of an
earlier point, ignoring case, whitespace and trailing punctuation, counts as earlier, and a point with the basis
`previous` without such text, or `editor_note` without an editor's hint, becomes an advisory. Everything else new
(pace, the load of a scene, wording, a term the dialogue should still introduce) is an advisory under `advisories` in
`review.json`: it does not block and goes to the writing of the episode, which applies it where it can.

### Redesign with a hint

If an episode's teaching plan keeps open points after the focused correction round and the final step
(`teaching_design_failed`), the hold card offers **„Lehrkonzept mit Hinweis neu entwerfen“** (redesign the teaching
plan with a hint); on the command line `pla approve <project> --redesign-teaching <episode> --hint "…"`.

- The hint, for example „Keine Abfragesprache wörtlich vorlesen, Abkürzungen beim ersten Mal ausschreiben“ ("Do not
  read query language out verbatim; spell out abbreviations the first time"), is bound to the run as a request
  (`teaching_redesigns.json`).
- On the next resume, the run moves the stopped draft with its review and focused correction to
  `teaching/<episode>/redesign_NN/` and designs this episode's teaching plan anew, with the hint in draft, revisions
  and review, and with new correction rounds.
- The approved table of contents and the other episodes stay.
- Each request is taken over once (`redesign.json`); if the new draft stops too, a new request is needed.

### Reports and evals

`episodes/<episode_id>/teaching_plan.md` makes the teaching plan readable. The detailed report in
`reports/script_quality.yaml` holds the reader answers, the evidenced individual verdicts and
`human_learning_validated: false`: simulated reading comprehension is not a measurement with real listeners.

The [regression tests with real model calls](../evals/teaching_quality/README.md) use the production review
functions, and the expected verdicts are not told to the reviewers. They complement the automated tests on missing
foundations, wrong quotes, resume and audio approval ([Evals](QUALITY.md#evals)).

### Rules from the 2026-09-19 quality audit

Three teaching-lane rules come from the [quality audit of 2026-09-19](specs/2026-09-19-quality-audit.md) of the sample
series ([what was built](specs/2026-09-19-quality-audit-plan.md#status)):

- `Concept.terms` holds the spoken names of a teaching plan's concepts. `teaching.prerequisite_context` passes them as
  `established_terms` per reviewed predecessor episode to teaching plan, writing, polishing and its comparison, so
  later episodes do not define them again.
- The polishing comparison names the most demanding passages (`demanding_passages`) and how the new version resolves
  each unclear reference ([Dialogue polishing](SCRIPTS.md#dialogue-polishing)).
- A non-blocking advisory stage (`script_advisories.py`; storage and display under `advisories` in
  [Quality gates](QUALITY.md#quality-gates)) counts `redefined_term` (a term an earlier episode established defined
  again more than once), `repeated_hedging` (more than `HEDGING_LIMIT` = 2 reminders that an example is invented or
  not measured), `long_cold_open` (a first segment over `COLD_OPEN_WORDS` = 100 words) and `over_target_duration` (an
  estimate over `DURATION_FACTOR` = 1.2 times the planned minutes). The first two exist only for German and English
  (why: D-081).

## From learning goal to research

First decide what the listener should be able to explain, predict, compare or decide in a new case after the episode,
then capture the prerequisites needed; only from these follow the questions to the literature. The
[Eberly Center](https://www.cmu.edu/teaching/designteach/design/learningobjectives.html) describes this alignment of
objectives, tasks and teaching.

A source passage can fit the subject and still be too advanced didactically, so also ask which terms, representations
or earlier lessons it presupposes; those the listener does not bring yet are research tasks.

The review distinguishes missing external evidence from a missing editorial connection (`kind: evidence` and
`kind: editorial_context` of a reported gap). The wording of one's own example cannot be researched on the web. A
marking or illustrative representation the next explanation needs may be added within the same example; invented
token boundaries must not appear as the output of a real tokenizer. Such tasks go into the automatic revision; only
missing scientific evidence goes into supplementary research. Earlier teaching plans serve the coherence of the series
and replace no scientific sources.

### Context from earlier episodes

Each episode's teaching plan gets the state of its prerequisite episodes from the same run, including indirect ones
(why: D-082).

- Only the nearest direct prerequisite, the latest earlier episode this episode names itself, comes in full: outline,
  reviewed terms, learning objectives and worked example.
- Every other prerequisite comes as a summary: question, `series_role`, core findings and, once reviewed, objective
  and spoken terms (`teaching.prerequisite_context`).
- A run that recorded the context in full keeps this form, so its saved reviews stay valid.
- The handover is stored per episode in `teaching/<episode_id>/continuity.json` and also reaches the script writer,
  polishing and the source review.
- Unreviewed drafts and later episodes are not taken over as established content; if only an earlier outline point
  exists, this is marked explicitly.
- Changed predecessor context requires a new review of the dependent teaching plan.

## One teaching block per step of thought

For each block, answer in writing:

1. Which question is open after the course so far?
2. What can the listener already explain at this point?
3. Which concrete example or comparison makes the new difficulty visible?
4. Which steps explain the mechanism, and why does each of them help?
5. Which understandable but wrong conclusion could arise, if one is likely?
6. Which new conclusion follows from this block and earlier ones?
7. Which sources support the statements, and where does one's own construction begin?

The blocks are reviewed before they are written out. A missing connection is closed by an explanation or additional
research; a transition such as „Damit kommen wir zu …“ ("This brings us to …") alone does not do this.

## Three editorial reviews

| Perspective | Review question | Expected evidence in the draft |
| --- | --- | --- |
| Narration | Why does the listener want the next answer right now? | The preceding attempt at a solution has made a concrete new difficulty visible. |
| Subject teaching | Which additional thinking does this section enable? | A justified prediction, calculation, distinction or transfer. |
| Learning support | Where could a newcomer lose the thread or form a wrong rule? | A prepared prerequisite, an explained transition or a targeted counter-case. |

These roles name review perspectives; they claim no involvement of external experts. A model review must name
concrete passages and missing steps; the absence of formal errors does not prove these reviews passed.

## Language and terms

Explanations use the project's language and the established technical terms of its field; where experts in that
language keep an English name, it stays English. The meaning is explained briefly in natural words at first need,
then the term is used consistently.

The rule is topic-neutral (`editorial.terminology`) (why: D-083). Machine-learning names such as Query, Key, Value,
Attention scores, Attention weights and Layer Normalization, with the ban on literal Germanisations such as
Suchanfrage, Schlüssel or skalierte Passung, are added only when the topic or central question names such a topic
(`editorial.MACHINE_LEARNING`) and the project is not English. The rule applies to the plan, the teaching plans, their
supplementary research, writing, dialogue polishing and every review of the script lane. Research keeps the earlier
rule word for word (`editorial.TERMINOLOGY`), because its stored answers are bound to the prompt text.

Subject depth demands explained mechanisms and reasons; a full numerical calculation only when the learning objective
needs it.

## Examples, dialogue and synthesis

A recurring main example creates a stable reference. A second example gets a specific task: to show a limit, or to
test whether the idea transfers. New examples must not silently swap the task, the variable quantities and the
learning objective at the same time.

Worked examples are combined with short opportunities to explain things oneself; the
[IES practice guide](https://ies.ed.gov/ncee/wwc/PracticeGuide/1) recommends, among other things, explanatory
questions and alternating between a demonstrated solution and an independent task. In the podcast these can become a
serious objection and a short pause for thought; this adaptation still has to prove itself in the reading and
listening review (see V-24).

A synthesis names its starting points and leads visibly to an additional insight; repeating the chapter headings is
not enough. Source finding, own derivation, constructed example and open conjecture are kept apart in the editorial
notes. In the spoken text, limits appear where they actually change understanding.

## Scope and adoption

University-level depth demands justified connections, checkable steps and limits; a high density of terms shows it as
little as a long text does. The scope follows from the required learning steps; at a file limit, the split comes at
an answered sub-question.

The new script is first provided in readable form; its recording needs the explicit approval of this text state
([Human approvals](BUSINESS_LOGIC.md#human-approvals)). A recording job already running stays bound to its text: changes
to its stored inputs do not switch it to a new version.

## Existing projects

- Completed research runs can serve as the source of a new script run.
- Earlier script runs do not get newer quality reviews entered as passed. After a version change they need a new
  `pla script` run; an old `resume` reports changed script inputs. In particular, `resume` turns neither script runs
  from before the `teaching` stage into teaching-reviewed ones nor older runs into polished ones after the polishing
  version change.
- `--revise` keeps the series plan and creates teaching plan and text reviews anew; see
  [Revising a script](SCRIPTS.md#revising-a-script).
