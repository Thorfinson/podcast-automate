"""Shared language and teaching requirements for generation and independent reviews."""
from .prompts import fragment

EPISODE_FRAMING = fragment("episode_framing")


def episode_series_context(plan, entry):
    """Use the full approved order, also when only one episode is being generated."""
    index = next(i for i, episode in enumerate(plan.episodes) if episode.episode_id == entry.episode_id)
    following = plan.episodes[index + 1] if index + 1 < len(plan.episodes) else None
    return {"topic": plan.topic, "central_question": plan.central_question,
            "explanation_path": plan.explanation_path,
            "episode_path": [{"episode_id": episode.episode_id, "title": episode.title,
                              "central_question": episode.central_question} for episode in plan.episodes],
            "episode_number": index + 1, "episode_count": len(plan.episodes),
            "is_first": index == 0, "is_last": index == len(plan.episodes) - 1,
            "next_episode": {"episode_id": following.episode_id, "title": following.title,
                             "central_question": following.central_question} if following else None}


TERMINOLOGY = fragment("terminology")

TEACHING_SCOPE = fragment("teaching_scope")

CONTINUITY = fragment("continuity")
