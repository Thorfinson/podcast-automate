---
title: Review-Loop Decision Gates and Authority Routing Plan
doc_type: plan
status: active
date: 2026-10-04
---

# Review-Loop Decision Gates and Authority Routing Plan

Goal: fewer review and repair calls per podcast, without weakening evidence, teaching or publication gates. Scope:
the review loops of dialogue polishing, the reader, editorial and teaching reviews, teaching design and the script
review. Series review and research are named where they touch the plan, but this plan does not change them
([Not in this plan](#21-not-in-this-plan)).

Written for the operator and the agents who implement it. Every code reference was checked against the working tree
on 4 October 2026. Rules that already hold are owned by [BUSINESS_LOGIC](../BUSINESS_LOGIC.md) and
[SCRIPTS](../SCRIPTS.md); this plan links to them and states only what would change.

## Status

**Planned; nothing implemented.** Step 0 (the call baseline, V-10) comes first and may change the scope of the plan.

The operator decided on 4 October 2026:

1. **A dismissable point gets one repair round** and then passes as a note (G-cap, [§7.2](#72-gate-variants)). Today
   such points pass only once all repairs are spent: three rounds in the script review, two in polishing.
   (why: D-TODO(review-gate-one-round))
2. **The final adjudicator (A3) may only repair or stop.** It never closes a point that blocks today without asking
   the operator ([§9.2](#92-a3-terminal-step)). (why: D-TODO(a3-repair-or-stop))
3. **Script text and source passages may go to TypeSafe** for the Jev gate
   ([§7.7](#77-data-and-decision-record)). When the gate is on but the OpenRouter key is missing, the Studio says so
   and offers to add it ([§7.8](#78-missing-key-in-the-studio)). (why: D-TODO(jev-gate-data-flow))
4. **A gate may miss at most 10 % of real blockers**, measured as the 95 % upper bound
   ([§13.2](#132-labels-and-sample-size)). The eval therefore needs at least 30 labelled blockers per activated stage.
   (why: D-TODO(gate-miss-rate-10-percent))

## 1. Problem

Strong reviewers are critical by design. When asked again after a repair, they often find a new point, and each
point costs a repair and another review:

```text
review -> issue -> repair -> review -> new issue -> repair -> ...
```

The operator reports roughly a thousand model calls for one podcast. No run has been counted by stage and role yet
(V-10), so how much of that comes from review loops rather than from research is unknown.

### 1.1 What already holds

The loops are already bounded and scoped in code. This plan builds on these rules and must not weaken them.

| Stage | Bound | Scope after a repair | Blocks wherever it points | When the repairs are spent |
| --- | --- | --- | --- | --- |
| Script review | `script_pipeline.MAX_REVIEW_REPAIRS = 3` | `follow_up_scope`: previous points and changed segments ([SCRIPTS](../SCRIPTS.md#scoped-follow-up-review)) | basis in `script_pipeline.CRITICAL_BASIS` (`factual_error`, `source_contradiction`) | points in `NOTED_CATEGORIES` (`clarity`, `depth`, `dialogue`) pass as notes; any other point stops the run with `script_review_failed`; a repair that broke the evidence of a passed draft is set aside (kept draft) |
| Episode teaching review (reader, editorial, teaching) | revision in the script stage ([SCRIPTS](../SCRIPTS.md#reader-editorial-and-teaching-reviews)) | | | its points are `depth` (a failed check or objective) or `clarity` (an essential gap); they pass as notes only when the script review itself ended with accepted notes, otherwise `teaching_review_failed` |
| Teaching design | one focused repair | `review_scope.json`: basis `previous` or `editor_note` | basis in `teaching.CRITICAL_BASIS` (`factual_error`, `unsupported_claim`, `source_contradiction`, `objective_unreachable`) | stops with `teaching_design_failed` |
| Dialogue polishing | two repairs | `scoped_points`: repaired criteria and changed segments | `FIDELITY_CRITERIA` (`meaning`, `completeness`) | only `spoken_language` left: notes; otherwise the checked draft stays the script and the run goes on |
| Series review | `MAX_SERIES_REPAIRS = 1` | the evidence review after a correction is scoped | | |

### 1.2 What is left

1. **Every first-round point blocks.** Every point of a first review costs a repair round, including points that pass
   as notes anyway once the repairs are spent. A script episode can spend three repair-and-review rounds on clarity
   points and then be published with them.
2. **Every review runs at the run's model and level.** Only `listener_readback` and `audio_expression` are capped at
   `medium` (`text_settings.STAGE_EFFORT_CAPS`).
3. **When a loop's repairs are spent, the run stops for the operator.** `script_review_failed`,
   `teaching_design_failed` and `teaching_review_failed` stop the run; no stronger model gets one bounded last
   attempt.
4. **Nothing records who settled a point.** The scope rules hold within one loop, but gate decisions, notes and a
   final adjudication need a record that survives a resume.

## 2. Design principles

1. A reviewer **proposes** points. A separate, bounded step decides whether a point is worth a repair. Only blocking
   points are repaired.
2. After a repair, check only the point that caused it and the text the repair changed. This is already code
   ([§1.1](#11-what-already-holds)) and stays so.
3. **Authority is the role a call plays, not a model.** The stage fixes the role. Which model serves it follows the
   run's text choice and the subscription rule ([§11](#11-model-selection-under-the-subscription-rule)).
4. **The gate may only do earlier what the loop already does at its end.** A point is dismissable only if today it
   would pass as a note once the repairs are spent.

> Models may stay critical, but their criticism no longer creates work automatically.

## 3. Non-goals

This plan does not:

- let any model waive a deterministic check (A0);
- weaken the rules of [§1.1](#11-what-already-holds): critical bases, fidelity criteria, claim drift and the kept draft;
- let Jev judge source fidelity or dismiss a point that blocks today ([§7.1](#71-dismissable-points));
- make a paid call on its own. A subscription run never moves to OpenRouter, and Jev runs only when switched on
  ([BUSINESS_LOGIC](../BUSINESS_LOGIC.md#subscriptions-first-never-an-automatic-switch-to-paid-apis));
- make a fixed text choice switch provider or model, or query quotas;
- change a saved run's models on resume;
- serve a role with a model below its ladder entry ([§11.3](#113-the-ladder));
- treat every disagreement between models as a reason for another repair.

## 4. Existing building blocks

- `provider_pool.AdapterPool` builds the adapter for every call and writes `provider_choice.json` with the
  provider, model, level and `prompt_version`, plus `run_effort` for a capped call.
- `subscriptions.choose_subscription` applies the automatic rule; `subscriptions.record_quota_failure` notes a
  Claude block in the account-wide store.
- `text_settings.STAGE_EFFORT_CAPS` and `stage_effort` lower the level per prompt family; `auto_candidates` builds
  the pair that a run under `auto` stores.
- `jev.JevClient.decide` sends typed questions in one request, batched by `jev.QUESTIONS_PER_REQUEST = 8`. The gap
  probe sets the opt-in pattern: `execution.jev_probe`, the OpenRouter key, and `jev_probe.json` with the cost
  ([RESEARCH](../RESEARCH.md#gap-probe)).
- `evals/jev_decisions/` measures Jev on saved runs, with yes/no questions only.
- The scope code of [§1.1](#11-what-already-holds): `script_pipeline.follow_up_scope`, `teaching.review_scope`,
  `polishing.scoped_points`.
- `script_budget.STAGE_CALLS`, and the projection in `run_budget.py`, which reads `MAX_REVIEW_REPAIRS` and
  `NOTED_CATEGORIES`.

## 5. Authority model

| Rank | Role | Served by | Decides |
| --- | --- | --- | --- |
| A0 | deterministic check | Python validators and contracts | everything it checks; absolute |
| G | materiality gate | G-cap (a rule) or G-jev (Jev), [§7](#7-materiality-gate) | whether a dismissable point costs a repair |
| A1 | routine review | the stage's reviewer at a capped level ([§11.3](#113-the-ladder)) | proposes points |
| A2 | expert review | the stage's reviewer at the run's level; every review today except `listener_readback` | proposes points |
| A3 | final adjudicator | the A3 entry of the run's choice ([§11.3](#113-the-ladder)) | ends a loop whose repairs are spent ([§9.2](#92-a3-terminal-step)) |

The gate decides materiality, not correctness. It may let a point block, turn it into a note that stays in the
report, or close it. It closes only points raised at A1 ([§7.3](#73-dispositions)).

### 5.1 A0 always wins

No model may waive any of these: missing or invalid IDs; source or section references; schema and contract failures;
mandatory coverage; deterministic structure; evidence receipts; hard duration and shape limits; any other
deterministic stage or publication invariant. A repair that fails A0 is discarded, whoever wrote it.

### 5.2 Finality

A point settled at one rank is not reopened at a lower rank while its scope is unchanged. Within one loop, the scope
rules of [§1.1](#11-what-already-holds) already do this. The issue record ([§6](#6-issue-record)) makes it also hold
for gate notes, across resumes and for A3.

A point reopens in two cases:

- a segment in its scope changes (scope digest);
- for a whole-episode point, an equal or higher rank raises the same category again. This is the rule
  `follow_up_scope` already applies.

### 5.3 Issue identity

The identity is `(stage, episode, category or criterion, sorted segment ids)`. A whole-episode point has `episode` in
place of the segment ids. Wording does not count, because reviewers rephrase their points every round. This is the
same key the scope code already uses.

Its ceiling: two different points of one category on the same segments merge into one. The scope rules already treat
such points alike, so this loses no protection that exists today.

## 6. Issue record

States: `candidate`, `blocking`, `note`, `dismissed`, `fixed`. There is no `uncertain` state, because an uncertain
gate answer blocks ([§7.3](#73-dispositions)).

Each point and each decision about it appends one record to `<stage work folder>/issues.jsonl`. Records are never
rewritten, so earlier records stay byte-identical on resume. This follows the run-folder invariant
([AGENTS](../../AGENTS.md#rules-for-the-tests-themselves)).

```json
{
  "issue_id": "sha256 of the identity",
  "stage": "dialogue_polish_review",
  "category": "spoken_language",
  "basis": null,
  "segment_ids": ["ep_002_seg_031"],
  "dismissable": true,
  "status": "note",
  "raised_by": {"role": "A2", "provider": "claude_code", "model": "claude-sonnet-5-5", "reasoning_effort": "high"},
  "decided_by": {"role": "G", "gate": "jev", "probability": 0.91},
  "scope_digest": "sha256 of speaker, text and references of the scope segments",
  "round": 1
}
```

- `raised_by` copies the call's `provider_choice.json`.
- The scope digest uses the same fields as `changed_segments`.

## 7. Materiality gate

### 7.1 Dismissable points

A point is dismissable only if today's loop would let it pass as a note once its repairs are spent. Every other point
blocks as it does today, and no gate variant can change that.

| Stage | Dismissable | Never dismissable |
| --- | --- | --- |
| Script review | `clarity`, `depth`, `dialogue` (`NOTED_CATEGORIES`) | `grounding`, `scope`, `structure`; any point with basis `factual_error` or `source_contradiction`; every claim-drift point |
| Episode teaching review | its `depth` and `clarity` points, when the script review ended with accepted notes | the same points in every other case |
| Dialogue polishing | `spoken_language` | `meaning`, `completeness`, `speaker_roles`, `episode_framing` |
| Teaching design | none; its loop ends in a stop | everything |

A reviewer can file a factual error under a dismissable category. For every dismissable point, G-jev therefore also
asks whether the point asserts a factual, source or meaning error. A yes above a low probability makes the point
block. A false yes costs only the repair the point would get today.

Widening a stage's dismissable set is a later decision for that stage, based on shadow data
([Not in this plan](#21-not-in-this-plan)).

### 7.2 Gate variants

- **G-cap (a rule, no model call).** A dismissable point keeps the loop going for one repair round (operator
  decision 1) and is a note after that. It still goes into the repair prompt while other points keep the loop going.
  This is what the loop already does when its repairs are spent, only earlier. It needs no data flow, no key and no
  eval.
- **G-jev (Jev on OpenRouter).** It is switched on per project, like the gap probe: an `execution.jev_gate` switch,
  the OpenRouter key and credit. One request per review round carries every dismissable point as questions, batched
  as in `jev.py`. It uses no subscription quota.
- **A subscription model as classifier** is only an eval baseline ([§13.4](#134-baselines)). On a subscription it
  would spend a call per round from the very quota the gate is meant to save.

Once G-cap is active for a stage, it is that stage's gate whenever G-jev is off or cannot run.

### 7.3 Dispositions

| Answer | Effect |
| --- | --- |
| `BLOCKING` | repair, as today |
| `UNCERTAIN` | repair, as today; no extra adjudication call |
| `NOTE_ONLY` | a note in the report; no repair |
| `NOT_AN_ISSUE` | closed if raised at A1; a note if raised at A2 or higher |

The eval of 2026-09-29 asked Jev only yes/no questions. Whether Jev answers a four-way choice with usable
probabilities is V-11. If it doesn't, the fallback is two yes/no questions per point: "Is this a reason to change the
script before publication?" and the factual-error question of [§7.1](#71-dismissable-points).

### 7.4 What the gate sees

The gate sees a compact bundle per point, not the whole episode:

- the point and its criterion;
- the affected segments and their neighbours;
- the relevant teaching objective, findings and source passages;
- the deterministic limits;
- an earlier disposition, if there is one.

One request stays below Jev's limit of 32,000 tokens. Passages of user-supplied documents (provided works, and
uploads without a URL) are left out unless the operator opts in, as in the Jev eval.

### 7.5 Thresholds

No threshold is set before the eval is scored. A probability of 0.85 may be used to label shadow reports. The
activation threshold is the probability that keeps the upper bound of the miss rate under 10 % (decision 4,
[§13.2](#132-labels-and-sample-size)).

### 7.6 When Jev fails

`JevClient.decide` raises a `blocked` error today (`openrouter_key_required`, `openrouter_credits`,
`openrouter_privacy`, `jev_unavailable`). The gate never passes that error on:

- **In shadow mode,** the failure is recorded and the run continues unchanged.
- **In active mode,** the round falls back to G-cap. It records `gate_fallback` with the error code, and the Studio
  shows it in the job status ([§7.8](#78-missing-key-in-the-studio)).

### 7.7 Data and decision record

The operator accepted the data flow on 4 October 2026 (decision 3). Before G-jev goes active:

- [SECURITY](../SECURITY.md#trust-boundaries) gets a new row: script segments, findings and source passages go to
  OpenRouter (Jev, TypeSafe) while the gate is on. TypeSafe's data policy is not documented (V-15).
- A decision entry in DECISIONS replaces, for this gate only, the rule in [RESEARCH](../RESEARCH.md#gap-probe) that
  Jev decides nothing.

Passages of user-supplied documents stay out unless the operator opts in ([§7.4](#74-what-the-gate-sees)).

### 7.8 Missing key in the Studio

The Studio keeps the OpenRouter key only in memory, so it is gone after every restart
([SECURITY](../SECURITY.md#openrouter-key-in-the-studio)). Today the gap-probe switch mentions a missing key once,
when the switch is turned on. For the gate, the hint follows the current key state instead:

- **Before a run.** While a project's `jev_gate` switch is on and no key is available (`key_available`), the project
  overview shows a lasting hint next to the switch, and so does the start of a script run. The hint has a button to
  the „OpenRouter-Key" panel. Starting is not blocked: without the key, the gate runs as G-cap.
- **During a run.** A job whose round fell back with `openrouter_key_required` says in its status that Jev is
  waiting for the key. The audio queue's "waiting for key" state (`studio.queue_view`) is the model for this. A key
  added now takes effect from the job's next start („Fortsetzen"), because a job receives the key when it starts
  (`studio_worker.probe_key`).
- **Other failures** (`openrouter_credits`, `openrouter_privacy`, `jev_unavailable`) use the existing explanations
  in `web/app.js` and also fall back to G-cap.
- The texts are German, in `web/app.js`, and are covered by the browser suite.

## 8. Scoped follow-up

The scoped follow-up is already code in all three loops ([§1.1](#11-what-already-holds)) and is documented in
[SCRIPTS](../SCRIPTS.md#scoped-follow-up-review). This plan keeps it unchanged, including the points that block
wherever they are. It adds only two things:

- the issue record ([§6](#6-issue-record)), so that a gate note on an unchanged segment stays a note across resumes;
- a count of the points a follow-up raised outside its scope (the script review's `advisories`, polishing's notes).
  This shows how much review output the code discards ([§16](#16-success-metrics)).

A follow-up prompt that asks only "fixed, not fixed or regression" for each previous point would shrink every
follow-up call. It would also drop the evidence check of changed segments that the full review does, so it is not
part of this plan.

## 9. Loop semantics

### 9.1 One stage

```text
review (stage role, A2 today)
  -> A0 validation (as today)
  -> points
       not dismissable ----------------------------> blocking
       dismissable -> gate (G-cap, G-jev, or off)
                        BLOCKING, UNCERTAIN, off -> blocking
                        NOTE_ONLY                -> note
                        NOT_AN_ISSUE             -> closed (A1) or note (A2 and higher)
  -> no blocking points: the stage passes, with its notes in the report
  -> repair -> A0 -> scoped follow-up (§8) -> repeat within the stage's bound (§1.1)
  -> bound spent and blocking points left: A3 terminal step (§9.2), where today the run stops
```

### 9.2 A3 terminal step

The A3 step applies only where a loop with spent repairs stops the run today: the script review, teaching design and
the episode teaching review. Polishing keeps its own end (the checked draft stays the script), and the series review
keeps its bound of one.

1. A3 checks and answers `PASS`, `ACCEPT_WITH_NOTES`, `REPAIR` or `BLOCK`.
2. On `REPAIR`: one repair, then A0, then one more A3 check. That check may answer only `PASS`, `ACCEPT_WITH_NOTES` or
   `BLOCK`.
3. A repair that fails A0 is discarded. The version before it stays, and the outcome is `BLOCK`.
4. `BLOCK` stops the run with today's code (`script_review_failed`, `teaching_design_failed`,
   `teaching_review_failed`), and the report carries the A3 verdict.
5. For a point that blocks today, A3 may answer only `REPAIR` or `BLOCK` (operator decision 2). A3 is one stronger
   repair attempt before the operator is asked; its `PASS` or `ACCEPT_WITH_NOTES` applies only to the other points.
6. If the run's choice cannot serve A3 at that moment ([§11.3](#113-the-ladder)), the stage stops as today, and a
   resume tries A3 again.

This step makes at most three calls per stage and episode. They go into `script_budget.STAGE_CALLS`.

## 10. Stage policy

| Stage | Prompt family | Reviewer role now | Gate on | Rollout |
| --- | --- | --- | --- | --- |
| Dialogue polishing | `dialogue_polish_review` | A2 | `spoken_language` | first |
| Reader review (input of the teaching review) | `listener_readback` | A1 (capped at `medium` today) | raises no points itself | — |
| Editorial and episode teaching review | `editorial_review`, `teaching_review` | A2 | their `depth` and `clarity` points ([§7.1](#71-dismissable-points)) | second |
| Script review | `script_review` | A2 | `clarity`, `depth`, `dialogue` | third |
| Teaching design review | `teaching_design_review` | A2 | none | A3 step only |
| Evidence and claim checks | (in `script_review`) | A2 | never | — |
| Series review | `series_review` | A2 | not in this plan | — |

`STAGE_AUTHORITY` in `text_settings.py` records the reviewer role per prompt family. It starts as in this table, with
A1 only for `listener_readback`, which is capped today.

Moving another review to A1 lowers its level. That reverses the rule of 2026-09-29 that evidence and teaching reviews
keep the run's level ([BUSINESS_LOGIC](../BUSINESS_LOGIC.md#lower-effort-for-two-stages)). Each such move needs two
things: its stage's eval must show that blocker recall holds and that the call spends less quota (V-14), and a
decision entry must record the move.

## 11. Model selection under the subscription rule

### 11.1 Today

[BUSINESS_LOGIC](../BUSINESS_LOGIC.md#text-providers-and-model-selection) owns these rules. The facts this plan
depends on:

- A run binds one text choice when it starts (`script_request.json`, `research_request.json`). A resume never
  changes it; only an explicit text switch does.
- A **fixed** choice (Sonnet 5.5, Opus 5.5, Astra, an OpenRouter model) never switches and never queries quotas.
- **`auto`** stores a candidate pair: Claude Sonnet 5.5 at `high` and Codex Astra at `xhigh`
  (`text_settings.auto_candidates`), with Claude preferred. Before every call, `choose_subscription` picks the
  preferred subscription while it has quota, otherwise the other, otherwise the run pauses.
- Codex quota is read before a call. Claude's quota is known only after a refused call.
- A Claude block is **one entry for the whole subscription**, whatever the model, in the account-wide store
  `~/.podcast-automate/subscriptions.json`. An Opus limit blocks it for 5 hours (`claude_code.claude_block_window`),
  so the Sonnet calls of every project stop too.
- OpenRouter runs only when chosen. Calls with web search always run on a subscription.
- The Codex subscription also offers Sol, Terra and Luna (`text_settings.CODEX_MODELS`). The OpenRouter catalog offers
  Astra, Opus 5.5, Sonnet 5.5 and DeepSeek V4.1 Flash, but not Luna.

### 11.2 Rules for roles

1. A role chooses only within the run's own text choice. Routing adds no provider: OpenRouter is used only if the run
   chose it, and Jev only if it is switched on.
2. The only new input of a call is its role. Quota state stays inside `choose_subscription`, and the fixed path still
   reads none.
3. A role is never served below its ladder entry. If no allowed entry can serve it right now, the run pauses as
   today; for A3, the stage stops as today ([§9.2](#92-a3-terminal-step)).
4. Calls with web search keep today's rule; roles don't apply to them.
5. `provider_choice.json` records the role, so that [§16](#16-success-metrics) can count calls by role.

### 11.3 The ladder

**Fixed choice.** One model serves every role.

- On a subscription, A1 calls use the A1 level cap, as `STAGE_EFFORT_CAPS` does today. On OpenRouter they keep the
  run's level, also as today.
- A3 runs on the same model as a bounded final step. Its value is the bound, not a stronger model.

**`auto`.** The candidate pair becomes a ladder with one entry per subscription and role:

| Role | Claude Max subscription | Codex subscription | Asked first |
| --- | --- | --- | --- |
| A1 | Sonnet 5.5 at `medium`, or the run's level if that is lower | Astra at `medium`, or the run's level if that is lower | Claude, as today |
| A2 | Sonnet 5.5 at the run's level (today's candidate) | Astra at the run's level (today's candidate) | Claude, as today |
| A3 | none until V-12, then Opus 5.5 at `xhigh` | Astra at `xhigh` | Codex |

- A2 is exactly today's pair, so every existing review call keeps its model and level.
- A1 changes only the level, as `STAGE_EFFORT_CAPS` does. Whether that saves quota is V-14. Luna as the Codex A1
  entry is V-13.
- A3 asks Codex first, for two reasons. Its quota can be read before the call. And until an Opus limit is noted for
  Opus alone (V-12), one Opus call could block all Claude work in every project for 5 hours. Until V-12, Claude has no
  A3 entry. When Codex has no quota, A3 is not available and the stage stops as today.
- With the preset „Automatisch · Claude, sonst Codex · high", A2 uses `high` on both subscriptions, A1 uses `medium`,
  and A3 keeps `xhigh`.
- Claude's prompt limit applies to the Claude model of the role. A prompt too large for it moves to Codex, as today.

### 11.4 Binding, resume and switches

- A new run stores its ladder with its candidates, as part of the run's inputs. A resume with a different ladder is
  refused with `inputs_changed`, like a changed candidate list.
- A run saved without a ladder uses its one candidate per subscription for every role. That is today's behaviour,
  plus A3 as a bounded step on the same model.
- Checkpoints stay bound to the prompt text, not to the provider. A call answered by another role's model stays
  valid, as after a provider change today.
- A text switch to `claude` or `astra` applies the catalog ladder with that preference for A1 and A2; A3 keeps Codex
  first. A switch to `claude-only`, `astra-only` or `openrouter` acts as a fixed choice. `text_switch.json` records the
  ladder used.

When this is built, the sections of BUSINESS_LOGIC on [`auto`](../BUSINESS_LOGIC.md#fixed-providers-and-the-rule-auto),
[the level caps](../BUSINESS_LOGIC.md#lower-effort-for-two-stages),
[binding](../BUSINESS_LOGIC.md#the-choice-is-bound-to-the-run) and
[switching](../BUSINESS_LOGIC.md#switching-a-job-to-another-provider) change in the same change.

## 12. Changes by module

| Module | Change |
| --- | --- |
| `review_authority.py` (new) | Roles, issue identity and record, finality. The dismissable sets are read from the stage modules; `CRITICAL_BASIS`, `FIDELITY_CRITERIA` and `NOTED_CATEGORIES` stay where they are. No provider or model names. |
| `decision_gate.py` (new) | G-cap and G-jev, shadow records, fallback ([§7.6](#76-when-jev-fails)). No repair logic. `jev.py` stays the transport. |
| `text_settings.py` | `STAGE_AUTHORITY`; the ladder catalog next to `auto_candidates`; the A1 level cap. `audio_expression` keeps its stage cap. |
| `provider_pool.py` | A `role` argument per call: take the ladder entry of the chosen subscription; A3 asks Codex first; the fixed path stays unchanged apart from the A1 cap; write `role` to `provider_choice.json`. |
| `subscriptions.py` | Only after V-12: note an Opus limit for Opus alone. |
| `execution.py`, `studio*.py`, `web/app.js` | The `jev_gate` switch next to `jev_probe`, with a German label; the key hint before and during a run ([§7.8](#78-missing-key-in-the-studio)); `gate_fallback` in the job status. |
| `script_pipeline.py`, `teaching.py`, `polishing.py` | The gate before a repair; the A3 terminal step instead of today's stop (not in polishing). Every rule of [§1.1](#11-what-already-holds) is kept. |
| `script_budget.py`, `run_budget.py` | The gate and A3 calls in `STAGE_CALLS` and the projection. G-cap changes the repair rounds the projection expects. |
| Docs | BUSINESS_LOGIC (model selection, budgets), SCRIPTS and TEACHING (review loops), SECURITY (data flow), RESEARCH (the Jev rule), PRODUCT (the Jev gate), CHANGELOG. |

## 13. Eval work

### 13.1 Cases

Add a third case type, `materiality`, to `evals/jev_decisions/`. Each case is one dismissable point with its bundle
([§7.4](#74-what-the-gate-sees)) and a label.

### 13.2 Labels and sample size

Saved runs are a biased source of labels. The old loop repaired every point, and in a flapping loop a point was often
replaced for reasons unrelated to whether it was real. History therefore only selects the cases. A person labels a
sample without seeing the pipeline's outcome, as V-5 does for the research evidence corpus.

The labels are `BLOCKING`, `NOTE_ONLY` and `NOT_AN_ISSUE`; a case with ambiguous history gets no label. The
factual-error question of [§7.1](#71-dismissable-points) is labelled too.

With n labelled blockers and none missed, the 95 % upper bound of the miss rate is about 3/n. The operator's limit of
10 % (decision 4) therefore needs at least 30 labelled blockers per stage, all of them caught. Each miss raises the
number: with one miss, about 48 are needed.

### 13.3 Metrics

All metrics are reported by stage and category:

- blocker recall, and the miss rate with its upper bound;
- precision of notes and dismissals;
- the uncertain rate;
- calibration by probability;
- recall of the factual-error question;
- repairs avoided and subscription calls avoided;
- USD per decision.

### 13.4 Baselines

Compare the gate variants against:

- today's rule (every point blocks);
- G-cap;
- G-jev;
- Sonnet 5.5 and Astra at `medium` as classifiers, counting the calls they cost.

Real-model checks stay under `evals/` and are run by hand, never in the test suites.

## 14. Shadow mode

Before a gate decides anything, it runs in shadow mode:

- G-cap is computed for every round, at no cost.
- G-jev runs when the project's switch is on. It costs OpenRouter credit and no subscription quota.
- Each round appends to `<stage work folder>/gate_shadow.jsonl`: the points, today's action, and the gate's answer and
  probability. The report script joins the later outcome from the run folder; it is never written back.
- A Jev failure is recorded and changes nothing.

The shadow report answers:

- how many repairs each gate would have prevented;
- how many blockers it would have missed;
- which categories are safe;
- where it is uncertain;
- how many subscription calls it would have saved.

## 15. Steps

Each step is checkpointable and can be switched off again. Its tests follow the mapping in
[AGENTS](../../AGENTS.md#source-module-to-test-modules).

0. **Baseline (V-10).** Count the calls per run by stage, role and outcome from `calls/*/provider_choice.json`, and
   the repair rounds per category from the review reports. If review loops are not a large share of the calls, change
   the scope of this plan before step 1.

**Phase A: infrastructure, no change in behaviour**

1. The issue record ([§6](#6-issue-record)), and `role` in `provider_choice.json`.
2. The role-aware pool and the stored ladder ([§11](#11-model-selection-under-the-subscription-rule)). Every stage
   except `listener_readback` keeps A2, which is today's candidate.

**Phase B: shadow mode**

3. Shadow gate ([§14](#14-shadow-mode)).
4. Materiality eval with human labels ([§13](#13-eval-work)).
5. Shadow and baseline report ([§16](#16-success-metrics)).

**Phase C: first active stage**

6. Dialogue polishing: G-cap with one round, together with the key hint ([§7.8](#78-missing-key-in-the-studio)).
   G-jev follows once three things hold: its eval keeps the miss rate under 10 %, the decision entry exists, and the
   SECURITY row is in place ([§7.7](#77-data-and-decision-record)).
7. Editorial and episode teaching reviews, in the same way.

**Phase D: terminal step and ladder**

8. The A3 terminal step ([§9.2](#92-a3-terminal-step)), with Codex first under `auto`.
9. Script review gate on `clarity`, `depth` and `dialogue`.
10. Moves to A1, one stage at a time, each after V-14 and a decision entry.
11. Opus as the Claude A3 entry after V-12; Luna as the Codex A1 entry after V-13.

## 16. Success metrics

Record the baseline (step 0) before anything is activated. Track per podcast and per episode:

```text
calls by stage and role                 repairs by stage and category
subscription calls (Claude, Codex)      openrouter_usd (text, Jev)
points: proposed, blocking, note, dismissed, reopened
out_of_scope_proposed                   operator stops by code
A3 steps and outcomes                   final hard and evidence failures
published_audio_minutes
```

The primary metrics are **subscription calls per published audio minute**, per subscription, and OpenRouter USD per
minute. A Jev request and an Astra call do not cost the same, so a plain call count is only secondary. Codex quota can
also be read as `usedPercent` before and after a run; Claude reports no figure in advance.

Also track:

- repairs per accepted point;
- new points per repair;
- the share of proposed points that block;
- operator stops per episode;
- calls from the first review to the end of the stage.

## 17. Acceptance criteria

**Phase A**

- Every existing test is green, existing checkpoints resume unchanged, and A0 is unchanged.
- Tests show that:
  - a lower rank cannot reopen a higher rank's decision while the scope is unchanged;
  - a point that blocks today is never dismissable;
  - a fixed choice keeps its model for every role and reads no quota;
  - a resume with a changed ladder is refused;
  - a run saved without a ladder behaves as before.

**Phase B**

- Shadow records are written without changing any outcome or run file.
- The eval scores materiality cases.
- The report shows blocker misses with their upper bound, and repairs avoided.

**Phase C**, for the activated stage:

- no deterministic gate is bypassed;
- G-cap gives a dismissable point exactly one repair round;
- for G-jev:
  - the upper bound of the miss rate is under 10 %;
  - a Jev failure falls back to G-cap as in [§7.6](#76-when-jev-fails);
  - the key hint of [§7.8](#78-missing-key-in-the-studio) shows before and during a run;
  - the SECURITY row and the decision entry exist.

**Phase D**

- A3 is checkpointed and resumable, and makes at most three calls.
- Its repair passes A0 before it is accepted.
- Under `auto`, no role is served below its entry.
- An Opus limit cannot block Sonnet calls before Opus becomes a ladder entry.

**Overall**, against the baseline:

- fewer subscription calls per audio minute and fewer operator stops;
- no regression in evidence checks, teaching-quality evals, script validity or publish-time gates.

No target percentage is set before the baseline exists.

## 18. Testing impact

The changes touch high-fanout modules: `provider_pool`, `text_settings`, `subscriptions`, `script_pipeline`,
`teaching`, `polishing` and `script_budget`. Select tests by the mapping in
[AGENTS](../../AGENTS.md#when-to-run-what). For the provider modules, that includes `test_scripting`,
`test_research`, `test_status_summary`, `test_studio` and `test_text_selection`, with quota fakes as in
`test_provider_pool` (`QuotaFakes`).

New tests:

- issue identity and finality;
- dismissable points per stage;
- gate dispositions and fallback;
- the A3 bound, and an A3 repair that fails A0;
- role choice for fixed choices and `auto`, with no quota query on the fixed path;
- ladder binding on resume and across text switches;
- `STAGE_CALLS` in `test_run_budget`, checked against what the fixture pipeline spends.

## 19. Mini eval podcast: review-loop control

This is a small real-model case that checks how the gate and A3 behave on a known script. The state machine itself
(scope enforcement, finality, dismissable points, the A3 bound, fallbacks) is tested in the Python suite with patched
responses ([§18](#18-testing-impact)), not here.

Location:

```text
evals/review_loop_mini/
  README.md
  episode.json
  teaching_plan.json
  evidence.json
  cases.json
  run.py
```

### 19.1 Topic

**Why do seasons happen?** A two-host episode of about 6 to 8 spoken minutes. Build the clean reference from
`evals/teaching_quality/seasons.md` and its source
([NASA, What Causes the Seasons?](https://spaceplace.nasa.gov/seasons/en/)). Store short evidence summaries with the
eval; it uses no live retrieval.

After the episode, the listener can:

- explain that axial tilt, not the distance from the Sun, drives the seasons;
- connect tilt to sunlight angle and day length;
- explain why the two hemispheres have opposite seasons;
- tell the main mechanism apart from the minor effect of the elliptical orbit.

### 19.2 Cases

Each case is the clean reference plus a deterministic mutation; no model invents the defects.

| Case | Content | Expected |
| --- | --- | --- |
| Clean | one deliberate repetition of the mechanism, a flashlight analogy, one wordy but acceptable transition, a recap | every point is dismissable and ends as a note or is closed; no repair |
| Factual blocker | one segment makes the distance the cause of summer | the reviewer finds it; it is not dismissable (basis `factual_error`, a blocking category, or the factual-error question); one repair |
| Reasoning blocker | one segment reverses tilt and day length for one hemisphere | as for the factual blocker |
| Ambiguous | how much to say about orbital eccentricity | `NOTE_ONLY`; `UNCERTAIN` is allowed but costs a repair round, which is counted |
| Repair scope | the factual blocker taken through a repair | the follow-up raises no blocking point on unchanged segments; out-of-scope points it raises are counted |

No case is expected to reach A3. A separate fixture forces it.

### 19.3 Runs and metrics

Each case runs five times, because one run says nothing about variance. Each run records:

- points proposed, blocking, noted and dismissed;
- gate requests and calls by role;
- repairs;
- out-of-scope points proposed;
- the terminal status;
- calls from the first review to the end of the stage.

Over all runs, the eval checks that:

- no seeded blocker is missed;
- the clean case gets no repair;
- the repaired blocker ends without another full review;
- no normal case reaches A3.

### 19.4 Comparison

During Phases B and C, run today's loop and the gated loop on the same cases. Report for each:

- calls and repairs;
- unique points, and points created after a repair;
- the final blocker status and the final script digest.

The gated loop must end with the seeded defects corrected and the clean case untouched.

### 19.5 Keep it small

The eval must stay:

- fast enough to run by hand during the work;
- small enough that every expected point is understandable;
- deterministic in where defects are injected;
- free of live search;
- sensitive only to review-loop behaviour.

Real-project shadow data ([§14](#14-shadow-mode)) cover the rest.

## 20. Verification list

| ID | Item | How to verify | Needed for | Status |
| --- | --- | --- | --- | --- |
| V-10 | Where the calls of a podcast go, by stage, role and outcome, and the repair rounds per category. The figure of about a thousand calls is reported, not counted. | For the latest complete projects, count `calls/*/provider_choice.json` by `prompt_version` family per run, and the repair rounds per category from the review reports. | Step 0; [§16](#16-success-metrics) | open |
| V-11 | Jev answers a four-way choice question with usable probabilities. The eval of 2026-09-29 asked only yes/no questions. | One probe request per variant, then the materiality eval. | [§7.3](#73-dispositions) | open |
| V-12 | An Opus limit refuses only Opus: Sonnet calls succeed while Opus is limited. | Read a recorded Opus-limit failure (`opus_limit` in `claude_block_window`) and try a Sonnet call while the limit holds. | Opus as the Claude A3 entry ([§11.3](#113-the-ladder)) | open |
| V-13 | Luna on the Codex subscription keeps the stage's blocker recall and spends less Codex quota than Astra at `medium`. | Run the stage eval with Luna; read Codex `usedPercent` before and after a fixed set of calls. | Luna as the Codex A1 entry | open |
| V-14 | A review at `medium` spends measurably less subscription quota than one at the run's level, and keeps the stage's blocker recall. | Paired calls with Codex `usedPercent` deltas; the stage's eval at both levels. | Any move to A1 ([§10](#10-stage-policy)) | open |
| V-15 | How TypeSafe handles decision requests: retention and training use. | OpenRouter's provider page and TypeSafe's terms; the account's privacy setting. | The wording of the SECURITY row ([§7.7](#77-data-and-decision-record)); no longer a precondition, since the operator accepted the data flow on 2026-10-04 | open |

## 21. Not in this plan

- **An A2 confirmation of gate decisions, or an A2 adjudication of `UNCERTAIN`.** Add these only if shadow data show
  many uncertain answers whose adjudication would save more repairs than it costs.
- **Widening a stage's dismissable set** beyond what passes as a note today. That is a decision per stage, based on
  shadow data.
- **A per-call router that takes quota states and a cost policy as inputs.** The subscription rule already reads
  quota.
- **`typesafe/jev-router` choosing OpenRouter models.** OpenRouter runs only when explicitly chosen, and the router is
  unverified.
- **OpenRouter models as ladder entries** (for example DeepSeek V4.1 Flash as A1). They are paid, and relevant only to
  fixed OpenRouter runs.
- **Gating the series review, the research reviews, or evidence and claim checks.** Jev stays where
  [RESEARCH](../RESEARCH.md#gap-probe) uses it.
- **A follow-up prompt that checks only "fixed or not"** ([§8](#8-scoped-follow-up)).

## Sources

- Code as of 2026-10-04: `provider_pool.py`, `subscriptions.py`, `text_settings.py`, `claude_code.py`
  (`claude_block_window`), `jev.py`, `script_pipeline.py`, `script_checkpoints.py`, `teaching.py`, `polishing.py`,
  `series_review.py`, `script_budget.py`, `run_budget.py`
- [BUSINESS_LOGIC: Text providers and model selection](../BUSINESS_LOGIC.md#text-providers-and-model-selection)
- [SCRIPTS: Scoped follow-up review](../SCRIPTS.md#scoped-follow-up-review)
- [RESEARCH: Gap probe](../RESEARCH.md#gap-probe)
- [Jev decision checks](../../evals/jev_decisions/README.md)
- [Teaching-quality eval](../../evals/teaching_quality/README.md)
