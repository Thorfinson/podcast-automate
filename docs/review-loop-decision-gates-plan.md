# Review-Loop Decision Gates and Authority Routing Plan

Status: planning document  
Scope: research, teaching, script, polishing and review loops  
Primary goal: reduce repeated model-review/repair loops without weakening evidence, teaching or publication gates.

## 1. Problem

The pipeline currently uses strong generative models for many review and repair steps. These models are intentionally critical and, when asked to review a changed artifact again, often find a new issue even after the previous one was repaired. That creates review flapping:

```text
review
  -> issue
  -> repair
  -> full review
  -> new issue
  -> repair
  -> ...
```

The effect compounds across research, teaching design, episode writing, dialogue polishing, episode review and series review. A single podcast can therefore require roughly one thousand model calls even when the final artifact is already close to acceptable.

The main optimization target is not merely a cheaper model per call. It is to avoid creating unnecessary calls.

## 2. Design principle

A generative reviewer may **propose** an issue, but a proposal is not automatically a blocker.

The pipeline introduces two separate concepts:

1. **Issue discovery** — generative models may identify possible problems.
2. **Issue disposition** — a bounded decision gate decides whether a proposed issue is materially blocking, informational, invalid, or uncertain.

The pipeline then repairs only issues that have earned blocking status.

A second core rule is:

> After a repair, re-check only the issue that caused the repair and the text directly changed by that repair. Do not perform an unrestricted fresh search for unrelated improvements.

This separates quality assurance from open-ended editing.

## 3. Non-goals

This plan does not:

- allow a decision model to override deterministic validation;
- use Jev as the final judge of source fidelity or factual grounding;
- replace expert models for synthesis, evidence reasoning or difficult adjudication;
- silently downgrade a call below its required authority level;
- treat every model disagreement as a reason for another repair;
- require OpenRouter for high-authority calls when an OpenAI or Anthropic subscription is available.

## 4. Existing building blocks

The repository already contains most of the mechanics required for this design:

- `src/podcast_automate/provider_pool.py` is the central per-call provider boundary.
- `src/podcast_automate/text_settings.py` already applies stage-specific reasoning caps.
- `src/podcast_automate/jev.py` already implements a cheap typed decision client.
- `evals/jev_decisions/` already measures Jev on saved production-like data.
- `src/podcast_automate/script_pipeline.py` already carries `previous_issues` and `changed_segments`.
- `src/podcast_automate/teaching.py` already has review scope, focused repairs and checkpointed follow-ups.
- `src/podcast_automate/polishing.py` already scopes comparison points across repair rounds.
- deterministic validators already protect schemas, IDs, references, claim receipts, coverage, duration and other hard invariants.

The implementation should extend these mechanisms rather than replace them.

## 5. Authority model

Every quality decision receives an authority level.

| Level | Meaning | Typical implementation | May overrule lower semantic judgments? |
| --- | --- | --- | --- |
| A0 | deterministic authority | Python validators and contracts | yes, absolute |
| A1 | routine reviewer | OpenRouter Luna or another cheap qualified model | no |
| A2 | expert reviewer | Sonnet 5.5 or Sol-class model | yes, A1 |
| A3 | final adjudicator | Opus or Astra-class model | yes, A1 and A2 |

### 5.1 A0 always wins

A3 is the highest **semantic** authority, but A0 remains absolute.

No model may waive:

- invalid or missing IDs;
- invalid source or section references;
- schema/contract failures;
- mandatory coverage failures;
- deterministic structure failures;
- invalid evidence receipts;
- source-reference mismatches;
- explicit hard duration or shape constraints;
- any other condition already encoded as a deterministic publication or stage invariant.

If an A3-generated repair violates A0, the repair is invalid.

### 5.2 Finality

An issue dismissed by A2 cannot be reopened by A1 unless the relevant artifact changed.

An issue dismissed by A3 cannot be reopened by A1 or A2 unless the relevant artifact changed.

This prevents authority regression and repeated rediscovery of already-settled criticism.

## 6. Issue lifecycle

Introduce an explicit lifecycle for model-proposed issues.

Recommended states:

```text
candidate
blocking
note
dismissed
uncertain
fixed
```

