"""Read completed episode checkpoints without publishing or approving them."""
from __future__ import annotations

import hashlib
import json
import re

from .errors import AppError
from .models import EpisodeScript
from .runner import manifest_path
from .scripting import script_metrics, script_review_signature
from .script_models import SeriesPlan
from .storage import digest, inside, file_hash


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
            original = json.loads(raw)
            candidate, state = original, "draft"
            try:
                polished = read(f"polishing/{identifier}/script.json")
                result = read(f"polishing/{identifier}/result.json")
                checkpoint = read(f"polishing/{identifier}/checkpoint.json")
                if (result.get("status") == "passed" and checkpoint.get("candidate") == polished
                        and result.get("original_digest") == digest(original)
                        and result.get("polished_digest") == digest(polished)):
                    candidate, state = polished, "polished"
            except (OSError, ValueError, AppError):
                pass
            if state == "polished":
                try:
                    reviewed = read(f"reviewed/{identifier}.json")
                    review = read(f"reviews/{identifier}.json")
                    checkpoint = read(f"reviews/{identifier}_checkpoint.json")
                    teaching = read(f"reviews/{identifier}_teaching.json")
                    series = SeriesPlan.model_validate(plan)
                    selected = next(e for e in series.episodes if e.episode_id == identifier)
                    signature = script_review_signature(run.get("input_hash"),
                        file_hash(inside(work, f"polishing/{identifier}/script.json")), series, selected, work)
                    if (not review.get("issues") and checkpoint.get("draft") == reviewed
                            and checkpoint.get("review") == review and teaching.get("status") == "passed"
                            and teaching.get("script_digest") == digest(reviewed)
                            and checkpoint.get("input_hash") == signature):
                        candidate, state = reviewed, "reviewed"
                except (OSError, ValueError, AppError):
                    pass
            script = EpisodeScript.model_validate(candidate)
            if script.episode_id != identifier:
                continue
            rows.append({"script": script.model_dump(), "hash": digest(script.model_dump()),
                         "metrics": script_metrics(script), "preview": True, "state": state,
                         "run_id": run["run_id"], "episode_number": number})
        except (OSError, ValueError, AppError, KeyError, TypeError):
            # One incomplete checkpoint must not hide the other readable episodes.
            continue
    return rows
