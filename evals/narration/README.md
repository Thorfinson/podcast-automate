# Narration evaluation

Real-model checks of the narration stage (D-180, [narration plan](../../docs/specs/2026-10-23-narration-plan.md)),
run by hand. Every part is offline by default, where it only checks its cases and inputs, and spends the chosen
subscription with `--live` (default: Claude Code, Sonnet 5.5, high).

```
python evals/narration/run.py recall --output .studio/evals/narration --live
python evals/narration/run.py calibrate projects/<orlagau> --output .studio/evals/narration --live
python evals/narration/run.py compare projects/<orlagau> --output .studio/evals/narration --live --episodes ep_014,ep_002 --score
```

Keep the output outside the repository (`.studio/` is local): `compare` copies the project once per variant.

## recall

[`cases/recall.json`](cases/recall.json) holds eight verified findings whose cited sections tell more than the finding.
The review of 2026-10-23 found them by hand in the research runs of 2026-10-07 (Asimov v2, Ontologies v2) and in
Orlagau: Milgram's subjects sweating and fumbling cigarettes, the Mule's "two of him", Seldon's 1.7 % chance of being
executed, the Second Foundation revealing itself, the revoked fact ranked above its replacement, 99 % of deleted faces
recovered, Opus annotating for 0.26 USD a character, Jutta's divorce and priory. Each case runs
`narration.story_material` on that one finding and its cited sections (one call); a case is found when a story item's
text or anchor holds one of its `expect_any` words. Offline it checks that the cited sections are readable and still
hold the case's `section_terms`.

One finding per call is easier than an episode's dozen: a found case shows the extraction can keep such material, not
that it keeps all of it in a whole episode.

## calibrate

The narration review, asked as the pipeline asks it, on published Orlagau scripts (default ep_001 to ep_004, the ones
the user heard). Two calls per episode: story material and review. Expectations from
[`cases/orlagau_reference.json`](cases/orlagau_reference.json): the user found none of them really good, so
storytelling stays at 6 or below; each episode gets at least one story point (`compressed_story`, `unused_material` or
`missing_story`); an episode with three or more counted workshop phrases gets a `workshop_talk` point. The model
reading of 2026-10-21 and what it found missing per episode are in the same file for a side-by-side reading.

## compare

Each variant is an editorial revision (`pla script --revise <episode>`) in its own copy of the project, so the project
and its recordings stay untouched. The copy keeps the research run and the published script run; `inputs/` and
`sources/` are hard-linked, the rest is copied.

| Variant | What runs |
| --- | --- |
| A | The published script (no run). |
| B | The pipeline before the narration stage, with a storytelling instruction as revision feedback: what a style note or feedback alone does. |
| C | The new pipeline with the narration review left out: story material, core story and story rules. |
| D | The new pipeline. |
| N | The new pipeline with a detail no source holds ("412 witnesses at the Wartburg") added to the narrated draft. The evidence review must name it; `caught` records whether a review issue concerns a segment carrying it. |

A revision inherits the published plan, so the core limit of the series plan is not part of this comparison. The
scripts of A to D go into `blind/<episode>/Fassung_<letter>.md` under shuffled letters, with the key in
`blind/<episode>_key.json`; read or listen before opening the key. `--score` also runs the narration review on every
blind script (two calls each), with the D run's story material.

A revision makes about 15 to 25 calls; four variants of two episodes and the scores are about 150. A stopped run
resumes when the part is called again with the same output.

## Limits

A model's score is not a listener's. A found recall case does not show the episode tells the story well, and a caught
negative control does not show every invented detail is caught. The expectations are one user's listening note and one
model reading, not a measured listener test.
