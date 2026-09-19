Archived record of 19 September 2026. The findings and the plan were carried out; the current state is in [quality-audit-2026-09-19-implementation.md](../quality-audit-2026-09-19-implementation.md).

# Podcast Studio quality audit, 19 September 2026

Read-only audit of how well Podcast Studio supports and demonstrates the skills needed for accurate, insightful, understandable and engaging deep-dive audio. No code, settings, prompts or project files were changed; no paid generation was started. The sample series and the code citations reflect the working tree on 19 September 2026, including uncommitted changes. Written for the operator of Podcast Studio.

## A. Executive summary

Podcast Studio is a local, evidence-bound pipeline for German two-host deep-dive series. Its strongest asset is source discipline: every finding carries verbatim anchors into stored source sections, every spoken segment carries finding references, and the four traceability probes run for this audit all matched the stored source text. The teaching layer is also real. The reviewed episodes build a worked example through an entire mechanism, the second host makes deductions and objections rather than prompting, and the 130 words-per-minute estimate matched measured audio within three percent.

The most consequential weaknesses:

- **A false evidence gap reached the listener.** Episode 3 tells the listener that the sources do not give DeepSeek-V3's load-balancing rule. The stored V3 source contains that rule verbatim. The dossier, the open-questions file and the script all inherited the gap, and no review could catch it because reviews only see the passages they are handed.
- **The self-evaluation cannot discriminate on the defects a human editor finds.** The quality report shows 114 of 114 model checks passing for this series. Yet an editorial note in the project flagged two dense passages that the polishing review had passed, and the false gap above went undetected.
- **Series-level redundancy and hedging.** Core terms are re-defined in five or six of six episodes, and disclaimers that an example is hypothetical appear three to nine times per episode. Both contradict the user's own brief.
- **No control over speech delivery.** Text is sent verbatim to both speech engines. There is no pronunciation table, no delivery instruction, no pause policy, no back-transcription and no way to re-render one segment.

Limits of this review: the auditor did not listen to the audio, so vocal delivery is unverified. The one full sample series was produced on 13 to 16 September with the previous research pipeline, and the current question-level research pipeline has not been exercised on a real topic in the repository. Output ratings describe that one series.

## B. Scope and evidence

**What was inspected.** Product documentation and spec (`README.md`, `SPEC.md`, eleven files under `docs/`), all 52 prompt texts under `src/podcast_automate/prompts/`, the browser UI, and the source modules for research, scripting, teaching, polishing, review, speech and audio assembly. Three read-only code investigations produced file-and-line citations that were spot-checked before use.

**Intended audience and formats, established from evidence.** The product targets one private listener, the operator, who is a curious adult without specialist prerequisites but wants university-level depth. It produces nonfiction, two-host explanatory deep-dive series in German, with episodes capped at 30 minutes and series length derived from the topic. Production is AI-generated with human gates at plan approval, script reading and audio approval. Straight audiobook narration and interviews are outside the product's scope, and those criteria are marked not applicable.

**Samples.** One complete series, "Die Entwicklung der Transformer-Architektur" (`projects/die-entwicklung-der-transformer-architek-13325a`), six episodes, all with exported MP3s. Episodes 1, 3, 4 and 6 were read in full, episode 2 in the polishing before-and-after excerpts, and episode 5's opening. The archived user-rejected pilot on energy-based models (`evals/teaching_quality/pilot_rejected.json`) was also read. Sample size is one series and one rejected pilot. Generalization beyond machine-learning topics is not supported by this evidence.

| Episode | Words | Estimated minutes | Measured minutes | Host A word share | Hedging disclaimers |
|---|---|---|---|---|---|
| ep_001 | 3802 | 29.5 | 29.6 | 74 % | 9 |
| ep_002 | 3045 | 23.7 | 23.4 | 69 % | 3 |
| ep_003 | 3330 | 25.8 | 26.4 | 64 % | 4 |
| ep_004 | 2461 | 19.1 | 18.8 | 72 % | 6 |
| ep_005 | 2891 | 22.5 | 23.1 | 74 % | 4 |
| ep_006 | 2644 | 20.5 | 20.9 | 67 % | 5 |

