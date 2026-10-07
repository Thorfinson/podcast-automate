"""One text-generation choice per run, applied per model call.

``text_generation_settings`` builds and checks the saved provider form for a run. ``AdapterPool``
turns that form into an adapter for each call: a fixed provider as before, or the subscription
rule for ``auto`` (the preferred subscription while it has quota, else the other, else pause; new runs
prefer Claude). The decision is written to
``provider_choice.json`` in the call directory; a mid-call switch after a quota error, or after a failure that
finds the subscription unusable (login, plan, CLI version), is recorded in ``provider_switch.json`` and repeats
the same call once with the other subscription. A call whose
streaming output stalls is repeated once on the same provider (``stall_retry.json``), and so is one whose
answer Claude could not bring into the requested shape (``format_retry.json``) and a research call whose web search
was not observed (``search_retry.json``, D-155); a prompt too large for Claude's window is routed to Codex under the
automatic rule.

A provider that bills the user's key (``claude_api``, OpenRouter) leaves one row per attempt in ``billing.json`` of the
call directory (D-148): a stall or format retry repeats the call in the same directory, and the money of every attempt
counts, a rejected answer's too. research.settle_call counts the rows into the run budget.

A run whose selection names ``web_search: "perplexity"`` (D-151) answers every search call in two steps instead of a
model's own web tools: its model plans the queries (``search_plan/``), Perplexity's Search API runs them
(``search_results.json``, one billing row per request), and its model completes the task from those results, whose
addresses every candidate must come from (AdapterPool.searched).
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

from . import subscriptions
from .claude_code import (ADAPTER_VERSION as CLAUDE_ADAPTER_VERSION, API_ADAPTER_VERSION as CLAUDE_API_ADAPTER_VERSION,
                          MAX_BUDGET_USD, ClaudeCodeAdapter, prompt_limit)
from .codex import CodexAdapter
from .errors import AppError
from .logs import logger
from .models import TextProbeOutput, now
from .openrouter import ADAPTER_VERSION, DEFAULT_MAX_OUTPUT_TOKENS, OpenRouterAdapter
from .prompts import instructions
from .storage import read_optional_json, write_json
from .web_search import (ADAPTER_VERSION as SEARCH_ADAPTER_VERSION, MAX_QUERIES_PER_REQUEST, PerplexitySearch,
                         SearchPlan)
from .text_settings import (AUTO_PREFERENCE, BILLED_TEXT_PROVIDERS, DEFAULT_CLAUDE_EFFORT, DEFAULT_CLAUDE_MODEL,
                            SUBSCRIPTION_PROVIDERS, TEXT_PROVIDERS, auto_candidates, provider_model, stage_effort,
                            validate_model, validate_reasoning)


# Failures repeated once on the same provider before they stop the run, with the receipt each leaves in the call
# folder: a stalled stream, an answer the Claude CLI could not bring into the requested shape, and a research call that
# ran no observable web search (D-155: four such stops in the Sep/Oct runs, where a resume usually passed).
REPEATED_ONCE = {"stall": "stall_retry.json", "claude_structured_output": "format_retry.json",
                 "search_not_observed": "search_retry.json"}
# Failures that say a subscription cannot be used right now (login, plan, CLI), not that its quota is spent. Under
# the automatic rule the call moves to the other subscription, as after a quota error, and the store notes it
# (subscriptions.record_unavailable); a fixed provider still stops with the error (2026-10-02: an expired login
# stopped auto runs although the other subscription had quota). A failed Codex turn that codex.classify_failure cannot
# name (``codex_failed``) counts too, since 2026-10-04: after the Codex account fell to the free plan, every call of
# the Transformer run failed two seconds after its start and stopped the run three times, with Claude ready. A
# failed turn's output is lost either way, so the call loses nothing by moving. A failed Claude turn
# (``claude_failed``) moves to Codex alike since D-155 (2026-09-30/10-01: two such stops waited for a resume by hand).
UNAVAILABLE_CODES = frozenset({"authentication_required", "subscription_required", "claude_version", "claude_missing",
                               "codex_missing", "missing_executable", "unsupported_claude_launcher",
                               "unsupported_codex_launcher", "codex_failed", "claude_failed"})


# Failures of a billed attempt that may have used model work without reporting its cost; they count at an estimate.
UNPRICED_CODES = frozenset({"timeout", "stall"})
BILLING_NAME = "billing.json"


def own_billing(directory) -> list[dict]:
    rows = read_optional_json(directory / BILLING_NAME, []) or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def read_billing(directory) -> list[dict]:
    """The billing rows of one call directory, oldest first, with those of the steps a search call keeps in its
    subfolders (search_plan/); none for a subscription call."""
    rows = own_billing(directory)
    for folder in sorted(path for path in directory.glob("*") if path.is_dir()) if directory.is_dir() else ():
        rows.extend(own_billing(folder))
    return rows


def bill(directory, row, *, replace=False) -> None:
    """Append a billing row, or replace the last one (an attempt that started and has now ended)."""
    rows = own_billing(directory)
    if replace and rows:
        rows[-1] = row
    else:
        rows.append(row)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / BILLING_NAME, rows)


def ended_row(started, *, metadata=None, error=None) -> dict:
    """The row of an ended attempt: priced from the receipt or the failure's ``billed_usd``, unpriced after a timeout
    or stall, free otherwise (refused before any model work, such as a missing key)."""
    usd = (metadata or {}).get("separately_billed_cost") if error is None else error.details.get("billed_usd")
    if isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd >= 0:
        state = "priced"
    else:
        usd, state = None, "unpriced" if error is not None and error.code in UNPRICED_CODES else (
            "unpriced" if error is None else "free")
    return {**started, "state": state, "usd": usd, "code": error.code if error is not None else None, "ended_at": now()}


# The Anthropic key a Studio worker received with its job (studio_worker.main). Every pool of that worker that runs
# Claude on the key uses it: also the expression layer and the companion kit, whose ``api_key`` is OpenRouter's.
_WORKER_KEYS: dict[str, str] = {}


def use_anthropic_key(key) -> None:
    """Hold the Anthropic key for this process's claude_api pools; an empty key clears it."""
    use_worker_key("anthropic", key)


