"""What the Studio does by itself between the user's visits: allowances set ahead, the production report, restart
when idle, and the resumes that need no decision (D-155)."""
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate import studio_allowances
from podcast_automate.errors import AppError
from podcast_automate.models import Failure, RunManifest, StageRecord, TopicBrief, now
from podcast_automate.run_budget import REASKED_MARKER
from podcast_automate.storage import read_yaml, write_json, write_yaml
from podcast_automate.studio import LOGIN_CHECKS, Studio, relaunch
from tests import test_studio


class AllowanceTests(unittest.TestCase):
    setUp = test_studio.StudioHttpTests.setUp
    request = test_studio.StudioHttpTests.request

    def stopped(self, code, *, kind="script", job_id="job1", progress=None, run_id="run_s"):
        work = self.root / "runs" / run_id
        if not (work / "run_manifest.yaml").exists():
            write_yaml(work / "run_manifest.yaml", RunManifest(
                run_id=run_id, kind=kind, project_hash="p", input_hash="a" * 64,
                stages={"review": StageRecord(status="blocked")}).model_dump(mode="json"))
        write_json(self.root / "studio/job.json", {
            "id": job_id, "status": "blocked", "action": "resume", "error_code": code, "message": "Angehalten.",
            "finished_at": now(), "progress": progress or {},
            "run": {"run_id": run_id, "kind": kind, "stages": {}}})

    def allow(self, fresh=0, extra=0):
        status, body, _ = self.request("/api/projects/example/allowances", {"fresh_attempts": fresh, "extra_calls": extra})
        self.assertEqual(status, 200, body)

    def test_allowances_are_the_users_to_set_and_bounded(self):
        for wrong in ({"fresh_attempts": 5, "extra_calls": 0}, {"fresh_attempts": 1, "extra_calls": 99},
                      {"fresh_attempts": "1", "extra_calls": 0}):
            self.assertEqual(self.request("/api/projects/example/allowances", wrong)[0], 400, wrong)
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["allowances"], {"fresh_attempts": 0, "extra_calls": 0,
                                                "used": {"fresh_attempts": 0, "extra_calls": 0}})
        self.allow(2, 250)
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual((detail["allowances"]["fresh_attempts"], detail["allowances"]["extra_calls"]), (2, 250))

    def test_fresh_attempts_are_granted_up_to_the_allowance_and_the_run_resumes(self):
        self.allow(fresh=1)
        self.stopped("script_review_failed")
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["job"]["allowance"], {"kind": "fresh_attempts", "number": 1, "of": 1})
        with patch("podcast_automate.run_budget.approve_fresh_attempts", return_value={"reviews": ["ep_002"]}) as grant, \
                patch.object(self.app, "start") as start:
            self.assertEqual(self.app.apply_allowances(), ["example"])
            grant.assert_called_once_with(self.root, "run_s")
            start.assert_called_once_with("example", {"action": "resume", "run_id": "run_s"})
            # The same stop is never granted twice, and a later stop of the run finds the allowance spent.
            self.assertEqual(self.app.apply_allowances(), [])
            self.stopped("script_review_failed", job_id="job2")
            self.assertEqual(self.app.apply_allowances(), [])
        self.assertIsNone(json.loads(self.request("/api/projects/example")[1])["job"]["allowance"])
        log = json.loads((self.root / "studio/allowance_log.json").read_text(encoding="utf-8"))
        self.assertEqual([(row["kind"], row["job_id"], row["code"]) for row in log],
                         [("fresh_attempts", "job1", "script_review_failed")])

    def test_the_call_limit_is_raised_by_the_projections_need_within_the_allowance(self):
        self.allow(extra=100)
        projection = {"budget_projection": {"used": 140, "minimum_remaining_calls": 20}, "model_calls": 140,
                      "model_call_limit": 150}
        self.stopped("script_budget_insufficient", progress=projection)
        with patch("podcast_automate.studio_allowances.current_limit", return_value=150), \
                patch("podcast_automate.run_budget.approve_model_call_limit") as raise_limit, \
                patch.object(self.app, "start"):
            self.assertEqual(self.app.apply_allowances(), ["example"])
            # 140 used plus 20 needed and a quarter more for corrections: 165, fifteen of the hundred allowed.
            raise_limit.assert_called_once_with(self.root, "run_s", 165)
            self.stopped("script_budget_insufficient", job_id="job2",
                         progress={**projection, "budget_projection": {"used": 160, "minimum_remaining_calls": 400}})
        with patch("podcast_automate.studio_allowances.current_limit", return_value=165), \
                patch("podcast_automate.run_budget.approve_model_call_limit") as raise_limit, \
                patch.object(self.app, "start"):
            self.app.apply_allowances()
            # More is needed than is left: the rest of the allowance, 85 calls, and no more.
            raise_limit.assert_called_once_with(self.root, "run_s", 250)
        self.assertEqual(json.loads(self.request("/api/projects/example")[1])["allowances"]["used"],
                         {"fresh_attempts": 0, "extra_calls": 100})

    def test_a_calibrated_script_projection_raises_the_limit_by_its_expectation(self):
        """The script projection carries an expectation from the project's last completed script run (2026-10-02);
        the suggestion follows it, the minimum stays the floor, and without a calibration the old rule holds."""
        from podcast_automate.studio_allowances import suggested_calls
        base = {"used": 140, "minimum_remaining_calls": 20}
        job = lambda projection: {"progress": {"budget_projection": projection, "model_calls": 140, "model_call_limit": 150}}
        self.assertEqual(suggested_calls(job({**base, "expected_remaining_calls": 60, "calibration": {"run_id": "r"}})), 200)
        self.assertEqual(suggested_calls(job({**base, "expected_remaining_calls": 10, "calibration": {"run_id": "r"}})), 160)
        self.assertEqual(suggested_calls(job({**base, "expected_remaining_calls": 20, "calibration": None})), 165)
        self.assertEqual(suggested_calls(job(base)), 165)

    def test_an_allowance_waits_for_a_free_project_and_is_never_spent_on_a_resume_that_cannot_start(self):
        """2026-10-02: the approval and its log row were written, then the resume met a Gemini recording holding the
        project; the run never resumed by itself and its allowance was spent."""
        from podcast_automate.errors import AppError
        from podcast_automate.storage import project_lock
        self.allow(fresh=1)
        self.stopped("script_review_failed")
        with patch("podcast_automate.run_budget.approve_fresh_attempts", return_value={"reviews": ["ep_002"]}) as grant, \
                patch.object(self.app, "start") as start:
            with project_lock(self.root, shared=True):
                self.assertEqual(self.app.apply_allowances(), [])
            grant.assert_not_called()
            start.assert_not_called()
            self.assertFalse((self.root / "studio/allowance_log.json").exists())
            # A resume refused after the grant is tried again on a later pass, without granting twice.
            start.side_effect = AppError("Belegt.", code="project_busy")
            self.assertEqual(self.app.apply_allowances(), [])
            start.side_effect = None
            self.assertEqual(self.app.apply_allowances(), ["example"])
            self.assertEqual((grant.call_count, start.call_count), (1, 2))
            self.assertEqual(self.app.apply_allowances(), [])
        log = json.loads((self.root / "studio/allowance_log.json").read_text(encoding="utf-8"))
        self.assertEqual([(row["kind"], row["resumed"]) for row in log], [("fresh_attempts", True)])

    def test_an_allowance_covers_a_run_parked_behind_a_job_of_another_lane(self):
        self.allow(fresh=1)
        self.stopped("script_review_failed")
        write_json(self.root / "studio/paused_script.json",
                   json.loads((self.root / "studio/job.json").read_text(encoding="utf-8")))
        write_json(self.root / "studio/job.json", {"id": "chat", "action": "assistant", "status": "completed", "run": None})
        with patch("podcast_automate.run_budget.approve_fresh_attempts", return_value={"reviews": ["ep_002"]}), \
                patch.object(self.app, "start") as start:
            self.assertEqual(self.app.apply_allowances(), ["example"])
        start.assert_called_once_with("example", {"action": "resume", "run_id": "run_s"})

    def test_editorial_stops_and_spent_search_rounds_stay_with_the_user(self):
        self.allow(fresh=3, extra=1000)
        for code, progress in (("research_gap_unread", {}), ("teaching_review_failed", {}),
                               ("research_budget_exhausted", {"model_calls": 40, "model_call_limit": 750})):
            with self.subTest(code=code):
                self.stopped(code, kind="research" if code.startswith("research_budget") else "script",
                             job_id="job_" + code, progress=progress, run_id="run_" + code)
                with patch.object(self.app, "start") as start:
                    self.assertEqual(self.app.apply_allowances(), [])
                    start.assert_not_called()
        self.assertFalse((self.root / "studio/allowance_log.json").exists())

    def test_a_stop_with_nothing_to_grant_is_recorded_once_and_left_to_the_user(self):
        self.allow(fresh=2)
        self.stopped("script_review_failed")
        with patch.object(self.app, "start") as start:
            # The real approval finds no stage that spent its attempts in this bare run folder.
            self.assertEqual(self.app.apply_allowances(), [])
            self.assertEqual(self.app.apply_allowances(), [])
            start.assert_not_called()
        log = json.loads((self.root / "studio/allowance_log.json").read_text(encoding="utf-8"))
        self.assertEqual([row["skipped"] for row in log], [True])
        self.assertEqual(json.loads(self.request("/api/projects/example")[1])["allowances"]["used"]["fresh_attempts"], 0)

    def test_a_script_stop_that_asks_anew_on_resume_takes_a_fresh_attempt_and_resumes(self):
        """D-155: the pre-approval was tried and skipped on all four invalid_script stops of the 2026-09-30 series,
        although a resume writes the episode anew; now it covers them like every other correction loop."""
        self.allow(fresh=2)
        spent = f"Skript verletzt Struktur- oder Quellenzuordnung. Der Aufruf wurde 3 Mal mit Korrekturhinweis wiederholt; {REASKED_MARKER}."
        run = RunManifest(run_id="run_s", kind="script", status="blocked", project_hash="p", input_hash="a" * 64,
                          stages={"writing": StageRecord(status="blocked", error=Failure(code="invalid_script", message=spent))})
        write_yaml(self.root / "runs/run_s/run_manifest.yaml", run.model_dump(mode="json"))
        write_json(self.root / "studio/job.json", {"id": "job1", "status": "blocked", "action": "resume", "message": spent,
                                                   "finished_at": now(), "run": run.model_dump(mode="json")})
        self.assertTrue(self.app.job(self.root)["fresh_attempts"])
        with patch.object(self.app, "start") as start:
            self.assertEqual(self.app.apply_allowances(), ["example"])
        self.assertEqual(start.call_args.args, ("example", {"action": "resume", "run_id": "run_s"}))
        log = json.loads((self.root / "studio/allowance_log.json").read_text(encoding="utf-8"))
        self.assertEqual([(row["code"], row["kind"], row.get("skipped"), row["resumed"]) for row in log],
                         [("invalid_script", "fresh_attempts", None, True)])
        self.assertEqual(len(json.loads((self.root / "runs/run_s/fresh_attempts.json").read_text(encoding="utf-8"))), 1)