**Tests actually performed.**

| ID | Input or sample | Expected | Observed | Evidence | Verdict |
|---|---|---|---|---|---|
| T1 | Both test suites | Green | Python 460 run, 1 skipped, 137 s; Node 68 pass | Runner output | Pass |
| T2 | Four spoken claims traced to stored source sections | Text matches | 8 heads, 2048 selected tokens, 20+20 layers with cache from final encoder states, frozen model plus KL loss all present verbatim | `models/source_index.yaml` | Pass |
| T3 | Declared gap in ep_003 about the V3 bias rule | Stored source lacks the rule | Section `sec_6891d807643ea0ef` contains "decrease the bias term by γ if overloaded, increase it if underloaded" | Same file; `research/dossier.yaml` f13 | Fail |
| T4 | Factual segments without references | None | 0 of 238 segments lack references except one greeting | `episodes/*/script.yaml` | Pass |
| T5 | Cross-episode repetition scan | Terms defined once | Token and Query/Key/Value re-defined in 6 of 6 episodes; KV-Cache, Inference, Training, Softmax in 5 of 6; no exact duplicate sentence within any episode | Scan over `script.yaml` | Fail |
| T6 | Sentence-length statistics | Spoken-length sentences | Average 11 to 12 words, 90th percentile 17 to 18, longest 31 | Same | Pass |
| T7 | Loudness, peak, silence on ep_001 and ep_004 | −16 LUFS, no clipping, no dead air | −16.2 LUFS integrated, peak −1.7 dBFS, flat factor 0, no silence over 1.5 s | FFmpeg ebur128, astats, silencedetect | Pass |
| T8 | Duration estimate versus measured audio | Within 10 % | Within 3 % on all six episodes | `reports/script_quality.yaml`, ffprobe | Pass |
| T9 | Polishing review versus editorial note on ep_002 segments 031 and 032 | Review flags dense passages | Review passed spoken_language citing exactly those segments; the rewrite is light rewording | `runs/run_20260913_182611_981976_0b7a6dfc/polishing/ep_002/review.json`, `editorial/ep_002_polishing_review.md` | Fail |
| T10 | Limited-evidence case, ep_006 on a model card only | Honest hedging, no invention | Claims attributed to the model description; caveats dominate the episode; research used 5 of 40 calls and 1 of 3 search rounds | `reports/research_quality.json`, ep_006 | Partial |
| T11 | Repair loop influence | Findings drive revisions | ep_002 polish caught an invented "previous episode covered translation" claim and repaired it; ep_005 needed two polish repairs | `polishing/ep_002/issues.json`, quality report | Pass |

**Unverified.** Vocal delivery, pronunciation and pacing. Conflicting-source handling on real output, since this legacy dossier has no synthesis relations. The current question-level research pipeline on a real run. Human listening review, which every report records as not done.

## C. Ten-skill audit matrix

