"""A second opinion on a blocked research question before the run stops for the operator.

When a question blocks, one advisor call reads what the ledger knows about it: the unmet criteria, the
review's objections, the sources read, the downloads that failed and why, and the run's limits. It may
search the web itself. It names the cause in plain German and recommends a new attempt, an explicit
gap or a higher limit, with a concrete hint (works, free copies, other search routes).

A recommended new attempt starts automatically, at most ``MAX_AUTO_RETRIES`` times per question. Every
other decision stays with the operator, who sees the advice next to the question with the hint already
filled in. The advice is search guidance, never evidence: whatever the new attempt finds still passes
the independent review.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import Contract, NonEmpty
from .text_settings import DEFAULT_CLAUDE_MODEL

ADVICE_VERSION = "block_advice.v1"
MAX_AUTO_RETRIES = 1
# The advisor's own setting: Opus 5.5 at its deepest level where the run already uses the Claude subscription.
ADVISOR_EFFORT = "xhigh"


class AdvisedSource(Contract):
    title: NonEmpty
    url: str
    note: str


class BlockAdvice(Contract):
    diagnosis: NonEmpty
    recommendation: Literal["retry", "accept_gap", "raise_limit"]
    limit: Literal["sources", "search_rounds", "model_calls", "none"]
    hint: str
    sources: list[AdvisedSource] = Field(max_length=5)


def advisor_selection(selection):
    """The advisor's text choice. A run on the Claude subscription asks Opus 5.5 at xhigh; the automatic
    choice already prefers it; another provider the user chose is kept."""
    if selection and selection.get("provider") == "claude_code":
        return {**selection, "model": DEFAULT_CLAUDE_MODEL, "reasoning_effort": ADVISOR_EFFORT}
    return selection


def block_key(row):
    """One advice per block: a new attempt, automatic or explicit, is a new block to advise on."""
    return f"{row.get('retries', 0)}.{row.get('auto_retries', 0)}"


def advice_request(spec, row, sources, failures, limits):
    """What the advisor reads, as plain data. ``sources`` and ``failures`` are the run's; ``limits`` its state."""
    return {"question": spec.question, "kind": spec.kind, "acceptance": spec.acceptance, "queries": spec.queries,
            "block": {"outcome": row.get("outcome"), "reason": row.get("reason", ""), "feedback": row.get("feedback", []),
                      "web_searches": row.get("web_attempts", 0), "local_searches": len(row.get("search_receipts", [])),
                      "sections_read": len(row.get("read_refs", [])), "explicit_retries": row.get("retries", 0),
                      "automatic_retries_used": row.get("auto_retries", 0), "automatic_retries_allowed": MAX_AUTO_RETRIES},
            "previous_advice": row.get("advice"),
            "sources_read": [{"title": s.title, "url": s.final_url or s.url} for s in sources],
            "failed_downloads": failures, "limits": limits}


def retry_feedback(advice):
    """The hint as the new attempt's feedback: works and free copies first, then the change of strategy."""
    routes = [f"{s.title} ({s.url}): {s.note}" if s.url else f"{s.title}: {s.note}" for s in advice.sources]
    return [*(["Note from the research advisor: " + advice.hint] if advice.hint else []),
            *(["Suggested sources: " + "; ".join(routes)] if routes else []),
            "The research advisor asked for a new attempt after this question was blocked. Change the strategy: "
            "other search terms, other sources or other passages. Do not repeat the steps that already failed."]
