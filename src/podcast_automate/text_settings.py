"""Explicit text-generation choices, independent of personal CLI configuration.

The model catalogs below are snapshots of provider offerings. ``CATALOG_VERIFIED_ON`` records
when they were last checked; ``pla doctor`` reports their age so stale entries are noticed.
"""
import re
from datetime import date

from .errors import AppError

CATALOG_VERIFIED_ON = date(2026, 9, 29)
CATALOG_STALE_DAYS = 90
DEFAULT_CODEX_MODEL = "gpt-6-astra"
DEFAULT_REASONING_EFFORT = "xhigh"
REASONING_EFFORTS = ("low", "medium", "high", "xhigh")
# These four effort levels are supported by each preset in the installed Codex catalog.
CODEX_MODELS = {
    "gpt-6-astra": "GPT-6 Astra",
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "gpt-5.6-terra": "GPT-5.6 Terra",
    "gpt-5.6-luna": "GPT-5.6 Luna",
    "gpt-5.5": "GPT-5.5",
}
# Claude Code CLI 2.1.284 with a claude.ai subscription login, verified on 2026-09-29. Opus 5.5 and
# the level xhigh need CLI 2.1.280 or newer, Sonnet 5.5 needs 2.1.284 (claude_code.MODEL_MINIMUM_CLI).
# Sonnet 5.5 at high is the default since 2026-09-29: the Opus 5.5 runs before took 1.5 to 2.5 minutes per
# script review at medium. Opus 5.5 and Opus 5 stay listed for runs that saved them.
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5-5"
DEFAULT_CLAUDE_EFFORT = "high"
CLAUDE_MODELS = {"claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-opus-5-5": "Claude Opus 5.5",
                 "claude-opus-5": "Claude Opus 5"}
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Stages whose task needs less thought than the run's level: the placing of expression tags for Gemini's reading,
# and every review at the routine role A1 (STAGE_AUTHORITY), today only the first-time listener, who should take in
# only what the dialogue itself explains. Each such call uses at most this level on a subscription; evidence and
# teaching reviews, writing and every repair keep the run's own (2026-09-29, after the review calls of Astra at
# xhigh took 7 to 10 minutes each). prompt_version's family names the stage; provider_choice.json records the run's
# level as ``run_effort``.
STAGE_EFFORT_CAPS = {"audio_expression": "medium"}
A1_EFFORT = "medium"
# The role each review plays (review-loop plan §5, §10): A1 routine review at A1_EFFORT, A2 expert review at the run's
# level. A call of any other family plays A2 too, so it keeps today's candidates. Moving a review to A1 needs its
# stage's eval and a decision entry (plan §10).
STAGE_AUTHORITY = {"listener_readback": "A1", "dialogue_polish_review": "A2", "editorial_review": "A2",
                   "teaching_review": "A2", "script_review": "A2", "teaching_design_review": "A2",
                   "series_review": "A2"}