| Skill | Product support | Observed output quality | Evidence and rationale | Proposals |
|---|---|---|---|---|
| A. Research discipline | Implemented | 3 | Verbatim-anchor, reference-existence, per-source quote limits and finding-coverage validators are code, not prompts (`research.py:146-162`, `research_evidence.py:36-59`, `script_evidence.py:12-33`). Four of four traces matched (T2). Source claims, interpretation and limits are typed per finding (`evidence_models.py:22-27`). Against that: one false gap spoken as fact (T3), all eight sources are vendor or primary papers with no independent assessment, and coverage was partial on five of six questions when the series was planned. | P-01, P-02, P-03 |
| B. Analytical depth | Implemented | 4 | ep_003 separates available capacity from executed work, load from communication, and explains what a controlled comparison can and cannot show. ep_004's rank-2048-versus-2049 thought experiment and its linear-versus-quadratic reasoning are genuine analysis. Counterarguments are limited to "vendor comparisons do not isolate causes" because no critical sources were retrieved. | P-02 |
| C. Audience and editorial judgment | Implemented | 3 | The brief's prior knowledge is honored; backpropagation is not re-taught. But the brief asks for no repeated definitions and for limits named once, while T5 shows re-definitions in nearly every episode and hedging three to nine times per episode. | P-04, P-05, P-11 |
| D. Storytelling and structure | Implemented | 4 | Each episode opens with a concrete question inside 90 seconds, resolves it in the last chapter and hands off to the next. The finale connects the series' decisions. The cross-episode series review did not run on this series (`complete_series_review: false`), so progression across episodes is model-unverified. Opening monologues run to 157 words. | P-11, P-12 |
| E. Writing for the ear | Partial | 3 | Sentence statistics are good (T6). Abbreviations are introduced before use. But dense abstract chains survive polishing (T9), disclaimers repeat, and names such as "V3.2-Exp", "V3.1-Terminus", "H800" and French phrases are delivery risks with no spoken-form control. | P-05, P-06, P-07 |
| F. Teaching through examples | Implemented | 4 | A worked-example scene is deterministically required (`script_checks.py:46-47`). "My feet hurt" is carried through the whole 2017 model; Mara's letter is reused across episodes 2 to 6 with controlled variants. Analogy limits are stated, sometimes too often. Examples are schematic rather than measured, which the scripts say honestly. | P-05 |
| G. Vocal delivery | Partial | Unverified | Technical checks pass (T7). No pronunciation table, no delivery instruction sent to either engine (`speech.py:137`, `qwen_worker.py:145`), model-chosen pauses without guidance (`models.py:101`, `audio.py:120-125`), no retry, no back-transcription, no per-segment re-render, no MP3 chapter markers. Every report carries `human_listening_reviewed: false`. | P-07, P-08, P-09 |
| H. Co-host dialogue | Implemented | 4 | Host B predicts and corrects herself: "Dann war mein Schluss zu schnell" in ep_006, "Moment" in ep_003 and ep_004. Host A answers the actual objection. Word share 64 to 74 percent for Host A, no fake surprise found. Hosts are never named, and in closings Host B sometimes speaks like a second expert. | P-13 |
| I. Editing and revision | Partial | 3 | Separate reviews exist for evidence, teaching, editorial, spoken language and audio, and findings do drive bounded repairs (T11; `script_pipeline.py:295-320`, `polishing.py:106-118`). But revision is whole-episode only (`studio.py:513`, `studio_worker.py:119`), there is no diff view, no protected passages, no deterministic repetition or contradiction check, and the polishing review passed passages an editor rejected (T9). | P-06, P-08, P-10, P-12 |
| J. Self-evaluation and consistency | Partial | 3 | Prompt versions, hash-bound checkpoints, 460 tests and three real-model regression cases are solid engineering. But the report shows 100 percent pass on a series with two confirmed defects, reviewer caveats are never consumed, no listener-feedback capture exists, and evals are three hand-picked cases with no held-out topic. | P-10, P-16 |

Not applicable: audiobook narration criteria and real-interview criteria. Interviews are simulated dialogue between two editorial roles, never presented as real interviews or quotations, which is the correct behavior.

## D. Concrete output examples

**What works: a substantive second host.** In ep_001, Host A asks whether changing only the Value at "feet" would change the attention weights. Host B answers: "Nein. In die Vergleiche gehen weiterhin dieselbe Query und dieselben Keys ein. Also bleiben die Attention scores und die daraus berechneten Attention weights gleich. Aber beim Addieren trägt feet jetzt mit demselben Anteil einen anderen Inhalt bei." That is a prediction from the mechanism, not a cue for the next definition.

**What works: an example that carries the limit.** In ep_004 the same entry is placed at rank 2048 and then at rank 2049: "Bei Rang zweitausendachtundvierzig kann dieser Eintrag direkt beitragen. Bei Rang zweitausendneunundvierzig kann er es bei diesem Zugriff nicht. Welche Antwort daraus entsteht, haben wir in unserem Gedankenexperiment nicht gemessen." The example makes a hard threshold concrete and states exactly what it does not show.

### Failure 1: a false gap spoken as fact (ep_003)