def use_worker_key(kind, key) -> None:
    """Hold a key of this worker (``anthropic`` for claude_api, ``perplexity`` for the web search); empty clears it."""
    if key:
        _WORKER_KEYS[kind] = key
    else:
        _WORKER_KEYS.pop(kind, None)


def with_web_search(selection, web_search):
    """A selection that searches through Perplexity (D-151) names it with the search adapter's version; the model's
    own search adds nothing, so every selection saved before keeps its form and hash."""
    if web_search in (None, "model"):
        return selection
    if web_search != "perplexity":
        raise AppError("Websuche über das Textmodell oder über Perplexity wählen.", code="invalid_backend",
                       status="blocked")
    return {**selection, "web_search": "perplexity", "web_search_version": SEARCH_ADAPTER_VERSION}


def address(url) -> str:
    """An address as the search results and the candidates are compared: scheme and host in lower case, no
    fragment, no trailing slash."""
    try:
        parts = urlsplit(str(url).strip())
    except ValueError:
        return str(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))


def text_key(selection, openrouter_key=None, anthropic_key=None):
    """The key a run's text choice needs, or None for a subscription choice."""
    provider = (selection or {}).get("provider")
    return {"openrouter": openrouter_key, "claude_api": anthropic_key}.get(provider)


def bookkeeping(action, *args, **kwargs):
    """A note in the shared quota store never decides a call: when the store is busy or unreadable, the call keeps
    its own outcome (2026-10-02: a lock timeout after a paid answer raised project_busy, and the answer was bought
    again on resume)."""
    try:
        return action(*args, **kwargs)
    except (AppError, OSError, ValueError) as exc:
        logger("provider_pool").warning("Kontingentvermerk nicht gespeichert (%s): %s",
                                        getattr(exc, "code", type(exc).__name__), exc)
        return None


def subscription_selection(config, backend, *, model=None, reasoning_effort=None) -> dict:
    """The saved form of a subscription choice: one fixed Claude provider or the automatic candidate pair."""
    if backend == "auto":
        provider_model("auto", model)
        effort = validate_reasoning(reasoning_effort, provider="auto")
        return {"provider": "auto", "prefer": AUTO_PREFERENCE, "candidates": auto_candidates(config.runtime.codex_model, effort),
                "adapter_versions": {"claude_code": CLAUDE_ADAPTER_VERSION}}
    if backend == "claude_code":
        model = provider_model("claude_code", validate_model(model)) or DEFAULT_CLAUDE_MODEL
        effort = validate_reasoning(reasoning_effort if reasoning_effort is not None else DEFAULT_CLAUDE_EFFORT,
                                    provider="claude_code", model=model)
        return {"provider": "claude_code", "model": model, "reasoning_effort": effort,
                "adapter_version": CLAUDE_ADAPTER_VERSION}
    raise AppError("Unbekannter Abo-Anbieter.", code="invalid_backend", status="blocked")


