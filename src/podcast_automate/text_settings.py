"""Explicit text-generation choices, independent of personal CLI configuration.

The model catalogs below are snapshots of provider offerings. ``CATALOG_VERIFIED_ON`` records
when they were last checked; ``pla doctor`` reports their age so stale entries are noticed.
"""
import re
from datetime import date

from .errors import AppError

CATALOG_VERIFIED_ON = date(2026, 9, 26)
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
# Claude Code CLI 2.1.283 with a claude.ai subscription login, verified on 2026-09-26. Opus 5.5 and
# the level xhigh need CLI 2.1.280 or newer. Opus 5 stays listed for runs that saved it.
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_CLAUDE_EFFORT = "xhigh"
CLAUDE_MODELS = {"claude-opus-5-5": "Claude Opus 5.5", "claude-opus-5": "Claude Opus 5"}
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Which Claude Code level carries the same intent as a Codex level. Shown in catalogs and
# documentation; never applied as a silent conversion of a saved choice.
EFFORT_EQUIVALENTS = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}
SUBSCRIPTION_PROVIDERS = ("codex_cli", "claude_code")
# The subscription the automatic rule asks first; the other one takes over when its quota is out.
AUTO_PREFERENCE = "claude_code"
TEXT_PROVIDERS = ("codex_cli", "claude_code", "openrouter", "auto")
# Verified against https://openrouter.ai/api/v1/models on 2026-09-16.
OPENROUTER_MODELS = {
    "openai/gpt-6-astra-pro": "GPT-6 Astra Pro",
    "openai/gpt-6-astra": "GPT-6 Astra",
    "anthropic/claude-fable-5.1": "Claude Fable 5.1",
    "deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
}
OPENROUTER_EFFORTS = {model: (*REASONING_EFFORTS, "max") for model in OPENROUTER_MODELS}
OPENROUTER_EFFORTS["deepseek/deepseek-v4.1-flash"] = ("low", "high", "max")
TEXT_PRESETS = [
    {"id": "auto_subscriptions", "label": "Automatisch · Claude, sonst Codex", "provider": "auto",
     "model": None, "reasoning_effort": None},
    {"id": "claude_opus_sub", "label": "Opus 5.5 · Claude-Abo", "provider": "claude_code",
     "model": "claude-opus-5-5", "reasoning_effort": "xhigh"},
    {"id": "codex_astra", "label": "Astra · Codex-Abo", "provider": "codex_cli",
     "model": "gpt-6-astra", "reasoning_effort": "xhigh"},
    {"id": "openrouter_astra", "label": "Astra · OpenRouter", "provider": "openrouter",
     "model": "openai/gpt-6-astra", "reasoning_effort": None},
    {"id": "openrouter_astra_pro", "label": "Astra Pro · OpenRouter", "provider": "openrouter",
     "model": "openai/gpt-6-astra-pro", "reasoning_effort": None},
    {"id": "openrouter_fable", "label": "Claude Fable 5.1 · OpenRouter", "provider": "openrouter",
     "model": "anthropic/claude-fable-5.1", "reasoning_effort": None},
    {"id": "openrouter_deepseek", "label": "DeepSeek V4.1 Flash · max · OpenRouter", "provider": "openrouter",
     "model": "deepseek/deepseek-v4.1-flash", "reasoning_effort": "max"},
]
PROVIDER_NOTES = {
    "codex_cli": "Codex CLI mit ChatGPT-Abo; kein API-Guthaben. Das Kontingent wird vor jedem Aufruf gelesen.",
    "claude_code": "Claude Code CLI mit Claude-Max-Abo (claude.ai-Anmeldung); keine API-Kosten. Ein erreichtes "
                   "Limit wird erst beim Aufruf sichtbar und danach bis zum Reset vermerkt.",
    "openrouter": "OpenRouter-API mit eigenem Key und Guthaben.",
    "auto": "Automatische Abo-Wahl je Modellaufruf: Claude über das Claude-Max-Abo, bis dessen Kontingent erschöpft "
            "ist, dann Codex über das ChatGPT-Abo. Ohne Kontingent pausiert der Lauf bis zum frühesten Reset. Modell "
            "und Stufe kommen aus dem Katalog und werden nicht einzeln angegeben.",
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


def auto_candidates(codex_model=None):
    """The two subscription configurations an automatic run may use, one per provider."""
    return {"codex_cli": {"model": codex_model or DEFAULT_CODEX_MODEL, "reasoning_effort": DEFAULT_REASONING_EFFORT},
            "claude_code": {"model": DEFAULT_CLAUDE_MODEL, "reasoning_effort": DEFAULT_CLAUDE_EFFORT}}


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
            return DEFAULT_CLAUDE_MODEL
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
        if effort is not None:
            raise AppError("Die automatische Abo-Wahl verwendet die Katalogstandards beider Anbieter. "
                           "Eine Reasoning-Stufe nur für einen festen Anbieter angeben.", code="invalid_backend")
        return None
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
