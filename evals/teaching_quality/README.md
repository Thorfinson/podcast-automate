# Teaching-quality regression controls

The application uses `assess_teaching` for both production scripts and these controls. Expected labels are not passed to the reviewers or simulated reader. `seasons.md` is an original short teaching dialogue; the glossary control contains related terms without developing their meaning. `pilot_rejected.json` archives the actual script rejected by the user and its audience brief. All three cases run by default. No private pilot project is needed: the project argument supplies runtime and budget settings only.

Scientific background for the seasons control: [NASA, What Causes the Seasons?](https://spaceplace.nasa.gov/seasons/en/). The flashlight comparison and hypothetical planet are teaching constructions. This benchmark checks pedagogical discrimination, not source retrieval, numerical derivations, speech synthesis or actual human learning.

```powershell
# Validate and snapshot the cases without model calls:
.\.venv\Scripts\python.exe scripts\evaluate-teaching.py projects\windows-pilot --output projects\quality-evaluation

# Real subscription-backed checks, including the user-rejected pilot:
.\.venv\Scripts\python.exe scripts\evaluate-teaching.py projects\windows-pilot --output projects\quality-evaluation --live
```

Results and exact input hashes are saved under the output directory. Valid cached checks are reused; changed scripts, teaching targets or review versions invalidate the cache. A mismatch exits nonzero and must be investigated. Three controls are an initial regression set, not a measured guarantee across topics. Preserve failed results when adjusting the rubric and add held-out topics as real projects receive feedback.

The [13 September 2026 verification report](../../docs/quality-verification.md) records the current results and earlier failures. The [archived machine-readable record](results/2026-09-13-v3.json) includes the actual reader answers, editorial judgments, examiner judgments and script hashes for all three final cases.