Recommended metadata:

```json
{
  "issue_id": "stable-id",
  "category": "clarity",
  "reason": "...",
  "segment_ids": ["s041", "s042"],
  "status": "candidate",
  "created_by": {
    "authority": "A1",
    "provider": "openrouter",
    "model": "..."
  },
  "last_decided_by": null,
  "decision_confidence": null,
  "artifact_digest": "...",
  "scope_digest": "..."
}
```

Stable issue identity should derive from the affected criterion/category, artifact scope and normalized issue basis, not from free-form wording alone.

## 7. Jev materiality gate

Jev is used as a **decision gate**, not as another free-form reviewer.

For each candidate issue it chooses exactly one of:

```text
NOT_AN_ISSUE
NOTE_ONLY
BLOCKING
UNCERTAIN
```

### 7.1 Meaning

- `NOT_AN_ISSUE`: close the issue; no repair.
- `NOTE_ONLY`: preserve it in reports; no repair.
- `BLOCKING`: allow the issue to enter the repair path.
- `UNCERTAIN`: do not repair yet; escalate to an A2 adjudicator.

### 7.2 Context

Do not send the entire episode unless an eval proves that necessary.

Prefer a compact issue bundle:

- candidate issue and criterion;
- affected segment(s);
- small neighboring context;
- relevant teaching objective;
- relevant findings;
- relevant source passages;
- applicable deterministic limits;
- previous disposition, if any.

This keeps decision cost low and makes the question narrow enough for a decision model.

### 7.3 Confidence policy

Initial conservative policy:

```text
high-confidence decision -> accept Jev disposition
medium confidence          -> A2 adjudication
low confidence             -> A2 adjudication
```

Do not hard-code production thresholds until the materiality eval is scored. A starting experimental threshold such as 0.85 may be used in shadow reports, but activation thresholds must be selected from measured false-negative rates.

### 7.4 Evidence restriction

The existing Jev evaluation showed that section relevance is a good fit, while claim-fidelity judgment is much weaker.

Therefore Jev must not become the sole final judge for questions such as:

- whether a script strengthened a causal claim;
- whether a qualification was dropped;
- whether a numeric relation changed;
- whether a source actually supports the spoken claim.

Those remain expert evidence-review tasks.

## 8. Strict scoped follow-up

After a blocking issue is repaired, the next check is not a new unrestricted review.

The follow-up receives:

- the issue that caused the repair;
- before/after affected segments;
- directly relevant neighboring segments;
- relevant findings/evidence/objectives;
- deterministic defects, if any.

Allowed follow-up results:

```text
FIXED
NOT_FIXED
REGRESSION
UNCERTAIN
```

The follow-up is not allowed to introduce unrelated issues outside the changed scope.

A new issue may enter the loop only if:

1. A0 detects a new deterministic failure; or
2. the repair directly created a new semantic problem in changed material.

This rule should be enforced in code, not only in prompt text.

## 9. Revised loop semantics

### 9.1 Generic loop

```text
artifact
  |
  v
A0 deterministic validation
  |
  v
A1 review
  |
  v
candidate issues
  |
  v
Jev materiality gate
  |
  +--> NOT_AN_ISSUE -> close
  |
  +--> NOTE_ONLY ----> report, no repair
  |
  +--> UNCERTAIN ----> A2 adjudication
  |
  +--> BLOCKING -----> A2 confirm/repair path
                           |
                           v
                        repair
                           |
                           v
                    A0 validation
                           |
                           v
                    scoped A2 check
                           |
                  +--------+--------+
                  |                 |
                pass              fail
                                    |
                                    v
                              A3 adjudication
```

### 9.2 A3 terminal semantics

A3 returns one of:

```text
PASS
ACCEPT_WITH_NOTES
REPAIR
BLOCK
```

If A3 returns `REPAIR`, allow exactly one final repair and exactly one final A3 check.

The final A3 check returns only:

```text
PASS
ACCEPT_WITH_NOTES
BLOCK
```

There is no A4 and no return to A1.

A3 finality applies only after A0 passes.

## 10. Stage-specific policy

### 10.1 Script evidence / claim review

Preferred authority chain:

