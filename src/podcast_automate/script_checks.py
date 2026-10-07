"""Deterministic checks for outlines and dialogue scripts, plus the source context each episode sees.

Nothing here calls a model except ``checked_series_plan``, which asks for bounded repairs of an
invalid outline and checkpoints every draft before the next paid call.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .errors import AppError
from .models import EpisodeScript
from .prompts import instructions
from .research_models import ResearchDossier
from .script_artifacts import SPOKEN_WORDS_PER_MINUTE, script_metrics
from .research_reader import source_facts
from .script_models import MAX_EPISODE_MINUTES, EpisodePlan, SeriesPlan, episode_findings
from .storage import digest, file_hash, write_json
from .teaching import prerequisite_context

MAX_PLAN_REPAIRS = 3
# v7: core and supporting findings, research limits placed in episodes, the final episode as a whole the synthesis.
SERIES_PLAN_VERSION = "series_plan.v7-core-limits"
# The repair call repeats the planning prompt, so its meaning changed with it.
SERIES_PLAN_REPAIR_VERSION = "series_plan_repair.v3-core-limits"


def validate_plan(plan: SeriesPlan, dossier: ResearchDossier, *, limit_ids=None) -> list[str]:
    """``limit_ids`` are the ids of the research limits the planner was given; without them a plan's
    research_limit_ids are not checked (an inherited plan, a plan made before research limits reached it)."""
    errors = []
    known = {f.id for f in dossier.findings}
    episode_ids = [e.episode_id for e in plan.episodes]
    if plan.topic != dossier.topic or len(episode_ids) != len(set(episode_ids)):
        errors.append("Keep the topic unchanged and episode IDs unique.")
    omitted = [item.finding_id for item in plan.omitted_findings]
    if len(omitted) != len(set(omitted)) or not set(omitted) <= known:
        errors.append("Omissions must refer to distinct known finding IDs.")
    covered, supporting, earlier = set(), set(), set()
    for episode in plan.episodes:
        scene_ids = [s.scene_id for s in episode.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            errors.append(f"{episode.episode_id}: scene IDs must be unique.")
        selected = set(episode.finding_ids)
        if not selected <= known:
            errors.append(f"{episode.episode_id}: unknown finding IDs.")
        scene_findings = {f for scene in episode.scenes for f in scene.finding_ids}
        if scene_findings != selected:
            errors.append(f"{episode.episode_id}: scenes must cover exactly the episode's core finding_ids.")
        backing = set(episode.supporting_finding_ids)
        if not backing <= known or backing & selected:
            errors.append(f"{episode.episode_id}: supporting_finding_ids must be known finding IDs and never one of "
                          "the episode's own core finding_ids.")
        supporting.update(backing)
        if limit_ids is not None and not set(episode.research_limit_ids) <= set(limit_ids):
            errors.append(f"{episode.episode_id}: research_limit_ids may only name limit_id values from research_limits.")
        if not set(episode.prerequisite_episodes) <= earlier:
            errors.append(f"{episode.episode_id}: prerequisites must be earlier episodes.")
        # The final episode of a series is as a whole its synthesis (2026-10-02): a synthesis scene that traces a case
        # through the assembled answer may stand in for its worked example. A qualitative case counts everywhere.
        finale = len(plan.episodes) > 1 and episode is plan.episodes[-1]
        if not any(s.purpose == "worked_example" or (finale and s.purpose == "synthesis") for s in episode.scenes):
            errors.append(f"{episode.episode_id}: include a worked_example scene, not just definitions: one concrete "
                          "case traced step by step through the idea; a qualitative case without numbers counts." +
                          (" In the final episode a synthesis scene that traces such a case also counts." if finale else ""))
        if not episode.series_role.strip():
            errors.append(f"{episode.episode_id}: state in series_role what this episode contributes to the answer "
                          "to the series' central question.")
        recalled = set(episode.recap_finding_ids)
        if not recalled <= covered or recalled & selected:
            errors.append(f"{episode.episode_id}: recap_finding_ids may only recall findings introduced in earlier "
                          "episodes, never the episode's own.")
        covered.update(selected)
        earlier.add(episode.episode_id)
    placed = covered | supporting
    if placed | set(omitted) != known or placed & set(omitted):
        unplaced = sorted(known - placed - set(omitted))
        named = ", ".join(unplaced[:20]) + (f" and {len(unplaced) - 20} more" if len(unplaced) > 20 else "")
        errors.append("Place every finding as core (finding_ids) or supporting (supporting_finding_ids) in an episode, "
                      "or explain its omission, never both." + (f" Not placed: {named}." if unplaced else ""))
    edges = {item: set() for item in known}
    for dependency in plan.dependencies:
        if dependency.before not in known or dependency.after not in known:
            errors.append("Dependencies must use known finding IDs.")
        else:
            edges[dependency.after].add(dependency.before)
    remaining = set(known)
    while remaining:
        ready = {item for item in remaining if not edges[item] & remaining}
        if not ready:
            errors.append("Explanation dependencies must not contain a cycle.")
            break
        remaining -= ready
    positions = {}
    for episode_number, episode in enumerate(plan.episodes):
        for scene_number, scene in enumerate(episode.scenes):
            for finding in scene.finding_ids:
                positions.setdefault(finding, (episode_number, scene_number))
    for dependency in plan.dependencies:
        if dependency.after in positions and (dependency.before not in positions or
                                             positions[dependency.before] > positions[dependency.after]):
            errors.append(f"Explain {dependency.before} before {dependency.after}.")
    return errors


def plan_dependency_conflicts(plan, dossier):
    """Locate the first introduction of each claim, including repeats in later episodes."""
    positions = {}
    for episode_number, episode in enumerate(plan.episodes, 1):
        for scene_number, scene in enumerate(episode.scenes, 1):
            for finding in scene.finding_ids:
                positions.setdefault(finding, {"episode": episode_number, "episode_id": episode.episode_id,
                    "scene": scene_number, "scene_id": scene.scene_id, "title": scene.title})
    statements = {f.id: f.statement for f in dossier.findings}
    conflicts = []
    for dependency in plan.dependencies:
        before, after = positions.get(dependency.before), positions.get(dependency.after)
        if after and (not before or (before["episode"], before["scene"]) > (after["episode"], after["scene"])):
            conflicts.append({"before": dependency.before, "after": dependency.after,
                "reason": dependency.reason, "before_statement": statements.get(dependency.before),
                "after_statement": statements.get(dependency.after),
                "before_first_introduction": before, "after_first_introduction": after})
    return conflicts


def plan_failure_message(conflicts):
    message = "Das Inhaltsverzeichnis enthält nach der automatischen Korrektur noch einen Widerspruch. "
    if conflicts:
        conflict = conflicts[0]
        before, after = conflict["before_first_introduction"], conflict["after_first_introduction"]
        if before:
            return (message + f'„{before["title"]}“ (Folge {before["episode"]}, Abschnitt {before["scene"]}) '
                    f'muss vor „{after["title"]}“ (Folge {after["episode"]}, Abschnitt {after["scene"]}) '
                    "eingeführt werden. Der Entwurf und die Prüfdetails sind gespeichert.")
        return (message + f'Die Grundlage für „{after["title"]}“ in Folge {after["episode"]} fehlt im Plan. '
                "Der Entwurf und die Prüfdetails sind gespeichert.")
    return message + "Die Zuordnung der Rechercheergebnisse oder der Aufbau ist noch ungültig. Entwurf und Prüfdetails sind gespeichert."


def load_plan_checkpoint(work, signature, *, allow_legacy=False):
    checkpoint = work / "planning_checkpoint.json"
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved.get("input_hash") == signature:
            return SeriesPlan.model_validate(saved["draft"]), saved["repairs"]
        return None, 0
    # Older runs saved structured responses but not an explicit planning checkpoint.
    # The caller has already checked the run's full input hash. Never adopt an old
    # response for an editorial revision with different instructions.
    plan, repairs = None, 0
    if allow_legacy:
        for path in sorted((work / "calls").glob("call_*/metadata.json")):
            metadata = json.loads(path.read_text(encoding="utf-8"))
            version = metadata.get("prompt_version", "")
            response = path.with_name("response.json")
            if not response.exists():
                continue
            if version.startswith("series_plan."):
                plan, repairs = SeriesPlan.model_validate_json(response.read_text(encoding="utf-8")), 0
            elif version.startswith("series_plan_repair.") and plan is not None:
                plan = SeriesPlan.model_validate_json(response.read_text(encoding="utf-8"))
                repairs += 1
    return plan, repairs


def checked_series_plan(work, prompt, invoke, dossier, central_question, signature, *, allow_legacy=False,
                        limit_ids=None):
    plan, repairs = load_plan_checkpoint(work, signature, allow_legacy=allow_legacy)
    if plan is None:
        plan = invoke(prompt, SeriesPlan, SERIES_PLAN_VERSION)
    while True:
        errors = validate_plan(plan, dossier, limit_ids=limit_ids)
        if plan.central_question != central_question:
            errors.append("Keep the project's central_question unchanged.")
        conflicts = plan_dependency_conflicts(plan, dossier)
        # Save every result before the next paid call. Resume keeps both the draft
        # and the repair count, including when quota/budget stops a correction.
        write_json(work / "planning_checkpoint.json", {"input_hash": signature,
                   "draft": plan.model_dump(), "repairs": repairs})
        write_json(work / "plan_errors.json", errors)
        write_json(work / "plan_repair_details.json", {"errors": errors, "dependency_conflicts": conflicts})
        if not errors:
            return plan
        if repairs >= MAX_PLAN_REPAIRS:
            raise AppError(plan_failure_message(conflicts), code="invalid_plan", status="blocked")
        repair = ("\n" + instructions("series_plan_repair") + "\n")
        plan = invoke(prompt + repair + json.dumps({"errors": errors, "dependency_conflicts": conflicts,
                      "draft": plan.model_dump()}, ensure_ascii=False), SeriesPlan, SERIES_PLAN_REPAIR_VERSION)
        repairs += 1


# The share of its planned minutes a script must reach (validate_script).
MIN_DURATION_SHARE = 0.85
# How far above the plan the writer's target is set, per writing model. ONLY Claude Sonnet 5.5 is measured: at high it
# wrote 71 % of the target it was shown (Transformer ep_012, 2026-10-04, each scene 64 to 75 % of its share), so a draft
# like that lands at 92 % of the plan; at 1.35 a 60-minute episode written at 75 % would already pass the hour, which
# validate_script returns as too long. Every other model is shown the plan itself until its own drafts are measured.
WRITER_TARGET_FACTORS = {"claude-sonnet-5-5": 1.3, "anthropic/claude-sonnet-5.5": 1.3}


def writer_target_factor(text_generation) -> float:
    """The target factor of the model that writes first under a run's text choice: its fixed model, or under ``auto``
    the preferred candidate's (AdapterPool.plan). Under ``auto`` the other subscription writes while the first has no
    quota, and its drafts get the first one's target."""
    selection = text_generation or {}
    if selection.get("provider") == "auto":
        selection = (selection.get("candidates") or {}).get(selection.get("prefer", "codex_cli")) or {}
    return WRITER_TARGET_FACTORS.get(selection.get("model"), 1.0)


