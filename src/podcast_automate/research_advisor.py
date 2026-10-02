"""A second opinion on a blocked research question before the run stops for the operator.

When a question blocks, one advisor call reads what the ledger knows about it: the unmet criteria, the
review's objections, the sources read, the downloads that failed and why, and the run's limits. It may
search the web itself. It names the cause in plain German and recommends a new attempt, an explicit
gap or a higher limit, with a concrete hint (works, free copies, other search routes).

A recommended new attempt starts automatically, at most ``MAX_AUTO_RETRIES`` times per question, and
never after an automatic attempt that read no new passage: that attempt was stuck, and the next one
would repeat it. Only then does the question wait for the operator, who can adopt every open retry
recommendation with one click. Accepting a gap and raising a limit always stay with the operator. The
advice is search guidance, never evidence: whatever a new attempt finds still passes the independent
review.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import Contract, NonEmpty

# v2 (2026-10-02): the advisor reads every earlier advice on the question with what became of it, and whether it
# may search the web itself.
ADVICE_VERSION = "block_advice.v2"
MAX_AUTO_RETRIES = 5
# The advisor's own setting: Opus 5.5 at its deepest level where the run already uses the Claude subscription.
# It stays on Opus when the run writes with the default Sonnet 5.5: one call per blocked question, asked for depth.
ADVISOR_MODEL = "claude-opus-5-5"
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
        return {**selection, "model": ADVISOR_MODEL, "reasoning_effort": ADVISOR_EFFORT}
    return selection


def automatic_retry(row):
    """Whether a retry recommendation may start by itself, else why not: ``limit`` after MAX_AUTO_RETRIES
    automatic attempts, ``no_progress`` when the last automatic attempt read no new passage."""
    if row.get("auto_retries", 0) >= MAX_AUTO_RETRIES:
        return False, "limit"
    basis = row.get("auto_retry_read")
    if basis is not None and len(row.get("read_refs", [])) <= basis:
        return False, "no_progress"
    return True, None


def block_key(row):
    """One advice per block: a new attempt, automatic or explicit, is a new block to advise on."""
    return f"{row.get('retries', 0)}.{row.get('auto_retries', 0)}"


def advice_result(advice, row):
    """What became of one earlier advice, read off the block the question is in now: the new attempt that followed
    it (automatic, the editor's, or none), how that attempt ended and how many passages it read beyond the ones
    read when the advice was given."""
    retries, automatic = (int(part) for part in advice.get("key", "0.0").split("."))
    attempt = ("automatic" if row.get("auto_retries", 0) > automatic else
               "editor" if row.get("retries", 0) > retries else None)
    basis = advice.get("sections_read", row.get("auto_retry_read"))
    return {"new_attempt": attempt, "ended_as": row.get("outcome"), "reason": row.get("reason", ""),
            "new_sections_read": len(row.get("read_refs", [])) - basis if basis is not None else None}


def advice_history(row):
    """Every earlier advice on this question with what became of it, oldest first (the user's rule for repeated
    reviews: a review remembers its earlier verdicts). Before 2026-10-02 the advisor saw only the latest advice,
    although one question can be advised up to six times. The latest advice is settled against the block now on
    record once a new attempt has made it a new block."""
    history = list(row.get("advice_history", []))
    latest = row.get("advice")
    if latest and latest.get("key") != block_key(row) and not any(
            entry.get("key") == latest.get("key") and entry.get("at") == latest.get("at") for entry in history):
        history.append({**latest, "result": advice_result(latest, row)})
    return history


def advice_request(spec, row, sources, failures, limits, *, web_search=True):
    """What the advisor reads, as plain data. ``sources`` and ``failures`` are the run's; ``limits`` its state;
    ``web_search`` whether this call may search the web itself."""
    return {"question": spec.question, "kind": spec.kind, "acceptance": spec.acceptance, "queries": spec.queries,
            "block": {"outcome": row.get("outcome"), "reason": row.get("reason", ""), "feedback": row.get("feedback", []),
                      "web_searches": row.get("web_attempts", 0), "local_searches": len(row.get("search_receipts", [])),
                      "sections_read": len(row.get("read_refs", [])), "explicit_retries": row.get("retries", 0),
                      "automatic_retries_used": row.get("auto_retries", 0), "automatic_retries_allowed": MAX_AUTO_RETRIES},
            "earlier_advice": advice_history(row), "web_search": web_search,
            "sources_read": [{"title": s.title, "url": s.final_url or s.url} for s in sources],
            "failed_downloads": failures, "limits": limits}


def retry_feedback(advice):
    """The hint as the new attempt's feedback: works and free copies first, then the change of strategy."""
    routes = [f"{s.title} ({s.url}): {s.note}" if s.url else f"{s.title}: {s.note}" for s in advice.sources]
    return [*(["Note from the research advisor: " + advice.hint] if advice.hint else []),
            *(["Suggested sources: " + "; ".join(routes)] if routes else []),
            "The research advisor asked for a new attempt after this question was blocked. Change the strategy: "
            "other search terms, other sources or other passages. Do not repeat the steps that already failed."]
