"""Explicit text-generation choices, independent of personal CLI configuration."""
import re

from .errors import AppError

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


def text_preset(preset_id):
    preset = next((row for row in TEXT_PRESETS if row["id"] == preset_id), None)
    if preset is None:
        raise AppError("Unbekannte Modellauswahl. Studio neu laden.", code="invalid_backend")
    return {key: preset[key] for key in ("provider", "model", "reasoning_effort")}


def provider_model(provider, model):
    """Use the namespace of the explicitly chosen provider; never downgrade Pro."""
    if provider == "openrouter" and model in {"gpt-6-astra", "gpt-6-astra-pro"}:
        return "openai/" + model
    if provider == "codex_cli" and model == "openai/gpt-6-astra":
        return "gpt-6-astra"
    if provider == "codex_cli" and model in {"gpt-6-astra-pro", "openai/gpt-6-astra-pro"}:
        raise AppError("Astra Pro bitte mit OpenRouter auswählen. Für das Codex-Abo steht Astra zur Verfügung.",
                       code="invalid_backend")
    return model


def validate_reasoning(effort, *, provider="codex_cli", model=None):
    allowed = OPENROUTER_EFFORTS.get(model, (*REASONING_EFFORTS, "max")) if provider == "openrouter" else REASONING_EFFORTS
    if effort is not None and effort not in allowed:
        raise AppError("Unterstützte Reasoning-Stufen für diese Auswahl: " + ", ".join(allowed) + ".", code="invalid_backend")
    return effort


def validate_model(model):
    if model is not None and (not isinstance(model, str) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model) or "://" in model):
        raise AppError("Eine gültige Textmodell-ID eingeben.", code="invalid_backend")
    return model
