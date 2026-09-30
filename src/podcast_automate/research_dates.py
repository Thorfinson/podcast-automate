"""The date a research run works from, for recency rules (2026-09-30: in AI practice everything changes within
three months, so "current" has to count back from a fixed day)."""


def run_date(work):
    """The date a run started, from its id (run_YYYYMMDD_…): stable across resumes, so prompts that name it stay
    identical and their saved answers stay valid."""
    stamp = work.name.split("_")[1] if work.name.startswith("run_") else ""
    return f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if len(stamp) == 8 and stamp.isdigit() else ""


def research_day(config, work):
    """The run's date for a recency rule, only when the brief sets one, so other prompts stay as they were."""
    return {"research_date": run_date(work)} if config.recency_months else {}
