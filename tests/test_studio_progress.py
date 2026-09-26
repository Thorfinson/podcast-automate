import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.studio_progress import read, script_progress, watch
from podcast_automate.studio_progress import safe_script_progress
from podcast_automate.model_trace import ModelTrace
from podcast_automate.storage import write_json


class StudioProgressTests(unittest.TestCase):
    def test_trace_is_available_independently_of_optional_status_summary(self):
        trace = ModelTrace(self.work / "calls/call_004", "test")
        trace.record("reasoning", "A provider-visible progress note")
        trace.finish()
        progress = safe_script_progress(self.root, self.run)
        self.assertEqual(progress["model_trace"]["lines"][0]["text"], "A provider-visible progress note")
        self.assertNotIn("status_summary", progress)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / "runs/run_test"
        self.run = {"run_id": "run_test", "kind": "script", "status": "running",
                    "stages": {"planning": {"status": "completed"}, "teaching": {"status": "running"}}}
        write_json(self.work / "series_plan.json", {"episodes": [
            {"episode_id": "ep_001", "title": "First lesson"}, {"episode_id": "ep_002", "title": "Second lesson"}]})
        folder = self.work / "teaching/ep_001"
        review = {"issues": [], "research_gaps": []}
        plan = {"episode_id": "ep_001"}
        write_json(folder / "plan.json", plan)
        write_json(folder / "review.json", review)
        write_json(folder / "checkpoint.json", {"design": plan, "review": review})
        (folder / "plan.md").write_text("# First lesson\nAn accepted teaching design.", encoding="utf-8")
        write_json(self.work / "calls/call_004/output_schema.json", {"title": "TeachingPlanReview"})
        write_json(self.work / "budget.json", {"model_calls": 4})

    def test_reports_real_current_episode_and_readable_completed_result(self):
        progress = script_progress(self.root, self.run)
        self.assertEqual(progress["current_episode"], "ep_002")
        self.assertEqual(progress["completed_segments"], 1)
        self.assertEqual(progress["total_segments"], 2)
        self.assertIn("geprüft", progress["activity"])
        self.assertIn("accepted teaching design", progress["episodes"][0]["teaching_preview"])
        self.assertEqual(progress["episodes"][1]["teaching_preview"], "")

    def test_research_progress_exposes_quality_and_preserves_real_call_timing(self):
        write_json(self.work / "research_activity.json", {"phase": "research", "activity": "Read new evidence",
                   "model_call_limit": 150, "search_round_limit": 12})
        write_json(self.work / "research_quality_gate.json", {"closed": 1, "total": 3, "passed": False,
                   "requirements": [{"question": "Why?", "missing": ["Missing full chapter"]}]})
        write_json(self.work / "budget.json", {"model_calls": 4, "search_rounds": 2})
        run = {**self.run, "kind": "research", "status": "running"}
        progress = script_progress(self.root, run)
        self.assertEqual(progress["phase"], "research")
        self.assertEqual(progress["research_quality"]["closed"], 1)
        self.assertEqual(progress["search_rounds"], 2)
        self.assertIsNotNone(progress["model_call_started_at"])
        stopped = script_progress(self.root, {**run, "status": "pending"})
        self.assertIsNone(stopped["model_call_started_at"])

    def test_first_retrieval_reports_a_counter_and_readable_failures(self):
        write_json(self.work / "research_activity.json", {"phase": "research", "activity": "Gefundene Originaltexte werden eingelesen",
                   "updated_at": "2026-09-01T10:00:00+00:00"})
        write_json(self.work / "discovery.json", {"candidates": [{}] * 30})
        write_json(self.work / "retrieval_progress.json", {"imported": 5, "attempted": 7, "failures": [
            {"source": "https://x.test/a.pdf", "reason": "Quellenabruf fehlgeschlagen (HTTP 403).", "code": "source_download_failed"}]})
        run = {**self.run, "kind": "research", "stages": {"discovery": {"status": "completed"}, "retrieval": {"status": "running"}}}
        progress = script_progress(self.root, run)
        self.assertEqual(progress["activity"], "Originaltexte werden eingelesen: 7 von bis zu 30 Quellen abgerufen, 5 lesbar")
        self.assertEqual(progress["retrieval"]["failures"][0]["reason"], "Quellenabruf fehlgeschlagen (HTTP 403).")
        # The run's own change time, unlike updated_at, which only says when this view was read.
        self.assertEqual(progress["changed_at"], "2026-09-01T10:00:00+00:00")
        done = script_progress(self.root, {**run, "stages": {"retrieval": {"status": "completed"}}})
        self.assertEqual(done["activity"], "Gefundene Originaltexte werden eingelesen")
        self.assertFalse(done["retrieval"]["running"])

    def test_outline_corrections_and_review_repairs_are_named(self):
        run = {**self.run, "stages": {"planning": {"status": "running"}}}
        write_json(self.work / "calls/call_005/output_schema.json", {"title": "SeriesPlan"})
        self.assertEqual(script_progress(self.root, run)["activity"], "Inhaltsverzeichnis wird entworfen")
        write_json(self.work / "planning_checkpoint.json", {"input_hash": "x", "draft": {}, "repairs": 1})
        write_json(self.work / "plan_errors.json", ["Grundlage fehlt"])
        progress = script_progress(self.root, run)
        self.assertEqual(progress["activity"], "Inhaltsverzeichnis wird korrigiert · Korrekturrunde 2 von 3")
        self.assertEqual(progress["plan_repair"], {"round": 2, "limit": 3})
        write_json(self.work / "calls/call_006/output_schema.json", {"title": "EpisodeScript"})
        review = {**self.run, "stages": {"planning": {"status": "completed"}, "review": {"status": "running"}}}
        self.assertEqual(script_progress(self.root, review)["activity"], "Skript wird nach den Prüfeinwänden überarbeitet")

    def test_parallel_episodes_are_listed_with_their_open_calls_and_a_failure_winds_down(self):
        write_json(self.work / "series_plan.json", {"episodes": [{"episode_id": f"ep_00{i}", "title": title}
                   for i, title in enumerate(("Eins", "Zwei", "Drei"), 1)]})
        run = {**self.run, "stages": {"planning": {"status": "completed"}, "writing": {"status": "running"}}}
        since = "2026-09-26T10:00:00+00:00"
        write_json(self.work / "calls/call_004/response.json", {"issues": []})  # the fixture's teaching review has answered
        for episode, status in (("ep_001", "running"), ("ep_002", "running"), ("ep_003", "interrupted")):
            write_json(self.work / "stage_activity/writing" / f"{episode}.json", {"episode_id": episode, "status": status,
                       "started_at": "2026-09-26T10:01:00+00:00", "finished_at": "2026-09-26T10:05:00+00:00"})
        for number, episode, started in ((5, "ep_001", "2026-09-26T10:02:00+00:00"), (6, "ep_002", "2026-09-26T10:03:00+00:00"),
                                         (7, None, "2026-09-26T09:00:00+00:00")):
            directory = self.work / f"calls/call_00{number}"
            write_json(directory / "output_schema.json", {"title": "EpisodeScript"})
            write_json(directory / "activity.json", {"status": "running", "started_at": started, **({"subject": episode} if episode else {})})
        progress = safe_script_progress(self.root, run, since)
        # call_007 is older than this worker: a call an earlier, stopped worker left open.
        self.assertEqual([(row["call"], row["label"]) for row in progress["open_calls"]], [("call_005", "Eins"), ("call_006", "Zwei")])
        self.assertEqual(progress["model_call_started_at"], "2026-09-26T10:02:00+00:00")
        self.assertEqual(progress["active_episodes"], ["ep_001", "ep_002"])
        self.assertEqual([row["stage_status"] for row in progress["episodes"]], ["running", "running", "interrupted"])
        self.assertEqual(progress["stopping"], {"episodes": ["Drei"]})
        stopped = safe_script_progress(self.root, {**run, "status": "blocked", "stages": {"writing": {"status": "blocked"}}})
        self.assertEqual((stopped["open_calls"], stopped["stopping"]), ([], None))
        self.assertEqual(stopped["active_episodes"], [])

    def test_research_calls_are_named_by_their_question_and_a_stopping_marker_counts_only_for_this_worker(self):
        write_json(self.work / "research_activity.json", {"phase": "research", "activity": "Antwort wird geprüft"})
        write_json(self.work / "calls/call_005/output_schema.json", {"title": "ResearchDecision"})
        write_json(self.work / "calls/call_005/activity.json", {"status": "running", "started_at": "2026-09-26T10:02:00+00:00"})
        write_json(self.work / "calls/call_005/work_context.json", {"schema": "ResearchDecision", "question": "Wie wirkt X?"})
        write_json(self.work / "question_research/stopping.json", {"task": "t2", "question": "Warum Y?", "at": "2026-09-26T10:04:00+00:00", "code": "timeout"})
        run = {**self.run, "kind": "research"}
        progress = safe_script_progress(self.root, run, "2026-09-26T10:00:00+00:00")
        self.assertEqual(progress["open_calls"][0]["label"], "Wie wirkt X?")
        self.assertEqual(progress["stopping"], {"question": "Warum Y?", "code": "timeout"})
        # A marker from an earlier worker is history, not a wind-down of this one.
        self.assertIsNone(safe_script_progress(self.root, run, "2026-09-26T11:00:00+00:00")["stopping"])

    def test_refresh_is_distinct_from_model_activity_and_saved_results(self):
        first = script_progress(self.root, self.run)
        second = script_progress(self.root, self.run)
        self.assertGreaterEqual(second["updated_at"], first["updated_at"])
        self.assertEqual(second["model_call_started_at"], first["activity_started_at"])
        self.assertIsNone(first["last_result_at"])
        write_json(self.work / "calls/call_004/response.json", {"issues": []})
        completed = script_progress(self.root, self.run)
        self.assertIsNone(completed["model_call_started_at"])
        self.assertIsNotNone(completed["last_result_at"])
        self.assertEqual(completed["activity_started_at"], first["activity_started_at"])

    def test_research_progress_reports_the_plan_gate_and_only_a_matching_receipt_as_approved(self):
        from podcast_automate.storage import digest
        write_json(self.work / "research_activity.json", {"activity": "Der Rechercheplan wartet auf Freigabe"})
        write_json(self.work / "research_questions.json", {"closed": 0, "total": 3, "phase": "awaiting_plan_approval", "questions": []})
        projection = {"tasks": 3, "projected_calls": 27, "projected_hours": 1.8, "seconds_per_call": 240, "plan_hash": "c" * 64}
        write_json(self.work / "question_research/plan_projection.json", projection)
        run = {**self.run, "kind": "research", "status": "blocked", "input_hash": "b" * 64}
        review = script_progress(self.root, run)["plan_review"]
        self.assertEqual((review["awaiting"], review["approved"], review["approval"], review["projection"]), (True, False, None, projection))
        value = {"run_id": "run_test", "input_hash": "b" * 64, "plan_hash": "c" * 64, "max_tasks": None,
                 "approved_at": "2026-09-19T20:00:00+00:00", "source": "studio"}
        write_json(self.work / "plan_approval.json", {"value": value, "sha256": digest(value)})
        review = script_progress(self.root, run)["plan_review"]
        self.assertEqual((review["awaiting"], review["approved"], review["approval"]["source"]), (True, True, "studio"))
        # A receipt for another plan, another run or with a broken checksum is not an approval.
        write_json(self.work / "plan_approval.json", {"value": {**value, "plan_hash": "d" * 64}, "sha256": digest({**value, "plan_hash": "d" * 64})})
        self.assertFalse(script_progress(self.root, run)["plan_review"]["approved"])
        write_json(self.work / "plan_approval.json", {"value": {**value, "max_tasks": 1}, "sha256": digest(value)})
        review = script_progress(self.root, run)["plan_review"]
        self.assertEqual((review["approved"], review["approval"]), (False, None))
        self.assertFalse(script_progress(self.root, {**run, "input_hash": "e" * 64})["plan_review"]["approved"])

    def test_research_progress_uses_question_ledger_instead_of_stale_global_score(self):
        write_json(self.work / "research_activity.json", {"activity": "Eine Frage wird geprüft"})
        write_json(self.work / "research_quality_gate.json", {"closed": 0, "total": 2})
        ledger = {"closed": 3, "total": 7, "phase": "questions", "questions": [
            {"id": "definition", "status": "verified", "answer": "A supported definition"}]}
        write_json(self.work / "research_questions.json", ledger)
        progress = script_progress(self.root, {**self.run, "kind": "research"})
        self.assertEqual((progress["completed_segments"], progress["total_segments"]), (3, 7))
        questions = progress["research_questions"]
        self.assertEqual((questions["closed"], questions["total"], questions["phase"], questions["reopenable"]), (3, 7, "questions", 0))
        self.assertEqual(questions["questions"][0]["answer"], "A supported definition")
        self.assertEqual(progress["research_quality"]["closed"], 0)

    def test_an_older_ledger_learns_which_blocks_a_resume_reopens_from_the_saved_state(self):
        write_json(self.work / "research_activity.json", {"activity": "Einzelne Recherchefragen bleiben offen"})
        # A ledger the worker wrote before the field existed, and the state it was derived from.
        write_json(self.work / "research_questions.json", {"closed": 1, "total": 3, "phase": "blocked", "questions": [
            {"id": "t_done", "status": "verified"},
            {"id": "t_unsearched", "status": "blocked", "outcome": "evidence_block"},
            {"id": "t_searched", "status": "blocked", "outcome": "evidence_block"}]})
        row = {"status": "blocked", "outcome": "evidence_block", "web_attempts": 0, "fallbacks": 2, "step": 3}
        state = {"limits": {"steps_per_question": 10, "web_attempts": 2}, "tasks": {
            "t_done": {**row, "status": "verified"}, "t_unsearched": row, "t_searched": {**row, "web_attempts": 1}}}
        write_json(self.work / "question_research/state.json", {"value": state, "sha256": "unchecked here"})
        questions = script_progress(self.root, {**self.run, "kind": "research"})["research_questions"]
        self.assertEqual(questions["reopenable"], 1)
        self.assertEqual([(q["id"], q["reopenable"], q["web_attempts"]) for q in questions["questions"]],
                         [("t_done", False, 0), ("t_unsearched", True, 0), ("t_searched", False, 1)])

    def test_research_progress_reports_the_execution_mode_the_run_was_started_with(self):
        write_json(self.work / "research_activity.json", {"activity": "Drei Teilfragen laufen"})
        run = {**self.run, "kind": "research"}
        # A run started before the field existed answered one task at a time.
        self.assertEqual(script_progress(self.root, run)["execution"], {"text": "sequential", "audio": "sequential"})
        write_json(self.work / "research_request.json", {"text_generation": None,
                   "execution": {"text": "parallel", "audio": "sequential"}})
        self.assertEqual(script_progress(self.root, run)["execution"], {"text": "parallel", "audio": "sequential"})

    def test_publisher_recovers_from_transient_job_read_and_progress_io_failures(self):
        job_path = self.root / "studio/job.json"
        job = {"id": "job_one", "status": "running", "run": self.run}
        write_json(job_path, job)
        attempts = []

        def flaky_read(path, default=None):
            if path == job_path and not attempts:
                attempts.append("job read")
                return default
            return read(path, default)

        def flaky_progress(*args):
            attempts.append("progress")
            if len(attempts) == 2:
                raise PermissionError("temporarily locked")
            return script_progress(*args)

        def flaky_write(path, data):
            attempts.append("write")
            if attempts.count("write") == 1:
                raise PermissionError("temporarily locked")
            write_json(path, data)

        def tick(_):
            if (self.work / "progress.json").exists():
                self.assertEqual(json.loads(job_path.read_text()), job)
                write_json(job_path, {**job, "status": "completed"})

        with patch("podcast_automate.studio_progress.read", side_effect=flaky_read), \
             patch("podcast_automate.studio_progress.script_progress", side_effect=flaky_progress), \
             patch("podcast_automate.studio_progress.write_json", side_effect=flaky_write), \
             patch("time.sleep", side_effect=tick) as sleep:
            watch(self.root, "job_one")
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(json.loads((self.work / "progress.json").read_text())["model_calls"], 4)

    def test_publisher_exits_if_job_stays_unreadable(self):
        with patch("time.sleep") as sleep:
            watch(self.root, "job_one")
        self.assertEqual(sleep.call_count, 29)

    def test_stale_accepted_file_does_not_mark_pending_review_complete(self):
        write_json(self.work / "teaching/ep_001/checkpoint.json", {"design": {"episode_id": "ep_001"}, "review": None})
        progress = script_progress(self.root, self.run)
        self.assertEqual(progress["completed_segments"], 0)
        self.assertEqual(progress["episodes"][0]["teaching_preview"], "")

    def test_respects_single_episode_selection_and_ignores_non_script_jobs(self):
        write_json(self.work / "script_request.json", {"episode": "ep_002"})
        self.assertEqual(script_progress(self.root, self.run)["total_segments"], 1)
        self.assertIsNone(script_progress(self.root, {**self.run, "kind": "episode_audio"}))

    def test_invalid_episode_paths_cannot_read_outside_the_run(self):
        write_json(self.work / "series_plan.json", {"episodes": [{"episode_id": "../../private", "title": "Untrusted"}]})
        self.assertEqual(script_progress(self.root, self.run)["episodes"], [])

    def test_blocked_job_retains_completed_results_and_explains_the_remaining_review(self):
        write_json(self.work / "teaching/ep_002/checkpoint.json", {"review": {"issues": ["Explain the missing mechanism."]}})
        run = {**self.run, "status": "blocked", "stages": {"teaching": {"status": "blocked", "error": {"code": "teaching_design_failed"}}}}
        progress = script_progress(self.root, run)
        self.assertEqual(progress["completed_segments"], 1)
        self.assertEqual(progress["review_issues"], ["Explain the missing mechanism."])
        self.assertTrue(progress["episodes"][0]["teaching_preview"])

    def test_blocked_final_review_exposes_actual_issues(self):
        issues = [{"category": "depth", "segment_ids": ["seg_001"], "reason": "Explain the conclusion."}]
        write_json(self.work / "reviews/ep_001_checkpoint.json", {"review": {"issues": issues}})
        run = {**self.run, "status": "blocked", "stages": {
            "review": {"status": "blocked", "error": {"code": "script_review_failed"}}}}
        progress = script_progress(self.root, run)
        self.assertEqual(progress["current_episode"], "ep_001")
        self.assertEqual(progress["review_issues"], issues)

    def test_compatibility_publisher_never_modifies_job_or_run_and_exits_on_job_change(self):
        job_path = self.root / "studio/job.json"
        job = {"id": "job_one", "status": "running", "run": self.run}
        write_json(job_path, job)
        def change_job(_):
            self.assertEqual(json.loads(job_path.read_text()), job)
            write_json(job_path, {**job, "id": "job_two"})
        with patch("time.sleep", side_effect=change_job) as sleep:
            watch(self.root, "job_one")
        self.assertEqual(sleep.call_count, 1)
        self.assertTrue((self.work / "progress.json").exists())