def word_budget(episode: EpisodePlan, factor=1.0) -> dict:
    """The spoken words the writer is given for ``episode`` (prompt write_episode_length): the floor validate_script
    holds a draft to, a target ``factor`` above the planned duration (writer_target_factor; 1.0 for every model but
    Sonnet 5.5), and that target spread over the scenes by their explanation steps. The pauses are left aside, so the
    floor asks a few words more than the check needs. The hour stays with the check, not the target: capped at 59
    minutes, Sonnet's target for a one-hour episode would have left a draft at 71 % of it below the floor.

    Until 2026-10-04 the writer derived the budget from target_minutes itself, and every first draft of the Transformer
    series came in at 58 to 75 % of it. Shown the plan itself as the target, ep_012's first draft reached 71 %.

    The chapter-end recaps and reflection beats of the listenability rules (2026-10-06) share this budget: the planned
    minutes stay, so the same time carries fewer facts, which is the point. The factors are not measured under them."""
    def words(minutes):
        return math.ceil(minutes * SPOKEN_WORDS_PER_MINUTE)
    target = words(episode.target_minutes * factor)
    steps = [len(scene.explanation_steps) for scene in episode.scenes]
    return {"minimum_words": words(episode.target_minutes * MIN_DURATION_SHARE), "target_words": target,
            "scene_words": {scene.scene_id: round(target * count / sum(steps))
                            for scene, count in zip(episode.scenes, steps)}}


