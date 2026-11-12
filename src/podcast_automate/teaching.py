"""What a script run's episodes know of the episodes before them, and the version names their inputs carry.

Until D-184 (2026-11-01) this module designed each episode's lesson before the writing prompt wrote it (the teaching
design, its review and repairs) and checked the dialogue against that design afterwards (listener readback, editorial
and teaching review). The story agent, the only writer since, plans each episode itself; its teaching stage collects
the story material and an orientation sheet instead (script_pipeline.ScriptRun.teaching).
"""
from __future__ import annotations

from .editorial import terminology

# Part of every script run's inputs (scripting.run_inputs): the values stay, so the hashes of runs that wait for their
# outline's approval or for a resume stay as they were.
TEACHING_VERSION = "teaching.v3"
DESIGN_VERSION = "teaching_design.v2"
# Stored in every script-review checkpoint: bumping it makes a resumed in-flight run re-run the review of its saved
# draft (draft and repair count are kept). That is intended whenever a composed fragment such as episode_framing.txt
# changes meaning, or the rule of which review points block (v6, D-210: a stopped review is judged again by today's
# follow_up_scope instead of repairing a point its review had already let go).
EDITORIAL_REVIEW_VERSION = "editorial_review.v6-residual"


def prerequisite_context(plan, entry, work):
    """The earlier episodes this one builds on, as the script review reads them: never a future episode.

    Only the nearest prerequisite, the latest earlier episode this one names as its own, comes in full with its outline.
    Every other prerequisite comes as a summary: its question, series_role and findings (2026-10-02: Asimov's finale
    builds on every earlier episode and got 474,000 characters of context). The rows depend only on the plan. What an
    earlier episode actually said, the story agent reads in its published text (story_agent.earlier_episodes)."""
    previous = {}
    for candidate in plan.episodes:
        if candidate.episode_id == entry.episode_id:
            break
        previous[candidate.episode_id] = candidate
    required = set(entry.prerequisite_episodes)
    pending = list(required)
    while pending:
        identifier = pending.pop()
        if identifier in previous:
            for prerequisite in previous[identifier].prerequisite_episodes:
                if prerequisite not in required:
                    required.add(prerequisite)
                    pending.append(prerequisite)
    direct = [identifier for identifier in previous if identifier in entry.prerequisite_episodes]
    full = set(direct[-1:])
    rows = []
    for identifier, earlier in previous.items():
        if identifier not in required:
            continue
        if identifier in full:
            rows.append({"episode_id": identifier, "title": earlier.title, "status": "outline_only",
                         "outline": earlier.model_dump()})
        else:
            rows.append({"episode_id": identifier, "title": earlier.title, "status": "outline_only",
                         "context": "summary", "central_question": earlier.central_question,
                         **({"series_role": earlier.series_role} if earlier.series_role else {}),
                         "finding_ids": list(earlier.finding_ids)})
    return rows


def project_terminology(config):
    """The terminology rule for this project's topic and language (editorial.terminology)."""
    return terminology(config.language, config.topic, getattr(config, "central_question", ""))
