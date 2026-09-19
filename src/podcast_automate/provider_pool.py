"""One text-generation choice per run, applied per model call.

``text_generation_settings`` builds and checks the saved provider form for a run. ``AdapterPool``
turns that form into an adapter for each call: a fixed provider as before, or the subscription
rule (Codex while it has quota, else Claude, else pause) for ``auto``. The decision is written to
``provider_choice.json`` in the call directory; a mid-call switch after a quota error is recorded
in ``provider_switch.json`` and repeats the same call once with the other subscription. A call whose
streaming output stalls is repeated once on the same provider (``stall_retry.json``), and a prompt
too large for Claude's window is routed to Codex under the automatic rule.
"""
from __future__ import annotations

import json

from . import subscriptions
from .claude_code import ADAPTER_VERSION as CLAUDE_ADAPTER_VERSION, MAX_BUDGET_USD, PROMPT_LIMIT_CHARS, ClaudeCodeAdapter
from .codex import CodexAdapter
from .errors import AppError
from .models import TextProbeOutput, now
from .openrouter import ADAPTER_VERSION, DEFAULT_MAX_OUTPUT_TOKENS, OpenRouterAdapter
from .prompts import instructions
from .storage import write_json
from .text_settings import (DEFAULT_CLAUDE_EFFORT, DEFAULT_CLAUDE_MODEL, SUBSCRIPTION_PROVIDERS, TEXT_PROVIDERS,
                            auto_candidates, provider_model, validate_model, validate_reasoning)


def subscription_selection(config, backend, *, model=None, reasoning_effort=None) -> dict:
    """The saved form of a subscription choice: one fixed Claude provider or the automatic candidate pair."""
    if backend == "auto":
        provider_model("auto", model)
        validate_reasoning(reasoning_effort, provider="auto")
        return {"provider": "auto", "prefer": "codex_cli", "candidates": auto_candidates(config.runtime.codex_model),
                "adapter_versions": {"claude_code": CLAUDE_ADAPTER_VERSION}}
    if backend == "claude_code":
        model = provider_model("claude_code", validate_model(model)) or DEFAULT_CLAUDE_MODEL
        effort = validate_reasoning(reasoning_effort if reasoning_effort is not None else DEFAULT_CLAUDE_EFFORT,
                                    provider="claude_code", model=model)
        return {"provider": "claude_code", "model": model, "reasoning_effort": effort,
                "adapter_version": CLAUDE_ADAPTER_VERSION}
    raise AppError("Unbekannter Abo-Anbieter.", code="invalid_backend", status="blocked")


def text_generation_settings(config, *, backend=None, model=None, max_output_tokens=None, reasoning_effort=None, saved=None):
    if saved is not None:
        # A resumed run keeps its saved form; any differing option is a changed input, never a re-validation.
        if ((backend is not None and backend != saved.get("provider")) or
                (model is not None and model != saved.get("model")) or
                (max_output_tokens is not None and max_output_tokens != saved.get("max_output_tokens")) or
                (reasoning_effort is not None and reasoning_effort != saved.get("reasoning_effort"))):
            raise AppError("Anbieter, Modell, Reasoning-Stufe oder Tokenlimit geändert. Einen neuen script-Lauf starten; "
                           "resume verwendet die gespeicherte Auswahl.", code="inputs_changed", status="blocked")
        return saved
    backend = backend or config.text_backend
    validate_reasoning(reasoning_effort, provider=backend, model=model)
    if backend not in TEXT_PROVIDERS:
        raise AppError("Unbekannter Skriptanbieter.", code="invalid_backend", status="blocked")
    if backend != "openrouter" and max_output_tokens is not None:
        raise AppError("--max-output-tokens wird nur mit --backend openrouter verwendet.", code="invalid_backend", status="blocked")
    if backend in {"codex_cli", "openrouter"}:
        return {"provider": backend, "model": model if model is not None else
                (config.runtime.codex_model if backend == "codex_cli" else None),
                "max_output_tokens": (max_output_tokens if max_output_tokens is not None else DEFAULT_MAX_OUTPUT_TOKENS)
                    if backend == "openrouter" else None,
                "adapter_version": ADAPTER_VERSION if backend == "openrouter" else None,
                "provider_sort": "throughput" if backend == "openrouter" else None,
                "reasoning_effort": reasoning_effort}
    return {"model": None, "max_output_tokens": None, "adapter_version": None, "provider_sort": None,
            "reasoning_effort": None, **subscription_selection(config, backend, model=model, reasoning_effort=reasoning_effort)}


def check_adapter_versions(text_generation) -> None:
    """A saved run binds the adapter contract it started with; a newer adapter needs a new run."""
    provider = text_generation.get("provider")
    if provider == "openrouter" and text_generation.get("adapter_version") != ADAPTER_VERSION:
        raise AppError("OpenRouter-Adapter geändert; einen neuen script-Lauf starten.", code="inputs_changed", status="blocked")
    if provider == "claude_code" and text_generation.get("adapter_version") != CLAUDE_ADAPTER_VERSION:
        raise AppError("Claude-Code-Adapter geändert; einen neuen script-Lauf starten.", code="inputs_changed", status="blocked")
    if provider == "auto" and (text_generation.get("adapter_versions") or {}).get("claude_code") != CLAUDE_ADAPTER_VERSION:
        raise AppError("Claude-Code-Adapter geändert; einen neuen script-Lauf starten.", code="inputs_changed", status="blocked")


