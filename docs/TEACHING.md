---
title: Teaching
doc_type: business-logic
status: current
last_reviewed: 2026-11-01
covers:
  - src/podcast_automate/teaching.py
  - src/podcast_automate/teaching_research.py
  - src/podcast_automate/narration.py
  - src/podcast_automate/story_agent.py
  - src/podcast_automate/editorial.py
  - src/podcast_automate/cast.py
  - src/podcast_automate/script_advisories.py
  - src/podcast_automate/prompts/
---

# Teaching

Every series follows one editorial standard, and every episode's material is prepared in the script run's `teaching`
stage before the [story agent](SCRIPTS.md#story-agent) writes it. This doc holds both: the standard the reviews and
the research hold an episode to, and what the teaching stage does.

Until 2026-11-01 each episode also got a teaching plan between research and dialogue (learning objectives, a worked
example, a narrative arc, storytelling devices from the catalogs of `dramaturgy.py`, a core story), reviewed against
the sources and repaired, and after writing a listener readback, an editorial review and a teaching review assessed
the dialogue against it. The story agent plans each episode itself, so all of this went with the writing prompt
(why: D-184). Teaching plans of episodes published before stay in their folders (`teaching_plan.md`).

## Editorial standard

### Required depth

Beyond extraction and an argument map, every series must deliver a synthesis: what connects with what and how, what
the statements rest on, how positions differ, and what stays open, regardless of the number of sources.

For each central explanatory question, an episode must show:

- precise terms and the prerequisites needed,
- a comprehensible explanation of how and why, as far as the sources support it,
- at least one thoroughly worked example or case analysis; a qualitative case without numbers counts,
- evidence and the relevant limits, alternatives or uncertainties,
- an answer to the episode question and its contribution to the series question.

The elements must relate to one another in content; merely mentioning them does not pass the
[depth check](QUALITY.md#quality-gates). A counterargument is not invented when the sources carry none; actual limits
or open questions are named instead. The script review checks the required depth and the worked-out explanation
steps ([Evidence review](SCRIPTS.md#evidence-review)); the depth rule it reads is `prompts/teaching_scope.txt`
(`editorial.TEACHING_SCOPE`): university-level depth needs supported mechanisms, reasons and limits, not spoken
equations or exact parameters, and arithmetic only where the brief needs it.

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
why a step helps and where the limits are. A technical term follows the idea, when it helps understanding or
recognition. A few continuous images give orientation; frequent switches between unconnected metaphors make listening
harder.

The tone is clear, adult and at eye level, without oversimplification; missing specialist knowledge is no reason to
explain the obvious at length. Necessary terms are introduced once, briefly, then used as a matter of course.
Repeated definitions, lecturing preambles, announcing every small step, several summaries of the same idea and
multiple look-backs are cut in favour of a natural conversation. A recap of one or two sentences at a chapter end and
a reflection beat after a dense block are not such summaries ([Listenability](#listenability)).

Metaphors are explicitly illustrations that should open up a connection. The explanation names their important limit
once, where it matters, and separates invented everyday situations from evidenced experiments. The dossier stores
them as `illustration` and `illustration_limit`, and the source review checks that the image renders the evidenced
connection correctly; the story agent gets a finding's illustration with it.

### Subject perspectives

Person-centred research distinguishes an evidenced statement of this person, their interpretation, and the findings
of further sources. Technical terms are explained in their respective context.

For health topics, current professional primary sources and guidelines belong in the research. Reference ranges,
decision thresholds and claimed "optimal values" are kept apart in the source model and placed by their origin.
Personal diagnosis or treatment from individual lab results is not a main use case ([Out of scope](PRODUCT.md#out-of-scope)).

### Series goal and example form

The series goal from `project.yaml` (`series_goal`) reaches the series plan, the script review and the series review
([Series goal, task aims and source types](RESEARCH.md#series-goal-task-aims-and-source-types) for the research). If
it weights *understanding* with 2 or 3, an episode first explains an idea or theory in its own logic, attributed to
its originator, then its tests, data and critique ([Role, recap and theory first](SCRIPTS.md#role-recap-and-theory-first));
the series review then also checks `exposition`. If it weights *applying* likewise, the episodes end in what to do in
practice, and the series review checks `guidance`. The story agent's brief does not carry the series goal; it reaches
the agent through the outline the plan made with it.

The worked example may be a qualitative case, such as a situation from the author's work or a historical episode
followed through the theory to what it explains or predicts. A study, measurement or calculation is needed only when
the episode's question demands it.

## Hosts and conversation

The default style profile `de_calm_deep` (`TopicBrief.style_profile_id`) uses German, a calm, thorough tone, medium
speaking pace, little humour and two hosts. Their roles are independent of the TTS voice (`cast.HOST_ROLES`); the
story agent gets them in its brief, the reviews in their payload:

- `host_a` (Host A), the calm, precise expert, develops explanations and mechanisms, supplies relevant details,
  works through examples, connects findings and answers the partner's concrete objection.
- `host_b` (Host B), the curious conversation partner who thinks along, asks about mechanisms, voices obvious doubts,
  tests an assumption, raises justified objections, makes a testable prediction, marks unclear terms and asks about
  significance or consequences. They may draw conclusions themselves, need not feign ignorance, and must not mainly
  supply agreement or cues. A plausible question creates an occasion for the next explanation; a generic „Spannend,
  erzähl mehr“ ("Exciting, tell me more") does not.

A third host adds its own role ([Three hosts](SCRIPTS.md#three-hosts)). Until 2026-11-01 the roles lived in
`polishing.py`; `cast.py` keeps their text byte for byte, since every script run's inputs carry it (`host_roles`).

Rules for the conversation:

- Speaker changes follow the line of thought and need a reason in the content: an objection, an addition, testing a
  guess, a summary in one's own words, or a new perspective. Speakers need not alternate constantly.
- Turns stay short enough to follow by ear: about 80 spoken words, longer only for a step of a worked example that
  needs it; consecutive segments of one host are one turn. The partner speaks about a third of the words. These are
  aims, not quotas. Until 2026-10-06 long monologues were explicitly welcome and there was no target share between
  the hosts; the expert then spoke 70 to 87 % of the words of three finished series.
- Sentence length may vary; short reactions or self-corrections should serve the explanation. Forced ping-pong,
  mechanical alternation, forced interruptions, artificial enthusiasm, echo lines and sprinkled filler words are not
  a quality goal; the agent's `check.py` refuses a turn that opens with only the other host's last words.
- Questions open up the topic; there are no mandatory quiz or answer pauses for the listener.

Intro, outro and the series introduction in episode 1: [Opening and ending](SCRIPTS.md#opening-and-ending).

## Listenability

After three finished series the user found the episodes so dense that one could hardly breathe, „Fakten, Fakten,
Fakten“ (2026-10-06): the expert spoke 70 to 87 % of the words, with 9 to 25 turns over 120 words per episode.
Rewrites the user liked kept the same facts and gave the partner about 35 % of the words, no turn over about 55, the
partner reacting, summing up and asking „is that one anecdote, or has someone measured it?“. Since then one rule
block (`prompts/listenability.txt`, `editorial.LISTENABILITY`) goes to the script review (why: D-141):

- One big idea every chapter serves; the episode's question held open and its answer earned at the end, not
  announced; a recap of one or two sentences or the next question at a chapter end; a signpost where the conversation
  changes direction; a reflection beat after a dense block (what it means, why it matters), often the partner's; the
  turn length and share above.
- **Breathers** (why: D-144). The user, about the first series: „Der stetige Strom von Information flutet meinen
  Kopf“. The rules ask for a breather when new information has piled up, rarely a whole chapter without one: a
  question the hosts stop to think about, worded so a short pause fits; a light moment; or the point said once more in
  the plainest words. The content decides where and how often, never a schedule (the user: „immer organisch bleiben,
  nicht algorithmisch“). The expression layer then sets the pauses there ([Expression tags](AUDIO.md#expression-tags)).
- **Repetition that helps the ear is not padding:** the chapter-end recap, a reflection beat, a callback and a short
  recall where an earlier point is used again. Empty repetition stays forbidden: restating what is already clear in
  the same chapter, several summaries of the same idea, a repeated definition, a recap of a recap.
- **No invented facts.** A recap or reflection restates what the episode said, with the references of the claims it
  restates; the partner asks only what the supplied material answers. Depth, explanation steps, the origin of a
  statement and theory first stay; the evidence rules win.
- **Measured, not guessed.** `script_advisories.dialogue_shape` counts the turns (consecutive segments of one host,
  also across a chapter), the partner's share and every turn over `TURN_WORDS` = 80 words; the script review reads
  it. Two advisories report `long_turns` (more than `LONG_TURN_LIMIT` = 2 turns over `LONG_TURN_WORDS` = 120 words)
  and `low_partner_share` (under `PARTNER_SHARE_FLOOR` = 25 %).

The story agent does not get this block: its task carries its own way of telling (one turn the material holds, a
perspective, host_b's early bet, scenes told as moments; `data/story_agent_task.md`), and the critiques judge
tension, interest and storytelling ([Story agent](SCRIPTS.md#story-agent)).

## Teaching stage

The `teaching` stage prepares each episode in turn (`script_pipeline.ScriptRun.teaching`; why: D-180, D-181):

1. `continuity.json`, the earlier episodes this one builds on ([Context from earlier
   episodes](#context-from-earlier-episodes)), and `source_context.json`, the episode's source passages.
2. The gaps whose corpus hits this episode reads but nobody has read yet go first into the
   [supplementary research](#supplementary-research) ([Gap probe](RESEARCH.md#in-the-script-run)).
3. The [story material](#story-material) of the sections the episode's findings cite.
4. The [story research](#story-research) of a story those sections only point to; when it adds sources, the material
   is collected again from them.
5. The [orientation sheet](#orientation-sheet) and its check.

Everything is saved under `runs/<run_id>/teaching/<episode_id>/`; an episode counts as done when its
`orientation.json` and `story_material.json` exist (`script_checkpoints.finished`). At least three calls per episode:
story material, orientation sheet and its check ([Budgets](BUSINESS_LOGIC.md#script-lower-bound-and-expected-calls)).

### Story material

Findings are claims with evidence and limits, and the research kept the number a criterion asked for and left the
scene beside it in its source section: the listener's verdict on Orlagau (2026-10-21) was that the striking stories
were told in one or two sentences. So one call per episode collects what the episode's cited sections tell as story
(`narration.story_material`, `prompts/story_material.txt`, `story_material.v2-missing`; why: D-180):

- events and who acted, scenes, surprising results, telling phrases, consequences, cases, at most 12 items
  (`StoryItem`: kind, text of at most 60 words told as the source tells it, the section, a verbatim anchor of at most
  25 words, the findings that cite the section, and whether a finding already tells it);
- only from the sections a finding of the episode cites (`narration.allowed_sections`), since every later selection
  of the episode's sources keeps them and the evidence review always sees where an item comes from; no such section,
  no call;
- code drops an item whose section, anchor or findings do not hold; when more than half are dropped, the call is
  asked once more with the rejected items;
- saved per prompt in `story_material.json`; it never stops the run: an empty list leaves the episode without story
  material, and the agent tells from the sections and the corpus.

The story agent gets the items in `material.json`, and the core findings no item tells.

### Story research

The same call names in `missing` at most two stories the sections only point to, such as a chronicle a scholar
summarises or a case named in passing, each with the account to read and the episode's core findings it belongs to
(`narration.MissingStory`). `ScriptRun.research_story` reads them once per episode through the
[supplementary research](#supplementary-research):

- it writes them as the episode's `research_needed.json` with `story: true` and asks
  `teaching_research.research_foundations` with `optional`, where every question may stand as a remaining gap, since
  not finding a story is a result;
- a supplement that adds explanations extends the core findings with the new sections, and the material is collected
  again from them; one that finds nothing, is refused after its corrections or has no supplement left
  (`narration.STORY_RESEARCH_SETTLED`) is recorded, and the episode goes on with the material it has;
- `story_research.json` holds the outcome (`added`, `not_found`, `refused`) and keeps the research to one attempt; a
  stop for quota or budget resumes it;
- an open gap of the episode comes first, and the research uses one of the episode's three supplements.

### Orientation sheet

One call drafts an orientation sheet (`prompts/orientation_sheet.txt`): the people, groups, places, periods,
institutions and terms a listener needs placed, at most 25, from general knowledge rather than the episode's sources.
A second call checks every entry (`prompts/orientation_check.txt`) and must return each exactly once, in order. Only
the entries found correct reach the agent, as background for the listener, never evidence for the episode's claims;
the others are kept under `dropped` in `orientation.json` with their verdict (`story_agent.orientation`). The drafted
sheet is kept in `orientation_draft.json` before its check, so a stop during the check asks only the check again. In
the lab the check caught real errors, such as Burger's data dated 2009 instead of 2006.

### Context from earlier episodes

Each episode's `continuity.json` lists the earlier episodes it builds on, including indirect ones, from the plan
alone (`teaching.prerequisite_context`; why: D-082):

- Only the nearest direct prerequisite, the latest earlier episode this episode names itself, comes in full with its
  outline.
- Every other prerequisite comes as a summary: question, `series_role` and core findings.
- Later episodes are never included.

The script review gets these rows as `prerequisite_context`, and they bind its saved verdicts
(`script_checks.script_review_signature`). What an earlier episode actually said, the story agent reads in its
published text ([What the session gets](SCRIPTS.md#what-the-session-gets)). Until 2026-11-01 the rows also carried
each earlier episode's reviewed teaching design, its learning objectives and the terms it established.

## Supplementary research

The teaching stage researches an episode's open questions on the web within the approved outline
(`teaching_research.research_foundations`): the unread corpus hits of the gaps the episode reads
([Gap probe](RESEARCH.md#in-the-script-run)) and its [story research](#story-research). At most three primary sources
per episode (fewer when `research_limits.sources` is lower), evidenced additions and an independent source review.
Like all research, it searches with the run's web search: the subscriptions' or the API key's own tools, or Perplexity
([Research runs and their web search](BUSINESS_LOGIC.md#research-runs-and-their-web-search)).

- The questions are written to `teaching/<episode_id>/research_needed.json`; routed gaps also as
  `research_needed.md`, readable in the project's language with the sections to read. On success the teaching stage
  continues without another click; the approved series plan is kept.
- Additions lie with their original sources and checksums under `teaching/<episode_id>/supplement/`; the story
  agent's material, the reviews and the show notes use them, also after a resume. A changed operational field
  (`storage.OPERATIONAL_FIELDS`) does not invalidate them (`invalid_supplement`).
- Up to three rounds per episode, for different gaps (`teaching_research.MAX_SUPPLEMENTS`); results are reused after
  an interruption. A fourth stops with `teaching_research_required`.
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
- If a routed gap comes back neither settled nor with new evidence, the run stops with `teaching_research_required`
  instead of running in a paid circle.

## Language and terms

Explanations use the project's language and the established technical terms of its field; where experts in that
language keep an English name, it stays English. The meaning is explained briefly in natural words at first need,
then the term is used consistently.

The rule is topic-neutral (`editorial.terminology`) (why: D-083). Machine-learning names such as Query, Key, Value,
Attention scores, Attention weights and Layer Normalization, with the ban on literal Germanisations such as
Suchanfrage, Schlüssel or skalierte Passung, are added only when the topic or central question names such a topic
(`editorial.MACHINE_LEARNING`) and the project is not English. The rule applies to the plan, the story material, the
supplementary research, the script review and the series review. The story agent's task asks it instead to introduce
every person, place, period and term the first time in one plain clause. Research keeps the earlier rule word for word
(`editorial.TERMINOLOGY`), because its stored answers are bound to the prompt text.

Subject depth demands explained mechanisms and reasons; a full numerical calculation only when the episode's question
needs it.

## Advisories

A non-blocking advisory stage (`script_advisories.py`; storage and display under `advisories` in
[Quality gates](QUALITY.md#quality-gates)) comes from the [quality audit of 2026-09-19](specs/2026-09-19-quality-audit.md)
of the sample series and counts per published episode:

- `redefined_term`: a term an earlier episode established defined again more than once. The established terms came
  from earlier episodes' teaching designs (`Concept.terms`), so an episode written since D-184 has none to count
  against;
- `repeated_hedging`: more than `HEDGING_LIMIT` = 2 reminders that an example is invented or not measured;
- `long_cold_open`: a first segment over `COLD_OPEN_WORDS` = 100 words;
- `over_target_duration`: an estimate over `DURATION_FACTOR` = 1.2 times the planned minutes, at the run's speaking
  rate;
- `long_turns` and `low_partner_share` ([Listenability](#listenability)).

The first two exist only for German and English (why: D-081).

## Scope and adoption

University-level depth demands justified connections, checkable steps and limits; a high density of terms shows it as
little as a long text does. The scope follows from the required steps of the explanation; at a file limit, the split
comes at an answered sub-question.

The new script is first provided in readable form; its recording needs the explicit approval of this text state
([Human approvals](BUSINESS_LOGIC.md#human-approvals)). A recording job already running stays bound to its text: changes
to its stored inputs do not switch it to a new version.