Original: "Die vorliegenden Passagen nennen eine Geschwindigkeit für die Aktualisierung einer Bias-Größe, also einer veränderbaren Verschiebung. Sie liefern aber nicht die Regel, die diese Größe mit beobachteter Last und anschließender Auswahl verbindet. Wir können deshalb nicht belegt verfolgen, wie unser überlasteter Experte dadurch weniger Anforderungen erhält."

Improved: "Die Regel ist einfach. Nach jedem Trainingsschritt wird die Last jedes Experten über den ganzen Batch beobachtet. War ein Experte überlastet, wird seine Bias-Größe um einen festen Betrag gesenkt. War er unterlastet, wird sie um denselben Betrag erhöht. Diese Größe fließt nur in die Auswahl ein. Wie stark ein ausgewählter Experte am Ergebnis beteiligt ist, bestimmt weiterhin die ursprüngliche Passung. Für unseren überlasteten Experten heißt das: Nach dem nächsten Schritt sinkt seine Bias-Größe, und er wird beim nächsten Vergleich seltener ausgewählt."

Explanation: every sentence in the improved version is supported by stored section `sec_6891d807643ea0ef` of the DeepSeek-V3 report, including the point that the gating value still comes from the original affinity score. The rewrite must not be pasted into the script. It requires updating finding f13 through the research pipeline so that references remain intact. The fact that the passage was stored but never read is the defect P-01 addresses.

### Failure 2: a dense passage that passed polishing (ep_002, segments 031 and 032)

Original: "Schauen wir dafür in den Vergleich hinein. Er multipliziert die entsprechenden Komponenten von Query und Key miteinander und addiert die Produkte. Das ist ein Skalarprodukt. Nun ist jede Komponente des rückprojizierten Key eine lineare Kombination der kompakten Komponenten. Eine einzelne kompakte Komponente kann dadurch an mehreren Stellen im großen Key auftauchen. Beim anschließenden Vergleich bestimmt die Query, wie stark diese Stellen eingehen. Wir können die Rechnung deshalb auch von dieser einen kompakten Komponente aus betrachten: Wie viel trägt sie über all diese Stellen insgesamt zum Vergleich bei? Fassen wir das für jede kompakte Komponente zusammen, erhalten wir eine angepasste Query im gemeinsamen kompakten Raum."

Improved:

Aiden: "Schauen wir in den Vergleich hinein. Er nimmt Query und Key, multipliziert Komponente für Komponente und addiert alles. Das ist ein Skalarprodukt. Jetzt der entscheidende Punkt: Den großen Key der Schubladen-Position gibt es gar nicht mehr als gespeicherte Zahlenliste. Gespeichert ist nur ihr kompakter Zustand. Der große Key wäre eine gelernte, beim Zugriff feste lineare Umrechnung davon."

Sohee: "Dann steckt in jeder Komponente des großen Keys ein Stück von jeder kompakten Komponente, mit festen Faktoren."

Aiden: "Genau. Und weil beides linear ist, dürfen wir umgruppieren. Statt den Key erst groß zu rechnen und dann mit der Query zu vergleichen, rechnen wir die Query einmal mit denselben Faktoren in den kompakten Raum um. Dann vergleichen wir diese angepasste Query direkt mit dem gespeicherten kompakten Zustand. Das Ergebnis ist dasselbe Skalarprodukt. Der gespeicherte Zustand bleibt unangetastet, und die Umrechnung der Query passiert einmal pro Zugriff, nicht für jede frühere Position."

Explanation: the meaning, the linearity argument and the "same result" claim are unchanged and are all present in the original segments 031 to 033. The rewrite anchors the abstraction to the running example, gives the partner one restating turn so the key step is heard twice in different words, and replaces "von dieser einen kompakten Komponente aus betrachten" with the operational move "Query einmal umrechnen". No fact is added.

### Failure 3: repeated disclaimers (ep_001)

Within one episode the script says "Wir verändern hier nur ein Gedankenbeispiel; das ist kein beobachteter Modelllauf", later "Sie beschreibt keine gemessene Spezialisierung", later "kein ausgelesenes Ergebnis eines bestimmten Netzes", and later "Wir legen damit keine konkrete Tokenzerlegung eines Modells fest."