```text
A1: cheap qualified reviewer, optional
A2: Sol-class evidence reviewer
A3: Astra-class adjudicator
```

Jev may gate materiality of qualitative review issues but must not replace the source/claim fidelity review itself.

### 10.2 Teaching, dialogue and editorial quality

Preferred authority chain:

```text
A1: OpenRouter Luna
A2: Sonnet-class reviewer
A3: Opus-class adjudicator
```

These stages are good first targets because many findings are judgments of clarity, framing, pacing or pedagogy rather than hard factual claims.

### 10.3 Research routing

Jev remains appropriate for narrow choices such as:

- whether a section is relevant to a research gap;
- whether a candidate source passage deserves reading;
- bounded routing decisions.

Actual answer generation, synthesis and evidence adjudication remain generative-model tasks.

## 11. Three resource pools

Treat available capacity as three independent lanes:

1. **OpenAI subscription / Codex**
2. **Anthropic subscription / Claude Code**
3. **OpenRouter paid API**

Authority is chosen before provider/model routing.

The router receives at least:

```text
prompt_family
minimum_authority
domain
attempt
previous_failures
prompt_size
expected_output_size
openai_quota_state
anthropic_quota_state
cost_policy
```

The router may choose only among providers/models that meet the minimum authority requirement.

### 11.1 Example roles

| Logical role | Preferred resources |
| --- | --- |
| decision | Jev on OpenRouter |
| cheap_text | Luna-class model on OpenRouter |
| writer | Sonnet or Sol-class |
| evidence | Sol-class, with Sonnet as qualified fallback where appropriate |
| escalation | Opus or Astra-class |

### 11.2 Jev Router

If the lane decision is `OPENROUTER`, a provider-side model router such as `typesafe/jev-router` may optionally choose the concrete OpenRouter model and reasoning effort.

That router is subordinate to the pipeline policy:

```text
pipeline authority/policy
        |
        +-- OpenAI subscription
        +-- Anthropic subscription
        +-- OpenRouter
              |
              +-- optional Jev Router
```

It must never select a model below the pipeline's required authority class.

## 12. Changes by module

### 12.1 New module: `review_authority.py`

Responsibilities:

- authority enum/order;
- issue lifecycle model;
- stable issue identity;
- disposition merge rules;
- rules preventing lower-authority reopening;
- helper for terminal A3 decisions;
- serialization/checkpoint compatibility.

Keep provider/model names out of this module.

### 12.2 New module: `decision_gate.py`

Responsibilities:

- generic bounded decision interface;
- Jev materiality request/response;
- confidence handling;
- shadow-mode logging;
- deterministic fallback when Jev is unavailable;
- no direct repair logic.

The current `jev.py` can remain the low-level Jev transport.

### 12.3 `provider_pool.py`

Extend routing from "one provider choice for the run" toward per-call policy while preserving current fixed-provider behavior.

Add a route request carrying:

- prompt family;
- minimum authority;
- domain;
- attempt/failure metadata.

Do not break the current fixed-provider path or its quota semantics.

### 12.4 `text_settings.py`

Add logical stage policy beside `STAGE_EFFORT_CAPS`, for example:

```python
STAGE_AUTHORITY = {
    "listener_readback": "A1",
    "dialogue_polish_review": "A1",
    "teaching_design_review": "A1",
    "editorial_review": "A1",
    "teaching_review": "A1",
    "script_review": "A2",
}
```

This is a minimum authority map, not a model map.

### 12.5 `script_pipeline.py`

Replace the current generic "issue -> repair -> full review" behavior with:

1. candidate issue discovery;
2. materiality disposition;
3. A2 adjudication when required;
4. scoped repair;
5. scoped follow-up;
6. A3 terminal path when still unresolved.

Reuse existing `previous_issues`, `changed_segments`, review checkpoints and `follow_up_scope`.

The existing `MAX_REVIEW_REPAIRS` should eventually be replaced by authority-aware terminal semantics, not merely increased or decreased.

### 12.6 `teaching.py`

Apply the same distinction between:

- deterministic design defects;
- candidate semantic review issues;
- material blockers;
- notes/dismissals.

Reuse `review_scope`, focused repair and existing checkpoint files.

A stronger reviewer may settle earlier semantic objections without starting a repair.

