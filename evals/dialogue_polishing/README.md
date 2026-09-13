# Live dialogue-polishing check

The [13 September 2026 record](2026-09-13.json) contains the actual original excerpt, rewritten dialogue, four-criterion comparison, deliberately false control and call metadata. The excerpt is the user's selected logarithm passage with its immediate neighbors from the German pilot. It assumes that energy, weights and normalization were introduced earlier; it is not a stand-alone introduction to the subject.

The production `polish_dialogue` function generated and compared the positive case in two Codex calls. The expert now handles the explanation; the partner develops a relevant doubt that the following answer addresses. The comparison accepted meaning, completeness, speaker roles and spoken language, with quotations from the actual texts.

For the negative control, an added assertion wrongly says that changing a parameter leaves all other weights unchanged and guarantees a higher probability for the observation. One further call to the same production comparison rejected this as a change of meaning. The false candidate was supplied by the test; no model was asked to repair it. An earlier control attempt was stopped by the duration validator before any comparison call and is not counted as a model result.

All three calls used the Codex subscription path. These are small development checks, not a held-out benchmark, a source review or a human listening test. The rest of the production pipeline still checks source support and teaching quality after polishing. A role assignment does not establish expertise, and a model comparison does not prove that listeners will understand the result.

Local readable views and raw call files are under `projects/dialogue-polish-check/`. The canonical pilot and its approved audio inputs were not changed. Automated regression tests for interruption, bounded repairs, fabricated evidence, changed inputs and audio gating are in `tests/test_polishing.py`.
