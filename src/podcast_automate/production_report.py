"""Where a text run spent its calls and its time: per stage, per prompt version, per provider, with stops and approvals.

Read from what every run already keeps: ``calls/call_NNN`` (activity, metadata, provider choice), ``failures/``,
``fresh_attempts.json``, ``budget_approval.json``, ``text_switch.json`` and the Studio's allowance log. Prompt
versions are listed separately, so a fix's effect shows as calls of the new version (2026-09-29: the retrospective
after three podcasts first read the whole run as one, although the review fixes had only run its last hours).
Nothing is written; the Studio computes it when asked.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .storage import read_optional_json as read
from .subscriptions import parse_iso

STAGE_LABELS = {
    "series_plan": "Inhaltsverzeichnis", "series_plan_repair": "Inhaltsverzeichnis · Korrektur",
    "knowledge_model": "Wissensmodell", "teaching_design": "Lehrkonzept", "teaching_design_review": "Lehrkonzept · Prüfung",
    "teaching_design_repair": "Lehrkonzept · Korrektur", "teaching_design_focused_repair": "Lehrkonzept · gezielte Korrektur",
    "teaching_research": "Nachrecherche", "write_episode": "Folge schreiben", "write_episode_repair": "Folge schreiben · Korrektur",
    "dialogue_polish": "Dialogschliff", "dialogue_polish_review": "Dialogschliff · Prüfung",
    "dialogue_polish_repair": "Dialogschliff · Korrektur", "script_review": "Belegprüfung",
    "script_review_repair": "Belegprüfung · Korrektur", "listener_readback": "Hörerprobe", "editorial_review": "Redaktion",
    "teaching_review": "Lehrprüfung", "series_review": "Serienprüfung", "audio_expression": "Ausdruck für die Vertonung"}


def minutes_between(start, end):
    first, last = parse_iso(start), parse_iso(end)
    return max(0.0, (last - first).total_seconds() / 60) if first and last else 0.0


def production_report(work, *, allowance_rows=()):
    stages = defaultdict(lambda: {"calls": 0, "failed": 0, "minutes": 0.0, "providers": Counter()})
    versions = defaultdict(lambda: {"calls": 0, "minutes": 0.0, "first": None, "last": None})
    providers = defaultdict(lambda: {"calls": 0, "minutes": 0.0, "reported_usd": 0.0, "billed_usd": 0.0})
    starts, ends = [], []
    for folder in sorted((work / "calls").glob("call_*")):
        activity = read(folder / "activity.json", {}) or {}
        metadata = read(folder / "metadata.json", {}) or {}
        choice = read(folder / "provider_choice.json", {}) or {}
        version = metadata.get("prompt_version") or choice.get("prompt_version") or "unbekannt"
        family = version.split(".")[0]
        started, updated = activity.get("started_at"), activity.get("updated_at")
        minutes = minutes_between(started, updated)
        provider = choice.get("provider") or metadata.get("provider") or "unbekannt"
        stage = stages[family]
        stage["calls"] += 1
        stage["minutes"] += minutes
        stage["providers"][provider] += 1
        if activity.get("status") not in {"completed", None}:
            stage["failed"] += 1
        row = versions[version]
        row["calls"] += 1
        row["minutes"] += minutes
        if started:
            row["first"] = min(filter(None, [row["first"], started]))
            row["last"] = max(filter(None, [row["last"], started]))
            starts.append(started)
        if updated:
            ends.append(updated)
        spent = providers[provider]
        spent["calls"] += 1
        spent["minutes"] += minutes
        spent["reported_usd"] += float(metadata.get("reported_cost_usd") or 0)
        spent["billed_usd"] += float(metadata.get("separately_billed_cost") or 0)
    total_minutes = sum(stage["minutes"] for stage in stages.values())
    stops = Counter(path.name.split("_")[0] for path in (work / "failures").glob("*.txt"))
    fresh = read(work / "fresh_attempts.json", []) or []
    budget = read(work / "budget_approval.json", {}) or {}
    return {
        "calls": sum(stage["calls"] for stage in stages.values()),
        "failed_calls": sum(stage["failed"] for stage in stages.values()),
        "model_minutes": round(total_minutes, 1),
        "span": {"first": min(starts) if starts else None, "last": max(ends) if ends else None},
        "stages": sorted(({"stage": family, "label": STAGE_LABELS.get(family, family), "calls": row["calls"],
                           "failed": row["failed"], "minutes": round(row["minutes"], 1),
                           "share": round(row["minutes"] / total_minutes, 3) if total_minutes else 0,
                           "minutes_per_call": round(row["minutes"] / row["calls"], 1),
                           "providers": dict(row["providers"])} for family, row in stages.items()),
                         key=lambda row: -row["minutes"]),
        "versions": sorted(({"version": version, **{key: round(value, 1) if key == "minutes" else value
                                                    for key, value in row.items()}}
                            for version, row in versions.items()), key=lambda row: row["first"] or ""),
        "providers": {name: {key: round(value, 2) for key, value in row.items()} for name, row in providers.items()},
        "stops": {"total": sum(stops.values()), "by_stage": dict(stops)},
        "approvals": {"fresh_attempts": len(fresh) if isinstance(fresh, list) else 0,
                      "model_calls": budget.get("model_calls"),
                      "text_switch": (read(work / "text_switch.json", {}) or {}).get("text_generation"),
                      "allowances": [row for row in allowance_rows if row.get("run_id") == work.name
                                     and not row.get("skipped")]}}
