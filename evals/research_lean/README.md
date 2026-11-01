# Normal against lean research

Does a research run with far fewer sub-questions still give the story agent (D-181) what it needs to tell an episode
well and without traps? Asked by the operator on 2026-10-30, after the story agent made the script side lean: research
is now the larger cost. On the 2026-10 runs it took 1,505 calls (Orlagau v2, 52 model hours) and 2,021 calls (Asimov v2,
117 model hours), and the planner always fills its cap (`question_budget.affordable_tasks`: about 44 sub-questions for a
new project's 750 calls, whatever the topic).

## Design

- One brief ([brief.json](brief.json)): the Stanford Prison Experiment, one German episode of about 15 minutes. Its
  popular version and its archive differ (instructions to the guards, the staged breakdown, Zimbardo's double role), so
  a research that misses the critique leads the episode into a trap.
- Two projects with that brief, researched by Sonnet 5.5 high, five sub-questions at a time:
  - `normal`: at most 12 sub-questions;
  - `lean`: at most 4.
  The cap is the plan approval's own (`pla approve --research-plan --max-tasks N`): the planner cuts its plan once and
  presents it again. Everything else is the product's default.
- Each research is written by one script run with `--writer agent` (Opus 5.5 xhigh, critiques by Sonnet 5.5 high).
- The two published scripts are judged blind by the narration lab's v3 judge (Sonnet and Opus, mean of both, the judge
  the operator calibrated on 2026-10-27) and by its pair judge in both orders; a side wins a scale only when both orders
  agree.
- Beside the scores the report puts what each side cost (sub-questions, sources, findings, calls, model hours,
  reported cost, wall clock) and what the script review still found.

The brief's 15 minutes are a planning wish the plan weighs against the content
([Episode and series length](../../docs/BUSINESS_LOGIC.md#episode-and-series-length)), so each side plans its own
series and the sides compare their `ep_001`. The lean script run of 2026-10-30 was started without `--episode` and wrote
all four episodes of its outline (four sessions of 47 to 61 minutes); the normal one writes `ep_001` only.

One episode per side, as the operator asked: in the lab one agent sample varied by up to a point, so a difference below
about half a point says nothing on its own; the operator's reading decides.

## Run

```powershell
.\.venv\Scripts\python.exe evals\research_lean\run.py setup
.\.venv\Scripts\pla.exe research .studio\evals\research_lean\normal --backend claude_code --model claude-sonnet-5-5 --reasoning-effort high
.\.venv\Scripts\pla.exe approve .studio\evals\research_lean\normal --research-plan --max-tasks 12
.\.venv\Scripts\pla.exe resume .studio\evals\research_lean\normal          # plans again under the cap and stops
.\.venv\Scripts\pla.exe approve .studio\evals\research_lean\normal --research-plan
.\.venv\Scripts\pla.exe resume .studio\evals\research_lean\normal
.\.venv\Scripts\pla.exe script .studio\evals\research_lean\normal --episode ep_001 --writer agent --backend claude_code --model claude-sonnet-5-5 --reasoning-effort high
# the same for lean with --max-tasks 4
.\.venv\Scripts\python.exe evals\research_lean\run.py judge --live
.\.venv\Scripts\python.exe evals\research_lean\run.py report
```

Projects, logs, judgments and `report.json` are under `.studio/evals/research_lean/` (git-ignored). The scripts to read
are `judge/normal/draft.md` and `judge/lean/draft.md`.

## Results

2026-10-30 to 11-01, one sample per side, Sonnet 5.5 high for research, critiques and judges, Opus 5.5 xhigh for the
agent. Both sides ran on the code before the target fix of 11-01, so both agents had 1.3 times the plan as target.

| | normal (12) | lean (4) |
| --- | --- | --- |
| Planned without a cap | 41 sub-questions | 40 sub-questions |
| Research calls / model hours / reported cost | 236 / 4.6 h / 90 USD | 116 / 2.3 h / 45 USD |
| Calls per sub-question | 20 | 29 |
| Findings | 71 | 23 |
| Outline the script run planned from it | 7 episodes, 278 min | 4 episodes, 154 min |
| ep_001 planned / written | 56 min, 13 core findings / 7,466 words | 44 min, 7 core findings / 6,797 words |
| Agent session | 41 min, 92 turns, 11.0 USD, 1 critique (8/8/8) | 47 min, 127 turns, 11.6 USD, 3 critiques (8/8/7 → 8/8/8) |
| Corpus sections the agent used | 27 | 35 |
| v3 judge, Sonnet / Opus | 8/8/8 / 9/8/9 | 8/9/8 / 9/9/8 |
| v3 mean of both | 8.33 | 8.50 |
| Pair judge, both orders agreeing (Sonnet and Opus alike) | | tension, interest and overall to lean; storytelling a tie |
| First script review: blocking issues | 7 (5 grounding, 2 structure) | 10 (7 grounding, 2 structure, 1 scope) |

- The lean side's episode is at least as good by both judges and wins the pair comparisons; the difference in the mean
  (0.17) is within one sample's noise.
- Both drafts drew the same kinds of grounding issues: scenes from corpus sections without a finding to cite, a
  source's caution stated as fact, a detail no section holds. The normal side's include one carried over from its
  research (a finding that misstates Zimbardo's 2018 dating); the lean side's include a detail the review found in no
  section (who gave the guard his sunglasses; the agent had used a section on the guards' borrowed sunglasses, so this
  may be the citation gap again) and a miscounted authorship (two authors for three). Neither side avoided the traps the review catches.
- More research also made a longer series: 71 findings planned 7 episodes, 23 planned 4, for a brief that wished for
  15 minutes.
- Broad sub-questions cost more each (29 against 20 calls), so a third of the sub-questions cost half, not a third.

Not settled by one topic and one sample: the operator's reading of `judge/normal/draft.md` and `judge/lean/draft.md`
decides.