class ResumeWithoutDecisionTests(unittest.TestCase):
    """D-155: technical stops the scheduler resumes by itself; none of them takes an editorial decision."""
    request = test_studio.StudioHttpTests.request

    def setUp(self):
        test_studio.StudioHttpTests.setUp(self)
        self.addCleanup(LOGIN_CHECKS.clear)

    def stopped(self, code, *, status="blocked", kind="research", stage="dossier", message="Angehalten.", count=None,
                finished_at="2026-09-01T10:00:00+00:00", selection=None, job_level=False):
        run = RunManifest(run_id="run_x", kind=kind, status=status, project_hash="p", input_hash="i",
                          stages={stage: StageRecord(status=status, attempts=1,
                                                     error=None if job_level else Failure(code=code, message=message))})
        work = self.root / "runs/run_x"
        write_yaml(work / "run_manifest.yaml", run.model_dump(mode="json"))
        if selection is not None:
            write_json(work / f"{kind}_request.json", {"text_generation": selection})
        job = {"id": "job_x", "action": "resume", "status": status, "finished_at": finished_at, "message": message,
               "run": run.model_dump(mode="json"), **({"error_code": code} if job_level else {}),
               **({"auto_resume_count": count} if count is not None else {})}
        write_json(self.root / "studio/job.json", job)
        return job

    def test_a_login_stop_resumes_once_a_login_check_passes_and_never_by_waiting(self):
        """2026-10-01: three runs stopped at the same minute for an expired Claude login; each was resumed by hand within
        two minutes of ``claude auth login``. The login stays the user's; the resume after it needs no decision."""
        self.stopped("authentication_required", selection={"provider": "claude_code"})
        # Waiting never resumes it, and the page announces no time.
        self.assertNotIn("auto_resume_at", self.app.job(self.root))
        self.assertEqual(self.app.due_resumes(time.time() + 86400), [])
        checks, valid = [], {"claude_code": False, "codex_cli": False}

        def logged_in(provider, settings=None):
            checks.append(provider)
            return valid[provider]
        base = time.time()
        with patch("podcast_automate.subscriptions.logged_in", side_effect=logged_in), \
                patch.object(self.app, "start") as start:
            self.assertEqual(self.app.resume_after_login(base), [])
            self.assertEqual(checks, ["claude_code"], "a fixed Claude run asks only Claude's login")
            # At most one check every five minutes, and none while the project is busy.
            self.assertEqual(self.app.resume_after_login(base + 299), [])
            with patch.object(self.app, "ready_to_start", return_value=False):
                self.assertEqual(self.app.resume_after_login(base + 300), [])
            self.assertEqual(checks, ["claude_code"])
            valid["claude_code"] = True
            self.assertEqual(self.app.resume_after_login(base + 300), ["example"])
            self.assertEqual(start.call_args.args, ("example", {"action": "resume", "run_id": "run_x", "auto_resume_count": 1}))
            # Under the automatic rule either login lets the run go on.
            LOGIN_CHECKS.clear()
            valid.update(claude_code=False, codex_cli=True)
            self.stopped("authentication_required", selection={"provider": "auto", "prefer": "claude_code",
                                                               "candidates": {"claude_code": {}, "codex_cli": {}}})
            self.assertEqual(self.app.resume_after_login(base), ["example"])
            self.assertEqual(checks[-2:], ["claude_code", "codex_cli"])
            # Bounded like every automatic resume: three in a row without progress, then it waits for the user.
            LOGIN_CHECKS.clear()
            calls = len(checks)
            self.stopped("authentication_required", selection={"provider": "codex_cli"}, count=3)
            self.assertEqual(self.app.resume_after_login(base), [])
            # Only the login stop: a decision or a credit stop is never checked.
            for code in ("research_questions_blocked", "anthropic_credits", "research_plan_review"):
                self.stopped(code, selection={"provider": "codex_cli"})
                self.assertEqual(self.app.resume_after_login(base), [])
            self.assertEqual(len(checks), calls)
        self.assertEqual(start.call_count, 2)

    def test_a_checkpoint_or_program_error_resumes_once_after_the_code_changed(self):
        """2026-10-01/02: all five invalid_research_checkpoint and processing_failed stops of the series cleared with a
        resume 1 to 62 minutes after the code fix."""
        self.stopped("invalid_research_checkpoint")
        with patch("podcast_automate.studio.code_updated_at", return_value="2026-09-01T09:00:00+00:00"):
            self.assertNotIn("auto_resume_at", self.app.job(self.root))
            self.assertEqual(self.app.due_resumes(), [])
        with patch("podcast_automate.studio.code_updated_at", return_value="2026-09-01T10:05:00+00:00"):
            shown = self.app.job(self.root)
            self.assertEqual((shown["auto_resume_kind"], shown["auto_resume_at"]), ("code_update", "2026-09-01T10:05:00+00:00"))
            self.assertEqual(self.app.due_resumes(), [("example", "run_x", 0)])
            # A program error the worker caught names its code on the job.
            self.stopped("processing_failed", status="failed", job_level=True)
            self.assertEqual(self.app.due_resumes(), [("example", "run_x", 0)])
            # The resume after the update stopped again: it waits for the next update.
            self.stopped("processing_failed", status="failed", job_level=True, count=1,
                         finished_at="2026-09-01T10:30:00+00:00")
            self.assertNotIn("auto_resume_at", self.app.job(self.root))
            self.assertEqual(self.app.due_resumes(), [])
            with self.started():
                self.stopped("invalid_research_checkpoint")
                self.assertEqual(self.app.resume_due(), ["example"])
        self.assertEqual(json.loads((self.root / "studio/job.json").read_text(encoding="utf-8"))["auto_resume_count"], 1)

    started = test_studio.StudioStopTests.started

    def test_a_missing_search_or_an_unreadable_answer_resumes_but_a_spent_correction_loop_waits_for_its_allowance(self):
        from podcast_automate.subscriptions import parse_iso
        base = parse_iso("2026-09-01T10:00:00+00:00").timestamp()
        for code in ("search_not_observed", "invalid_model_output"):
            with self.subTest(code=code):
                self.stopped(code)
                shown = self.app.job(self.root)
                self.assertEqual((shown["auto_resume_kind"], shown["auto_resume_at"]), ("transient", "2026-09-01T10:10:00+00:00"))
                self.assertEqual(self.app.due_resumes(base + 600), [("example", "run_x", 0)])
        # The same code after a correction loop spent its attempts on rejected answers is no transient failure: the
        # fresh-attempt pre-approval covers it (run_budget.reasked_stop), not the waiting.
        spent = f"Review verweist auf unbekannte Segmente: seg_9. Der Aufruf wurde 2 Mal mit Korrekturhinweis wiederholt; {REASKED_MARKER}."
        self.stopped("invalid_model_output", kind="script", stage="review", message=spent)
        self.assertNotIn("auto_resume_at", self.app.job(self.root))
        self.assertEqual(self.app.due_resumes(base + 86400), [])
        self.assertEqual(self.request("/api/projects/example/allowances", {"fresh_attempts": 1, "extra_calls": 0})[0], 200)
        with patch.object(self.app, "start") as start:
            self.assertEqual(self.app.apply_allowances(), ["example"])
        self.assertEqual(start.call_args.args, ("example", {"action": "resume", "run_id": "run_x"}))

    def run_worker(self, job, perform):
        from podcast_automate import studio_worker
        from podcast_automate.logs import release_logging
        write_json(self.root / "studio/job.json", job)
        request = {"action": "resume", "run_id": "run_x", "text": {}}
        with patch.dict(os.environ, {"PLA_SUBSCRIPTIONS_STORE": str(self.workspace / "subscriptions.json")}), \
                patch.object(studio_worker, "perform", side_effect=perform), \
                patch.object(studio_worker, "keep_awake", return_value=lambda: None), \
                patch.object(studio_worker, "watch", return_value=None), \
                patch.object(studio_worker, "start_monitor", side_effect=OSError("no monitor in tests")), \
                patch.object(studio_worker.sys, "argv", ["studio_worker", str(self.root)]), \
                patch.object(studio_worker.sys, "stdin", io.StringIO(json.dumps(request))):
            try:
                studio_worker.main()
            finally:
                release_logging(self.root / "studio/worker.log")
        return json.loads((self.root / "studio/job.json").read_text(encoding="utf-8"))

    def test_the_count_of_automatic_resumes_starts_again_once_a_resumed_job_answered_a_call(self):
        """D-155: a research run of 33 to 35 hours on one subscription meets the five-hour window more than three times;
        the count carried every automatic resume since the last manual one, progress or not. MAX_AUTO_RESUMES now
        bounds resumes in a row without progress, as STUDIO.md words it."""
        job = self.stopped("subscriptions_exhausted", status="waiting_for_quota", count=2)
        paused = RunManifest(run_id="run_x", kind="research", status="waiting_for_quota", project_hash="p", input_hash="i",
                             stages={"dossier": StageRecord(status="waiting_for_quota", error=Failure(
                                 code="subscriptions_exhausted", message="Kein Abo hat gerade Kontingent."))})
        calls = self.root / "runs/run_x/calls"
        write_json(calls / "call_001/response.json", {"answer": "before the resume"})

        def answered(root, request, progress=None):
            write_json(calls / "call_002/response.json", {"answer": "after the resume"})
            return {"run": paused.model_dump(mode="json")}

        def silent(root, request, progress=None):
            write_json(calls / "call_003/failure.json", {"code": "timeout"})
            return {"run": paused.model_dump(mode="json")}

        def raised(root, request, progress=None):
            write_json(calls / "call_004/response.json", {"answer": "then a stop"})
            raise AppError("Zeitlimit erreicht.", code="timeout", status="failed")
        self.assertEqual(self.run_worker(job, silent)["auto_resume_count"], 2)
        self.assertEqual(self.run_worker(job, answered)["auto_resume_count"], 0)
        self.assertEqual(self.run_worker(job, raised)["auto_resume_count"], 0)
        # A resume by hand carries no count, and nothing is added.
        by_hand = {key: value for key, value in job.items() if key != "auto_resume_count"}
        self.assertNotIn("auto_resume_count", self.run_worker(by_hand, answered))