# A prompt_version carrying this tag is a call of the final adjudicator A3, the bounded last attempt of a loop whose
# repairs are spent (plan §9.2). Under ``auto`` it asks Codex first, at A3_EFFORT; Claude has no A3 entry until an
# Opus limit is noted for Opus alone (V-12), since one Opus limit blocks the whole Claude subscription for 5 hours.
A3_TAG = "+a3"
A3_EFFORT = "xhigh"
EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")
# The levels an automatic choice may set for both subscriptions at once.
SHARED_EFFORTS = tuple(effort for effort in REASONING_EFFORTS if effort in CLAUDE_EFFORTS)
# Which Claude Code level carries the same intent as a Codex level. Shown in catalogs and
# documentation; never applied as a silent conversion of a saved choice.
EFFORT_EQUIVALENTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}
SUBSCRIPTION_PROVIDERS = ("codex_cli", "claude_code")
# The subscription the automatic rule asks first; the other one takes over when its quota is out.
AUTO_PREFERENCE = "claude_code"
TEXT_PROVIDERS = ("codex_cli", "claude_code", "openrouter", "auto")
# The user's choice of 2026-10-03, verified against https://openrouter.ai/api/v1/models that day: each reports
# structured_outputs, response_format and reasoning_effort. Astra Pro and Claude Fable 5.1 are no longer offered.
OPENROUTER_MODELS = {
    "openai/gpt-6-astra": "GPT-6 Astra",
    "anthropic/claude-opus-5.5": "Claude Opus 5.5",
    "anthropic/claude-sonnet-5.5": "Claude Sonnet 5.5",
    "deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
}
OPENROUTER_EFFORTS = {model: (*REASONING_EFFORTS, "max") for model in OPENROUTER_MODELS}
OPENROUTER_EFFORTS["deepseek/deepseek-v4.1-flash"] = ("low", "high", "max")
TEXT_PRESETS = [
    {"id": "auto_subscriptions", "label": "Automatisch · Claude, sonst Codex", "provider": "auto",
     "model": None, "reasoning_effort": None},
    {"id": "auto_subscriptions_high", "label": "Automatisch · Claude, sonst Codex · high", "provider": "auto",
     "model": None, "reasoning_effort": "high"},
    {"id": "claude_sonnet_sub", "label": "Sonnet 5.5 · Claude-Abo · high", "provider": "claude_code",
     "model": "claude-sonnet-5-5", "reasoning_effort": "high"},
    {"id": "claude_opus_sub", "label": "Opus 5.5 · Claude-Abo", "provider": "claude_code",
     "model": "claude-opus-5-5", "reasoning_effort": "xhigh"},
    {"id": "codex_astra", "label": "Astra · Codex-Abo", "provider": "codex_cli",
     "model": "gpt-6-astra", "reasoning_effort": "xhigh"},
    # The levels of the OpenRouter presets are the user's choice of 2026-10-03.
    {"id": "openrouter_astra", "label": "Astra · xhigh · OpenRouter", "provider": "openrouter",
     "model": "openai/gpt-6-astra", "reasoning_effort": "xhigh"},
    {"id": "openrouter_opus", "label": "Opus 5.5 · medium · OpenRouter", "provider": "openrouter",
     "model": "anthropic/claude-opus-5.5", "reasoning_effort": "medium"},
    {"id": "openrouter_sonnet", "label": "Sonnet 5.5 · high · OpenRouter", "provider": "openrouter",
     "model": "anthropic/claude-sonnet-5.5", "reasoning_effort": "high"},
    {"id": "openrouter_deepseek", "label": "DeepSeek V4.1 Flash · max · OpenRouter", "provider": "openrouter",
     "model": "deepseek/deepseek-v4.1-flash", "reasoning_effort": "max"},
]
PROVIDER_NOTES = {
    "codex_cli": "Codex CLI mit ChatGPT-Abo; kein API-Guthaben. Das Kontingent wird vor jedem Aufruf gelesen.",
    "claude_code": "Claude Code CLI mit Claude-Max-Abo (claude.ai-Anmeldung); keine API-Kosten. Ein erreichtes "
                   "Limit wird erst beim Aufruf sichtbar und danach bis zum Reset vermerkt.",
    "openrouter": "OpenRouter-API mit eigenem Key und Guthaben.",
    "auto": "Automatische Abo-Wahl je Modellaufruf: Claude (Sonnet 5.5) über das Claude-Max-Abo, bis dessen Kontingent erschöpft "
            "ist, dann Codex über das ChatGPT-Abo. Ohne Kontingent pausiert der Lauf bis zum frühesten Reset. Die Modelle "
            "kommen aus dem Katalog; die Stufe ist deren Standard oder eine gemeinsame Stufe wie high für beide.",
}


def catalog_age(today=None):
    """Days since the model catalogs were verified, and whether that exceeds the review interval."""
    days = ((today or date.today()) - CATALOG_VERIFIED_ON).days
    return {"verified_on": CATALOG_VERIFIED_ON.isoformat(), "age_days": days, "stale": days > CATALOG_STALE_DAYS}


def text_preset(preset_id):
    preset = next((row for row in TEXT_PRESETS if row["id"] == preset_id), None)
    if preset is None:
        raise AppError("Unbekannte Modellauswahl. Studio neu laden.", code="invalid_backend")
    return {key: preset[key] for key in ("provider", "model", "reasoning_effort")}