### 12.7 `polishing.py`

Use the materiality gate before a comparison point becomes blocking.

After repair, keep the existing before/after scoping but prohibit fresh unrelated issues outside changed segments.

Spoken-language-only findings are a good first production target for the new behavior.

### 12.8 Research modules

Do not immediately rewrite the full question-research state machine.

First reuse Jev for narrow bounded routing where it is already empirically strong. Introduce authority semantics later for:

- answer review disagreements;
- synthesis/audit objection routing;
- repeated re-openings.

Research evidence correctness remains conservative until dedicated evals show safety.

## 13. Eval work

Extend `evals/jev_decisions/` with a third case type:

```text
materiality
```

### 13.1 Historical labels

Construct cases from saved runs where possible.

Possible labels:

- `BLOCKING`: later stronger/scoped review confirmed the issue and a repair was necessary;
- `NOTE_ONLY`: issue remained as an accepted note without blocking publication;
- `NOT_AN_ISSUE`: later review/adjudication explicitly closed or superseded it;
- `UNCERTAIN`: saved reviewers materially disagreed or the history is insufficient.

Do not manufacture a definitive label from ambiguous history.

### 13.2 Metrics

Accuracy alone is insufficient.

Report at least:

- true blocker recall;
- false-negative rate for blockers;
- note/dismiss precision;
- uncertain rate;
- calibration by confidence;
- estimated repairs avoided;
- estimated expert calls avoided;
- decisions per dollar;
- breakdown by issue category and stage.

The most important safety metric is false negatives on real blockers.

### 13.3 Baselines

Compare:

- Jev decision;
- simple deterministic heuristics;
- cheap Luna structured classification;
- expert reviewer adjudication where historical labels exist.

## 14. Shadow mode

Before Jev can control repair behavior, run it in shadow mode.

For every candidate issue save:

```json
{
  "existing_pipeline_action": "...",
  "jev_decision": "...",
  "probabilities": {},
  "confidence": 0.0,
  "later_outcome": null
}
```

The current pipeline behavior remains authoritative.

Shadow reports should answer:

- how many repairs Jev would have prevented;
- how many blockers it would have missed;
- which categories are safe;
- where it is systematically uncertain;
- how much OpenAI/Anthropic quota would have been saved.

## 15. Rollout order

Activate the new behavior progressively.

1. Dialogue polish review
2. Listener readback / editorial / teaching review
3. Teaching-design review
4. Low-risk research routing
5. Script semantic review
6. Evidence/claim review last

Each stage advances only after its shadow/eval metrics meet the stage's acceptance criteria.

## 16. Success metrics

Record a baseline before activation.

Per podcast and per episode track:

```text
total_model_calls
review_calls
repair_calls
re_review_calls
decision_calls
A2_escalations
A3_escalations
issues_created
issues_blocking
issues_note_only
issues_dismissed
issues_reopened
repairs_per_stage
openrouter_cost
openai_subscription_calls
anthropic_subscription_calls
final_hard_failures
final_evidence_failures
published_audio_minutes
```

Primary operational metric:

```text
model_calls / published_audio_minute
```

Also track:

- repairs per accepted issue;
- new issues introduced per repair;
- percentage of candidate issues that become blockers;
- percentage of A1 issues overturned by A2/A3;
- average calls from first review to terminal decision.

## 17. Acceptance criteria

### Phase A: infrastructure

Complete when:

- authority and issue lifecycle are represented explicitly;
- existing checkpoints can resume without losing issue state;
- A0 rules remain unchanged;
- tests prove a lower authority cannot reopen a higher-authority dismissal;
- tests prove fixed-provider runs keep their existing provider semantics.

### Phase B: shadow materiality

Complete when:

- Jev materiality decisions are recorded without changing outcomes;
- real-model eval can score materiality cases;
- reports expose blocker false negatives and repairs avoided.

### Phase C: first active stage

Activate one low-risk stage only when:

- blocker false-negative rate is within the agreed threshold;
- no deterministic gate can be bypassed;
- scoped follow-up prohibits unrelated new issues;
- fallback behavior is deterministic when Jev/OpenRouter is unavailable.

### Phase D: authority escalation

