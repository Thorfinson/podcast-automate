# Narration workshop

> Archive since 2026-11-01 (D-184): the lab's best process became the story agent (`story_agent.py`, D-181), the
> only writer, and the writing prompt, polishing and the teaching design it compared against were removed. `run.py`
> went with them; it is in git history (commit `f6d51cd`). The cases, prompts and results below stay as the
> evidence of D-181.

Real-model checks of how the script pipeline tells a story, on fixed ten-minute cases, run by hand
(`run.py --help`). The user's goal (2026-10-26): tune the prompts on one short piece until it reaches 10/10/10, then
scale up to an episode, three episodes and a series.

## Cases

Each case is one episode of about ten minutes set into the real outline of its project, in place of the episode it
is cut from, so series context and prerequisites stay real:

| Case | Project | Replaces | Story |
| --- | --- | --- | --- |
| `kuchavez` | Orlagau v2 | `ep_002` | Thietmar's report of the armour, the duel and the hanged men of Zwenkau; Hengst's and Billig's readings |
| `milgram` | Asimov v2 | `ep_007` | Milgram's 1963 obedience experiment, its result and the dispute over its explanation |

`cases/<case>.md` is the element list the user approved: what the sources tell, by weight (M must, S should, K
can), each with its source anchor, and the traps a telling must avoid. It is the yardstick of coverage, not a plan
of the episode.

## Stages

1. **0a Material** (done by hand on 2026-10-26): which M elements reach the findings. Kuchavez 2.5 of 10 in the
   findings, 10 of 10 with the story research; Milgram about 7.5 of 13, without the drama, and with a detail of a
   later experiment variant (the groan at 75 volts) mixed into the 1963 setup.
2. **0b Input ladder** (`ladder`): the same pipeline from three inputs, I1 findings only, I2 as now, I3 every
   section of the element list attached as evidence. It shows how much of the gap the research causes.
3. Writing, then the narration review and rework, then the teaching design, then the whole chain: one change per
   round against the best variant so far, each variant written two or three times, decided by a blind pair
   comparison and the user's reading.

## Judging

`judge_coverage.txt` scores a text against the element list (1 told, 0.5 mentioned, 0 missing; traps committed).
`judge_quality.txt` (v3, 2026-10-27) rates tension, interest and storytelling on anchored scales up to 10 and names
only the weak spots that cost points; `rejudge` asks it by Sonnet and Opus and averages both. Until v3 one prompt
was judge and writer critique at once: its list of up to eight points to 10 filled up and held every score at 8 or
below. The critique a writer gets keeps that list (`critique_quality.txt`). Neither is the pipeline's own narration
review, so the pipeline is not tuned to please its reviewer. The user's verdict decides; a model's 10 alone does not
count.

## Variants

`variants/<name>/` holds a variant's prompts, outline overrides, `process.json` or, for the agentic writer, its
`TASK.md`. Only the variants the work builds on are versioned (agent6, narrative13 and craft, whose design run
narrative13 writes on); the other experimental variants stay local (`.gitignore`).

## Output

`<output>/<case>/projects/<input>/` is the run's copy of the project (inputs, sources, models and the research run
hard-linked), `ladder/<input>/` the kept samples (`draft.md` to read, `metrics.json`), the design (`plan.md`) and
the story material, `judge/` the saved judgments and `report.md` the tables.
