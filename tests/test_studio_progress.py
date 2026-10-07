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
        # Resolved, as watch() resolves its root (an 8.3 TEMP differs from its long form).
        self.root = Path(temp.name).resolve()
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

    def test_a_blocked_ledger_saved_before_the_field_says_from_its_state_whether_spent_answers_are_kept(self):
        """The runs stopped on 2026-10-01 saved their ledger before keeps_spent_answers existed: the Studio reads the
        prompt generation from the state, so it offers the resume that keeps their verified answers."""
        from podcast_automate.research_ledger import save_value
        write_json(self.work / "research_activity.json", {"activity": "Einzelne Recherchefragen bleiben konkret unbelegt"})
        write_json(self.work / "research_questions.json", {"closed": 1, "total": 2, "phase": "blocked", "questions": []})
        run = {**self.run, "kind": "research", "status": "blocked", "input_hash": "b" * 64}
        for generation, kept in ((2, False), (3, True)):
            save_value(self.work / "question_research/state.json", {"prompt_generation": generation, "tasks": {}})
            self.assertIs(script_progress(self.root, run)["research_questions"]["keeps_spent_answers"], kept)
        # A ledger that carries the field is not overruled.
        write_json(self.work / "research_questions.json", {"closed": 1, "total": 2, "phase": "blocked", "questions": [],
                                                           "keeps_spent_answers": False})
        self.assertIs(script_progress(self.root, run)["research_questions"]["keeps_spent_answers"], False)

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

    def test_publisher_writes_only_a_changed_progress_and_once_a_minute_as_the_heartbeat(self):
        """2026-10-02: 2-2.6 MB of progress.json and research_activity.json were rewritten with fsync every 2 s,
        unchanged or not, and research_activity.json read and rewritten outside its writer's lock."""
        job_path = self.root / "studio/job.json"
        write_json(job_path, {"id": "job_one", "status": "running", "run": self.run})
        write_json(self.work / "research_activity.json", {"activity": "Gehört dem Rechercheprozess"})
        activity = (self.work / "research_activity.json").read_bytes()
        clock, ticks, written = [1000.0], [], []

        def tick(_):
            ticks.append(1)
            clock[0] += 2
            if len(ticks) == 3:
                write_json(self.work / "budget.json", {"model_calls": 5})  # a real change
            if len(ticks) == 4:
                clock[0] += 60  # a minute without change: the heartbeat write
            if len(ticks) == 6:
                write_json(job_path, {"id": "job_one", "status": "completed"})

        def write(path, data):
            written.append(path.name)
            write_json(path, data)
        with patch("podcast_automate.studio_progress.write_json", side_effect=write), \
                patch("podcast_automate.studio_progress.time.monotonic", side_effect=lambda: clock[0]), \
                patch("time.sleep", side_effect=tick):
            watch(self.root, "job_one")
        self.assertEqual(written, ["progress.json"] * 3, "first state, the change, the heartbeat")
        self.assertEqual(json.loads((self.work / "progress.json").read_text())["model_calls"], 5)
        self.assertEqual((self.work / "research_activity.json").read_bytes(), activity)

    def test_the_assignment_and_the_ledger_are_derived_again_only_after_their_files_changed(self):
        """2026-10-02: each poll parsed the 16 MB question state for the assignment view and the ledger again."""
        from podcast_automate import research_status
        write_json(self.work / "research_activity.json", {"activity": "Eine Frage wird geprüft"})
        write_json(self.work / "research_questions.json", {"closed": 0, "total": 1, "phase": "questions", "questions": [
            {"id": "q1", "question": "Warum?", "status": "researching", "reason": "Noch offen."}]})
        write_json(self.work / "question_research/state.json", {"value": {"tasks": {}}, "sha256": "x"})
        run = {**self.run, "kind": "research"}
        with patch("podcast_automate.research_status.work_insight", wraps=research_status.work_insight) as insight, \
                patch("podcast_automate.studio_progress.read", wraps=read) as reads:
            first = script_progress(self.root, run)
            second = script_progress(self.root, run)
            self.assertEqual(insight.call_count, 1)
            self.assertEqual(sum(call.args[0].name == "research_questions.json" for call in reads.call_args_list), 1)
            self.assertEqual(first["research_questions"], second["research_questions"])
            self.assertEqual(first["work_insight"], second["work_insight"])
            write_json(self.work / "question_research/state.json", {"value": {"tasks": {}, "phase": "audit"}, "sha256": "x"})
            third = script_progress(self.root, run)
            self.assertEqual(insight.call_count, 2)
            self.assertNotEqual(third["work_insight"]["assignment"], first["work_insight"]["assignment"])
            write_json(self.work / "research_questions.json", {"closed": 1, "total": 1, "phase": "questions", "questions": []})
            self.assertEqual(script_progress(self.root, run)["research_questions"]["closed"], 1)
            # The project card's view leaves the assignment out.
            self.assertIsNone(script_progress(self.root, run, light=True)["work_insight"])
            self.assertEqual(insight.call_count, 2)

    def test_the_limits_shown_are_the_projects_current_ones_with_the_runs_raises(self):
        """Limits are no longer part of a run's hash; the run's snapshot showed the limits it was started with."""
        from podcast_automate.models import TopicBrief
        from podcast_automate.storage import write_yaml
        write_yaml(self.work / "project_snapshot.yaml", {"research_limits": {"model_calls": 100}})
        write_yaml(self.root / "project.yaml", TopicBrief(topic="Projekt", research_limits={"model_calls": 900,
                                                                                             "search_rounds": 30}).model_dump(mode="json"))
        write_json(self.work / "research_activity.json", {"activity": "Eine Frage wird geprüft"})
        research = script_progress(self.root, {**self.run, "kind": "research"})
        self.assertEqual((research["model_call_limit"], research["search_round_limit"]), (900, 30))
        self.assertEqual(script_progress(self.root, self.run)["model_call_limit"], 900)
        # Without a readable project file the snapshot still answers.
        (self.root / "project.yaml").unlink()
        self.assertEqual(script_progress(self.root, self.run)["model_call_limit"], 100)

    def test_publisher_exits_if_job_stays_unreadable(self):
        with patch("time.sleep") as sleep:
            watch(self.root, "job_one")
        self.assertEqual(sleep.call_count, 29)

    def review_done(self, episode, issues):
        draft = {"episode_id": episode, "segments": []}
        write_json(self.work / "reviewed" / f"{episode}.json", draft)
        write_json(self.work / "reviews" / f"{episode}.json", {"issues": issues})
        write_json(self.work / "reviews" / f"{episode}_checkpoint.json", {"draft": draft, "review": {"issues": issues}})

    def test_an_episode_accepted_with_noted_points_counts_as_reviewed_and_the_series_review_is_named(self):
        """2026-09-29: the Studio showed Asimov at 5 of 14 and Ontologies at 2 of 10 for hours, because an episode
        whose review kept only clarity or depth notes counted as open; the budget projection counted it too."""
        run = {**self.run, "stages": {"planning": {"status": "completed"}, "review": {"status": "running"}}}
        self.review_done("ep_001", [{"category": "depth", "segment_ids": ["s1"], "reason": "Note."}])
        self.review_done("ep_002", [{"category": "grounding", "segment_ids": ["s1"], "reason": "Unsupported."}])
        progress = script_progress(self.root, run)
        self.assertEqual((progress["completed_segments"], progress["current_episode"]), (1, "ep_002"))
        self.review_done("ep_002", [])
        write_json(self.work / "calls/call_005/output_schema.json", {"title": "SeriesReview"})
        progress = script_progress(self.root, run)
        self.assertEqual((progress["completed_segments"], progress["current_episode"]), (2, None))
        self.assertEqual(progress["activity"], "Serienprüfung: alle Folgen werden im Zusammenhang geprüft")
        write_json(self.work / "series_repair.json", {"receipt": {"episodes": ["ep_002", "ep_001"]}})
        write_json(self.work / "calls/call_006/output_schema.json", {"title": "ScriptReview"})
        self.assertEqual(script_progress(self.root, run)["activity"],
                         "Korrektur der Serienprüfung: Folgen 1, 2 werden überarbeitet und nachgeprüft")

    def test_placing_the_tags_after_a_script_run_is_named_while_it_runs(self):
        run = {**self.run, "status": "running", "stages": {"publish": {"status": "completed"}}}
        write_json(self.root / "studio/expression/progress.json", {"status": "running", "run_id": "run_test", "done": 3, "total": 14})
        self.assertEqual(script_progress(self.root, run)["activity"], "Ausdruck für die Vertonung wird gesetzt · 3 von 14 Folgen")
        write_json(self.root / "studio/expression/progress.json", {"status": "running", "run_id": "run_other", "done": 3, "total": 14})
        self.assertNotIn("Ausdruck", script_progress(self.root, run)["activity"], "another run's tagging is not this run's")

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

    def test_the_activity_line_follows_the_interface_language(self):
        """D-152: the Studio's own activity lines come from the catalog; German stays as it was."""
        from podcast_automate.studio_text import using
        run = {**self.run, "stages": {"planning": {"status": "running"}}}
        write_json(self.work / "calls/call_005/output_schema.json", {"title": "SeriesPlan"})
        with using("en"):
            self.assertEqual(script_progress(self.root, run)["activity"], "Drafting the table of contents")
            write_json(self.work / "planning_checkpoint.json", {"input_hash": "x", "draft": {}, "repairs": 1})
            write_json(self.work / "plan_errors.json", ["Grundlage fehlt"])
            self.assertEqual(script_progress(self.root, run)["activity"],
                             "Correcting the table of contents · correction round 2 of 3")
        with using("de"):
            self.assertEqual(script_progress(self.root, run)["activity"],
                             "Inhaltsverzeichnis wird korrigiert · Korrekturrunde 2 von 3")
        write_json(self.work / "calls/call_006/output_schema.json", {"title": "UnknownSchema"})
        with using("en"):
            self.assertEqual(script_progress(self.root, {**self.run, "stages": {"review": {"status": "running"}}})["activity"],
                             "Processing saved results")

    def test_the_research_pipelines_german_lines_read_in_english_in_the_english_interface(self):
        """The research pipeline writes its activity in German (it feeds the status brief); the English interface shows
        the catalog's English where a German template matches, a line it does not know as written, and German stays."""
        from podcast_automate.studio_progress import activity_text
        from podcast_automate.studio_text import using
        write_json(self.work / "research_activity.json", {"activity": "Neue Originalquelle für diese Frage wird gesucht: Wie alt?"})
        write_json(self.work / "research_questions.json", {"closed": 0, "total": 2, "phase": "questions", "questions": [
            {"id": "q1", "question": "Wie alt?", "status": "researching", "activity": "Antwort wird unabhängig geprüft"},
            {"id": "q2", "question": "Wer?", "status": "pending", "activity": "Ein ganz neuer Satz der Pipeline"}]})
        run = {**self.run, "kind": "research"}
        with using("en"):
            progress = script_progress(self.root, run)
        self.assertEqual(progress["activity"], "Searching for a new original source for this question: Wie alt?")
        self.assertEqual([row["activity"] for row in progress["research_questions"]["questions"]],
                         ["The answer is being reviewed independently", "Ein ganz neuer Satz der Pipeline"])
        # The ledger is cached per language: the German page still reads German.
        german = script_progress(self.root, run)
        self.assertEqual(german["activity"], "Neue Originalquelle für diese Frage wird gesucht: Wie alt?")
        self.assertEqual(german["research_questions"]["questions"][0]["activity"], "Antwort wird unabhängig geprüft")
        with using("en"):
            # A repeated attempt names the attempt after the line it repeats, not as part of a question.
            self.assertEqual(activity_text("Neue Originalquelle für diese Frage wird gesucht: Wie alt? · Anlauf 2 nach Abweisung"),
                             "Searching for a new original source for this question: Wie alt? · attempt 2 after a rejection")
            self.assertEqual(activity_text("Modellaufruf · Anlauf 3 nach Abweisung"), "Model call · attempt 3 after a rejection")
        self.assertEqual(activity_text("Modellaufruf · Anlauf 3 nach Abweisung"), "Modellaufruf · Anlauf 3 nach Abweisung")

    def test_the_research_assignment_names_its_catalog_id_beside_the_german_text(self):
        """research_status keeps its German assignment for the status brief; the page shows assignment.<id> (D-152)."""
        write_json(self.work / "research_activity.json", {"activity": "Eine Frage wird geprüft"})
        write_json(self.work / "question_research/state.json", {"value": {"tasks": {}, "phase": "audit"}, "sha256": "x"})
        run = {**self.run, "kind": "research"}
        insight = script_progress(self.root, run)["work_insight"]
        self.assertEqual((insight["assignment_id"], insight["assignment"]),
                         ("phase.audit", "Das zusammengesetzte Dossier wird abschließend geprüft."))
        write_json(self.work / "calls/call_007/output_schema.json", {"title": "AnswerReview"})
        self.assertEqual(script_progress(self.root, run)["work_insight"]["assignment_id"], "AnswerReview")
        write_json(self.work / "question_research/state.json", {"value": {"tasks": {}, "phase": "questions"}, "sha256": "x"})
        write_json(self.work / "calls/call_008/output_schema.json", {"title": "SomethingElse"})
        self.assertEqual(script_progress(self.root, run)["work_insight"]["assignment_id"], "saved_results")
