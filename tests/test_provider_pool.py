"""Per-call provider choice in real runs: switch after a quota error, pause when both are out, fixed stays fixed."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from podcast_automate import subscriptions
from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.models import RuntimeSettings, TextProbeOutput, TopicBrief
from podcast_automate.provider_pool import AdapterPool, text_generation_settings
from podcast_automate.runner import run_probe
from podcast_automate.script_budget import calls_per_episode
from podcast_automate.scripting import outline_hash, run_script
from podcast_automate.status_summary import ProgressDigest, update_summary
from podcast_automate.storage import init_project, read_yaml, write_json
from podcast_automate.studio import BriefProposal
from podcast_automate.studio_worker import perform
from tests import script_fixtures as fixtures
from tests import test_studio


STORE_ENV = patch.dict(os.environ, {"PLA_SUBSCRIPTIONS_STORE": str(Path(tempfile.mkdtemp()) / "subscriptions.json")})


def setUpModule():
    # Never touch ~/.podcast-automate from a test, whichever path a run takes.
    STORE_ENV.start()


def tearDownModule():
    STORE_ENV.stop()


def snapshot(provider, *, available, usable=True, resets_at=None, reason=None):
    return {"provider": provider, "available": available, "usable": usable, "resets_at": resets_at,
            "blocked_until": resets_at if provider == "claude_code" else None, "reason": reason,
            "plan": "max" if provider == "claude_code" else "prolite", "windows": [], "login": {}}


class QuotaFakes:
    """Both subscriptions as the pool sees them; a quota failure flips the provider that raised it."""

    def __init__(self, test_case, *, codex=True, claude=True):
        self.codex, self.claude = codex, claude
        self.reset = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        self.claude_reset = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        store = Path(tempfile.mkdtemp()) / "subscriptions.json"
        for target in (patch.dict(os.environ, {"PLA_SUBSCRIPTIONS_STORE": str(store)}),
                       patch.object(subscriptions, "codex_quota", side_effect=self.codex_quota),
                       patch.object(subscriptions, "claude_quota", side_effect=self.claude_quota)):
            target.start()
            test_case.addCleanup(target.stop)

    def codex_quota(self, settings, *, refresh=False, clock=None):
        return snapshot("codex_cli", available=self.codex, resets_at=None if self.codex else self.reset,
                        reason=None if self.codex else "rate_limit_reached")

    def claude_quota(self, *, refresh=False, clock=None):
        return snapshot("claude_code", available=self.claude, resets_at=None if self.claude else self.claude_reset,
                        reason=None if self.claude else "opus_limit")


class AutomaticScriptRunTests(fixtures.ScriptProjectCase):
    def test_claude_limit_switches_the_failing_call_to_codex_without_repeating_finished_work(self):
        fakes = QuotaFakes(self)
        codex_calls, claude_calls = [], []

        def claude(adapter, prompt, output_type, directory, **kwargs):
            claude_calls.append((directory.name, adapter.model, adapter.reasoning_effort))
            if len(claude_calls) == 3:
                fakes.claude = False
                raise AppError("Claude-Abo-Kontingent erreicht", code="claude_quota_exhausted", status="waiting_for_quota")
            return self.model(prompt, output_type, directory, **kwargs)

        def codex(adapter, prompt, output_type, directory, **kwargs):
            codex_calls.append((directory.name, adapter.settings.codex_model, adapter.reasoning_effort))
            return self.model(prompt, output_type, directory, **kwargs)

        with patch("podcast_automate.scripting.CodexAdapter.structured", autospec=True, side_effect=codex), \
                patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", autospec=True, side_effect=claude):
            run = run_script(self.root, backend="auto")
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(self.calls), 11)
        self.assertEqual([c[0] for c in claude_calls], ["call_001", "call_002", "call_003"])
        self.assertEqual(claude_calls[0][1:], ("claude-opus-5-5", "xhigh"))
        self.assertEqual(codex_calls[0], ("call_003", "gpt-6-astra", "xhigh"))
        self.assertEqual(len(codex_calls), 9)
        work = self.root / "runs" / run.run_id
        self.assertEqual(json.loads((work / "budget.json").read_text())["model_calls"], 1 + calls_per_episode() + 1)
        first = json.loads((work / "calls/call_001/provider_choice.json").read_text(encoding="utf-8"))
        self.assertEqual((first["provider"], first["mode"], first["reason"]), ("claude_code", "auto", "claude_available"))
        switched = json.loads((work / "calls/call_003/provider_choice.json").read_text(encoding="utf-8"))
        self.assertEqual(switched["provider"], "codex_cli")
        self.assertIn("claude_exhausted_until", switched["reason"])
        switch = json.loads((work / "calls/call_003/provider_switch.json").read_text(encoding="utf-8"))
        self.assertEqual((switch["from"], switch["to"], switch["error_code"]), ("claude_code", "codex_cli", "claude_quota_exhausted"))
        self.assertFalse((work / "calls/call_004/provider_switch.json").exists())
        request = json.loads((work / "script_request.json").read_text(encoding="utf-8"))
        self.assertEqual((request["text_generation"]["provider"], request["text_generation"]["prefer"]), ("auto", "claude_code"))
        self.assertEqual(request["text_generation"]["candidates"]["claude_code"], {"model": "claude-opus-5-5", "reasoning_effort": "xhigh"})
        self.assertEqual(request["text_generation"]["adapter_versions"], {"claude_code": "claude_code.v1"})
        self.assertEqual(read_yaml(self.root / "reports/script_quality.yaml")["text_generation"]["provider"], "auto")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("finished")), \
                patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", side_effect=AssertionError("finished")):
            self.assertEqual(run_script(self.root, resume=True, run_id=run.run_id).status, "completed")

    def test_resume_rejects_a_changed_candidate_form_or_provider(self):
        QuotaFakes(self)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model), \
                patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", side_effect=self.model):
            planned = run_script(self.root, backend="auto", plan_only=True)
        work = self.root / "runs" / planned.run_id
        with self.assertRaises(AppError) as changed:
            run_script(self.root, resume=True, run_id=planned.run_id, backend="codex_cli")
        self.assertEqual(changed.exception.code, "inputs_changed")
        with self.assertRaises(AppError) as changed:
            run_script(self.root, resume=True, run_id=planned.run_id, reasoning_effort="max")
        self.assertEqual(changed.exception.code, "inputs_changed")
        request = json.loads((work / "script_request.json").read_text(encoding="utf-8"))
        request["text_generation"]["candidates"]["claude_code"]["reasoning_effort"] = "max"
        write_json(work / "script_request.json", request)
        with self.assertRaises(AppError) as changed:
            run_script(self.root, resume=True, run_id=planned.run_id, approved_plan_hash=outline_hash(work))
        self.assertEqual(changed.exception.code, "inputs_changed")

    def test_both_subscriptions_out_pauses_before_any_call_naming_the_earliest_reset(self):
        fakes = QuotaFakes(self, codex=False, claude=False)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("no call")), \
                patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", side_effect=AssertionError("no call")):
            run = run_script(self.root, backend="auto")
        self.assertEqual(run.status, "waiting_for_quota")
        error = run.stages["planning"].error
        self.assertEqual(error.code, "subscriptions_exhausted")
        self.assertIn("Frühester Reset", error.message)
        self.assertIn("Codex", error.message)
        work = self.root / "runs" / run.run_id
        # The reservation of a call that never reached a provider is refunded, not charged.
        budget = json.loads((work / "budget.json").read_text())
        self.assertEqual((budget["model_calls"], budget["sequence"], budget["refunded"]), (0, 1, [1]))
        self.assertFalse((work / "calls/call_001/provider_choice.json").exists())
        fakes.codex = True
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            self.assertEqual(run_script(self.root, resume=True, run_id=run.run_id).status, "completed")

    def test_second_quota_failure_pauses_with_both_resets_and_keeps_the_call_directory(self):
        fakes = QuotaFakes(self, claude=False)

        def codex(prompt, output_type, directory, **kwargs):
            fakes.codex = False
            raise AppError("Abo-Kontingent erreicht", code="quota_exhausted", status="waiting_for_quota")

        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=codex), \
                patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", side_effect=AssertionError("blocked")):
            run = run_script(self.root, backend="auto")
        self.assertEqual(run.status, "waiting_for_quota")
        self.assertEqual(run.stages["planning"].error.code, "subscriptions_exhausted")
        self.assertIn("Sperre bis", run.stages["planning"].error.message)
        work = self.root / "runs" / run.run_id
        choice = json.loads((work / "calls/call_001/provider_choice.json").read_text(encoding="utf-8"))
        self.assertEqual(choice["provider"], "codex_cli")
        self.assertFalse((work / "calls/call_001/provider_switch.json").exists())

    def test_fixed_providers_never_query_quota_and_claude_runs_use_claude_only(self):
        with patch.object(subscriptions, "codex_quota", side_effect=AssertionError("no quota query")), \
                patch.object(subscriptions, "claude_quota", side_effect=AssertionError("no quota query")), \
                patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            self.assertEqual(run_script(self.root).status, "completed")
        with self.assertRaises(AppError) as invalid:
            run_script(self.root, backend="auto", model="gpt-6-astra")
        self.assertEqual(invalid.exception.code, "invalid_backend")
        seen = []

        def claude(adapter, prompt, output_type, directory, **kwargs):
            seen.append((adapter.model, adapter.reasoning_effort))
            return self.model(prompt, output_type, directory, **kwargs)

        with patch.object(subscriptions, "codex_quota", side_effect=AssertionError("no quota query")), \
                patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("no codex")), \
                patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", autospec=True, side_effect=claude):
            run = run_script(self.root, backend="claude_code", reasoning_effort="max")
        self.assertEqual(run.status, "completed")
        self.assertEqual(set(seen), {("claude-opus-5-5", "max")})
        request = json.loads((self.root / "runs" / run.run_id / "script_request.json").read_text(encoding="utf-8"))
        self.assertEqual(request["text_generation"]["adapter_version"], "claude_code.v1")
        choice = json.loads((self.root / "runs" / run.run_id / "calls/call_001/provider_choice.json").read_text(encoding="utf-8"))
        self.assertEqual((choice["provider"], choice["mode"]), ("claude_code", "fixed"))


class PoolUnitTests(unittest.TestCase):
    def test_openrouter_text_keeps_openrouter_but_research_uses_the_subscription_rule(self):
        config = TopicBrief(topic="Thema")
        pool = AdapterPool(config.runtime, text_generation_settings(config, backend="openrouter", model="openai/gpt-6-astra"),
                           api_key="test-key")
        self.assertEqual(pool.plan()[0], "openrouter")
        mode, prefer, candidates = pool.plan(search=True)
        self.assertEqual((mode, prefer, set(candidates)), ("auto", "claude_code", {"codex_cli", "claude_code"}))
        auto = text_generation_settings(config, backend="auto")
        self.assertEqual(auto["candidates"]["codex_cli"], {"model": "gpt-6-astra", "reasoning_effort": "xhigh"})
        self.assertEqual(auto["candidates"]["claude_code"], {"model": "claude-opus-5-5", "reasoning_effort": "xhigh"})
        self.assertEqual(auto["prefer"], "claude_code")
        self.assertIsNone(auto["model"])
        for kwargs in ({"model": "x"}, {"reasoning_effort": "low"}, {"max_output_tokens": 10}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AppError):
                text_generation_settings(config, backend="auto", **kwargs)
        claude = text_generation_settings(config, backend="claude_code")
        self.assertEqual((claude["model"], claude["reasoning_effort"]), ("claude-opus-5-5", "xhigh"))
        with self.assertRaises(AppError):
            text_generation_settings(config, backend="claude_code", model="anthropic/claude-fable-5.1")
        self.assertEqual(text_generation_settings(config, backend="claude_code", model="opus")["model"], "claude-opus-5-5")
        # A named Opus 5 stays Opus 5; only the bare alias follows the catalog default.
        self.assertEqual(text_generation_settings(config, backend="claude_code", model="anthropic/claude-opus-5")["model"],
                         "claude-opus-5")


class ProbeCliAndDoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Projekt ä"
        init_project(self.root, TopicBrief(topic="Energiebasierte Modelle"))

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(args))
        return code, json.loads(output.getvalue())

    def test_text_probe_with_claude_binds_the_backend_to_the_run(self):
        result = TextProbeOutput(topic="Energiebasierte Modelle", focus_questions=["Warum?"], note="Keine Recherche")
        with patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", return_value=(result, {"provider": "claude_code"})), \
                patch("podcast_automate.runner.CodexAdapter.probe", side_effect=AssertionError("no codex")):
            code, data = self.invoke("text-probe", str(self.root), "--backend", "claude_code", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(data["status"], "completed")
        run_id = data["run"]["run_id"]
        self.assertEqual(json.loads((self.root / "runs" / run_id / "probe_request.json").read_text())["backend"], "claude_code")
        self.assertTrue((self.root / "probes/text" / run_id / "metadata.json").is_file())
        with self.assertRaises(AppError) as changed:
            run_probe(self.root, resume=True, run_id=run_id, backend="codex_cli")
        self.assertEqual(changed.exception.code, "inputs_changed")
        code, data = self.invoke("resume", str(self.root), "--run-id", run_id, "--api-key", "x", "--json")
        self.assertEqual((code, data["code"]), (1, "invalid_backend"))

    def test_quota_command_and_doctor_report_both_subscriptions(self):
        overview = {"checked_at": "2026-09-19T10:00:00+00:00", "any_usable": True, "any_available": False,
                    "codex_cli": snapshot("codex_cli", available=False, resets_at="2026-09-22T20:31:18+00:00", reason="rate_limit_reached"),
                    "claude_code": snapshot("claude_code", available=False, usable=False, reason="authentication_required"),
                    "lines": ["Codex-Abo (prolite): kein Kontingent", "Claude-Abo: nicht nutzbar"]}
        with patch("podcast_automate.subscriptions.quota_overview", return_value=overview):
            code, data = self.invoke("quota", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(data["status"], "waiting_for_quota")
        self.assertEqual(data["lines"][0], "Codex-Abo (prolite): kein Kontingent")
        available = {**overview, "any_available": True,
                     "claude_code": {**snapshot("claude_code", available=True), "login": {"cli_version": "2.1.92"}}}
        with patch("podcast_automate.doctor.quota_overview", return_value=available), \
                patch("podcast_automate.doctor.CodexAdapter.check_login",
                      side_effect=AppError("codex login", code="authentication_required", status="blocked")):
            code, data = self.invoke("doctor", "--skip-tts", "--json")
        names = {check["name"]: check for check in data["checks"]}
        self.assertFalse(names["codex_login"]["ok"])
        self.assertTrue(names["claude_login"]["ok"])
        self.assertIn("2.1.92", names["claude_login"]["detail"])
        self.assertTrue(names["subscription_quota"]["ok"])
        self.assertTrue(data["ready"])
        self.assertEqual(code, 0)
        with patch("podcast_automate.doctor.quota_overview", return_value=overview), \
                patch("podcast_automate.doctor.CodexAdapter.check_login",
                      side_effect=AppError("codex login", code="authentication_required", status="blocked")):
            code, data = self.invoke("doctor", "--skip-tts", "--json")
        self.assertFalse(data["ready"])


class StatusAndStudioTests(test_studio.StudioHttpTests):
    def test_status_digest_follows_the_run_provider(self):
        work = self.root / "runs/run_test"
        job = {"id": "job_one", "status": "running", "run": {"run_id": "run_test", "kind": "script", "status": "running",
               "stages": {"planning": {"status": "running"}}}}
        write_json(self.root / "studio/job.json", job)
        write_json(work / "budget.json", {"model_calls": 1, "search_rounds": 0})
        digest = ProgressDigest(summary="Der Plan entsteht.", evidence_ids=["stage"])
        seen = []

        def claude(adapter, prompt, output_type, directory, **kwargs):
            seen.append((adapter.model, adapter.reasoning_effort, adapter.settings.text_timeout_seconds, adapter.max_budget_usd))
            return digest, {"provider": "claude_code", "requested_model": adapter.model}

        write_json(work / "script_request.json", {"text_generation": {"provider": "claude_code", "model": "claude-opus-5"}})
        with patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", autospec=True, side_effect=claude):
            update_summary(self.root, job, clock=lambda: 1000)
        self.assertEqual(seen, [("claude-haiku-4-5", "low", 90, 1.0)])
        state = json.loads((work / "status_reports/state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["provider"], state["model"]), ("claude_code", "claude-haiku-4-5"))
        QuotaFakes(self, codex=False)
        write_json(work / "calls/call_002/output_schema.json", {"title": "TeachingPlan"})
        write_json(work / "script_request.json", {"text_generation": {"provider": "auto", "prefer": "codex_cli", "candidates": {}}})
        with patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", autospec=True, side_effect=claude), \
                patch("podcast_automate.status_summary.CodexAdapter.structured", side_effect=AssertionError("codex is out")):
            update_summary(self.root, job, clock=lambda: 2000)
        self.assertEqual(len(seen), 2)
        state = json.loads((work / "status_reports/state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["provider"], state["model"]), ("claude_code", "claude-haiku-4-5"))

    def test_worker_uses_claude_or_the_rule_for_the_assistant_and_research(self):
        proposal = BriefProposal(message="Vorschlag", topic="Titel", central_question="Warum?", prior_knowledge="",
                                 depth_request="Tief", focus_questions=[], excluded_topics=[])
        seen = []

        def claude(adapter, *args, **kwargs):
            seen.append((adapter.model, adapter.reasoning_effort))
            return proposal, {}

        with patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", autospec=True, side_effect=claude):
            perform(self.root, {"action": "assistant", "message": "Hilfe", "text": {"provider": "claude_code"}})
        self.assertEqual(seen, [("claude-opus-5-5", "xhigh")])
        QuotaFakes(self, codex=False)
        with patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", autospec=True, side_effect=claude), \
                patch("podcast_automate.studio_worker.CodexAdapter.structured", side_effect=AssertionError("codex is out")):
            perform(self.root, {"action": "assistant", "message": "Hilfe", "text": {"provider": "auto"}})
        self.assertEqual(len(seen), 2)
        choice = json.loads(sorted((self.root / "studio/assistant").glob("call_*/provider_choice.json"))[-1].read_text(encoding="utf-8"))
        self.assertEqual((choice["provider"], choice["mode"]), ("claude_code", "auto"))
        with patch("podcast_automate.studio_worker.run_research") as research:
            research.return_value.model_dump.return_value = {"status": "pending"}
            perform(self.root, {"action": "research", "text": {"provider": "auto"}})
            research.assert_called_once_with(self.root, backend="auto", model=None, reasoning_effort=None, plan_review="required")
            perform(self.root, {"action": "research", "text": {"provider": "openrouter", "model": "openai/gpt-6-astra"}})
            self.assertEqual(research.call_args.kwargs, {"backend": "auto", "plan_review": "required"})

    def test_job_detail_exposes_the_latest_provider_decision_and_switch(self):
        run = {"run_id": "run_test", "kind": "script", "status": "waiting_for_quota", "stages": {}}
        write_json(self.root / "studio/job.json", {"id": "saved", "status": "waiting_for_quota", "run": run})
        auto = {"provider": "auto", "prefer": "codex_cli", "candidates": {"codex_cli": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
                "claude_code": {"model": "claude-opus-5", "reasoning_effort": "high"}}}
        write_json(self.root / "runs/run_test/script_request.json", {"text_generation": auto})
        write_json(self.root / "runs/run_test/calls/call_001/provider_choice.json", {"provider": "codex_cli", "mode": "auto"})
        write_json(self.root / "runs/run_test/calls/call_002/provider_choice.json", {
            "provider": "claude_code", "model": "claude-opus-5", "mode": "auto", "reason": "codex_exhausted_until x; claude_available",
            "snapshots": {"codex_cli": snapshot("codex_cli", available=False, resets_at="2026-09-22T20:31:18+00:00")}})
        write_json(self.root / "runs/run_test/calls/call_002/provider_switch.json", {"from": "codex_cli", "to": "claude_code", "error_code": "quota_exhausted", "message": "m"})
        detail = json.loads(self.request("/api/projects/example")[1])
        choice = detail["job"]["provider_choice"]
        self.assertEqual((choice["call"], choice["provider"], choice["switch"]["from"]), ("call_002", "claude_code", "codex_cli"))
        self.assertEqual(choice["snapshots"]["codex_cli"]["resets_at"], "2026-09-22T20:31:18+00:00")
        self.assertEqual(detail["job"]["text_generation"]["provider"], "auto")
        boot = json.loads(self.request("/api/bootstrap")[1])
        self.assertEqual(boot["text_defaults"]["provider"], "auto")
        self.assertTrue(boot["capabilities"]["subscription_auto"])
        self.assertEqual(boot["text_catalog"]["auto_candidates"]["claude_code"]["model"], "claude-opus-5-5")
        self.assertEqual(boot["text_catalog"]["effort_equivalents"]["xhigh"], "xhigh")
        self.assertEqual(detail["text"]["provider"], "codex_cli")


if __name__ == "__main__":
    unittest.main()