Complete when:

- A1 -> A2 -> A3 escalation is checkpointed and resumable;
- A3 is terminal except for one explicitly bounded final repair;
- a repair must pass A0 before semantic acceptance;
- provider routing cannot select below minimum authority.

### Phase E: production objective

The project should demonstrate a material reduction in:

- total calls;
- repair calls;
- repeated review calls;

without regression in:

- source/evidence checks;
- teaching-quality evals;
- script validity;
- publish-time hard gates.

Do not set a target percentage until baseline data is captured.

## 18. Testing impact

Expected source changes eventually touch high-fanout modules such as `provider_pool.py`, `script_pipeline.py`, `teaching.py`, `polishing.py`, research modules and new shared schemas.

Follow `AGENTS.md` for mapped tests.

Important additions should include:

- issue-state and authority unit tests;
- lower-authority reopening tests;
- terminal A3 tests;
- scoped-follow-up tests;
- Jev unavailable/fallback tests;
- resume/checkpoint invariants;
- provider authority-floor tests;
- budget projection updates when call topology changes.

Real-model checks belong under `evals/` and must remain manual, never part of unit tests.

## 19. Implementation sequence

Recommended implementation order:

1. Add authority/issue data model with no behavior change.
2. Add shadow-only decision gate using existing Jev transport.
3. Add materiality eval and historical case builder.
4. Add metrics/reporting for candidate -> disposition -> repair outcomes.
5. Convert dialogue-polish review to active gating.
6. Enforce strict scoped follow-up there.
7. Convert teaching/editorial review.
8. Add provider routing metadata and minimum-authority enforcement.
9. Add A2/A3 escalation state machine.
10. Convert script semantic review.
11. Evaluate research-review integration separately.
12. Evaluate evidence/claim integration last.

Each step should be independently checkpointable and reversible.

## 20. Expected architectural result

The intended system is:

```text
generative reviewer
        |
        v
candidate issue
        |
        v
bounded decision gate
        |
   +----+---------+-----------+
   |              |           |
 dismiss         note       blocking
                              |
                              v
                       expert authority
                              |
                      +-------+-------+
                      |               |
                    close           repair
                                      |
                                      v
                               scoped re-check
                                      |
                                      v
                                   terminal
```

The quality philosophy becomes:

> Models may remain highly critical, but criticism no longer automatically creates work.

and:

> A repair is checked for whether it solved the issue that justified the repair, not used as an invitation to restart open-ended criticism from scratch.

This is the main mechanism expected to reduce loop-driven call growth.


## 21. Mini eval podcast: review-loop control

Add one deliberately small end-to-end regression case whose purpose is not to benchmark general podcast quality, but to test whether the new gate architecture stops unproductive review loops while still catching a real blocker.

Recommended location:

```text
evals/review_loop_mini/
  README.md
  episode.json
  teaching_plan.json
  evidence.json
  cases.json
  run.py
```

### 21.1 Topic

Use a short two-host episode:

**Why do seasons happen?**

Target length: about 6–8 spoken minutes.

This topic is suitable because it has:

- a clear causal explanation;
- a famous factual misconception;
- legitimate pedagogical repetition;
- a useful analogy that reviewers may over-criticise;
- a small evidence set;
- no need for a large research corpus.

It also overlaps conceptually with the existing teaching-quality seasons control, so the repository already has experience evaluating teaching quality on this subject. The new case serves a different purpose: review-loop termination and materiality.

### 21.2 Learning objectives

The clean reference episode should let a listener:

1. explain that Earth's axial tilt, not Earth-Sun distance, drives the seasons;
2. connect tilt to sunlight angle and day length;
3. explain why the Northern and Southern Hemispheres have opposite seasons;
4. distinguish the main mechanism from secondary facts about Earth's slightly elliptical orbit.

Use a small, explicit evidence bundle based on authoritative background already used by the teaching-quality eval, plus short original evidence summaries stored with the eval. The eval must not depend on live retrieval.

### 21.3 Controlled issue classes

Build the mini eval from one clean reference script plus deterministic mutations. Do not ask a model to invent the defects.

At minimum include these cases:

#### Clean control

No publication-blocking defect.

