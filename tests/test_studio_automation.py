"""What the Studio does by itself between the user's visits: allowances set ahead, the production report, restart
when idle."""
import json
import unittest
from unittest.mock import patch

from podcast_automate.models import RunManifest, StageRecord, now
from podcast_automate.storage import read_yaml, write_json, write_yaml
from podcast_automate.studio import relaunch
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


if __name__ == "__main__":
    unittest.main()
