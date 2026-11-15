import json
import tempfile
import unittest
from pathlib import Path

from podcast_automate.call_activity import CallActivity
from podcast_automate.model_trace import ModelTrace, trace_view
from podcast_automate.question_scope import QuestionScopeReview, scoped_plan
from podcast_automate.research_tasks import QuestionPlan
from podcast_automate.errors import AppError
from tests.question_fixtures import task_value


class ModelTraceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)

    def test_shared_tail_discards_old_lines_across_parallel_calls(self):
        a = ModelTrace(self.work / "calls/call_001", "one")
        b = ModelTrace(self.work / "calls/call_002", "two")
        for n in range(45):
            writer = a if n % 2 else b
            writer.record("text", f"line-{n:02d}")
            writer.flush()
        a.finish()
        b.finish()
        saved = trace_view(self.work)
        self.assertEqual([r["text"] for r in saved["lines"]], [f"line-{n:02d}" for n in range(25, 45)])
        self.assertEqual({r["call"] for r in saved["lines"]}, {"call_001", "call_002"})
        self.assertNotIn("line-00", (self.work / "model_trace.json").read_text())
        self.assertFalse((self.work / "calls/call_001/model_trace.json").exists())

    def test_incremental_lines_and_credentials_are_redacted_before_storage(self):
        writer = ModelTrace(self.work / "calls/call_001", "test", secrets=("private-split-key",))
        writer.append("reasoning", "Checking private-split", "r")
        writer.flush()
        self.assertNotIn("private-split", (self.work / "model_trace.json").read_text())
        writer.append("reasoning", "-key now.\nNext li", "r")
        writer.append("reasoning", "ne.\n", "r")
        writer.finish()
        rows = trace_view(self.work)["lines"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["text"], "Next line.")
        self.assertNotIn("private-split-key", json.dumps(rows))

    def test_a_google_key_pattern_is_redacted_like_an_openrouter_one(self):
        from podcast_automate.model_trace import redact
        text = redact("Google said: key AIzaSyA-1234567890abcdefghijklmnop invalid; sk-or-v1-abcdef also")
        self.assertNotIn("AIzaSy", text)
        self.assertNotIn("sk-or-v1", text)
        self.assertIn("Google said", text)

    def test_codex_public_snapshot_dedup_and_permanent_timeout_diagnostics(self):
        directory = self.work / "calls/call_001"
        activity = CallActivity(directory, "ResearchDecision", "gpt-6-astra")
        for kind, text in [("item.updated", "Reading "), ("item.completed", "Reading evidence.")]:
            activity.observe(json.dumps({"type": kind, "item": {"id": "r", "type": "reasoning", "text": text,
                                        "encrypted_content": "HIDDEN"}}))
        activity.observe_stderr("stream disconnected; retrying. Bearer secret-do-not-save")
        activity.finish("timeout")
        rows = trace_view(self.work)["lines"]
        self.assertEqual([r["text"] for r in rows if r["kind"] == "reasoning"], ["Reading evidence."])
        diag = json.loads((directory / "diagnostics.json").read_text())
        self.assertEqual(diag["status"], "timeout")
        self.assertEqual(diag["stderr_lines"], 1)
        self.assertIn("connection", [e.get("category") for e in diag["events"]])
        self.assertNotIn("HIDDEN", json.dumps(rows))
        self.assertNotIn("secret-do-not-save", json.dumps(diag) + json.dumps(rows))

    def test_structured_output_retains_meaning_instead_of_json_scaffolding(self):
        writer = ModelTrace(self.work / "calls/call_001", "test")
        payload = {"action": "search_local", "reason": "Die Definition des gemeinsamen Einflusses fehlt.",
                   "searches": [{"query": "common cause", "source_id": "src_internal", "offset": 0,
                                 "include_notes": False}], "windows": [], "web_queries": [], "answer": None}
        raw = json.dumps(payload, indent=2, ensure_ascii=False)
        for offset in range(0, len(raw), 7):
            writer.append("text", raw[offset:offset+7], "answer")
        writer.finish()
        rows = trace_view(self.work)["lines"]
        self.assertEqual([row["text"] for row in rows], ["Vorhandene Quellen durchsuchen",
            "Einordnung: Die Definition des gemeinsamen Einflusses fehlt.", "Suchbegriff: common cause"])
        stored = (self.work / "model_trace.json").read_text(encoding="utf-8")
        for field in ("web_queries", "include_notes", "src_internal"):
            self.assertNotIn(field, stored)

    def test_compact_json_and_legacy_tail_are_readable_without_a_model_call(self):
        from podcast_automate.model_trace import readable_output
        self.assertEqual(readable_output('{"web_queries":["primary evidence"],"answer":null}'),
                         "Websuche: primary evidence")
        (self.work / "model_trace.json").write_text(json.dumps({"lines": [
            {"kind": "text", "text": line, "at": "today"} for line in
            ['"query": "common cause",', '"web_queries": [],', '"source_id": "internal",', '}']]}))
        self.assertEqual([r["text"] for r in trace_view(self.work)["lines"]], ["Suchbegriff: common cause"])

    def test_a_scope_split_preserves_coverage_and_every_criterion(self):
        # The state migration refine_state was never called by the pipeline and is gone; the split it rested on is
        # scoped_plan, which plan_tasks uses for both scope passes.
        one, two = task_value(), task_value("task_methods")
        two["acceptance"] = ["Randomization", "Forecasts"]
        plan = QuestionPlan(tasks=[one, two])
        review = QuestionScopeReview(decisions=[
            {"task_id": one["id"], "reason": "One concept", "parts": []},
            {"task_id": two["id"], "reason": "Two independent methods", "parts": [
                {"question": title, "criterion_indices": [n], "acceptance": [title], "queries": [title], "key_terms": [title]}
                for n, title in enumerate(two["acceptance"])]}])
        result, groups = scoped_plan(plan, review)
        self.assertEqual(result.tasks[0], plan.tasks[0], "a task the review keeps stays as it was")
        self.assertEqual(len(result.tasks), 3)
        self.assertEqual(groups[two["id"]], ["task_methods_a", "task_methods_b"])
        review.decisions[1].parts[1].criterion_indices = [0]
        with self.assertRaises(AppError):
            scoped_plan(plan, review)