class NewWorkspaceTests(unittest.TestCase):
    def test_a_new_workspace_starts_with_two_fresh_attempts_and_250_extra_calls_and_no_other_one_changes(self):
        """D-155: a newcomer meets every pre-approvable stop; the user's own tested values are the start of a workspace
        that has neither settings nor a project. Every other workspace keeps what it has."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        workspace = Path(temp.name).resolve()
        studio = Studio(workspace)
        self.assertEqual(studio.settings_values()["allowances"], {"fresh_attempts": 2, "extra_calls": 250})
        brief = TopicBrief(topic="Erstes Projekt", voice_profile={"host_a": "Aiden", "host_b": "Vivian"}).model_dump(mode="json")
        first = workspace / "projects" / studio.create({"config": brief, "text": {}})["id"]
        self.assertEqual(studio_allowances.allowances(first), {"fresh_attempts": 2, "extra_calls": 250})
        self.assertEqual(studio.settings_values(first)["allowances"], {"fresh_attempts": 2, "extra_calls": 250})
        # From the first project on the workspace is no longer new: the rule for the next project stays as it was.
        self.assertEqual(studio.settings_values()["allowances"], {"fresh_attempts": 0, "extra_calls": 0})
        second = workspace / "projects" / studio.create({"config": {**brief, "topic": "Zweites Projekt"}, "text": {}})["id"]
        self.assertFalse((second / "studio/allowances.json").exists())
        self.assertEqual(studio_allowances.allowances(second), {"fresh_attempts": 0, "extra_calls": 0})
        # A workspace whose settings were saved before its first project keeps them as well.
        other = Path(tempfile.mkdtemp(dir=temp.name)).resolve()
        (other / "projects").mkdir()
        write_json(other / "projects/.studio-settings.json", {"allowances": {"fresh_attempts": 1, "extra_calls": 0}})
        saved = Studio(other)
        self.assertEqual(saved.settings_values()["allowances"], {"fresh_attempts": 0, "extra_calls": 0})
        project = other / "projects" / saved.create({"config": brief, "text": {}})["id"]
        self.assertFalse((project / "studio/allowances.json").exists())
        self.assertEqual(studio_allowances.allowances(project), {"fresh_attempts": 1, "extra_calls": 0})


class ReportTests(unittest.TestCase):
    setUp = test_studio.StudioHttpTests.setUp
    request = test_studio.StudioHttpTests.request

    def call(self, work, number, version, provider, minutes, status="completed", cost=0.0):
        folder = work / "calls" / f"call_{number:03d}"
        start = f"2026-09-29T10:{number:02d}:00+00:00"
        write_json(folder / "activity.json", {"status": status, "started_at": start,
                                              "updated_at": f"2026-09-29T{10 + (number + minutes) // 60:02d}:{(number + minutes) % 60:02d}:00+00:00"})
        write_json(folder / "metadata.json", {"prompt_version": version, "provider": provider, "reported_cost_usd": cost})
        write_json(folder / "provider_choice.json", {"provider": provider, "prompt_version": version})

    def test_a_runs_calls_time_stops_and_approvals_by_stage_and_prompt_version(self):
        work = self.root / "runs/run_s"
        write_yaml(work / "run_manifest.yaml", RunManifest(run_id="run_s", kind="script", project_hash="p",
                                                           input_hash="a" * 64, stages={}).model_dump(mode="json"))
        self.call(work, 1, "script_review.v9-gaps-notes", "claude_code", 2, cost=1.5)
        self.call(work, 2, "script_review_repair.v2-delete-absence", "claude_code", 3)
        self.call(work, 3, "script_review.v9-gaps-notes+followup", "codex_cli", 9)
        self.call(work, 4, "write_episode.v7-audit-notes", "claude_code", 1, status="running")
        (work / "failures").mkdir(parents=True)
        (work / "failures/review_1_20260929_101500_000000.txt").write_text("Traceback", encoding="utf-8")
        write_json(work / "fresh_attempts.json", [{"reviews": ["ep_002"], "approved_at": now()}])
        write_json(self.root / "studio/allowance_log.json", [
            {"run_id": "run_s", "kind": "model_calls", "extra_calls": 15, "job_id": "j"},
            {"run_id": "run_other", "kind": "fresh_attempts", "job_id": "k"},
            {"run_id": "run_s", "kind": "fresh_attempts", "job_id": "l", "skipped": True}])
        status, body, _ = self.request("/api/projects/example/report?run_id=run_s")
        self.assertEqual(status, 200, body)
        report = json.loads(body)["report"]
        self.assertEqual((report["calls"], report["failed_calls"], report["model_minutes"]), (4, 1, 15.0))
        review = next(row for row in report["stages"] if row["stage"] == "script_review")
        self.assertEqual((review["label"], review["calls"], review["minutes"], review["providers"]),
                         ("Belegprüfung", 2, 11.0, {"claude_code": 1, "codex_cli": 1}))
        self.assertEqual(report["stages"][0]["stage"], "script_review", "the costliest stage first")
        # A fix's effect shows by prompt version, in the order the versions first ran.
        self.assertEqual([row["version"] for row in report["versions"]][:3],
                         ["script_review.v9-gaps-notes", "script_review_repair.v2-delete-absence",
                          "script_review.v9-gaps-notes+followup"])
        self.assertEqual(report["providers"]["claude_code"]["reported_usd"], 1.5)
        self.assertEqual(report["stops"], {"total": 1, "by_stage": {"review": 1}})
        self.assertEqual((report["approvals"]["fresh_attempts"], [row["job_id"] for row in report["approvals"]["allowances"]]),
                         (1, ["j"]))
        for wrong in ("run_missing", "../x", None):
            self.assertEqual(self.request(f"/api/projects/example/report?run_id={wrong}")[0], 400, wrong)


class RestartTests(unittest.TestCase):
    setUp = test_studio.StudioHttpTests.setUp
    request = test_studio.StudioHttpTests.request

    def test_a_restart_waits_until_nothing_runs_and_no_job_starts_meanwhile(self):
        self.assertEqual(self.request("/api/server/restart", {})[0], 400, "without serve() there is nothing to restart")
        stopped = []
        self.app.restart_hook = lambda: stopped.append(True)
        status, body, _ = self.request("/api/server/restart", {})
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["server"]["restart_requested"])
        with patch.object(self.app, "active_workers", return_value={"busy": object()}):
            self.assertFalse(self.app.restart_when_idle())
        self.assertEqual(stopped, [])
        self.assertTrue(self.app.restart_when_idle())
        self.assertEqual(stopped, [True])
        status, body, _ = self.request("/api/projects/example/start", {"action": "check"})
        self.assertEqual((status, json.loads(body)["code"]), (400, "studio_restarting"))

    def test_changed_code_marks_the_server_stale(self):
        self.assertFalse(json.loads(self.request("/api/projects")[1])["server"]["stale"])
        with patch("podcast_automate.studio.code_fingerprint", return_value="changed"):
            self.assertTrue(json.loads(self.request("/api/projects/example")[1])["server"]["stale"])

    def test_the_new_server_starts_on_the_same_workspace_and_port_without_a_browser(self):
        with patch("podcast_automate.studio.subprocess.Popen") as popen:
            relaunch(self.workspace, 8765, True)
        command = popen.call_args.args[0]
        self.assertEqual(command[1:], ["-m", "podcast_automate", "studio", str(self.workspace.resolve()),
                                       "--port", "8765", "--no-browser", "--lan"])
        # A new server that fails before its own log starts leaves its reason here (2026-10-02: no trace at all).
        self.assertTrue(popen.call_args.kwargs["stderr"].name.endswith("relaunch.log"))
        self.assertIn("Neustart", (self.workspace / ".studio/relaunch.log").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