def api_selection(config, *, model=None, reasoning_effort=None) -> dict:
    """The saved form of a Claude choice billed to the user's Anthropic key (D-145): always fixed, never automatic."""
    model = provider_model("claude_api", validate_model(model)) or DEFAULT_CLAUDE_MODEL
    effort = validate_reasoning(reasoning_effort if reasoning_effort is not None else DEFAULT_CLAUDE_EFFORT,
                                provider="claude_api", model=model)
    return {"provider": "claude_api", "model": model, "reasoning_effort": effort,
            "adapter_version": CLAUDE_API_ADAPTER_VERSION}


def text_generation_settings(config, *, backend=None, model=None, max_output_tokens=None, reasoning_effort=None, saved=None,
                             web_search=None):
    if saved is None and web_search not in (None, "model"):
        return with_web_search(text_generation_settings(config, backend=backend, model=model,
                                                        max_output_tokens=max_output_tokens,
                                                        reasoning_effort=reasoning_effort), web_search)
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
    if backend == "claude_api":
        return {"model": None, "max_output_tokens": None, "adapter_version": None, "provider_sort": None,
                "reasoning_effort": None, **api_selection(config, model=model, reasoning_effort=reasoning_effort)}
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
    if provider == "claude_api" and text_generation.get("adapter_version") != CLAUDE_API_ADAPTER_VERSION:
        raise AppError("Claude-API-Adapter geändert; einen neuen Lauf starten.", code="inputs_changed", status="blocked")
    if text_generation.get("web_search") == "perplexity" and (
            text_generation.get("web_search_version") != SEARCH_ADAPTER_VERSION):
        raise AppError("Perplexity-Suchadapter geändert; einen neuen Lauf starten.", code="inputs_changed",
                       status="blocked")


