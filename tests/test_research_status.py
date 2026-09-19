import json
import tempfile
import unittest
from pathlib import Path

from podcast_automate.research_status import work_insight, record_request
from podcast_automate.status_summary import evidence_snapshot
from podcast_automate.storage import write_json, file_hash


class ResearchStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / "runs/run_test"
        self.call = self.work / "calls/call_002"
        self.run = {"run_id": "run_test", "kind": "research", "status": "running", "stages": {}}
        self.row = {"current_refs": ["s1#p1", "s1#p2"], "actions": [{"reason": "Die Definition fehlt."}],
                    "feedback": ["Den gemeinsamen Einfluss erklären."], "step": 3, "no_progress": 2,
                    "catalog": [{"candidates": [1, 2, 3]}]}
        self.state = {"phase": "questions", "active_task": "task_one", "index_hash": "a" * 64,
                      "plan": {"tasks": [{"id": "task_one", "question": "Was ist Causation?", "acceptance": ["Erkläre den Mechanismus."]}]},
                      "tasks": {"task_one": self.row}}
        self.save_state()
        write_json(self.work / "question_research/indexes" / ("a" * 64 + ".json"), {"value": {"sources": [
            {"id": "s1", "title": "Original paper", "raw_path": "PRIVATE_PATH", "sections": [
                {"id": "p1", "page": 4, "text": "PRIVATE_FULL_TEXT"}, {"id": "p2", "page": 5}]}]}})
        write_json(self.call / "output_schema.json", {"title": "ResearchDecision"})
        write_json(self.call / "activity.json", {"status": "running", "started_at": "2026-09-16T12:00:00Z",
                   "updated_at": "2026-09-16T12:00:01Z", "events": [{"message": "Aufruf gestartet"}, {"message": "Modell bearbeitet den Auftrag"}]})
        write_json(self.work / "budget.json", {"model_calls": 2})

    def save_state(self):
        write_json(self.work / "question_research/state.json", {"value": self.state})

    def test_existing_worker_has_specific_context_without_changing_its_inputs(self):
        paths = [self.work / "question_research/state.json", self.work / "budget.json"]
        before = [file_hash(p) for p in paths]
        info = work_insight(self.work, self.run)
        self.assertEqual(info["basis"], "saved_state")
        self.assertEqual(info["question"], "Was ist Causation?")
        self.assertEqual(info["last_step"], "Die Definition fehlt.")
        self.assertEqual(info["material"]["section_count"], 2)
        self.assertEqual(info["material"]["sources"][0]["pages"], [4, 5])
        self.assertEqual(info["candidate_count"], 3)
        self.assertIn("2 Arbeitsschritte", info["warning"])
        self.assertEqual(before, [file_hash(p) for p in paths])
        self.assertNotIn("PRIVATE", json.dumps(info))
        self.assertIsNone(info["signals"]["last_content_at"])
        snapshot = evidence_snapshot(self.root, self.run)
        self.assertFalse(snapshot["live_events_available"])
        self.assertIn("Den gemeinsamen Einfluss", json.dumps(snapshot))

    def test_actual_request_takes_precedence_and_only_allowlisted_content_is_saved(self):
        record_request(self.call, "ResearchDecision", "Instructions not for display\n" + json.dumps({
            "task": {"question": "Exact submitted question", "acceptance": ["Check boundary"]},
            "sources": [{"title": "Submitted paper", "sections": [{"page": 9, "text": "PRIVATE_FULL_TEXT"}]}],
            "feedback": ["Missing proof sk-testsecret"], "api_key": "PRIVATE_KEY", "raw_prompt": "PRIVATE_PROMPT"}))
        info = work_insight(self.work, self.run)
        self.assertEqual(info["basis"], "request")
        self.assertEqual(info["question"], "Exact submitted question")
        self.assertEqual(info["material"]["section_count"], 1)
        saved = (self.call / "work_context.json").read_text()
        self.assertNotIn("PRIVATE", saved)
        self.assertNotIn("sk-testsecret", saved)

    def test_old_call_and_ui_poll_do_not_become_current_model_content(self):
        write_json(self.work / "model_trace.json", {"lines": [
            {"call": "call_001", "kind": "reasoning", "at": "2026-09-16T11:00:00Z", "text": "Earlier reasoning"}]})
        write_json(self.work / "research_activity.json", {"updated_at": "2099-01-01T00:00:00Z"})
        signal = work_insight(self.work, self.run)["signals"]
        self.assertIsNone(signal["last_content_at"])
        self.assertEqual(signal["last_event_at"], "2026-09-16T12:00:01Z")
        write_json(self.call / "diagnostics.json", {"last_content_at": "2026-09-16T12:03:00Z",
                   "last_stream_event_at": "2026-09-16T12:03:01Z", "categories": {"retry": 2, "PRIVATE": 1}})
        signal = work_insight(self.work, self.run)["signals"]
        self.assertEqual(signal["last_content_at"], "2026-09-16T12:03:00Z")
        self.assertEqual(signal["categories"], {"retry": 2})

    def test_review_only_names_cited_passages_and_synthesis_does_not_inherit_old_question(self):
        self.row["answer"] = {"findings": [{"evidence": [{"reference": "s1#p2"}]}]}
        self.save_state()
        write_json(self.call / "output_schema.json", {"title": "AnswerReview"})
        self.assertEqual(work_insight(self.work, self.run)["material"]["section_count"], 1)
        self.state["phase"] = "synthesis"
        self.save_state()
        write_json(self.call / "output_schema.json", {"title": "DossierPatch"})
        info = work_insight(self.work, self.run)
        self.assertEqual(info["question"], "")
        self.assertEqual(info["feedback"], [])

    def test_received_but_filtered_deltas_do_not_become_readable_progress(self):
        write_json(self.work / "model_trace.json", {"lines": [
            {"call": "call_002", "kind": "text", "at": "2026-09-16T12:00:20Z", "text": "Weitere Quellenabschnitte lesen"}]})
        write_json(self.call / "diagnostics.json", {"stream_deltas": 9000,
                   "last_content_at": "2026-09-16T12:05:00Z", "last_stdout_at": "2026-09-16T12:05:00Z"})
        signal = work_insight(self.work, self.run)["signals"]
        self.assertEqual(signal["last_content_at"], "2026-09-16T12:00:20Z")
        self.assertEqual(signal["last_visible_at"], "2026-09-16T12:00:20Z")
        self.assertEqual(signal["last_received_at"], "2026-09-16T12:05:00Z")
        self.assertEqual(signal["stream_deltas"], 9000)
        self.assertIsNone(signal["stream_chars"])

    def test_failure_completion_and_stop_are_not_reported_as_running(self):
        self.assertEqual(work_insight(self.work, {**self.run, "status": "interrupted"})["signals"]["state"], "stopped")
        write_json(self.call / "failure.json", {"code": "timeout"})
        self.assertEqual(work_insight(self.work, self.run)["signals"]["state"], "failed")
        write_json(self.call / "response.json", {"action": "answer"})
        self.assertEqual(work_insight(self.work, self.run)["signals"]["state"], "completed")

    def test_a_waiting_plan_names_the_gate_when_no_call_is_assigned(self):
        self.state["phase"] = "awaiting_plan_approval"
        self.save_state()
        write_json(self.call / "output_schema.json", {"title": "SomethingNew"})
        write_json(self.call / "response.json", {"ok": True})
        info = work_insight(self.work, {**self.run, "status": "blocked"})
        self.assertIn("Wartet auf Freigabe des Rechercheplans", info["assignment"])
        self.assertEqual(info["signals"]["state"], "completed")

    def test_unsafe_index_path_is_ignored(self):
        self.state["index_hash"] = "../../private"
        self.save_state()
        info = work_insight(self.work, self.run)
        self.assertEqual(info["material"]["source_count"], 0)
        self.assertEqual(info["material"]["unresolved_sections"], 2)

    def test_several_active_tasks_are_listed_and_the_first_stands_in_for_the_saved_context(self):
        self.state["plan"]["tasks"].append({"id": "task_two", "question": "Wie wirkt Confounding?", "acceptance": ["Mechanismus."]})
        self.state["tasks"]["task_two"] = {**self.row, "status": "researching", "activity": "Liest Originalabschnitte PRIVATE_PATH"}
        self.state["tasks"]["task_one"].update(status="reviewing", activity="Antwort wird geprüft")
        del self.state["active_task"]
        self.state["active_tasks"] = ["task_one", "task_two"]
        self.save_state()
        info = work_insight(self.work, self.run)
        self.assertEqual(info["question"], "Was ist Causation?")
        self.assertEqual([(row["id"], row["question"], row["status"], row["activity"]) for row in info["active_questions"]],
                         [("task_one", "Was ist Causation?", "reviewing", "Antwort wird geprüft"),
                          ("task_two", "Wie wirkt Confounding?", "researching", "Liest Originalabschnitte PRIVATE_PATH")])
        self.assertNotIn("PRIVATE_FULL_TEXT", json.dumps(info))
        # A ledger of an earlier version names one task; it is that one.
        del self.state["active_tasks"]
        self.state["active_task"] = "task_two"
        self.save_state()
        info = work_insight(self.work, self.run)
        self.assertEqual((info["question"], [row["id"] for row in info["active_questions"]]),
                         ("Wie wirkt Confounding?", ["task_two"]))


if __name__ == "__main__":
    unittest.main()