Improved: state once, when the sentence example is introduced: "Alles, was wir an diesem Satz durchspielen, ist ein Gedankenbeispiel. Gemessene Werte eines echten Modells sind es nicht." Then drop the later repeats and refer back with "in unserem Beispiel".

Explanation: uncertainty is preserved, because the listener hears the status of the example once at the point where it matters. The brief itself asks that limits be named once.

## E. Prioritized improvement backlog

### P-01 Verify every spoken "the sources do not say" claim against the full stored corpus

- Problem: the script tells the listener that evidence is missing when the stored source contains it.
- Evidence: T3; `research/dossier.yaml` f13; `research/open_questions.md`; ep_003 chapter 3.
- Listener impact: the listener is taught a false limit on a topic they asked about explicitly, which is worse than an omission.
- Likely cause (confirmed): the legacy discovery run read sampled sections of a 652-section source, the gap text propagated unchanged from dossier to teaching plan to script, and the script review is told to judge only against the passages it is handed.
- Recommended change: add a deterministic gap probe. Before planning and again before script review, extract each declared gap's key terms, run the existing lexical corpus search over every stored section, and write `gap_probes.json` with hits. Any gap with hits is routed back into the question reader as a read task instead of being spoken. Add to `script_review.txt`: "For every spoken statement that the sources do not explain or contain something, a gap_probe entry with zero corpus matches must be supplied. Without it, treat the statement as unsupported and return a grounding correction that names the matching section."
- Change location: code in the research question lane and the script review payload, plus that prompt line.
- Acceptance test: re-running the Transformer project produces a gap probe that finds `sec_6891d807643ea0ef`, finding f13 is updated with that reference, and no spoken segment in ep_003 claims the rule is missing.
- Priority: P0. Effort: medium, assuming the existing reader search can be called outside a model turn.
- Risk: more reader calls per gap; bound them with the existing step limits.

### P-02 Require and label independent sources for effect claims

- Problem: all eight sources are the vendor's papers, a model card and one textbook; there is no independent evaluation or critique, and the pipeline has no source-type taxonomy or independence requirement at discovery.
- Evidence: `research/source_candidates.yaml`; primary-source flag enforced only in supplementary searches at `question_answering.py:198`; concentration advisories gate nothing (`research_evidence.py:111-120`).
- Listener impact: the story of "what changed and why it helps" is told from the vendor's narrative, mitigated only by spoken attribution.
- Likely cause: confirmed for the sample.
- Recommended change: add to `research_discovery.txt`: "For every empirical or performance claim family, include at least one source not authored by the organization making the claim, or state in limitations that none was found." Add a deterministic advisory in the quality report listing claim families with vendor-only support, and require the series plan to assign each such family a spoken attribution once per episode.
- Change location: prompt, research quality report, plan validation.
- Acceptance test: quality report lists vendor-only families; each episode attributes them at least once; a topic with known third-party evaluations yields at least one independent source.
- Priority: P1. Effort: small to medium.
- Risk: some 2026 model claims have no independent test yet, so the honest outcome is the limitation, not a forced source.

### P-03 Validate the current research gate on a real run of this topic

- Problem: the sample series was planned with five of six questions partial and `complete_topic_coverage: false`, using 5 of 40 calls and 1 of 3 search rounds. Under the current code, coverage gaps block publishing at `research_quality.py:76-80` and `research.py:498`, but no real run in the repository demonstrates this.
- Evidence: `reports/research_quality.json`; the research-evidence eval is offline and synthetic by its own README.
- Listener impact: without validation, the next series may again be built on partial coverage or, conversely, block forever.
- Likely cause: hypothetical; the gate is new.
- Recommended change: a validation task. Re-run research for the Transformer topic with default limits, record calls, blocks and the resulting coverage, and compare the new dossier's gaps with T3-style probes.
- Change location: evaluation.
- Acceptance test: the run either passes with all six questions answered and no false gaps, or blocks with specific, corpus-verified gaps.
- Priority: P0 as a validation. Effort: small in engineering, moderate in subscription usage.

### P-04 Stop re-defining established terms across episodes