class AdapterPool:
    """Builds the adapter for each call of one run and applies the subscription rule where chosen."""

    def __init__(self, settings, text_generation: dict, *, api_key=None, cancel_check=None,
                 max_budget_usd=MAX_BUDGET_USD):
        self.settings = settings
        self.text_generation = text_generation
        self.provider = text_generation.get("provider")
        if self.provider not in TEXT_PROVIDERS:
            raise AppError("Unbekannter Textanbieter.", code="invalid_backend", status="blocked")
        if self.provider == "auto" and set(SUBSCRIPTION_PROVIDERS) - set(text_generation.get("candidates") or {}):
            raise AppError("Die automatische Abo-Wahl benötigt Kandidaten für Codex und Claude.",
                           code="invalid_backend", status="blocked")
        self.cancel_check = cancel_check
        self.max_budget_usd = max_budget_usd
        self.openrouter = OpenRouterAdapter(
            settings, model=text_generation["model"], api_key=api_key,
            max_output_tokens=text_generation.get("max_output_tokens") or DEFAULT_MAX_OUTPUT_TOKENS,
            reasoning_effort=text_generation.get("reasoning_effort")) if self.provider == "openrouter" else None
        self.last_choice = None

    def require_key(self):
        if self.openrouter is not None:
            self.openrouter.require_key()

    def plan(self, *, search=False):
        """Mode, preferred provider and candidate configurations for one call."""
        if self.provider == "auto":
            return "auto", self.text_generation.get("prefer", "codex_cli"), self.text_generation["candidates"]
        if self.provider == "openrouter":
            if not search:
                return "openrouter", "openrouter", {}
            # Live research needs a CLI with web tools; the subscriptions take over with catalog defaults.
            return "auto", "codex_cli", auto_candidates(self.settings.codex_model)
        return "fixed", self.provider, {self.provider: {"model": self.text_generation.get("model"),
                                                         "reasoning_effort": self.text_generation.get("reasoning_effort")}}

    def build(self, choice):
        provider = choice["provider"]
        if provider == "codex_cli":
            return CodexAdapter(self.settings.model_copy(update={"codex_model": choice.get("model")}),
                                reasoning_effort=choice.get("reasoning_effort"), cancel_check=self.cancel_check)
        if provider == "claude_code":
            return ClaudeCodeAdapter(self.settings, model=choice.get("model"),
                                     reasoning_effort=choice.get("reasoning_effort"),
                                     cancel_check=self.cancel_check, max_budget_usd=self.max_budget_usd)
        raise AppError("Unbekannter Textanbieter.", code="invalid_backend", status="blocked")

    def choose(self, mode, prefer, candidates, *, exclude=(), refresh=False):
        if mode == "fixed":
            return {"provider": prefer, **candidates[prefer], "mode": "fixed", "reason": "fixed_provider",
                    "snapshots": {}, "decided_at": now()}
        return subscriptions.choose_subscription(self.settings, candidates, prefer=prefer, exclude=exclude,
                                                 refresh=refresh)

    def structured(self, prompt, output_type, directory, *, prompt_version, search=False, research=False):
        mode, prefer, candidates = self.plan(search=search or research)
        if mode == "openrouter":
            self.openrouter.require_key()
            return self.openrouter.structured(prompt, output_type, directory, prompt_version=prompt_version, search=search)
        tried = []
        too_large = [name for name in candidates if name == "claude_code" and len(prompt) > PROMPT_LIMIT_CHARS]
        if mode == "auto" and too_large:
            # Excluded before the choice: the fixed provider path lets the adapter refuse the call itself.
            write_json(directory / "prompt_size.json", {"prompt_chars": len(prompt), "limit_chars": PROMPT_LIMIT_CHARS,
                       "excluded": too_large})
            tried.extend(too_large)
        choice = self.choose(mode, prefer, candidates, exclude=tried)
        stalled = False
        while True:
            self.last_choice = choice
            write_json(directory / "provider_choice.json", {**choice, "search": search, "prompt_version": prompt_version,
                       "prompt_chars": len(prompt)})
            adapter = self.build(choice)
            try:
                output, metadata = adapter.structured(prompt, output_type, directory,
                                                      prompt_version=prompt_version, search=search)
            except AppError as exc:
                if exc.code == "stall" and not stalled:
                    stalled = True
                    write_json(directory / "stall_retry.json", {"provider": choice["provider"], "message": str(exc),
                               "retried_at": now()})
                    continue
                if exc.status != "waiting_for_quota" or choice["provider"] not in SUBSCRIPTION_PROVIDERS:
                    raise
                # A Claude block is a cheap note for every mode; re-reading Codex windows only serves the rule.
                if mode == "auto" or choice["provider"] == "claude_code":
                    subscriptions.record_quota_failure(choice["provider"], exc, settings=self.settings)
                if mode != "auto":
                    raise
                tried.append(choice["provider"])
                try:
                    alternative = self.choose(mode, prefer, candidates, exclude=tried, refresh=True)
                except AppError as final:
                    # Both subscriptions are out: name both resets. A blocked login keeps the quota error.
                    if final.status == "waiting_for_quota":
                        raise final from exc
                    raise exc from final
                write_json(directory / "provider_switch.json", {
                    "from": choice["provider"], "to": alternative["provider"], "error_code": exc.code,
                    "message": str(exc), "switched_at": now()})
                choice = alternative
                continue
            if choice["provider"] == "claude_code":
                subscriptions.record_claude_success(metadata.get("rate_limit"))
            return output, metadata

    def probe(self, topic, directory):
        prompt = instructions("text_probe") + "\n" + json.dumps({"topic": topic}, ensure_ascii=False)
        output, metadata = self.structured(prompt, TextProbeOutput, directory, prompt_version="text_probe.v1")
        if output.topic != topic:
            raise AppError("Das Modell hat das Thema verändert.", code="invalid_model_output")
        return output, metadata
