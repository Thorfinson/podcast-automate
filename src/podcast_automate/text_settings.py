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


def validate_reasoning(effort):
    if effort is not None and effort not in REASONING_EFFORTS:
        raise AppError("Reasoning-Stufe auswählen: low, medium, high oder xhigh.", code="invalid_backend")
    return effort


def validate_model(model):
    if model is not None and (not isinstance(model, str) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model) or "://" in model):
        raise AppError("Eine gültige Textmodell-ID eingeben.", code="invalid_backend")
    return model