- Problem: Token, Query/Key/Value, KV-Cache, Inference, Training and Softmax are re-introduced in five or six of six episodes.
- Evidence: T5.
- Listener impact: a listener who has followed the series hears the same micro-definitions every episode, which the brief explicitly forbids.
- Likely cause (confirmed): `continuity.json` carries prior examples and objectives but no term registry, and no deterministic check exists.
- Recommended change: build an `established_terms` list per episode from earlier episodes' teaching plans, pass it in `prerequisite_context`, and add to `continuity.txt`: "Terms in prerequisite_context.established_terms were defined in earlier episodes. Use them without redefinition. At most one short recall clause per term per episode, only where the listener needs it." Add a deterministic warning that counts definition sentences for established terms and surfaces them as a series-review warning.
- Change location: code and prompt.
- Acceptance test: on a regenerated series, no established term is defined more than once per episode after episode 1, and the counter reports zero blocking cases.
- Priority: P1. Effort: small.

### P-05 Cut repeated hedging to one statement per example

- Problem: disclaimers that an example is hypothetical occur three to nine times per episode.
- Evidence: T5 counts; example 3 in section D.
- Listener impact: the disclaimers interrupt the reasoning and signal insecurity rather than rigor.
- Likely cause (confirmed): `write_episode.txt`, `continuity.txt` and `plain_language.txt` all demand distinguishing hypothetical from measured, and nothing limits frequency.
- Recommended change: add to `write_episode.txt` and `dialogue_polish.txt`: "Mark an invented example or thought experiment as illustrative exactly once, when it is introduced. Do not repeat that it is not a measured model run; later references say 'in unserem Beispiel'." Add to `dialogue_polish_review.txt` under spoken_language: "Repeated reminders that an example is hypothetical are a spoken_language failure." Add a deterministic counter over a small regex list that warns above two per episode.
- Change location: prompts and a check.
- Acceptance test: regenerated episodes contain at most one status statement per example; counter reports at most two per episode.
- Priority: P1. Effort: small.

### P-06 Make the polishing review examine the densest passages

- Problem: the review passed spoken_language on ep_002 segments 031 and 032, which the project's own editorial note rejected as dense.
- Evidence: T9.
- Listener impact: passages that need restructuring reach audio unchanged.
- Likely cause (confirmed): the review may choose its own evidence and quoted a mild rewording as proof.
- Recommended change: add to `dialogue_polish_review.txt`: "Before judging spoken_language, name the three most demanding passages of the candidate by segment ID. For each, state in one sentence what the listener must hold in mind and which earlier sentence resolves each pronoun or abstract noun. If any passage needs more than three unresolved referents, or a referent has no resolving sentence, fail spoken_language and give the correction." Enforce deterministically that the three named segments exist and are not the greeting or sign-off. Archive the ep_002 passage as a regression case in `evals/dialogue_polishing/`.
- Change location: prompt, validator, evaluation.
- Acceptance test: the archived passage fails spoken_language under the new prompt; the seasons control still passes.
- Priority: P1. Effort: small.

### P-07 Prepare spoken forms and back-check the audio

- Problem: text goes verbatim to the engines with no pronunciation table, and nothing checks the rendered words.
- Evidence: `speech.py:137`, `qwen_worker.py:145`; `SPEC.md` promises a central pronunciation profile that does not exist; `speech_quality_verified: false` in every report.
- Listener impact: mispronounced English terms, French phrases, version strings like "V3.2-Exp" and hardware names can undermine trust at exactly the technical moments.
- Likely cause: confirmed absence.
- Recommended change: a per-project spoken-form table mapping written term to spoken form, applied only at synthesis time and included in the cache key; a deterministic pre-synthesis report listing digits, abbreviations, non-German tokens and version strings for the operator to review; and an optional local back-transcription using a speech-recognition model in the existing TTS environment that reports word-level mismatches per segment.
- Change location: audio production and interface.
- Acceptance test: the difficult-names probe text renders with all table entries applied; back-transcription flags a deliberately dropped sentence; a human listener confirms the flagged terms on two episodes.
- Priority: P1. Effort: medium, assuming a Whisper-class model runs in `.venv-tts`.
- Tradeoff: back-transcription adds render time and is itself fallible, so it must flag, not block.