def validate_script(script: EpisodeScript, episode: EpisodePlan, *, check_duration=True) -> list[str]:
    errors = []
    if script.episode_id != episode.episode_id or script.purpose != "deep_dive":
        errors.append("Keep the requested episode ID and deep_dive purpose.")
    scenes = {s.scene_id: s for s in episode.scenes}
    # Findings recalled from earlier episodes and supporting ones may be cited in any scene (episode_findings).
    available_findings, introduced = {}, {*episode.recap_finding_ids, *episode.supporting_finding_ids}
    for scene in episode.scenes:
        introduced.update(scene.finding_ids)
        available_findings[scene.scene_id] = set(introduced)
    if [c.chapter_id for c in script.chapters] != list(scenes):
        errors.append("Use every planned scene, in order, as a chapter with the same ID.")
    covered = set()
    for segment in script.segments:
        scene = scenes.get(segment.scene_id)
        if scene is None or segment.chapter_id != segment.scene_id:
            errors.append(f"{segment.segment_id}: scene/chapter does not match the plan.")
        elif not set(segment.knowledge_refs) <= available_findings[scene.scene_id]:
            errors.append(f"{segment.segment_id}: use only current or previously introduced finding IDs.")
        covered.update(segment.knowledge_refs)
        if re.search(r"https?://|source_id|knowledge_refs|src_[a-f0-9]+|\[[^\]]+\]\(", segment.text):
            errors.append(f"{segment.segment_id}: citations or internal metadata must not be spoken.")
    # Only the core: supporting and recalled findings may be cited and need not be (two tiers, 2026-10-02).
    missing = [f for f in dict.fromkeys(episode.finding_ids) if f not in covered]
    if missing:
        errors.append("The dialogue must cover every planned finding.")
        errors.append("Core findings (finding_ids) not yet cited: " + ", ".join(missing) +
                      ". Supporting and recalled findings need not be cited.")
    if {s.speaker_id for s in script.segments} != {"host_a", "host_b"}:
        errors.append("Use both hosts in the dialogue.")
    metrics = script_metrics(script)
    if check_duration and metrics["estimated_minutes"] > MAX_EPISODE_MINUTES:
        errors.append(f"The planned speech estimate exceeds {MAX_EPISODE_MINUTES} minutes; shorten without losing "
                      "the explanation.")
    if check_duration and metrics["estimated_minutes"] < episode.target_minutes * MIN_DURATION_SHARE:
        # With the numbers since 2026-10-03: told only "less than 85%", the Transformer writer added about a tenth
        # per correction and fell short three times in a row.
        pauses = sum(s.pause_after_ms for s in script.segments) / 60_000

        def words_for(minutes):
            return math.ceil((minutes - pauses) * SPOKEN_WORDS_PER_MINUTE)
        errors.append(f"The script delivers less than {MIN_DURATION_SHARE:.0%} of the planned duration: {metrics['words']} "
                      f"spoken words make about {metrics['estimated_minutes']:.1f} of {episode.target_minutes:g} planned "
                      f"minutes at {SPOKEN_WORDS_PER_MINUTE} words per minute. Write at least "
                      f"{words_for(episode.target_minutes * MIN_DURATION_SHARE)} words, about "
                      f"{words_for(episode.target_minutes)} for the full plan. Develop the missing "
                      "reasoning, worked steps and consequences; do not fill the gap with repetition or longer pauses.")
    return errors


