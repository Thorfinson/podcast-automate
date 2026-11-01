"""Read completed episode checkpoints without publishing or approving them."""
from __future__ import annotations

import hashlib
import json
import re

from .errors import AppError
from .models import EpisodeScript
from .runner import manifest_path
from .script_artifacts import run_rate
from .scripting import script_metrics, script_review_signature
from .script_models import SeriesPlan
from .script_checkpoints import NOTED_CATEGORIES
from .storage import digest, inside, file_hash
from .story_agent import story_entry


def _strings(values):
    return [value for value in values if isinstance(value, str) and value.strip()]


def _rows(values, keys):
    return [{key: row.get(key) for key in keys} for row in values if isinstance(row, dict)]


def review_notes(episode):
    """What the reviews said but nothing acted on: caveats, dismissed gaps, advisories.

    Reviews record limitations of their own verdict, the examiners dismiss gaps with a
    reason, and the advisory counters observe patterns the prompts ask against. None of
    it blocks, so until now none of it reached the person reading the script.
    """
    if not isinstance(episode, dict):
        return {}
    teaching = episode.get("teaching_review") or {}
    groups = {
        "script_review": _strings((episode.get("model_review") or {}).get("limitations") or []),
        "teaching_review": _strings((teaching.get("review") or {}).get("limitations") or []),
        "editorial_review": _strings((teaching.get("editorial") or {}).get("limitations") or []),
        "dialogue_polish": _strings(((episode.get("dialogue_polish") or {}).get("review") or {}).get("limitations") or []),
        "dismissed_gaps": _rows(episode.get("dismissed_gaps") or [], ("stage", "objective_id", "gap", "reason")),
        "advisories": _rows(episode.get("advisories") or [], ("code", "count", "detail", "segment_ids", "params")),
        # What the narration review (D-180) left open, and the story the material does not hold.
        "narration": _rows([*((episode.get("narration") or {}).get("notes") or []),
                            *((episode.get("narration") or {}).get("research_points") or [])],
                           ("category", "fix", "problem", "correction", "segment_ids")),
    }
    notes = {name: rows for name, rows in groups.items() if rows}
    scores = (episode.get("narration") or {}).get("scores")
    if isinstance(scores, dict) and scores:
        notes["narration_scores"] = {key: value.get("score") for key, value in scores.items()
                                     if isinstance(value, dict) and isinstance(value.get("score"), int)}
    return notes


def script_previews(root, run):
    if not run or run.get("kind") != "script" or run.get("status") == "completed":
        return []
    work = manifest_path(root, run["run_id"]).parent

    def read(relative):
        data = json.loads(inside(work, relative).read_bytes())
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        return data

    try:
        plan = read("series_plan.json")
        request = read("script_request.json")
    except (OSError, ValueError, AppError):
        return []
    rows = []
    for number, entry in enumerate(plan.get("episodes") or [], 1):
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("episode_id")
        if not isinstance(identifier, str) or not re.fullmatch(r"ep_[a-z0-9_]+", identifier):
            continue
        if request.get("episode") and identifier != request["episode"]:
            continue
        try:
            raw = inside(work, f"drafts/{identifier}.json").read_bytes()
            stamp = read(f"drafts/{identifier}.checkpoint.json")
            if stamp.get("sha256") != hashlib.sha256(raw).hexdigest():
                continue
            candidate, state = json.loads(raw), "draft"
            try:
                # The story agent's draft is reviewed as written (D-181): "reviewed" once its review left nothing that
                # blocks. Until 2026-11-01 the state was reachable only after polishing, so an agent's episode stayed
                # "draft" in the preview.
                reviewed = read(f"reviewed/{identifier}.json")
                review = read(f"reviews/{identifier}.json")
                checkpoint = read(f"reviews/{identifier}_checkpoint.json")
                series = SeriesPlan.model_validate(plan)
                selected = story_entry(work, next(e for e in series.episodes if e.episode_id == identifier))
                signature = script_review_signature(run.get("input_hash"),
                                                    file_hash(inside(work, f"drafts/{identifier}.json")), series,
                                                    selected, work)
                blocking = [issue for issue in review.get("issues") or []
                            if not isinstance(issue, dict) or issue.get("category") not in NOTED_CATEGORIES]
                if (not blocking and checkpoint.get("draft") == reviewed and checkpoint.get("review") == review
                        and checkpoint.get("input_hash") == signature):
                    candidate, state = reviewed, "reviewed"
            except (OSError, ValueError, AppError, StopIteration):
                pass
            script = EpisodeScript.model_validate(candidate)
            if script.episode_id != identifier:
                continue
            rows.append({"script": script.model_dump(), "hash": digest(script.model_dump()),
                         "metrics": script_metrics(script, run_rate(work)), "preview": True, "state": state,
                         "run_id": run["run_id"], "episode_number": number})
        except (OSError, ValueError, AppError, KeyError, TypeError):
            # One incomplete checkpoint must not hide the other readable episodes.
            continue
    return rows