### P-08 Re-render one segment without re-running the script pipeline

- Problem: fixing one mispronounced word requires a text revision that rewrites the whole episode, re-runs teaching, polishing and all reviews, and then a full audio run.
- Evidence: `studio.py:513`, `studio_worker.py:119`; no segment-level action exists in `web/app.js`.
- Listener impact: small audio fixes are so expensive that they will not be made.
- Recommended change: a "spoken-form override" per segment that changes only what the engine hears, keeps the reviewed script text and hash, records the override in `audio_review.yaml`, and re-synthesizes only that segment through the existing per-segment cache. Wording changes that alter meaning still go through revision.
- Change location: interface, audio production.
- Acceptance test: one override costs one synthesis call, the episode is reassembled, and the report shows the override receipt.
- Priority: P1. Effort: medium.

### P-09 Apply a pause policy at assembly

- Problem: pauses are model-chosen per segment with no instruction, and the same value applies whether the next segment is the same speaker, a speaker change or a chapter break.
- Evidence: `models.py:101`, `audio.py:120-125`; ep_002 pauses range 250 to 650 ms; chapter boundaries in the ep_001 timeline get 0.7 s.
- Listener impact: dense explanatory audio needs breathing room at idea boundaries; this is a delivery risk, unverified by listening.
- Recommended change: deterministic minimums applied at assembly, for example 250 ms within a speaker, 450 ms at a speaker change and 900 ms at a chapter boundary, overriding smaller model values, with the values configurable per project.
- Change location: audio production.
- Acceptance test: timeline shows the minimums; a human listener compares one chapter before and after.
- Priority: P1. Effort: small.

### P-10 Show reviewer caveats and dismissed reader gaps to the human reader

- Problem: the reading page shows the script and open blocking issues only. Reviewer limitations, such as "setzt stellenweise Vorwissen voraus", and reader gaps the examiner dismissed as out of scope never reach the operator.
- Evidence: `web/app.js:476-477`; `teaching.py:473-477` turns only required gaps into issues; no code consumes the limitations lists.
- Listener impact: the human gate cannot focus on what the models themselves were unsure about.
- Recommended change: on the reading page, list per episode the limitations from evidence, editorial and polishing reviews and every dismissed gap with its reason.
- Change location: interface.
- Acceptance test: the page for ep_001 shows the five evidence-review limitations recorded in `reports/script_quality.yaml`.
- Priority: P1. Effort: small.

### P-11 Shorten and split cold opens

- Problem: first segments run to 157 words and pack series orientation, the example and two questions into one 70-second monologue.
- Evidence: `ep_001_seg_001`, `ep_004_seg_001`; timeline shows 70 s.
- Listener impact: the first minute is the hardest place to lose a listener.
- Recommended change: add to `episode_framing.txt`: "Keep the first spoken segment under 90 words. Put the series orientation and the episode question in separate turns; the partner may voice the episode question." Add a deterministic warning above 100 words in the first segment.
- Change location: prompt and check.
- Acceptance test: regenerated first segments are under 90 words and the episode question is spoken by the second turn.
- Priority: P1. Effort: small.

### P-12 Run the series review on this series and add a cross-episode repair path

- Problem: `complete_series_review: false` for the sample; a failed series review has no repair prompt and requires a new script run.
- Evidence: `reports/script_quality.yaml`; `series_review.py:99-135`; `docs/scripts.md`.
- Listener impact: contradictions or lost deferred questions across episodes are the errors a single-episode review cannot see.
- Recommended change: first, a validation task that runs the existing series review on the published six scripts and records its warnings. Then a bounded repair that targets only the episodes and segments named in a failing check, reusing the existing review-repair prompt with the series finding as the issue.
- Change location: workflow, evaluation.
- Acceptance test: the series review report exists for the sample; a seeded contradiction between two episodes produces a failing check that the repair resolves in at most two calls.
- Priority: P1. Effort: medium.

### P-13 Give the hosts names and label transcripts by role

