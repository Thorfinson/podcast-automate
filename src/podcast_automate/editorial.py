"""Shared language and teaching requirements for generation and independent reviews."""
import re

from .prompts import fragment

EPISODE_FRAMING = fragment("episode_framing")


def episode_series_context(plan, entry):
    """Use the full approved order, also when only one episode is being generated."""
    index = next(i for i, episode in enumerate(plan.episodes) if episode.episode_id == entry.episode_id)
    following = plan.episodes[index + 1] if index + 1 < len(plan.episodes) else None
    return {"topic": plan.topic, "central_question": plan.central_question,
            "explanation_path": plan.explanation_path,
            "episode_path": [{"episode_id": episode.episode_id, "title": episode.title,
                              "central_question": episode.central_question,
                              **({"series_role": episode.series_role} if episode.series_role else {})}
                             for episode in plan.episodes],
            "episode_number": index + 1, "episode_count": len(plan.episodes),
            "is_first": index == 0, "is_last": index == len(plan.episodes) - 1,
            "next_episode": {"episode_id": following.episode_id, "title": following.title,
                             "central_question": following.central_question} if following else None}


# The terminology rule every prompt composed until 2026-10-02, kept byte for byte for the callers that still
# compose it: research receipts bind their prompt text (research_patches.cached_call), so a changed rule would stop
# a resumed research run at its first replayed call. It names machine-learning terms in every call, also for the
# Asimov series (history, sociology); new prompts compose ``terminology`` instead.
TERMINOLOGY = fragment("terminology")
# The same rule without a field's own terms, and the machine-learning names only a machine-learning topic gets.
TOPIC_TERMINOLOGY = fragment("terminology_rule")
MACHINE_LEARNING_TERMS = fragment("terminology_machine_learning")
MACHINE_LEARNING = re.compile(r"\b(?:machine learning|maschinell\w* lern\w*|deep learning|neural network\w*|"
                              r"neuronal\w* netz\w*|transformer\w*|LLMs?|large language models?|language models?|"
                              r"sprachmodell\w*)\b", re.IGNORECASE)


def terminology(language, *subjects):
    """The terminology rule for a project: topic-neutral, plus the established English machine-learning names when
    ``subjects`` (its topic and central question) name a machine-learning topic written in another language. An
    English project keeps those names anyway; a history or sociology project never hears of Query and Key."""
    english = (language or "").split("-")[0].lower() == "en"
    machine_learning = any(MACHINE_LEARNING.search(text or "") for text in subjects)
    return TOPIC_TERMINOLOGY + (MACHINE_LEARNING_TERMS if machine_learning and not english else "")


TEACHING_SCOPE = fragment("teaching_scope")

CONTINUITY = fragment("continuity")

# How a dialogue lets the listener breathe and want the next answer: one big idea, an arc whose answer is held back,
# chapter-end recaps, reflection beats, short turns and a partner who speaks about a third (2026-10-06: listeners of
# three finished series found the episodes "Fakten, Fakten, Fakten"; the expert spoke 70 to 87 % of the words). For
# writing, polishing, its comparison and the script review; the teaching design plans the arc in its own fields.
LISTENABILITY = fragment("listenability")