It should intentionally contain a few things an open-ended reviewer may be tempted to criticise:

- one deliberate pedagogical repetition of the core mechanism;
- a simple flashlight/sunlight-angle analogy;
- a conversational transition that is acceptable but not maximally concise;
- one explicit recap before the conclusion.

Expected materiality:

```text
replication_for_learning -> NOT_AN_ISSUE or NOTE_ONLY
simple_analogy           -> NOT_AN_ISSUE
wordy_transition         -> NOTE_ONLY
recap                     -> NOT_AN_ISSUE
```

The clean control is critical. If it enters a repair loop merely because a reviewer can imagine improvements, the new architecture has failed its main purpose.

#### Seeded factual blocker

Mutate exactly one segment to state the classic false mechanism, for example by making Earth-Sun distance the cause of summer.

Expected disposition:

```text
BLOCKING
```

The issue must reach expert evidence authority if the cheap gate is uncertain. It must never be downgraded to a note merely to terminate the loop.

#### Seeded reasoning blocker

Mutate one segment so that the relation between tilt and hemisphere/day length is reversed while the surrounding explanation remains plausible.

Expected disposition:

```text
BLOCKING
```

This case prevents the gate from learning only one memorised misconception.

#### Ambiguous improvement

Include one point that is genuinely debatable but not obviously publication-blocking, such as how much detail the episode should give about orbital eccentricity for the target audience.

Expected disposition:

```text
UNCERTAIN or NOTE_ONLY
```

If uncertain, the case should exercise A2 adjudication without automatically causing a repair.

### 21.4 Repair-scope case

The most important mutation should run through an actual repair.

Start from the factual-blocker version:

```text
A1 review
  -> candidate factual issue
Jev gate
  -> BLOCKING
A2 confirms
  -> repair exactly the affected segment
A0 validates
  -> scoped follow-up
```

The repaired version then contains no blocker.

The scoped follow-up must answer only whether the original issue is fixed and whether the changed material introduced a direct regression.

It must not create a new issue about an unchanged introduction, analogy, recap or transition.

This is the direct regression test for the current loop failure mode.

### 21.5 Expected outcomes

The eval should treat these as hard expectations:

| Case | Repairs expected | A2 expected | A3 expected | Publish outcome |
| --- | ---: | ---: | ---: | --- |
| clean | 0 | 0 unless gate uncertain | 0 | pass |
| factual blocker | 1 | yes | normally no | pass after repair |
| reasoning blocker | 1 | yes | normally no | pass after repair |
| ambiguous improvement | 0 | optional | 0 | pass or pass with note |

A3 should be exercised by a separate synthetic adjudication fixture rather than forcing the normal mini podcast into A3. The normal path should demonstrate that terminal escalation is exceptional.

### 21.6 Metrics

Record for every case:

```text
candidate_issues
blocking_issues
notes
dismissals
decision_calls
A1_calls
A2_calls
A3_calls
repair_calls
scoped_followup_calls
out_of_scope_new_issues
terminal_status
```

Primary assertions:

1. **zero missed seeded blockers**;
2. **zero repairs on the clean control**;
3. **zero out-of-scope new issues after the scoped repair**;
4. the repaired blocker terminates without returning to an unrestricted review;
5. A3 is not used on the normal clean or single-blocker paths.

Secondary metric:

```text
calls from first review to terminal decision
```

This gives a small, stable measure of whether the architecture actually reduces loop amplification.

### 21.7 Shadow comparison

During early implementation, run both paths against the same mini cases:

```text
legacy loop
decision-gated loop
```

Report:

- total calls;
- repair calls;
- number of unique issues created;
- number of issues created only after an earlier repair;
- final blocker status;
- final script digest.

The expected result is not merely that the decision-gated path is cheaper. It must also end with the seeded factual/reasoning defects corrected and the clean control untouched.

### 21.8 Why this eval should stay small

Do not turn this into another general podcast benchmark.

Its value comes from being:

- fast enough to run manually during architecture work;
- small enough that every expected issue is human-understandable;
- deterministic in where defects are injected;
- independent of live search;
- sensitive specifically to review-loop behaviour.

Larger real-project shadow data should validate external validity later. This mini podcast is the regression control for the state machine itself.
