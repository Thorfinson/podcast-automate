"""Deterministic checks for outlines and dialogue scripts, plus the source context each episode sees.

Nothing here calls a model except ``checked_series_plan``, which asks for bounded repairs of an
invalid outline and checkpoints every draft before the next paid call.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import AppError
from .models import EpisodeScript
from .prompts import instructions
from .research_models import ResearchDossier
from .script_artifacts import script_metrics
from .script_models import EpisodePlan, SeriesPlan
from .storage import digest, file_hash, write_json
from .teaching import prerequisite_context

MAX_PLAN_REPAIRS = 3


def validate_plan(plan: SeriesPlan, dossier: ResearchDossier) -> list[str]:
    errors = []
    known = {f.id for f in dossier.findings}
    episode_ids = [e.episode_id for e in plan.episodes]
    if plan.topic != dossier.topic or len(episode_ids) != len(set(episode_ids)):
        errors.append("Keep the topic unchanged and episode IDs unique.")
    omitted = [item.finding_id for item in plan.omitted_findings]
    if len(omitted) != len(set(omitted)) or not set(omitted) <= known:
        errors.append("Omissions must refer to distinct known finding IDs.")
    covered, earlier = set(), set()
    for episode in plan.episodes:
        scene_ids = [s.scene_id for s in episode.scenes]
        if len(scene_ids) != len(set(scene_ids)):
            errors.append(f"{episode.episode_id}: scene IDs must be unique.")
        selected = set(episode.finding_ids)
        if not selected <= known:
            errors.append(f"{episode.episode_id}: unknown finding IDs.")
        scene_findings = {f for scene in episode.scenes for f in scene.finding_ids}
        if scene_findings != selected:
            errors.append(f"{episode.episode_id}: scenes must cover exactly the episode's findings.")
        if not set(episode.prerequisite_episodes) <= earlier:
            errors.append(f"{episode.episode_id}: prerequisites must be earlier episodes.")
        if not any(s.purpose == "worked_example" for s in episode.scenes):
            errors.append(f"{episode.episode_id}: include a worked example, not just definitions.")
        covered.update(selected)
        earlier.add(episode.episode_id)
    if covered | set(omitted) != known or covered & set(omitted):
        errors.append("Assign every finding to episodes or explain its omission, never both.")
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


def checked_series_plan(work, prompt, invoke, dossier, central_question, signature, *, allow_legacy=False):
    plan, repairs = load_plan_checkpoint(work, signature, allow_legacy=allow_legacy)
    if plan is None:
        plan = invoke(prompt, SeriesPlan, "series_plan.v4-audit")
    while True:
        errors = validate_plan(plan, dossier)
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
                      "draft": plan.model_dump()}, ensure_ascii=False), SeriesPlan, "series_plan_repair.v2")
        repairs += 1


def validate_script(script: EpisodeScript, episode: EpisodePlan, *, check_duration=True) -> list[str]:
    errors = []
    if script.episode_id != episode.episode_id or script.purpose != "deep_dive":
        errors.append("Keep the requested episode ID and deep_dive purpose.")
    scenes = {s.scene_id: s for s in episode.scenes}
    available_findings, introduced = {}, set()
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
    if covered != set(episode.finding_ids):
        errors.append("The dialogue must cover every planned finding.")
    if {s.speaker_id for s in script.segments} != {"host_a", "host_b"}:
        errors.append("Use both hosts in the dialogue.")
    metrics = script_metrics(script)
    if check_duration and metrics["estimated_minutes"] > 30:
        errors.append("The planned speech estimate exceeds 30 minutes; shorten without losing the explanation.")
    if check_duration and metrics["estimated_minutes"] < episode.target_minutes * 0.85:
        errors.append("The script delivers less than 85% of the planned duration. Develop the missing reasoning, "
                      "worked steps and consequences; do not fill the gap with repetition or longer pauses.")
    return errors


def episode_sources(episode, dossier, context, index=None):
    """Keep source context around evidence anchors, including paragraphs omitted by the dossier sampler."""
    refs = {e.reference for f in dossier.findings if f.id in episode.finding_ids for e in f.evidence}
    source_ids = {ref.split("#")[0] for ref in refs}
    documents = [source for source in context if source["source_id"] in source_ids]
    if index is not None:
        documents = [{"source_id": source.id, "title": source.title, "url": source.final_url,
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


SCRIPT_REVIEW_VERSION = "script_review.v9-gaps-notes"
# Deliberately independent of SCRIPT_REVIEW_VERSION: a review-policy bump must re-review the saved
# draft, which script_pipeline does through the versions it stores in the checkpoint, and must not
# discard the draft and its consumed repair allowance.
REVIEW_SIGNATURE_VERSION = "script_review.signature.v1"


def script_review_signature(input_hash, draft_hash, plan, entry, work):
    return digest({"input": input_hash, "draft": draft_hash, "plan": plan.model_dump(),
                "review": REVIEW_SIGNATURE_VERSION,
                   "continuity": prerequisite_context(plan, entry, work)})