class AdapterPool:
    """Builds the adapter for each call of one run and applies the subscription rule where chosen."""

    def __init__(self, settings, text_generation: dict, *, api_key=None, cancel_check=None,
                 max_budget_usd=MAX_BUDGET_USD, anthropic_key=None, perplexity_key=None):
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
        # The Anthropic key of a claude_api run: given, or the one this worker holds (use_anthropic_key); a
        # subscription call never receives it, and without either the adapter reads ANTHROPIC_API_KEY (D-145).
        self.anthropic_key = anthropic_key if anthropic_key is not None else _WORKER_KEYS.get("anthropic")
        # Search calls go to Perplexity when the run chose it (D-151), with the given or the worker's key.
        self.web_search = text_generation.get("web_search") or "model"
        self.perplexity = PerplexitySearch(perplexity_key if perplexity_key is not None else
                                           _WORKER_KEYS.get("perplexity")) if self.web_search == "perplexity" else None
        self.last_choice = None

    def require_key(self):
        if self.openrouter is not None:
            self.openrouter.require_key()
        if self.provider == "claude_api":
            self.build({"provider": "claude_api", "model": self.text_generation.get("model"),
                        "reasoning_effort": self.text_generation.get("reasoning_effort")}).require_key()
        if self.perplexity is not None:
            self.perplexity.require_key()

    def billed(self, search=False) -> bool:
        """Whether a call with this ``search`` flag bills a key, so it needs the run's money limit: the text model's
        (claude_api, OpenRouter) or, for a search call, Perplexity's."""
        if search and self.perplexity is not None:
            return True
        mode, prefer, _ = self.plan(search=search)
        return mode == "openrouter" or (mode == "fixed" and prefer in BILLED_TEXT_PROVIDERS)

    def plan(self, *, search=False):
        """Mode, preferred provider and candidate configurations for one call."""
        if self.provider == "auto":
            return "auto", self.text_generation.get("prefer", "codex_cli"), self.text_generation["candidates"]
        if self.provider == "openrouter":
            if not search or self.perplexity is not None:
                # With Perplexity's search (D-151) an OpenRouter run needs no subscription at all.
                return "openrouter", "openrouter", {}
            # Live research needs a CLI with web tools; the subscriptions take over with catalog defaults.
            return "auto", AUTO_PREFERENCE, auto_candidates(self.settings.codex_model)
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
        if provider == "claude_api":
            return ClaudeCodeAdapter(self.settings, model=choice.get("model"),
                                     reasoning_effort=choice.get("reasoning_effort"),
                                     cancel_check=self.cancel_check, max_budget_usd=self.max_budget_usd,
                                     auth="api_key", api_key=self.anthropic_key)
        raise AppError("Unbekannter Textanbieter.", code="invalid_backend", status="blocked")

    def choose(self, mode, prefer, candidates, *, exclude=(), refresh=False):
        if mode == "fixed":
            return {"provider": prefer, **candidates[prefer], "mode": "fixed", "reason": "fixed_provider",
                    "snapshots": {}, "decided_at": now()}
        return subscriptions.choose_subscription(self.settings, candidates, prefer=prefer, exclude=exclude,
                                                 refresh=refresh)

    def structured(self, prompt, output_type, directory, *, prompt_version, search=False, research=False):
        if search and self.perplexity is not None:
            return self.searched(prompt, output_type, directory, prompt_version=prompt_version)
        mode, prefer, candidates = self.plan(search=search or research)
        if mode == "openrouter":
            self.openrouter.require_key()
            return self.attempt(self.openrouter, {"provider": "openrouter", "model": self.openrouter.model},
                                prompt, output_type, directory, prompt_version=prompt_version, search=search)
        tried = []
        too_large = [name for name, choice in candidates.items()
                     if name == "claude_code" and len(prompt) > prompt_limit(choice["model"])]
        if mode == "auto" and too_large:
            # Excluded before the choice: the fixed provider path lets the adapter refuse the call itself.
            write_json(directory / "prompt_size.json", {"prompt_chars": len(prompt),
                       "limit_chars": prompt_limit(candidates["claude_code"]["model"]), "excluded": too_large})
            tried.extend(too_large)
        choice = self.choose(mode, prefer, candidates, exclude=tried)
        repeated = set()
        while True:
            # A stage with a lower level (text_settings.STAGE_EFFORT_CAPS) asks at that level and records the run's.
            effort = stage_effort(prompt_version, choice.get("reasoning_effort"))
            used = choice if effort == choice.get("reasoning_effort") else {
                **choice, "reasoning_effort": effort, "run_effort": choice.get("reasoning_effort")}
            self.last_choice = used
            write_json(directory / "provider_choice.json", {**used, "search": search, "prompt_version": prompt_version,
                       "prompt_chars": len(prompt)})
            adapter = self.build(used)
            # A success clears only the quota notes made before this call started (record_claude_success).
            started = now()
            try:
                output, metadata = self.attempt(adapter, used, prompt, output_type, directory,
                                                prompt_version=prompt_version, search=search)
            except AppError as exc:
                if exc.code in REPEATED_ONCE and exc.code not in repeated:
                    repeated.add(exc.code)
                    write_json(directory / REPEATED_ONCE[exc.code], {"provider": choice["provider"], "message": str(exc),
                               "retried_at": now()})
                    continue
                if choice["provider"] not in SUBSCRIPTION_PROVIDERS:
                    raise
                unavailable = mode == "auto" and exc.code in UNAVAILABLE_CODES
                if exc.status != "waiting_for_quota" and not unavailable:
                    raise
                if unavailable:
                    bookkeeping(subscriptions.record_unavailable, choice["provider"], exc)
                else:
                    # The paused run is resumed at this provider's reset (subscriptions.quota_retry_at).
                    exc.details.setdefault("provider", choice["provider"])
                    # A Claude block is a cheap note for every mode; re-reading Codex windows only serves the rule.
                    if mode == "auto" or choice["provider"] == "claude_code":
                        noted = bookkeeping(subscriptions.record_quota_failure, choice["provider"], exc,
                                            settings=self.settings)
                        if choice["provider"] == "codex_cli" and isinstance(noted, dict) and noted.get("resets_at"):
                            exc.details.setdefault("blocked_until", noted["resets_at"])
                if mode != "auto":
                    raise
                tried.append(choice["provider"])
                try:
                    alternative = self.choose(mode, prefer, candidates, exclude=tried, refresh=True)
                except AppError as final:
                    # Both subscriptions are out: name both resets. A blocked login keeps the quota error. A provider
                    # that is unusable (an expired login, too old a CLI) beside one out of quota stops with its own
                    # error, which the user can fix now: the quota pause waited for the other's reset, days at times.
                    if final.status == "waiting_for_quota" and not unavailable:
                        raise final from exc
                    raise exc from final
                write_json(directory / "provider_switch.json", {
                    "from": choice["provider"], "to": alternative["provider"], "error_code": exc.code,
                    "message": str(exc), "switched_at": now()})
                choice = alternative
                continue
            if choice["provider"] == "claude_code":
                bookkeeping(subscriptions.record_claude_success, metadata.get("rate_limit"), started_at=started)
            return output, metadata

    def attempt(self, adapter, choice, prompt, output_type, directory, *, prompt_version, search):
        """One adapter call; for a billed provider it leaves a row in billing.json that says it started, and replaces
        it when the attempt ends, so a killed worker's attempt is still counted (research.reconcile_budget)."""
        if choice["provider"] not in BILLED_TEXT_PROVIDERS:
            return adapter.structured(prompt, output_type, directory, prompt_version=prompt_version, search=search)
        started = {"attempt": len(read_billing(directory)) + 1, "provider": choice["provider"],
                   "model": choice.get("model"), "prompt_version": prompt_version, "started_at": now()}
        bill(directory, {**started, "state": "started", "usd": None})
        try:
            output, metadata = adapter.structured(prompt, output_type, directory, prompt_version=prompt_version,
                                                  search=search)
        except AppError as exc:
            bill(directory, ended_row(started, error=exc), replace=True)
            raise
        bill(directory, ended_row(started, metadata=metadata), replace=True)
        return output, metadata

    def searched(self, prompt, output_type, directory, *, prompt_version):
        """One search call through Perplexity (D-151). The run's model plans the queries, Perplexity runs them, and
        the run's model completes ``output_type`` from the results; a candidate whose address is not among them is
        asked again with the defect named, at most twice. The answer names the queries that ran."""
        from .research_patches import MAX_REJECTIONS, rejected_prompt
        maximum = 10 if output_type.__name__ == "ResearchDiscovery" else MAX_QUERIES_PER_REQUEST
        planned, _ = self.structured(instructions("search_queries", maximum=maximum) + "\n" +
                                     json.dumps({"task": prompt}, ensure_ascii=False), SearchPlan,
                                     directory / "search_plan", prompt_version=f"{prompt_version}.perplexity_queries")
        queries = list(dict.fromkeys(" ".join(q.split()) for q in planned.queries))[:maximum]
        requests, results, seen = [], [], set()
        for start in range(0, len(queries), MAX_QUERIES_PER_REQUEST):
            batch = queries[start:start + MAX_QUERIES_PER_REQUEST]
            row = {"attempt": len(own_billing(directory)) + 1, "provider": "perplexity", "model": "search",
                   "prompt_version": prompt_version, "started_at": now()}
            bill(directory, {**row, "state": "started", "usd": None})
            try:
                found = self.perplexity.search(batch, languages=planned.languages)
            except AppError as exc:
                bill(directory, {**row, "state": "free", "usd": None, "code": exc.code, "ended_at": now()}, replace=True)
                raise
            bill(directory, {**row, "state": "priced", "usd": found["usd"], "code": None, "ended_at": now()},
                 replace=True)
            requests.append({key: found[key] for key in ("queries", "request_id", "usd", "elapsed_seconds")}
                            | {"results": len(found["results"])})
            for result in found["results"]:
                if address(result["url"]) not in seen:
                    seen.add(address(result["url"]))
                    results.append(result)
        write_json(directory / "search_results.json", {"queries": queries, "languages": planned.languages,
                                                        "requests": requests, "results": results})
        task = instructions("search_select") + "\n" + json.dumps({"task": prompt, "queries": queries, "results": results},
                                                                   ensure_ascii=False)
        rejections = []
        while True:
            output, metadata = self.structured(rejected_prompt(task, rejections), output_type, directory,
                                               prompt_version=f"{prompt_version}.perplexity")
            invented = [candidate.url for candidate in getattr(output, "candidates", None) or []
                        if address(candidate.url) not in seen]
            if not invented:
                break
            rejections.append({"code": "invalid_search_selection", "message":
                               "Diese Adressen stehen in keinem Suchergebnis: " + ", ".join(invented[:5]) +
                               ". Nur Adressen aus den Ergebnissen übernehmen."})
            if len(rejections) > MAX_REJECTIONS:
                raise AppError("Die Antwort nannte wiederholt Quellen, die die Suche nicht gefunden hat; sie wurde "
                               "nicht übernommen.", code="invalid_search_selection", status="blocked")
        if "executed_queries" in type(output).model_fields:
            output = output.model_copy(update={"executed_queries": queries})
        metadata = {**metadata, "search_provider": "perplexity", "search_adapter_version": SEARCH_ADAPTER_VERSION,
                    "research_performed": True, "web_search_events": len(results), "web_search_requests": len(queries),
                    "observed_search_queries": queries}
        write_json(directory / "metadata.json", metadata)
        write_json(directory / "response.json", output.model_dump(mode="json"))
        return output, metadata

    def probe(self, topic, directory):
        prompt = instructions("text_probe") + "\n" + json.dumps({"topic": topic}, ensure_ascii=False)
        output, metadata = self.structured(prompt, TextProbeOutput, directory, prompt_version="text_probe.v1")
        if output.topic != topic:
            raise AppError("Das Modell hat das Thema verändert.", code="invalid_model_output")
        return output, metadata