# Words of one source an episode may quote verbatim (the user's choice, 2026-10-01). The rule held for the dossier
# until then; an assembled dossier keeps every verified answer with its excerpts, and what is broadcast quotes sparingly.
QUOTED_WORDS_PER_SOURCE = 25
# Consecutive words the spoken text must share with one passage to count as quoted: shorter runs are common phrases.
QUOTE_RUN = 6


def spoken_words(text):
    return re.findall(r"\w+", text.lower())


def quotation_errors(script: EpisodeScript, sources: list[dict]) -> list[str]:
    """Per source, the words of the dialogue that repeat one of its passages verbatim in runs of at least QUOTE_RUN
    words; more than QUOTED_WORDS_PER_SOURCE from one source is a defect the writer fixes in their own words. A
    translated quote is not verbatim and is not counted; ``sources`` are the passages the writer was given."""
    runs = {}
    for document in sources:
        for section in document["sections"]:
            words = spoken_words(section["text"])
            for start in range(len(words) - QUOTE_RUN + 1):
                runs.setdefault(tuple(words[start:start + QUOTE_RUN]), set()).add(document["source_id"])
    quoted, example = {}, {}
    for segment in script.segments:
        words = spoken_words(segment.text)
        covered = {}
        for start in range(len(words) - QUOTE_RUN + 1):
            for source_id in runs.get(tuple(words[start:start + QUOTE_RUN]), ()):
                covered.setdefault(source_id, set()).update(range(start, start + QUOTE_RUN))
        for source_id, positions in covered.items():
            quoted[source_id] = quoted.get(source_id, 0) + len(positions)
            example.setdefault(source_id, (segment.segment_id, " ".join(words[min(positions):max(positions) + 1])))
    return [f"{source_id}: the dialogue repeats {count} words of this source verbatim (for example in "
            f"{example[source_id][0]}: \"{example[source_id][1][:160]}\"); quote at most {QUOTED_WORDS_PER_SOURCE} words "
            "of a source per episode and say the rest in the hosts' own words."
            for source_id, count in sorted(quoted.items()) if count > QUOTED_WORDS_PER_SOURCE]


