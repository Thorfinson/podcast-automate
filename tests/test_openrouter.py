import contextlib
import getpass
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.models import Contract, RuntimeSettings, EpisodeScript
from podcast_automate.openrouter import OpenRouterAdapter, NoRedirect, ENDPOINT
from podcast_automate.script_models import ScriptReview
from podcast_automate.scripting import run_script
from podcast_automate.storage import file_hash, read_yaml
from tests import script_fixtures as fixtures


KEY = "test-only-openrouter-credential-12345"


class Detail(Contract):
    text: str
    count: int = 1


class Reply(Contract):
    detail: Detail


def envelope(content=None, **extra):
    return {"id": "gen-test", "model": "vendor/test-model", "provider": "TestProvider",
            "choices": [{"finish_reason": "stop", "message": {
                "content": content if content is not None else '{"detail":{"text":"Grüße","count":2}}'}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18, "cost": 0.002}, **extra}


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.adapter = OpenRouterAdapter(RuntimeSettings(text_timeout_seconds=7), model="vendor/test-model", api_key=KEY)
        self.requests = []

    def call(self, result, **kwargs):
        def respond(request, timeout):
            self.requests.append((request, timeout))
            if isinstance(result, Exception):
                raise result
            return io.BytesIO(json.dumps(result).encode() if not isinstance(result, bytes) else result)
        with patch("podcast_automate.openrouter.build_opener") as opener:
            opener.return_value.open.side_effect = respond
            return self.adapter.structured("Explain this topic.", Reply, self.root / "call", prompt_version="test", **kwargs)

    def test_authenticated_schema_request_validated_output_and_cost_without_secret(self):
        output, meta = self.call(envelope())
        request, timeout = self.requests[0]
        body = json.loads(request.data)
        self.assertTrue(body["stream"])
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        self.assertEqual(timeout, 7)
        self.assertNotIn(KEY, request.data.decode())
        self.assertEqual(body["provider"], {"require_parameters": True, "sort": "throughput"})
        schema = body["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertEqual(schema["schema"]["$defs"]["Detail"]["required"], ["text", "count"])
        self.assertNotIn("default", schema["schema"]["$defs"]["Detail"]["properties"]["count"])
        self.assertFalse(schema["schema"]["$defs"]["Detail"]["additionalProperties"])
        self.assertEqual(output.detail.text, "Grüße")
        self.assertEqual(meta["separately_billed_cost"], 0.002)
        self.assertEqual(meta["actual_model"], "vendor/test-model")
        self.assertFalse(meta["research_performed"])
        self.assertNotIn(KEY, repr(self.adapter.__dict__))
        for path in self.root.rglob("*.json"):
            self.assertNotIn(KEY, path.read_text(encoding="utf-8"))

    def test_http_errors_are_actionable_and_never_persist_raw_provider_errors(self):
        for code, expected, state in [(401, "openrouter_authentication", "blocked"),
                                      (402, "openrouter_credits", "waiting_for_quota"),
                                      (429, "openrouter_rate_limit", "waiting_for_quota"),
                                      (503, "openrouter_unavailable", "blocked"),
                                      (400, "openrouter_request", "blocked")]:
            with self.subTest(code=code), self.assertRaises(AppError) as caught:
                self.call(HTTPError(ENDPOINT, code, KEY, {}, io.BytesIO(KEY.encode())))
            self.assertEqual(caught.exception.code, expected)
            self.assertEqual(caught.exception.status, state)
            self.assertNotIn(KEY, str(caught.exception))
        self.assertFalse((self.root / "call/response.json").exists())

    def test_reasoning_is_explicit_only_when_selected_and_is_recorded(self):
        self.call(envelope())
        self.assertNotIn("reasoning", json.loads(self.requests[-1][0].data))
        self.adapter = OpenRouterAdapter(RuntimeSettings(), model="vendor/test-model", api_key=KEY,
                                         reasoning_effort="high")
        _, metadata = self.call(envelope())
        self.assertEqual(json.loads(self.requests[-1][0].data)["reasoning"], {"effort": "high", "exclude": False})
        self.assertEqual(metadata["requested_reasoning_effort"], "high")

    def test_http_200_error_body_is_not_treated_as_completed_work(self):
        with self.assertRaises(AppError) as caught:
            self.call({"error": {"code": 429, "message": KEY}})
        self.assertEqual(caught.exception.code, "openrouter_rate_limit")
        self.assertNotIn(KEY, str(caught.exception))

    def test_requested_models_keep_exact_ids_and_deepseek_max_in_schema_requests(self):
        for model, effort in (("openai/gpt-6-astra", None), ("openai/gpt-6-astra-pro", "xhigh"),
                              ("anthropic/claude-fable-5.1", None), ("deepseek/deepseek-v4.1-flash", "max")):
            with self.subTest(model=model):
                self.adapter = OpenRouterAdapter(RuntimeSettings(), model=model, api_key=KEY, reasoning_effort=effort)
                _, metadata = self.call(envelope(model=model))
                body = json.loads(self.requests[-1][0].data)
                self.assertEqual(body["model"], model)
                self.assertEqual(body["response_format"]["type"], "json_schema")
                self.assertEqual(body["provider"]["require_parameters"], True)
                self.assertEqual(metadata["requested_model"], model)
                if effort:
                    self.assertEqual(body["reasoning"]["effort"], effort)
        with self.assertRaises(AppError):
            OpenRouterAdapter(RuntimeSettings(), model="deepseek/deepseek-v4.1-flash", api_key=KEY, reasoning_effort="xhigh")

    def test_invalid_truncated_refused_and_incomplete_responses_are_rejected(self):
        cases = [b"not JSON", [], {"choices": []}, {"choices": [None]}, {"choices": ["invalid"]},
                 envelope('{"unknown":"value"}'),
                 envelope(choices=[{"finish_reason": "length", "message": {"content": "{}"}}]),
                 envelope(choices=[{"finish_reason": "content_filter", "message": {"content": None}}])]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(AppError):
                self.call(value)
        self.assertFalse((self.root / "call/response.json").exists())

    def test_credential_echo_in_content_or_metadata_is_blocked(self):
        for value in [envelope(json.dumps({"detail": {"text": KEY, "count": 1}})), envelope(model=KEY)]:
            with self.assertRaises(AppError) as caught:
                self.call(value)
            self.assertEqual(caught.exception.code, "credential_in_response")
        self.assertFalse((self.root / "call/response.json").exists())
        for path in self.root.rglob("*.json"):
            self.assertNotIn(KEY, path.read_text(encoding="utf-8"))

    def test_streaming_text_reasoning_usage_and_keepalives(self):
        chunks = [
            {"id": "gen-stream", "model": "vendor/test-model", "choices": [{"delta": {
                "reasoning_details": [{"type": "reasoning.summary", "summary": "Compare the evidence.\n"},
                                      {"type": "reasoning.encrypted", "data": "DO-NOT-SAVE"}]}}]},
            {"choices": [{"delta": {"content": '{"detail":{"text":"Grü'}}]},
            {"choices": [{"delta": {"content": 'ße","count":2}}'}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 7, "cost": 0.002}}]
        raw = b": OPENROUTER PROCESSING\n\n" + b"".join(
            ("data: " + json.dumps(c, ensure_ascii=False) + "\n\n").encode() for c in chunks) + b"data: [DONE]\n\n"
        result, metadata = self.call(raw)
        self.assertEqual(result.detail.text, "Grüße")
        self.assertEqual(metadata["separately_billed_cost"], 0.002)
        trace = (self.root / "call/model_trace.json").read_text(encoding="utf-8")
        self.assertIn("Compare the evidence", trace)
        self.assertIn("Grüße", trace)
        self.assertNotIn("DO-NOT-SAVE", trace)

    def test_stream_failure_keeps_trace_but_never_publishes_partial_answer(self):
        raw = ('data: {"choices":[{"delta":{"reasoning":"A visible interim note.\\n"}}]}\n\n'
               'data: {"error":{"code":"server_error","message":"private-raw-error"}}\n\n').encode()
        with self.assertRaises(AppError) as caught:
            self.call(raw)
        self.assertEqual(caught.exception.code, "openrouter_unavailable")
        self.assertFalse((self.root / "call/response.json").exists())
        self.assertIn("A visible interim note", (self.root / "call/model_trace.json").read_text())
        self.assertNotIn("private-raw-error", "".join(p.read_text() for p in self.root.rglob("*.json")))

    def test_truncated_sse_and_secret_split_between_chunks_are_rejected(self):
        with self.assertRaises(AppError):
            self.call(b'data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}\n\n')
        raw = b"".join(("data: " + json.dumps({"choices": [{"delta": {"reasoning": part}}]}) + "\n\n").encode()
                       for part in (KEY[:12], KEY[12:])) + b"data: [DONE]\n\n"
        with self.assertRaises(AppError) as caught:
            self.call(raw)
        self.assertEqual(caught.exception.code, "credential_in_response")
        saved = "".join(p.read_text() for p in self.root.rglob("*.json"))
        self.assertNotIn(KEY, saved)
        self.assertNotIn(KEY[:12], saved)

    def test_network_failure_is_resumable_and_redirect_does_not_forward_authorization(self):
        with self.assertRaises(AppError) as caught:
            self.call(URLError(KEY))
        self.assertEqual(caught.exception.code, "openrouter_connection")
        self.assertNotIn(KEY, str(caught.exception))
        req = Request(ENDPOINT, headers={"Authorization": "Bearer " + KEY})
        self.assertIsNone(NoRedirect().redirect_request(req, None, 307, "redirect", {}, "https://example.org"))

    def test_key_is_required_and_env_fallback_is_explicitly_overridable(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            self.adapter = OpenRouterAdapter(RuntimeSettings(), model="vendor/test-model")
            with self.assertRaises(AppError) as caught:
                self.call(envelope())
        self.assertEqual(caught.exception.code, "openrouter_key_required")
        self.assertEqual(self.requests, [])
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}):
            self.adapter = OpenRouterAdapter(RuntimeSettings(), model="vendor/test-model")
            self.call(envelope())
            self.assertEqual(self.requests[-1][0].get_header("Authorization"), "Bearer " + KEY)
            self.adapter = OpenRouterAdapter(RuntimeSettings(), model="vendor/test-model", api_key="replacement-key")
            self.call(envelope())
            self.assertEqual(self.requests[-1][0].get_header("Authorization"), "Bearer replacement-key")

    def test_search_and_credentials_in_prompt_never_reach_transport(self):
        with self.assertRaises(AppError):
            OpenRouterAdapter(RuntimeSettings(), model="vendor/test-model:online", api_key=KEY)
        with self.assertRaises(AppError) as caught:
            self.call(envelope(), search=True)
        self.assertEqual(caught.exception.code, "openrouter_search_unsupported")
        with patch("podcast_automate.openrouter.build_opener") as opener, self.assertRaises(AppError) as caught:
            self.adapter.structured(KEY, Reply, self.root, prompt_version="test")
        self.assertEqual(caught.exception.code, "credential_in_prompt")
        opener.assert_not_called()


class OpenRouterScriptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root

    def test_cli_hidden_key_runs_all_quality_stages_without_changing_project_or_spawning_codex(self):
        before = file_hash(self.root / "project.yaml")
        output = io.StringIO()
        with patch("podcast_automate.openrouter.OpenRouterAdapter.structured", side_effect=self.fixture.model), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("No Codex")), \
             patch("podcast_automate.cli.getpass.getpass", return_value=KEY), contextlib.redirect_stdout(output):
            code = main(["script", str(self.root), "--backend", "openrouter", "--model", "vendor/test-model", "--api-key", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.fixture.calls), 11)
        self.assertEqual(before, file_hash(self.root / "project.yaml"))
        self.assertNotIn(KEY, output.getvalue())
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(KEY.encode(), path.read_bytes())
        report = read_yaml(self.root / "reports/script_quality.yaml")
        self.assertEqual(report["text_generation"]["provider"], "openrouter")
        self.assertEqual(report["episodes"]["ep_001"]["teaching_review"]["status"], "passed")
        self.assertFalse(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])

    def test_rate_limit_resume_restores_provider_and_model_with_rotated_key_without_rewriting(self):
        paused = False
        def model(prompt, output_type, directory, **kwargs):
            nonlocal paused
            if output_type is ScriptReview and not paused:
                paused = True
                raise AppError("OpenRouter rate limit", code="openrouter_rate_limit", status="waiting_for_quota")
            return self.fixture.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.openrouter.OpenRouterAdapter.structured", side_effect=model):
            first = run_script(self.root, backend="openrouter", model="vendor/test-model", api_key=KEY)
            self.assertEqual(first.status, "waiting_for_quota")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["resume", str(self.root), "--api-key", "rotated-key", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(self.fixture.calls.count(EpisodeScript), 2)
        self.assertEqual(json.loads(output.getvalue())["run"]["run_id"], first.run_id)

    def test_resume_rejects_changed_backend_model_or_token_limit_before_calls(self):
        with patch("podcast_automate.openrouter.OpenRouterAdapter.structured", side_effect=self.fixture.model):
            run_script(self.root, backend="openrouter", model="vendor/test-model", api_key=KEY)
            calls = len(self.fixture.calls)
            for change in ({"model": "vendor/other"}, {"backend": "codex_cli"}, {"max_output_tokens": 40000}):
                with self.subTest(change=change), self.assertRaises(AppError) as caught:
                    run_script(self.root, resume=True, api_key=KEY, **change)
                self.assertEqual(caught.exception.code, "inputs_changed")
            self.assertEqual(len(self.fixture.calls), calls)

    def test_missing_key_blocks_without_network_or_budget_consumption(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}), \
             patch("podcast_automate.openrouter.build_opener", side_effect=AssertionError("No request")):
            run = run_script(self.root, backend="openrouter", model="vendor/test-model")
        self.assertEqual(run.status, "blocked")
        self.assertEqual(run.stages["planning"].error.code, "openrouter_key_required")
        self.assertFalse((self.root / "runs" / run.run_id / "budget.json").exists())

    def test_no_plaintext_fallback_when_console_cannot_hide_key(self):
        output = io.StringIO()
        with patch("podcast_automate.cli.getpass.getpass", side_effect=getpass.GetPassWarning("Cannot hide")), \
             contextlib.redirect_stdout(output):
            code = main(["script", str(self.root), "--backend", "openrouter", "--model", "vendor/test-model", "--api-key", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "openrouter_key_required")


if __name__ == "__main__":
    unittest.main()
