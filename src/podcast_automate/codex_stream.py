"""One ephemeral Codex app-server turn, using its public JSON-RPC stream."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time

from .errors import AppError
from .process import stop_process_tree


def run_app_server(command, *, prompt, schema, response_file, cwd, env, timeout,
                   model, effort, search, activity, cancel_check=None):
    args = command + ["app-server", "--listen", "stdio://",
                      "-c", 'model_provider="openai"', "-c", "features.shell_tool=false",
                      "-c", "features.hooks=false", "-c", "features.apps=false"]
    try:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    except OSError as exc:
        raise AppError("Codex App Server konnte nicht gestartet werden.", code="missing_executable") from exc
    inbox = queue.Queue(maxsize=256)
    stopped = threading.Event()
    deadline = time.monotonic() + timeout
    events, messages = [], {}
    thread_id = turn_id = None
    usage = None

    def enqueue(value):
        while not stopped.is_set():
            try:
                inbox.put(value, timeout=.1)
                return
            except queue.Full:
                pass

    def receive():
        try:
            for line in process.stdout:
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    enqueue(value)
        finally:
            enqueue(None)

    def errors():
        for line in process.stderr:
            activity.observe_stderr(line)

    workers = [threading.Thread(target=receive, daemon=True), threading.Thread(target=errors, daemon=True)]
    for worker in workers:
        worker.start()

    def send(value):
        try:
            process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise AppError("Codex-Stream wurde vorzeitig geschlossen.", code="codex_failed") from exc

    def emit(value):
        events.append(value)
        activity.observe(json.dumps(value))

    def failure(error):
        emit({"type": "turn.failed", "error": error})
        return subprocess.CompletedProcess(args, 1, "\n".join(json.dumps(e) for e in events), "")

    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "podcast_automate", "title": "Podcast Studio", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True}}})
        while True:
            if cancel_check is not None and cancel_check():
                raise AppError("Modellaufruf angehalten.", code="interrupted", status="interrupted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppError("Zeitlimit des einzelnen Codex-Aufrufs erreicht.", code="timeout")
            try:
                event = inbox.get(timeout=min(.2, remaining))
            except queue.Empty:
                continue
            if event is None:
                return failure({"message": "Codex app-server stream closed before turn completion"})
            # Never retain raw protocol payloads (prompts, account data, raw reasoning).
            method, params = event.get("method"), event.get("params") or {}
            if "id" in event and method:
                # No interactive approvals, external tools or credential refresh by this client.
                send({"id": event["id"], "error": {"code": -32601, "message": "Unsupported client request"}})
                return failure({"message": "Unexpected interactive request from Codex app-server"})
            if "id" in event:
                if "error" in event:
                    return failure(event["error"])
                result = event.get("result") or {}
                if event["id"] == 1:
                    send({"method": "initialized", "params": {}})
                    send({"id": 2, "method": "config/read", "params": {"includeLayers": False}})
                elif event["id"] == 2:
                    # app-server has no exec --ignore-user-config. Explicitly disable
                    # user MCP servers and tools, and clear custom editorial instructions.
                    config = result.get("config") or {}
                    overrides = {
                        "web_search": "live" if search else "disabled", "project_doc_max_bytes": 0,
                        "developer_instructions": "",
                        "features.shell_tool": False, "features.unified_exec": False,
                        "features.js_repl": False, "features.apps": False, "features.hooks": False,
                        "features.multi_agent": False, "features.memories": False,
                        "mcp_servers": {name: {"enabled": False} for name in (config.get("mcp_servers") or {})},
                    }
                    if effort is not None:
                        overrides["model_reasoning_effort"] = effort
                    send({"id": 3, "method": "thread/start", "params": {
                        "model": model, "modelProvider": "openai", "approvalPolicy": "never",
                        "sandbox": "read-only", "ephemeral": True, "cwd": str(cwd),
                        "config": overrides, "developerInstructions": "",
                        "selectedCapabilityRoots": [], "environments": [],
                        "allowProviderModelFallback": False}})
                elif event["id"] == 3:
                    if (result.get("modelProvider") != "openai" or
                            (model and result.get("model") != model) or
                            not result.get("thread", {}).get("ephemeral")):
                        return failure({"message": "Codex app-server did not preserve model/provider/ephemeral settings"})
                    thread_id = result["thread"]["id"]
                    activity.diagnostic("stream.connected")
                    request = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}],
                               "outputSchema": schema, "summary": "concise"}
                    if effort is not None:
                        request["effort"] = effort
                    send({"id": 4, "method": "turn/start", "params": request})
                elif event["id"] == 4:
                    turn_id = result["turn"]["id"]
                continue
            if params.get("threadId") != thread_id or thread_id is None:
                continue
            event_turn = params.get("turnId") or (params.get("turn") or {}).get("id")
            if turn_id and event_turn and event_turn != turn_id:
                continue
            if method == "turn/started":
                turn_id = params["turn"]["id"]
                emit({"type": "turn.started"})
            elif method in {"item/agentMessage/delta", "item/reasoning/summaryTextDelta"}:
                delta = params.get("delta", "")
                if not isinstance(delta, str) or not delta:
                    continue
                kind = "text" if method == "item/agentMessage/delta" else "reasoning"
                stream = str(params.get("itemId", ""))
                if kind == "reasoning":
                    stream += ":" + str(params.get("summaryIndex", 0))
                activity.stream_delta(kind, delta, stream)
            elif method in {"item/started", "item/completed"}:
                item = params.get("item") or {}
                if item.get("type") == "webSearch":
                    normalized = {k: item[k] for k in ("id", "query", "action") if k in item}
                    normalized["type"] = "web_search"
                    emit({"type": method.replace("/", "."), "item": normalized})
                elif method == "item/completed" and item.get("type") == "agentMessage":
                    if item.get("phase") != "commentary":
                        messages[item["id"]] = item.get("text", "")
                elif method == "item/started" and item.get("type") == "reasoning":
                    activity.record("Modell hat die Reasoning-Phase begonnen; sichtbare Zusammenfassungen folgen, sofern verfügbar")
            elif method == "thread/tokenUsage/updated":
                total = (params.get("tokenUsage") or {}).get("total") or {}
                usage = {snake: total[camel] for camel, snake in (
                    ("inputTokens", "input_tokens"), ("cachedInputTokens", "cached_input_tokens"),
                    ("outputTokens", "output_tokens"), ("reasoningOutputTokens", "reasoning_output_tokens"),
                    ("cacheWriteInputTokens", "cache_write_input_tokens")) if camel in total}
            elif method == "error":
                # Retryable errors do not invalidate a later successful terminal event.
                emit({"type": "error", "error": params.get("error") or {}})
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("status") != "completed":
                    return failure(turn.get("error") or {"message": "Codex turn " + str(turn.get("status"))})
                # The completed item is authoritative; deltas are display-only.
                if not messages:
                    return failure({"message": "Codex stream completed without a final message"})
                response_file.write_text(next(reversed(messages.values())), encoding="utf-8")
                emit({"type": "turn.completed", "usage": usage})
                return subprocess.CompletedProcess(args, 0, "\n".join(json.dumps(e) for e in events), "")
    finally:
        # The process belongs to this call only. Never leave an idle app-server
        # or a cancelled model turn running after the caller has returned.
        stopped.set()
        try:
            process.stdin.close()
            process.wait(timeout=2)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            stop_process_tree(process)
        for worker in workers:
            worker.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