def planning_dossier(dossier: ResearchDossier) -> dict:
    """The dossier as the series plan reads it. An assembled dossier holds every verified answer, 800 000 characters
    and more on the runs of 2026-10-01: the plan reads it without the excerpts and claim contracts, since it assigns
    findings by what they state, and each episode's writing reads its findings whole. A composed dossier stays as it was."""
    data = dossier.model_dump()
    if dossier.assembled:
        data.pop("source_assessments", None)
        data["findings"] = [{key: value for key, value in finding.items()
                             if key not in {"evidence", "claim_contract", "supporting_contracts"}}
                            for finding in data["findings"]]
    return data


def research_limits(gate: dict, dossier: ResearchDossier) -> list[dict]:
    """The limits the research run noted for the script, each with the findings it concerns where the gate names them.

    research_quality.render_quality tells the user the script states them ("das Skript benennt sie"), but until
    2026-10-02 the script lane read only ``passed`` and the hashes of research_quality_gate.json. Read here: each
    requirement's source limits and noted limits, the completeness objections noted as limits, the objections kept
    after two reworks (their task's findings) and the follow-up assessment's script notes. The gate is a hashed
    output of the completed research run, so the run inputs' ``research_run`` already binds it; a gate written
    before these keys existed yields no limits."""
    known = {f.id for f in dossier.findings}
    rows = {}

    def add(text, finding_ids=(), question=None):
        if not isinstance(text, str) or not text.strip():
            return
        row = rows.setdefault(text.strip(), {"text": text.strip(), "finding_ids": []})
        row["finding_ids"] = list(dict.fromkeys([*row["finding_ids"], *(f for f in finding_ids if f in known)]))
        if question and "question" not in row:
            row["question"] = question
    for requirement in gate.get("requirements") or []:
        # A requirement kept as a recorded limit (nothing it rests on changed since its last verdict) is a limit like a
        # source limit; one with nothing listed as missing is named by its reason.
        limited = requirement.get("source_limit") or requirement.get("recorded_limit")
        missing = (requirement.get("missing") or [requirement.get("reason")]) if limited else []
        items = [*missing, *(requirement.get("noted") or [])]
        for item in items:
            add(item, requirement.get("finding_ids") or [], requirement.get("question"))
    for item in gate.get("noted_limits") or []:
        add(item)
    for row in gate.get("noted_after_reworks") or []:
        prefix = f"{row.get('task_id')}__"
        add(row.get("objection"), [f.id for f in dossier.findings if f.id.startswith(prefix)])
    for item in gate.get("script_notes") or []:
        add(item)
    return [{"limit_id": f"limit_{number:03d}", **row} for number, row in enumerate(rows.values(), 1)]


def episode_limits(plan: SeriesPlan, entry: EpisodePlan, limits: list[dict]) -> list[dict]:
    """The research limits this episode states, each once in the series: those the plan placed here, and one the plan
    placed nowhere at the first episode whose core or supporting findings it concerns, else at the final episode,
    whose synthesis names the limits that remain. ``finding_ids`` keeps the affected findings this episode cites."""
    placed = {key for episode in plan.episodes for key in episode.research_limit_ids}
    cited = set(episode_findings(entry))
    rows = []
    for limit in limits:
        if limit["limit_id"] in placed:
            here = limit["limit_id"] in entry.research_limit_ids
        else:
            owner = next((episode for episode in plan.episodes
                          if set(limit["finding_ids"]) & {*episode.finding_ids, *episode.supporting_finding_ids}),
                         plan.episodes[-1])
            here = owner.episode_id == entry.episode_id
        if here:
            rows.append({"text": limit["text"], **({"question": limit["question"]} if limit.get("question") else {}),
                         "finding_ids": [f for f in limit["finding_ids"] if f in cited]})
    return rows