def auto_candidates(codex_model=None, effort=None):
    """The two subscription configurations an automatic run may use, one per provider. ``effort`` is one level
    both providers know, applied to both; without it each keeps its catalog default."""
    return {"codex_cli": {"model": codex_model or DEFAULT_CODEX_MODEL, "reasoning_effort": effort or DEFAULT_REASONING_EFFORT},
            "claude_code": {"model": DEFAULT_CLAUDE_MODEL, "reasoning_effort": effort or DEFAULT_CLAUDE_EFFORT}}


def a3_entry(codex_model=None):
    """The A3 rung of the ladder an automatic run stores: Codex only, at A3_EFFORT."""
    return {"prefer": "codex_cli",
            "candidates": {"codex_cli": {"model": codex_model or DEFAULT_CODEX_MODEL, "reasoning_effort": A3_EFFORT}}}


def call_role(prompt_version):
    """The role one call plays, read off its prompt_version: A3 by its tag, else its family's STAGE_AUTHORITY."""
    version = prompt_version or ""
    if A3_TAG in version:
        return "A3"
    return STAGE_AUTHORITY.get(version.split(".")[0], "A2")


def stage_effort(prompt_version, effort):
    """The level one call uses: the run's own, lowered to the cap of a stage STAGE_EFFORT_CAPS names or of the role
    A1. A call without an explicit level keeps the provider's default."""
    cap = STAGE_EFFORT_CAPS.get((prompt_version or "").split(".")[0]) or (
        A1_EFFORT if call_role(prompt_version) == "A1" else None)
    if cap is None or effort not in EFFORT_ORDER or EFFORT_ORDER.index(effort) <= EFFORT_ORDER.index(cap):
        return effort
    return cap


def provider_model(provider, model):
    """Use the namespace of the explicitly chosen provider; never downgrade Pro."""
    if provider == "openrouter" and model in {"gpt-6-astra", "gpt-6-astra-pro"}:
        return "openai/" + model
    if provider == "codex_cli" and model == "openai/gpt-6-astra":
        return "gpt-6-astra"
    if provider == "codex_cli" and model in {"gpt-6-astra-pro", "openai/gpt-6-astra-pro"}:
        raise AppError("Astra Pro bitte mit OpenRouter auswählen. Für das Codex-Abo steht Astra zur Verfügung.",
                       code="invalid_backend")
    if provider == "claude_code":
        if model in {"opus", "claude-opus"}:
            return "claude-opus-5-5"
        if model in {"sonnet", "claude-sonnet"}:
            return "claude-sonnet-5-5"
        if model == "anthropic/claude-opus-5":
            return "claude-opus-5"
        if model and "/" in model:
            raise AppError("Für das Claude-Abo eine Claude-Modell-ID wie claude-opus-5 wählen; "
                           "OpenRouter-IDs gehören zur OpenRouter-Auswahl.", code="invalid_backend")
    if provider == "auto" and model is not None:
        raise AppError("Die automatische Abo-Wahl verwendet die Katalogstandards beider Anbieter. "
                       "Modell und Reasoning-Stufe nur für einen festen Anbieter angeben.", code="invalid_backend")
    return model


def validate_reasoning(effort, *, provider="codex_cli", model=None):
    if provider == "openrouter":
        allowed = OPENROUTER_EFFORTS.get(model, (*REASONING_EFFORTS, "max"))
    elif provider == "claude_code":
        allowed = CLAUDE_EFFORTS
    elif provider == "auto":
        # One level for both subscriptions, so only a level both know.
        allowed = SHARED_EFFORTS
    else:
        allowed = REASONING_EFFORTS
    if effort is not None and effort not in allowed:
        raise AppError("Unterstützte Reasoning-Stufen für diese Auswahl: " + ", ".join(allowed) + ".", code="invalid_backend")
    return effort


def validate_model(model):
    if model is not None and (not isinstance(model, str) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model) or "://" in model):
        raise AppError("Eine gültige Textmodell-ID eingeben.", code="invalid_backend")
    return model
