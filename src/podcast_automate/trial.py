"""Trial project („Probelauf“, D-157): the whole pipeline on a narrow topic for little money and time.

A trial project is an ordinary project whose brief carries ``trial: true``. Two things make it small:

- ``storage.load_project`` keeps each research limit at the lower of the project's (or the workspace settings') value
  and ``TRIAL_LIMITS``, so the workspace settings never lift a trial. A raise for one run (``pla approve``, a stop
  card, a pre-approval) still works as for every run.
- The brief plans one short episode: ``target_total_minutes`` is at most ``TRIAL_MINUTES``.

The research plan has a cap of its own (``plan_cap``, applied by ``QuestionResearch.planning_allowance``): at most
``TRIAL_SUB_QUESTIONS``. The call limit then leaves room instead of setting the plan's size: the completed runs measured
16.4 to 23.4 calls per sub-question, so three take up to about 70, discovery, planning, an advisor call and the closing
calls about 15 more, and ``TRIAL_LIMITS.model_calls`` adds a margin above that, so a trial does not stop on its limit
(a first version sized the limit to fit exactly three sub-questions at 16 calls, which left no room).
"""
from __future__ import annotations

from .models import ResearchLimits, TopicBrief

TRIAL_SUB_QUESTIONS = 3
TRIAL_MINUTES = 20.0
# Per run, research and script alike. The call limit also carries one episode's script run: its lower bound is 11
# calls (table of contents, nine per episode, series review), and the completed runs measured 31 to 42.5 calls per
# episode (2026-10-02). Search rounds and sources: the 18-question runs of 2026-09-26/27 needed 31 and 46 rounds and 82
# and 114 sources, about 2.5 rounds and 6 sources per sub-question. 45 USD covers a trial's research (about 85 calls at
# the measured 0.37 USD of Claude Sonnet 5.5) and its one-episode script run (about 44 calls at 0.83 USD); a run on Opus
# 5.5 may reach it and stops with cost_limit_reached, from which a stop card offers a raise. The money limit only ever
# lowers one the user set (capped_limits).
TRIAL_LIMITS = ResearchLimits(model_calls=110, search_rounds=16, sources=40, cost_usd=45.0)
# A narrow, well-documented topic that one episode can explain, for a trial created without a topic of its own.
TRIAL_TOPICS = {"de-DE": "Wie entsteht ein Regenbogen?", "en-US": "How does a rainbow form?"}


def capped_limits(limits: ResearchLimits) -> ResearchLimits:
    """Each limit the lower of ``limits`` and ``TRIAL_LIMITS``.

    A money limit is lowered, never introduced: unset it refuses every billed call (``cost_limit_required``), the
    strictest setting there is, and setting one stays the user's decision (D-146)."""
    cost = None if limits.cost_usd is None else min(limits.cost_usd, TRIAL_LIMITS.cost_usd)
    return ResearchLimits(model_calls=min(limits.model_calls, TRIAL_LIMITS.model_calls),
                          search_rounds=min(limits.search_rounds, TRIAL_LIMITS.search_rounds),
                          sources=min(limits.sources, TRIAL_LIMITS.sources), cost_usd=cost)


def trial_brief(config: TopicBrief, *, sample_topic: bool = False) -> TopicBrief:
    """The brief of a new trial project: ``trial`` set and at most ``TRIAL_MINUTES`` planned (unset means
    ``TRIAL_MINUTES``). ``sample_topic`` replaces topic and central question with the narrow sample topic of the
    brief's language (``TRIAL_TOPICS``).

    ``pla init --trial`` and the Studio call it before ``init_project``; the limits need nothing here, since
    ``storage.load_project`` caps them for every brief with ``trial`` set."""
    update = {"trial": True, "target_total_minutes": min(config.target_total_minutes or TRIAL_MINUTES, TRIAL_MINUTES)}
    if sample_topic:
        update.update(topic=TRIAL_TOPICS[config.language], central_question=TRIAL_TOPICS[config.language])
    return TopicBrief.model_validate({**config.model_dump(mode="json"), **update})


def plan_cap(config: TopicBrief, cap=None):
    """The most sub-questions a research plan of this brief may hold: a trial's own cap, below any requested one;
    for other briefs the requested cap unchanged (None for none)."""
    if not config.trial:
        return cap
    return min(cap, TRIAL_SUB_QUESTIONS) if cap else TRIAL_SUB_QUESTIONS


def trial_facts() -> dict:
    """What a trial project is limited to, for ``pla init --trial`` and the Studio to show."""
    return {"sub_questions": TRIAL_SUB_QUESTIONS, "target_total_minutes": TRIAL_MINUTES,
            "limits": TRIAL_LIMITS.model_dump(mode="json")}