def episode_sources(episode, dossier, context, index=None):
    """Keep source context around evidence anchors, including paragraphs omitted by the dossier sampler."""
    cited = set(episode_findings(episode))
    refs = {e.reference for f in dossier.findings if f.id in cited for e in f.evidence}
    source_ids = {ref.split("#")[0] for ref in refs}
    documents = [source for source in context if source["source_id"] in source_ids]
    if index is not None:
        # Type, authors and date let the script attribute an idea to its author and name the year of an old source.
        documents = [{"source_id": source.id, "title": source.title, "url": source.final_url,
                      **({"authors": source.authors} if source.authors else {}), **source_facts(source),
                      "source_assessment": next((a.model_dump() for a in dossier.source_assessments if a.source_id == source.id), None),
                      "extraction_coverage": source.extraction_coverage.model_dump() if source.extraction_coverage else None,
                      "total_sections": len(source.sections), "sections": [
                          {"reference": f"{source.id}#{s.id}", "text": s.text, "page": s.page} for s in source.sections]}
                     for source in index.sources if source.id in source_ids]
    words = set(re.findall(r"\w{5,}", (json.dumps(episode.model_dump(), ensure_ascii=False) + " " +
        " ".join(f.statement for f in dossier.findings if f.id in episode.finding_ids)).lower()))
    allowance = max(1600, 120_000 // max(len(documents), 1))
    result = []
    for source in documents:
        sections = source["sections"]
        anchors = {i for i, s in enumerate(sections) if s["reference"] in refs}
        neighbors = {i + delta for i in anchors for delta in (-1, 1)} - anchors
        ranked = sorted(range(len(sections)), key=lambda i: (
            0 if i in anchors else 1 if i in neighbors else 2,
            -sum(word in sections[i]["text"].lower() for word in words), i))
        chosen, used = set(), 0
        for i in ranked:
            if i in anchors or used + len(sections[i]["text"]) <= allowance:
                chosen.add(i)
                used += len(sections[i]["text"])
        result.append({**source, "sections": [s for i, s in enumerate(sections) if i in chosen]})
    return result


def outline_hash(work: Path) -> str:
    """Bind human approval to the plan and its research/configuration snapshot."""
    return digest({name: file_hash(work / name) for name in
                   ("series_plan.json", "knowledge_model.json", "inputs.json", "script_request.json")})


# v11: core and supporting findings, research limits back a stated limit, framing checked by episode_framing alone.
# v12: a segment that follows its section where the finding misstates it is source_corrected, not drift.
# v13: a point the review files as an advisory stays one unless it concerns evidence or scope
# (script_pipeline.STRICT_CATEGORIES); the prompt is unchanged.
# v14 (2026-10-06): the listenability rules, and a wall of facts (no arc, no breathing beats, the expert holding the
# floor, measured in dialogue_shape) goes back as a dialogue point.
SCRIPT_REVIEW_VERSION = "script_review.v14-listenability"
# Versions whose saved verdict still stands when it blocked nothing: v12 and v13 only stop blocking, so an episode an
# earlier version passed is not reviewed again; one it blocked is, under today's version, before the next repair. v14
# adds only a dialogue point, which stops nothing once the repairs are spent (NOTED_CATEGORIES), so a v13 pass stands
# too: an episode in flight is not reworked for it, while every draft written from now on is held to it.
RELAXED_REVIEW_VERSIONS = frozenset({"script_review.v11-core-limits", "script_review.v12-source-corrected",
                                     "script_review.v13-reviewer-advisories"})
# Deliberately independent of SCRIPT_REVIEW_VERSION: a review-policy bump must re-review the saved
# draft, which script_pipeline does through the versions it stores in the checkpoint, and must not
# discard the draft and its consumed repair allowance.
REVIEW_SIGNATURE_VERSION = "script_review.signature.v1"


def script_review_signature(input_hash, draft_hash, plan, entry, work):
    return digest({"input": input_hash, "draft": draft_hash, "plan": plan.model_dump(),
                "review": REVIEW_SIGNATURE_VERSION,
                   "continuity": prerequisite_context(plan, entry, work)})