- Problem: hosts are never named in six episodes, and the transcript labels speakers with voice preset names such as "Sadaltager".
- Evidence: `exports/ep_001/*/transcript.md`; `episode_framing.txt` forbids invented biographies but not names.
- Recommended change: optional host names in the brief; use them in greetings and transcript labels; fall back to "Host A" and "Host B".
- Priority: P2. Effort: small.

### P-14 Embed chapters in the MP3 and fix show-note details

- Problem: chapters exist only in `chapters.json`; show notes for ep_001 list "Attention is All you Need" twice under different URLs and carry no chapter timestamps.
- Evidence: `audio.py:151-167`; `episodes/ep_001/show_notes.md`.
- Recommended change: write ID3 chapter frames from the timeline; de-duplicate sources by work identity; add timestamps.
- Priority: P2. Effort: small.

### P-15 Add a relative duration bound

- Problem: only the absolute 30-minute cap and an 85 percent floor exist; an episode planned at 12 minutes could deliver 29 without objection, and the conservative estimate is never used.
- Evidence: `script_checks.py:188-192`.
- Recommended change: warn above 120 percent of target.
- Priority: P2. Effort: small.

### P-16 Keep a project-level lessons file and grow the evals

- Problem: human feedback and reviewer limitations live only inside one run; nothing feeds prior corrections into future runs, and the real-model eval has three cases with no held-out topic.
- Evidence: `scripting.py:124`; `evals/teaching_quality/README.md`.
- Recommended change: a `style_notes.md` per project that the operator edits and that is prepended to writing and polishing prompts; archive the ep_002 dense passage and the ep_003 false gap as fixed eval cases; add one held-out topic outside machine learning.
- Priority: P2. Effort: medium.

## F. Recommended implementation order

**Immediate corrections:** P-01 gap probe, P-03 real-run validation of the research gate, P-05 hedging rule, P-04 term registry, P-06 densest-passage rule. These are mostly prompt lines and small validators, and together they address the two confirmed defects and the brief violations.

**Workflow and capability improvements:** P-07 spoken forms and back-transcription, P-08 per-segment override, P-09 pause policy, P-10 caveats on the reading page, P-11 cold-open rule, P-12 series review and repair, P-02 independence requirement.

**Ongoing evaluation and regression prevention:** P-16 lessons file and eval growth, P-13 to P-15 refinements, and the human listening protocol described below.

## G. Verification plan

Retest the immediate corrections by regenerating the Transformer series from its existing research with the new prompts and comparing three counts before and after: definition sentences for established terms in episodes 2 to 6, hedging statements per episode, and first-segment word counts. The ep_002 dense passage and the ep_003 false gap are the fixed regression cases. Run both test suites and the three eval sets after every prompt-version bump, and record the prompt hash in the results file as the evals already do.

Verify P-01 by re-running research on this topic and checking that the V3 bias rule appears as a finding with a reference into section `sec_6891d807643ea0ef`. Verify P-02 by inspecting the vendor-only advisory in the quality report. Verify P-07 with the existing audio probe extended by a paragraph containing "j'ai mal aux pieds", "DeepSeek-V3.2-Exp", "H800", "Kullback-Leibler" and three multi-digit numbers.

Checks that require a human listener: pronunciation of the terms above, whether pauses at chapter boundaries feel right, whether the two voices remain consistent across a 30-minute episode, and where attention drops. A one-page listening sheet per episode with timestamps for confusion, loss of interest and mispronunciation, stored next to `audio_review.yaml`, would give P-16 real data. Nothing in the current reports substitutes for this, and the reports say so honestly.

**Three next actions with the strongest justification from this audit:**

1. Re-run research on the Transformer topic with the current pipeline and probe every declared gap against the stored corpus. This validates the new gate on a real run and directly tests the false-gap defect, which is the most serious finding.
2. Have a human listen to episodes 1 and 4 with the listening sheet. Vocal delivery is the largest unverified area, and every proposal about audio depends on that evidence.
3. Apply the three prompt lines for hedging, established terms and densest-passage review, regenerate one episode, and measure the counts. This is the smallest intervention that addresses the brief violations found in every episode.
