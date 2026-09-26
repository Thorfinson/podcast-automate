import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from podcast_automate.errors import AppError
from podcast_automate.models import Failure, RunManifest, StageRecord, TopicBrief
from podcast_automate.runner import manifest_path, outputs_valid
from podcast_automate.script_models import SeriesPlan
from podcast_automate.scripting import outline_hash, run_script
from podcast_automate.speech import AudioChoice, audio_generation_record
from podcast_automate.storage import digest, file_hash, init_project, project_hash, read_yaml, write_json, write_yaml
from podcast_automate.studio import BriefProposal, Studio, make_server, record_interruption
from podcast_automate.studio_worker import perform
from tests import script_fixtures as fixtures
from tests.script_fixtures import example_plan, example_script


class OutlineGateTests(fixtures.ScriptProjectCase):
    def test_plan_stops_before_writing_and_cannot_be_resumed_without_approval(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            planned = run_script(self.root, plan_only=True)
            self.assertEqual(self.calls, [SeriesPlan])
            self.assertEqual(planned.status, "pending")
            self.assertEqual(planned.stages["teaching"].attempts, 0)
            self.assertFalse((self.root / "episodes/ep_001/script.md").exists())
            with self.assertRaises(AppError) as denied:
                run_script(self.root, resume=True)
            self.assertEqual(denied.exception.code, "plan_approval_required")
            work = manifest_path(self.root, planned.run_id).parent
            with self.assertRaises(AppError) as stale:
                run_script(self.root, resume=True, approved_plan_hash="a stale tab")
            self.assertEqual(stale.exception.code, "plan_changed")
            completed = run_script(self.root, resume=True, approved_plan_hash=outline_hash(work))
            self.assertEqual(completed.status, "completed")
            self.assertEqual(len(self.calls), 11)
            run_script(self.root, resume=True)
            self.assertEqual(len(self.calls), 11)

    def test_outline_feedback_changes_reviewed_hash_without_writing(self):
        def revised(prompt, output_type, directory, **kwargs):
            self.assertIn("Show the motivation first", prompt)
            self.assertIn("previous_outline", prompt)
            plan = example_plan()
            plan.explanation_path = "An opening problem leads into the concrete comparison."
            return plan, {}
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            first = run_script(self.root, plan_only=True)
        work = manifest_path(self.root, first.run_id).parent
        before = outline_hash(work)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=revised):
            result = run_script(self.root, resume=True, plan_only=True, outline_feedback="Show the motivation first")
        self.assertNotEqual(outline_hash(work), before)
        self.assertEqual(result.stages["writing"].attempts, 0)
        self.assertTrue(outputs_valid(self.root, result.stages["planning"]))
        with self.assertRaises(AppError):
            run_script(self.root, resume=True, approved_plan_hash=before)

    def test_studio_resume_of_interrupted_outline_does_not_grant_approval(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run = run_script(self.root, plan_only=True)
            result = perform(self.root, {"action": "resume", "run_id": run.run_id,
                                        "text": {"provider": "codex_cli"}})
        self.assertEqual(result["run"]["status"], "pending")
        self.assertEqual(self.calls, [SeriesPlan])
        self.assertFalse((manifest_path(self.root, run.run_id).parent / "plan_approval.json").exists())

    def test_studio_assistant_proposes_without_changing_the_brief(self):
        original = (self.root / "project.yaml").read_bytes()
        proposal = BriefProposal(message="Hier ist ein konkreter Vorschlag.", topic="New title",
            central_question="A deeper question?", prior_knowledge="None", depth_request="University depth",
            focus_questions=["Why?"], excluded_topics=[])
        with patch("podcast_automate.studio_worker.CodexAdapter.structured", return_value=(proposal, {})):
            result = perform(self.root, {"action":"assistant", "message":"Go deeper", "text":{}})
        self.assertEqual(result["proposal"]["topic"], "New title")
        self.assertEqual(original, (self.root / "project.yaml").read_bytes())
        self.assertIn("Go deeper", (self.root / "studio/chat.json").read_text())

    def test_worker_uses_selected_model_for_assistant_and_initial_research(self):
        from podcast_automate.studio import TextChoice
        selection = {"provider": "codex_cli", "model": "gpt-5.6-sol", "reasoning_effort": "high"}
        self.assertEqual(TextChoice().kwargs()["model"], "gpt-6-astra")
        self.assertEqual(TextChoice().kwargs()["reasoning_effort"], "xhigh")
        with patch("podcast_automate.studio_worker.run_research") as research:
            research.return_value.model_dump.return_value = {"status": "pending"}
            perform(self.root, {"action": "research", "text": selection})
            # Every Studio research run stops for the plan projection; only the research page approves it.
            research.assert_called_once_with(self.root, model="gpt-5.6-sol", reasoning_effort="high", plan_review="required")
        proposal = BriefProposal(message="A proposal.", topic="Title", central_question="Why?", prior_knowledge="",
                                 depth_request="Deep", focus_questions=[], excluded_topics=[])
        def reply(adapter, *args, **kwargs):
            self.assertEqual(adapter.settings.codex_model, "gpt-5.6-sol")
            self.assertEqual(adapter.reasoning_effort, "high")
            return proposal, {}
        with patch("podcast_automate.studio_worker.CodexAdapter.structured", autospec=True, side_effect=reply):
            perform(self.root, {"action": "assistant", "message": "Help", "text": selection})

    def test_the_worker_re_render_relies_on_the_saved_approval_instead_of_a_fresh_receipt(self):
        request = {"action": "audio", "episode": "ep_001", "text": {}, "script_hash": "s", "readable_hash": "r",
                   "config_hash": "c", "audio_settings": AudioChoice(voices=self.config.voice_profile).model_dump()}
        with patch("podcast_automate.studio_worker.run_episode_audio") as run:
            run.return_value.model_dump.return_value = {"status": "completed"}
            perform(self.root, {**request, "rerender": True})
            self.assertFalse(run.call_args.kwargs["approve_audio"])
            self.assertIn("gespeicherten Freigabe", run.call_args.kwargs["approval_note"])
            perform(self.root, request)
            self.assertTrue(run.call_args.kwargs["approve_audio"])
            self.assertIn("ausdrücklich", run.call_args.kwargs["approval_note"])

    def test_audio_hash_is_rechecked_inside_pipeline_before_gpu(self):
        from podcast_automate.episode_audio import run_episode_audio
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run_script(self.root)
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("GPU must stay idle")):
            with self.assertRaises(AppError) as denied:
                run_episode_audio(self.root, episode="ep_001", approve_audio=True, expected_script_hash="old")
        self.assertEqual(denied.exception.code, "script_edited")

    def test_revising_one_episode_keeps_other_episodes_reviewable(self):
        from podcast_automate.episode_audio import reviewed_episode
        from podcast_automate.models import EpisodeScript
        def multi_model(prompt, output_type, directory, **kwargs):
            value, metadata = self.model(prompt, output_type, directory, **kwargs)
            if output_type is SeriesPlan:
                second = value.episodes[0].model_copy(deep=True)
                second.episode_id = "ep_002"
                second.title = "Further consequences"
                value.episodes.append(second)
            if output_type is EpisodeScript:
                payload = json.loads(prompt.splitlines()[-1])
                # Both writing and polishing receive the selected episode as structured data.
                value.episode_id = payload.get("episode", payload.get("original_script"))["episode_id"]
            return value, metadata
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=multi_model):
            first = run_script(self.root)
            self.assertEqual(first.status, "completed")
            second = run_script(self.root, revise="ep_002", feedback="Clarify the consequence")
            self.assertEqual(second.status, "completed")
        owner, script, _ = reviewed_episode(self.root, self.config, "ep_001")
        self.assertEqual(owner.run_id, first.run_id)
        self.assertEqual(script.episode_id, "ep_001")


class StudioHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.root = self.workspace / "projects" / "example"
        self.config = TopicBrief(topic="A test project", voice_profile={"host_a":"Aiden", "host_b":"Vivian"})
        init_project(self.root, self.config)
        self.server = make_server(self.workspace, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.app = self.server.studio

    def request(self, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        base = {"X-Studio-Token":self.app.token, "Content-Type":"application/json"}
        base.update(headers or {})
        connection.request("GET" if data is None else "POST", path,
                           None if data is None else json.dumps(data), headers=base)
        response = connection.getresponse()
        body = response.read()
        result = response.status, body, dict(response.getheaders())
        connection.close()
        return result

    def publish_episode(self, episode="ep_001"):
        """The two files a published episode always has; nothing here is approved for audio."""
        script = example_script()
        write_yaml(self.root / "episodes" / episode / "script.yaml", script.model_dump())
        (self.root / "episodes" / episode / "script.md").write_text("# " + script.title, encoding="utf-8")

    def test_the_plan_approval_route_writes_a_run_bound_receipt_only_for_a_waiting_research_plan(self):
        from podcast_automate.research_ledger import save_value
        from podcast_automate.run_budget import read_plan_approval
        from tests.question_fixtures import task_value
        work = self.root / "runs/run_plan"
        write_yaml(work / "run_manifest.yaml", RunManifest(run_id="run_plan", kind="research", project_hash="0" * 64,
                                                           input_hash="b" * 64, stages={}).model_dump(mode="json"))
        plan = {"tasks": [task_value("task_a"), task_value("task_b")]}
        save_value(work / "question_research/state.json", {"plan": plan, "phase": "awaiting_plan_approval", "tasks": {}})
        status, body, _ = self.request("/api/projects/example/approve", {"kind": "plan", "run_id": "run_plan", "max_tasks": 1})
        self.assertEqual(status, 200, body)
        receipt = read_plan_approval(work)
        self.assertEqual((receipt.run_id, receipt.input_hash, receipt.plan_hash, receipt.max_tasks, receipt.source),
                         ("run_plan", "b" * 64, digest(plan), 1, "studio"))
        self.assertEqual(json.loads(body)["plan"]["max_tasks"], 1)
        # The route answers a rejected cap with 400; the cap rules themselves are pinned on approve_research_plan.
        self.assertEqual(self.request("/api/projects/example/approve",
                                      {"kind": "plan", "run_id": "run_plan", "max_tasks": 0})[0], 400)
        self.assertEqual(read_plan_approval(work).max_tasks, 1, "a rejected request leaves the receipt alone")
        # Without a cap the receipt approves the shown plan as it is; a running plan takes no cap any more.
        self.assertEqual(self.request("/api/projects/example/approve", {"kind": "plan", "run_id": "run_plan"})[0], 200)
        self.assertIsNone(read_plan_approval(work).max_tasks)
        save_value(work / "question_research/state.json", {"plan": plan, "phase": "questions", "tasks": {}})
        self.assertEqual(self.request("/api/projects/example/approve",
                                      {"kind": "plan", "run_id": "run_plan", "max_tasks": 1})[0], 400)
        script = self.root / "runs/run_script"
        write_yaml(script / "run_manifest.yaml", RunManifest(run_id="run_script", kind="script", project_hash="0" * 64,
                                                             input_hash="b" * 64, stages={}).model_dump(mode="json"))
        self.assertEqual(self.request("/api/projects/example/approve", {"kind": "plan", "run_id": "run_script"})[0], 400)
        self.assertFalse((script / "plan_approval.json").exists())
        self.assertEqual(self.request("/api/projects/example/approve", {"kind": "plan", "run_id": "run_plan"},
                                      {"X-Studio-Token": "wrong"})[0], 403)

    def test_local_page_and_project_are_real_and_mutations_need_csrf_token(self):
        status, body, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"Podcast Studio", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/api/projects/example")[0], 200)
        self.assertEqual(self.request("/api/key", {"key":"secret"}, {"X-Studio-Token":"wrong"})[0], 403)
        self.assertEqual(self.request("/api/bootstrap", headers={"Host":"evil.example"})[0], 403)
        self.assertEqual(self.request("/api/bootstrap", headers={"Origin":"https://evil.example"})[0], 403)
        self.assertEqual(self.request("/api/bootstrap", headers={"Sec-Fetch-Site":"cross-site"})[0], 403)
        self.assertNotEqual(self.request("/media/example/../../project.yaml")[0], 200)

    def test_progress_read_failure_keeps_last_snapshot_and_does_not_fail_job_api(self):
        run = {"run_id": "run_test", "kind": "script", "status": "running", "stages": {}}
        progress = {"phase": "script", "model_calls": 41, "total_segments": 6, "updated_at": "2026-09-13T20:00:00+00:00"}
        write_json(self.root / "studio/job.json", {"id": "active", "status": "running", "run": run})
        write_json(self.root / "runs/run_test/progress.json", progress)
        worker = Mock()
        worker.poll.return_value = None
        self.app.workers[self.root] = (worker, False)
        with patch("podcast_automate.studio_progress.script_progress", side_effect=PermissionError("temporarily locked")):
            status, body, _ = self.request("/api/projects/example")
        self.assertEqual(status, 200)
        job = json.loads(body)["job"]
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["progress"], progress)

    def test_secret_stays_in_memory_and_out_of_project_files_and_command_line(self):
        self.assertEqual(self.request("/api/key", {"key":"test-secret-key"})[0], 200)
        self.assertNotIn(b"test-secret-key", self.request("/api/bootstrap")[1])
        captured = []
        class Pipe(io.StringIO):
            def close(self):
                captured.append(self.getvalue())
                super().close()
        process = Mock(stdin=Pipe())
        process.poll.return_value = None
        with patch("podcast_automate.studio.subprocess.Popen", return_value=process) as launch:
            status, _, _ = self.request("/api/projects/example/start", {"action":"assistant", "message":"Help me"})
        self.assertEqual(status, 200)
        self.assertIn("test-secret-key", captured[0])
        self.assertNotIn("test-secret-key", str(launch.call_args.args))
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"test-secret-key", path.read_bytes())
        self.assertEqual(self.request("/api/projects/example/start", {"action":"research"})[0], 400)

    def test_save_preserves_runtime_and_rejects_stale_configuration(self):
        detail = json.loads(self.request("/api/projects/example")[1])
        changed = detail["config"]
        changed["topic"] = "A better question"
        changed["runtime"]["codex_executable"] = "untrusted-command"
        data = {"config":changed, "config_hash":detail["config_hash"], "text":{"provider":"codex_cli"}}
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 200)
        saved = read_yaml(self.root / "project.yaml")
        self.assertEqual(saved["topic"], "A better question")
        self.assertEqual(saved["runtime"]["codex_executable"], "codex")
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 400)

    def test_model_selection_roundtrips_and_invalid_effort_never_changes_saved_settings(self):
        bootstrap = json.loads(self.request("/api/bootstrap")[1])
        self.assertTrue(bootstrap["capabilities"]["text_reasoning_selection"])
        self.assertIn("gpt-6-astra", bootstrap["text_catalog"]["codex_models"])
        detail = json.loads(self.request("/api/projects/example")[1])
        data = {"config": detail["config"], "config_hash": detail["config_hash"],
                "text": {"provider": "codex_cli", "model": "gpt-6-astra", "reasoning_effort": "xhigh"}}
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 200)
        saved = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(saved["text"]["reasoning_effort"], "xhigh")
        self.assertEqual(saved["text"]["model"], "gpt-6-astra")
        self.assertEqual(saved["config_hash"], detail["config_hash"])
        before = (self.root / "studio/text.json").read_bytes()
        data["text"]["reasoning_effort"] = "invalid"
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 400)
        self.assertEqual(before, (self.root / "studio/text.json").read_bytes())

    def test_job_displays_its_saved_choice_instead_of_later_project_choices(self):
        run = {"run_id": "run_test", "kind": "script", "status": "pending", "stages": {}}
        selected = {"provider": "codex_cli", "model": "gpt-5.6-sol", "reasoning_effort": "high"}
        write_json(self.root / "studio/job.json", {"id": "saved", "status": "pending", "run": run})
        write_json(self.root / "runs/run_test/script_request.json", {"text_generation": selected})
        write_json(self.root / "studio/text.json", {"provider": "codex_cli", "model": "gpt-6-astra", "reasoning_effort": "xhigh"})
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["job"]["text_generation"], selected)
        self.assertEqual(detail["text"]["model"], "gpt-6-astra")

    def test_unpublished_script_is_readable_but_cannot_be_submitted_for_audio(self):
        run = {"run_id": "run_test", "kind": "script", "status": "running", "stages": {"writing": {"status": "running"}}}
        work = self.root / "runs/run_test"
        write_json(self.root / "studio/job.json", {"id": "active", "status": "running", "run": run})
        write_json(work / "series_plan.json", example_plan().model_dump())
        write_json(work / "script_request.json", {"episode": None})
        write_json(work / "drafts/ep_001.json", example_script().model_dump())
        write_json(work / "drafts/ep_001.checkpoint.json", {"sha256": file_hash(work / "drafts/ep_001.json")})
        worker = Mock()
        worker.poll.return_value = None
        self.app.workers[self.root] = (worker, False)
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["episodes"], [])
        self.assertEqual(detail["script_previews"][0]["state"], "draft")
        self.assertEqual(detail["job"]["progress"]["script_previews"], detail["script_previews"])
        # Even after an interruption, a preview has no publish record or audio approval.
        worker.poll.return_value = 0
        with patch("podcast_automate.studio.subprocess.Popen") as launch:
            status, _, _ = self.request("/api/projects/example/start", {"action": "audio", "episode": "ep_001",
                "approve_audio": True, "script_hash": detail["script_previews"][0]["hash"]})
        self.assertEqual(status, 400)
        launch.assert_not_called()
        self.assertFalse((self.root / "episodes/ep_001/script.yaml").exists())

    def test_published_episodes_expose_the_recorded_review_caveats(self):
        self.publish_episode()
        write_yaml(self.root / "reports/script_quality.yaml", {"episodes": {"ep_001": {
            "model_review": {"limitations": ["Eine Modellprüfung kann Fehler übersehen.", ""]},
            "teaching_review": {"review": {"limitations": ["Kein echtes Lernen gemessen."]},
                                "editorial": {"limitations": []}},
            "dialogue_polish": {"review": {"limitations": ["Kein Hörtest."]}},
            "dismissed_gaps": [{"stage": "teaching_review", "objective_id": "goal_one",
                                "gap": "Wie wird trainiert?", "reason": "Für das Lernziel nicht nötig.",
                                "extra": "wird nicht durchgereicht"}],
            "advisories": [{"code": "long_cold_open", "count": 157, "detail": "157 Wörter.",
                            "segment_ids": ["seg_001"]}]}}})
        notes = json.loads(self.request("/api/projects/example")[1])["episodes"][0]["review_notes"]
        self.assertEqual(notes["script_review"], ["Eine Modellprüfung kann Fehler übersehen."])
        self.assertEqual(notes["teaching_review"], ["Kein echtes Lernen gemessen."])
        self.assertEqual(notes["dialogue_polish"], ["Kein Hörtest."])
        self.assertNotIn("editorial_review", notes)
        self.assertEqual(notes["dismissed_gaps"], [{"stage": "teaching_review", "objective_id": "goal_one",
            "gap": "Wie wird trainiert?", "reason": "Für das Lernziel nicht nötig."}])
        self.assertEqual(notes["advisories"][0]["code"], "long_cold_open")

    def test_a_spoken_override_is_saved_per_segment_and_rejects_anything_unknown(self):
        self.publish_episode()
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml", {"audio_approved": True})
        status, body, _ = self.request("/api/projects/example/spoken_override",
                                       {"episode": "ep_001", "segment_id": "seg_002", "spoken": "  Anders gesprochen.  "})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["spoken_overrides"], {"seg_002": "Anders gesprochen."})
        decision = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")
        self.assertEqual(decision["spoken_overrides"], {"seg_002": "Anders gesprochen."})
        self.assertTrue(decision["audio_approved"])
        for payload in ({"episode": "../etc", "segment_id": "seg_002", "spoken": "x"},
                        {"episode": "ep_001", "segment_id": "seg_999", "spoken": "x"},
                        {"episode": "ep_404", "segment_id": "seg_002", "spoken": "x"},
                        {"episode": "ep_001", "segment_id": "seg_002", "spoken": "x" * 4001}):
            with self.subTest(payload=payload):
                self.assertEqual(self.request("/api/projects/example/spoken_override", payload)[0], 400)
        empty = self.request("/api/projects/example/spoken_override",
                             {"episode": "ep_001", "segment_id": "seg_002", "spoken": "   "})
        self.assertEqual(json.loads(empty[1])["spoken_overrides"], {})

    def test_a_spoken_override_reaches_the_reading_view_and_never_carries_a_key(self):
        self.publish_episode()
        self.assertEqual(self.request("/api/key", {"key": "sk-or-test-secret-key"})[0], 200)
        write_json(self.root / "studio/spoken_forms.json", {"schema_version": "1.0",
                   "entries": [{"written": "model", "spoken": "Modell"}, {"written": "H800", "spoken": "H achthundert"}]})
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml",
                   {"audio_approved": True, "spoken_overrides": {"seg_001": "So gesprochen."}})
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["episodes"][0]["spoken_overrides"], {"seg_001": "So gesprochen."})
        self.assertFalse(detail["episodes"][0]["human_listening_reviewed"])
        self.assertEqual([row["written"] for row in detail["spoken_forms"]["entries"]], ["model", "H800"])
        self.assertNotIn("sk-or-", json.dumps(detail))

    def test_the_pronunciation_report_is_computed_before_any_audio_run(self):
        script = example_script().model_copy(deep=True)
        script.segments[0].text = "Wir rechnen auf H800 mit 2048 Positionen und der KL-Abweichung."
        write_yaml(self.root / "episodes/ep_001/script.yaml", script.model_dump())
        (self.root / "episodes/ep_001/script.md").write_text("# " + script.title, encoding="utf-8")
        write_json(self.root / "studio/spoken_forms.json", {"schema_version": "1.0",
                   "entries": [{"written": "KL", "spoken": "Kullback-Leibler"}]})
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml", {"spoken_overrides": {"seg_002": "Anders."}})
        episode = json.loads(self.request("/api/projects/example")[1])["episodes"][0]
        self.assertEqual(episode["audio"], [])
        report = episode["pronunciation"]
        self.assertEqual([row["token"] for row in report["flagged"]["versions"]], ["H800"])
        self.assertEqual([row["token"] for row in report["flagged"]["numbers"]], ["2048"])
        self.assertNotIn("abbreviations", report["flagged"])  # the table entry resolved "KL"
        self.assertEqual(report["applied"], {"KL": 1})
        self.assertEqual(report["overrides"], ["seg_002"])
        self.assertFalse(report["human_pronunciation_reviewed"])

    def test_a_re_render_starts_on_the_saved_approval_and_is_refused_without_one(self):
        self.publish_episode()
        folder = self.root / "episodes/ep_001"
        audio = AudioChoice(voices=self.config.voice_profile)
        data = {"action": "audio", "episode": "ep_001", "rerender": True, "approve_audio": False,
                "script_hash": file_hash(folder / "script.yaml"), "readable_hash": file_hash(folder / "script.md"),
                "config_hash": project_hash(self.config), "audio_hash": digest(audio.model_dump())}
        with patch("podcast_automate.studio.subprocess.Popen") as process:
            status, body, _ = self.request("/api/projects/example/start", data)
            self.assertEqual((status, json.loads(body)["code"]), (400, "audio_approval_required"))
            # An approval of another script hash or of other voices does not count.
            write_yaml(folder / "audio_review.yaml", {"audio_approved": True, "scripts": {"ep_001": "older"},
                                                      "audio_generation": audio_generation_record(audio)})
            self.assertEqual(json.loads(self.request("/api/projects/example/start", data)[1])["code"], "audio_approval_required")
            write_yaml(folder / "audio_review.yaml", {"audio_approved": True, "scripts": {"ep_001": data["script_hash"]},
                "audio_generation": {"provider": "qwen3_local", "voices": {"host_a": "Ryan", "host_b": "Serena"}}})
            self.assertEqual(json.loads(self.request("/api/projects/example/start", data)[1])["code"], "audio_approval_required")
            process.assert_not_called()
            # The decision the pipeline writes: approved, this script, these voices, no pause policy.
            write_yaml(folder / "audio_review.yaml", {"audio_approved": True, "scripts": {"ep_001": data["script_hash"]},
                                                      "audio_generation": audio_generation_record(audio)})
            process.return_value.stdin = io.StringIO()
            process.return_value.stdin.close = Mock()
            process.return_value.poll.return_value = None
            status, body, _ = self.request("/api/projects/example/start", data)
            self.assertEqual(status, 200)
            payload = json.loads(process.return_value.stdin.getvalue())
        self.assertEqual(json.loads(body)["status"], "running")
        self.assertEqual((payload["action"], payload["rerender"], payload["episode"]), ("audio", True, "ep_001"))
        self.assertEqual((payload["script_hash"], payload["audio_settings"]), (data["script_hash"], audio.model_dump()))

    def test_host_names_are_saved_through_the_settings_route_and_shown_in_detail(self):
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertIsNone(detail["config"]["host_names"])
        self.assertEqual(detail["host_labels"], {"host_a": "Host A", "host_b": "Host B"})
        data = {"config": {**detail["config"], "host_names": {"host_a": "Mara", "host_b": "Jonas"}},
                "config_hash": detail["config_hash"], "text": detail["text"]}
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 200)
        saved = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(saved["config"]["host_names"], {"host_a": "Mara", "host_b": "Jonas"})
        self.assertEqual(saved["host_labels"], {"host_a": "Mara", "host_b": "Jonas"})
        self.assertNotEqual(saved["config_hash"], detail["config_hash"])
        self.assertEqual(read_yaml(self.root / "project.yaml")["host_names"], {"host_a": "Mara", "host_b": "Jonas"})
        # Half a pair is rejected by the brief itself; both empty means unset, and the hash is back.
        half = {"config": {**saved["config"], "host_names": {"host_a": "Mara", "host_b": ""}},
                "config_hash": saved["config_hash"], "text": saved["text"]}
        self.assertEqual(self.request("/api/projects/example/save", half)[0], 400)
        cleared = {"config": {**saved["config"], "host_names": None}, "config_hash": saved["config_hash"], "text": saved["text"]}
        self.assertEqual(self.request("/api/projects/example/save", cleared)[0], 200)
        final = json.loads(self.request("/api/projects/example")[1])
        self.assertIsNone(final["config"]["host_names"])
        self.assertEqual(final["config_hash"], detail["config_hash"])

    def test_an_override_equal_to_what_the_table_produces_is_not_stored(self):
        self.publish_episode()
        write_json(self.root / "studio/spoken_forms.json", {"schema_version": "1.0",
                   "entries": [{"written": "model", "spoken": "Modell"}]})
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml",
                   {"audio_approved": True, "spoken_overrides": {"seg_001": "Alt."}})
        applied = self.request("/api/projects/example/spoken_override",
                               {"episode": "ep_001", "segment_id": "seg_001", "spoken": "What does this Modell compare?"})
        self.assertEqual(json.loads(applied[1])["spoken_overrides"], {})
        real = self.request("/api/projects/example/spoken_override",
                            {"episode": "ep_001", "segment_id": "seg_001", "spoken": "What does this model compare?"})
        self.assertEqual(json.loads(real[1])["spoken_overrides"], {"seg_001": "What does this model compare?"})

    def test_a_save_without_the_table_leaves_the_table_file_alone(self):
        detail = json.loads(self.request("/api/projects/example")[1])
        data = {"config": detail["config"], "config_hash": detail["config_hash"], "text": detail["text"],
                "style_notes": "Kurz.", "style_notes_hash": detail["style_notes_hash"]}
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 200)
        self.assertFalse((self.root / "studio/spoken_forms.json").exists())
        write_json(self.root / "studio/spoken_forms.json", {"schema_version": "1.0",
                   "entries": [{"written": "H800", "spoken": "H achthundert"}]})
        before = (self.root / "studio/spoken_forms.json").read_bytes()
        detail = json.loads(self.request("/api/projects/example")[1])
        data = {"config": detail["config"], "config_hash": detail["config_hash"], "text": detail["text"]}
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 200)
        self.assertEqual((self.root / "studio/spoken_forms.json").read_bytes(), before)

    def test_the_spoken_form_table_is_saved_only_against_its_own_hash(self):
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["spoken_forms"], {"schema_version": "1.0", "entries": []})
        data = {"config": detail["config"], "config_hash": detail["config_hash"], "text": detail["text"],
                "spoken_forms": {"schema_version": "1.0", "entries": [{"written": "H800", "spoken": "H achthundert"}]},
                "spoken_forms_hash": detail["spoken_forms_hash"]}
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 200)
        saved = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(saved["spoken_forms"]["entries"], [{"written": "H800", "spoken": "H achthundert"}])
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 400)

    def test_the_listening_review_is_only_ever_set_by_a_person(self):
        self.publish_episode()
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml", {"audio_approved": True})
        self.assertEqual(self.request("/api/projects/example/listening_review",
                                      {"episode": "ep_001", "reviewed": "yes", "note": ""})[0], 400)
        status, body, _ = self.request("/api/projects/example/listening_review",
                                       {"episode": "ep_001", "reviewed": True, "note": " Kapitel 2 war dicht. "})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["human_listening_reviewed"])
        decision = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")
        self.assertTrue(decision["human_listening_reviewed"])
        self.assertEqual(decision["listening_note"], "Kapitel 2 war dicht.")

    def test_an_episode_without_a_quality_report_carries_no_notes(self):
        self.publish_episode()
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["episodes"][0]["review_notes"], {})

    def test_bad_model_does_not_leave_a_half_created_project(self):
        data = {"config":self.config.model_dump(), "text":{"provider":"openrouter", "model":None}}
        self.assertEqual(self.request("/api/projects", data)[0], 400)
        self.assertEqual(len(list((self.workspace / "projects").glob("*/project.yaml"))), 1)

    def test_audio_requires_checked_box_current_text_and_current_voices(self):
        folder = self.root / "episodes/ep_001"
        write_yaml(folder / "script.yaml", example_script().model_dump())
        (folder / "script.md").write_text("Text to read", encoding="utf-8")
        data = {"action":"audio", "episode":"ep_001", "script_hash":file_hash(folder / "script.yaml"),
                "readable_hash":file_hash(folder / "script.md"), "config_hash":project_hash(self.config)}
        with patch("podcast_automate.studio.subprocess.Popen") as process:
            self.assertEqual(self.request("/api/projects/example/start", data)[0], 400)
            data["approve_audio"] = True
            data["config_hash"] = "old voices"
            self.assertEqual(self.request("/api/projects/example/start", data)[0], 400)
            data["config_hash"] = project_hash(self.config)
            data["script_hash"] = "old text"
            self.assertEqual(self.request("/api/projects/example/start", data)[0], 400)
            process.assert_not_called()

    def test_gemini_audio_can_be_selected_without_changing_script_inputs(self):
        detail = json.loads(self.request("/api/projects/example")[1])
        before = (self.root / "project.yaml").read_bytes()
        audio = AudioChoice(provider="openrouter_gemini_tts",
                            voices={"host_a":"Sadaltager","host_b":"Aoede"}).model_dump()
        data = {"config":detail["config"], "config_hash":detail["config_hash"], "text":detail["text"],
                "audio_settings":audio, "audio_hash":detail["audio_hash"]}
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 200)
        self.assertEqual((self.root / "project.yaml").read_bytes(), before)
        saved = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(saved["audio_settings"], audio)
        self.assertEqual(saved["text"]["provider"], "codex_cli")
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 400)
        catalog = json.loads(self.request("/api/bootstrap")[1])["audio_catalog"]
        self.assertEqual(len(catalog["openrouter_gemini_tts"]["voices"]), 30)

    def test_stale_audio_provider_approval_blocks_before_worker(self):
        folder = self.root / "episodes/ep_001"
        write_yaml(folder / "script.yaml", example_script().model_dump())
        (folder / "script.md").write_text("Read this", encoding="utf-8")
        data = {"action":"audio", "episode":"ep_001", "approve_audio":True,
                "script_hash":file_hash(folder / "script.yaml"), "readable_hash":file_hash(folder / "script.md"),
                "config_hash":project_hash(self.config), "audio_hash":"old provider"}
        with patch("podcast_automate.studio.subprocess.Popen") as process:
            self.assertEqual(self.request("/api/projects/example/start", data)[0], 400)
            process.assert_not_called()

    def test_gemini_sample_requires_a_deliberate_api_action(self):
        with patch("podcast_automate.studio.subprocess.Popen") as process:
            self.assertEqual(self.request("/api/projects/example/start",
                {"action":"audio_sample", "voice":"Aoede", "language":"de-DE"})[0], 400)
            self.assertEqual(self.request("/api/projects/example/start",
                {"action":"audio_sample", "voice":"Aiden", "language":"de-DE", "approve_sample":True})[0], 400)
            process.assert_not_called()

    def test_gemini_library_requires_batch_approval_and_passes_only_selected_language(self):
        with patch("podcast_automate.studio.subprocess.Popen") as process:
            for data in ({"action":"audio_samples", "language":"de-DE"},
                         {"action":"audio_samples", "language":"fr-FR", "approve_samples":True}):
                self.assertEqual(self.request("/api/projects/example/start", data)[0], 400)
            process.assert_not_called()
            process.return_value.stdin = io.StringIO()
            process.return_value.stdin.close = Mock()
            status = self.request("/api/projects/example/start",
                {"action":"audio_samples", "language":"de-DE", "approve_samples":True})[0]
            self.assertEqual(status, 200)
            payload = json.loads(process.return_value.stdin.getvalue())
            self.assertEqual(payload["language"], "de-DE")
            self.assertEqual(payload["action"], "audio_samples")

    def test_shared_sample_inventory_and_playback_are_read_only_without_key(self):
        from podcast_automate.voice_samples import sample_path, sample_settings
        audio = sample_path(self.app.projects, "Aoede", "de-DE")
        audio.parent.mkdir(parents=True)
        audio.write_bytes(b"0123456789")
        write_json(audio.with_suffix(".json"), {"settings":sample_settings("Aoede", "de-DE"), "sha256":file_hash(audio)})
        with patch("podcast_automate.speech.build_opener") as build:
            first = json.loads(self.request("/api/bootstrap")[1])
            self.assertEqual(first["voice_samples"]["de-DE"]["Aoede"]["url"], "/samples/gemini/de-DE/Aoede")
            detail = json.loads(self.request("/api/projects/example")[1])
            self.assertEqual(first["voice_samples"], detail["voice_samples"])
            status, body, headers = self.request("/samples/gemini/de-DE/Aoede", headers={"Range":"bytes=2-5"})
            self.assertEqual((status, body), (206, b"2345"))
            self.assertEqual(headers["Content-Type"], "audio/mpeg")
            self.assertEqual(self.request("/samples/gemini/en-US/Aoede")[0], 404)
            self.assertEqual(self.request("/samples/gemini/de-DE/Aiden")[0], 400)
            build.assert_not_called()
        self.assertEqual(Studio(self.workspace).bootstrap()["voice_samples"], first["voice_samples"])

    def test_finished_audio_comes_from_exports_and_supports_seeking(self):
        folder = self.root / "episodes/ep_001"
        write_yaml(folder / "script.yaml", example_script().model_dump())
        (folder / "script.md").write_text("Script", encoding="utf-8")
        path = self.root / "exports/ep_001/run_test/audio.mp3"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"0123456789")
        write_json(folder / "audio_latest.json", {"script_sha256":file_hash(folder / "script.yaml"),
            "voices":self.config.voice_profile, "parts":[{"audio":"exports/ep_001/run_test/audio.mp3"}]})
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertTrue(detail["episodes"][0]["audio_current"])
        self.assertEqual(detail["episodes"][0]["audio"], ["exports/ep_001/run_test/audio.mp3"])
        status, body, headers = self.request("/media/example/exports/ep_001/run_test/audio.mp3", headers={"Range":"bytes=2-5"})
        self.assertEqual((status, body), (206, b"2345"))
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")

    def test_restart_marks_unfinished_job_as_interrupted_and_keeps_run(self):
        write_json(self.root / "studio/job.json", {"id":"test", "status":"running", "action":"plan",
                                                  "run":{"run_id":"run_saved"}})
        detail = self.app.job(self.root)
        self.assertEqual(detail["status"], "interrupted")
        self.assertEqual(detail["run"]["run_id"], "run_saved")

    def test_real_worker_persists_failure_without_running_a_model(self):
        # A missing saved run fails before any provider or GPU access, exercising the real IPC path.
        status, _, _ = self.request("/api/projects/example/start", {"action":"resume", "run_id":"run_missing"})
        self.assertEqual(status, 200)
        self.app.worker(self.root).wait(timeout=10)
        result = self.app.job(self.root)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("message", result)

    def test_second_launch_reopens_the_existing_workspace(self):
        from podcast_automate.studio import serve
        with patch("podcast_automate.studio.webbrowser.open") as opened, patch("builtins.print"):
            serve(self.workspace, self.server.server_port)
        opened.assert_called_once_with(f"http://127.0.0.1:{self.server.server_port}")

    def test_quit_is_a_protected_explicit_action(self):
        self.assertEqual(self.request("/api/quit", {}, {"X-Studio-Token":"wrong"})[0], 403)
        self.assertEqual(self.request("/api/quit", {})[0], 200)
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def test_stop_terminates_only_the_owned_studio_process(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.app.workers[self.root] = (process, False)
        write_json(self.root / "studio/job.json", {"id":"owned", "status":"running", "action":"assistant"})
        init_project(self.workspace / "projects/other", self.config)
        self.assertEqual(self.request("/api/projects/other/stop", {})[0], 400)
        self.assertIsNone(process.poll())
        self.assertEqual(self.request("/api/projects/example/stop", {})[0], 200)
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.app.job(self.root)["status"], "interrupted")

    def test_stopped_run_preserves_checkpoints_budget_and_readable_drafts(self):
        run = RunManifest(run_id="run_stopped", kind="script", status="running", project_hash="config",
            input_hash="approved-inputs", stages={"writing": StageRecord(status="completed", attempts=1,
                outputs={"saved-file": "saved-hash"}), "polishing": StageRecord(status="running", attempts=2),
                "review": StageRecord()})
        work = manifest_path(self.root, run.run_id).parent
        write_yaml(work / "run_manifest.yaml", run.model_dump(mode="json"))
        write_json(work / "series_plan.json", example_plan().model_dump())
        write_json(work / "script_request.json", {"episode": None})
        draft = work / "drafts/ep_001.json"
        write_json(draft, example_script().model_dump())
        write_json(draft.with_suffix(".checkpoint.json"), {"sha256": file_hash(draft)})
        write_json(work / "budget.json", {"model_calls": 74, "search_rounds": 2})
        write_json(work / "plan_approval.json", {"plan_hash": "approved-outline"})
        write_json(work / "polishing/ep_001/checkpoint.json", {"candidate": {}, "repairs": 1})
        preserved = {p: p.read_bytes() for p in work.rglob("*.json")}
        write_json(self.root / "studio/job.json", {"id": "owned", "status": "running", "run": run.model_dump(mode="json")})
        job = record_interruption(self.root, expected_job_id="owned")
        saved = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(saved.status, "pending")
        self.assertEqual(saved.stages["writing"], run.stages["writing"])
        self.assertEqual(saved.stages["polishing"].status, "pending")
        self.assertEqual(saved.stages["polishing"].attempts, 2)
        self.assertEqual(saved.stages["polishing"].error.code, "interrupted")
        self.assertEqual(saved.input_hash, run.input_hash)
        self.assertEqual(job["run"], saved.model_dump(mode="json"))
        self.assertIsNone(job["progress"]["model_call_started_at"])
        self.assertEqual(job["progress"]["script_previews"][0]["script"], example_script().model_dump())
        self.assertEqual({p: p.read_bytes() for p in preserved}, preserved)
        with self.assertRaises(AppError):
            record_interruption(self.root, expected_job_id="another-job")

    def test_stop_record_does_not_overwrite_a_just_completed_job(self):
        completed = {"id": "finished", "status": "completed", "message": "Ready"}
        path = self.root / "studio/job.json"
        write_json(path, completed)
        before = path.read_bytes()
        self.assertEqual(record_interruption(self.root), completed)
        self.assertEqual(path.read_bytes(), before)



class StudioStopTests(unittest.TestCase):
    """Stops as the Studio reports them: a readable message, an honest automatic resume, a run that stays visible."""
    setUp = StudioHttpTests.setUp
    request = StudioHttpTests.request

    def research_run(self, run_id="run_r", status="blocked", error=None, stage_status=None):
        run = RunManifest(run_id=run_id, kind="research", status=status, project_hash="p", input_hash="i",
                          stages={"dossier": StageRecord(status=stage_status or status, attempts=1, error=error)})
        write_yaml(manifest_path(self.root, run_id).parent / "run_manifest.yaml", run.model_dump(mode="json"))
        return run.model_dump(mode="json")

    def started(self):
        process = Mock(stdin=io.StringIO())
        process.poll.return_value = None
        return patch("podcast_automate.studio.subprocess.Popen", return_value=process)

    def test_stop_messages_are_german_without_cli_advice_local_paths_or_internal_ids(self):
        from podcast_automate.studio_messages import user_text
        english = user_text("Unanchored review disagreement; no automatic new research. Der Aufruf wurde 2 Mal wiederholt.")
        self.assertTrue(english["message"].startswith("Die Gesamtprüfung erhebt einen Einwand"))
        self.assertIn("Der Aufruf wurde 2 Mal wiederholt.", english["message"])
        self.assertIn("Unanchored review disagreement", english["detail"])
        rejected = user_text("Codex answered, but the answer violates its output contract: findings.0.support: Input should be x. "
                             "Return the complete answer again with exactly these defects corrected.")
        self.assertEqual(rejected["message"], "Die Antwort von Codex passte nicht zum erwarteten Format (findings.0.support).")
        self.assertEqual(user_text("Abo-Kontingent erreicht. Später mit 'pla resume' fortsetzen.")["message"],
                         "Abo-Kontingent erreicht. Später mit „Fortsetzen“ weitermachen.")
        needed = self.root / "runs" / "run_x" / "research_needed.md"
        self.assertEqual(user_text(f"Erforderliche Erklärgrundlagen fehlen: {needed}", self.root)["message"],
                         "Erforderliche Erklärgrundlagen fehlen: runs/run_x/research_needed.md")
        receipt = user_text("Lokale Verarbeitung fehlgeschlagen. Technische Details: runs/run_x/failures/dossier_1.txt", self.root)
        self.assertEqual((receipt["message"], receipt["file"]),
                         ("Lokale Verarbeitung fehlgeschlagen.", "runs/run_x/failures/dossier_1.txt"))
        self.assertEqual(user_text("Siehe src_0cca2395cb73c72c#sec_6891d807643ea0ef.")["message"], "Siehe Quellenstelle.")
        self.assertEqual(user_text("Folge ist zu lang. Die automatische Aufteilung folgt im Serien-Meilenstein.")["message"],
                         "Folge ist zu lang.")
        self.assertIsNone(user_text("Gespeicherte Rechercheänderung passt nicht zu ihren Eingaben.")["detail"])

    def test_a_stopped_job_names_its_newest_code_and_a_cleaned_message(self):
        run = self.research_run(error=Failure(code="interrupted", message="Angehalten."))
        write_json(self.root / "studio/job.json", {"id": "j", "action": "resume", "status": "blocked", "run": run,
                   "error_code": "inputs_changed", "message": "Rechercheeingaben geändert. Später mit pla resume fortsetzen."})
        job = self.app.job(self.root)
        # The worker's own refusal is newer than the interruption the stage still records.
        self.assertEqual((job["stop"]["code"], job["stop"]["run_kind"]), ("inputs_changed", "research"))
        self.assertNotIn("pla resume", job["message"])
        self.assertIn("pla resume", json.loads((self.root / "studio/job.json").read_text(encoding="utf-8"))["message"])

    def test_only_a_resume_the_scheduler_will_start_is_announced(self):
        past = "2026-01-01T00:00:00+00:00"
        run = self.research_run(status="waiting_for_quota", error=Failure(code="subscriptions_exhausted", message="leer"))
        job = {"id": "j", "action": "resume", "status": "waiting_for_quota", "retry_at": past, "run": run}
        write_json(self.root / "studio/job.json", job)
        self.assertEqual(self.app.job(self.root)["auto_resume_at"], past)
        # Empty OpenRouter credit does not come back by waiting: neither announced nor scheduled.
        credits = self.research_run(status="waiting_for_quota", error=Failure(code="openrouter_credits", message="leer"))
        write_json(self.root / "studio/job.json", {**job, "run": credits})
        self.assertNotIn("auto_resume_at", self.app.job(self.root))
        self.assertEqual(self.app.due_resumes(), [])
        # A chat has no run to resume, so no time is promised.
        write_json(self.root / "studio/job.json", {"id": "c", "action": "assistant", "status": "waiting_for_quota",
                                                   "retry_at": past, "run": None})
        self.assertNotIn("auto_resume_at", self.app.job(self.root))
        write_json(self.root / "studio/job.json", {**job, "auto_resume_count": 3})
        self.assertTrue(self.app.job(self.root)["auto_resume_exhausted"])

    def test_a_paused_run_stays_the_project_job_behind_a_chat_and_a_later_audio_job(self):
        run = self.research_run(error=Failure(code="research_questions_blocked", message="offen"))
        write_json(self.root / "studio/job.json", {"id": "r", "action": "research", "status": "blocked",
                                                   "started_at": "2026-09-01T10:00:00+00:00", "run": run})
        with self.started():
            self.app.start("example", {"action": "assistant", "message": "Kürzer bitte"})
        self.assertEqual(json.loads((self.root / "studio/paused_job.json").read_text(encoding="utf-8"))["id"], "r")
        chat = json.loads((self.root / "studio/job.json").read_text(encoding="utf-8"))
        write_json(self.root / "studio/job.json", {**chat, "status": "completed"})
        self.app.workers.clear()
        detail = self.app.detail("example")
        self.assertEqual((detail["job"]["id"], detail["main_job"]["action"]), ("r", "assistant"))
        self.assertEqual(detail["job"]["stop"]["code"], "research_questions_blocked")
        # A newer finished audio job does not hide the paused run either.
        audio_id = "a" * 32
        write_json(self.root / "studio/audio_jobs" / (audio_id + ".json"), {"id": audio_id, "episode": "ep_001",
                   "status": "completed", "started_at": "2026-09-02T10:00:00+00:00", "run": None})
        self.assertEqual(self.app.detail("example")["job"]["id"], "r")
        # Resuming the parked run takes it back into the job record, which keeps the run from the start.
        with self.started():
            resumed = self.app.start("example", {"action": "resume", "run_id": "run_r"})
        self.assertFalse((self.root / "studio/paused_job.json").exists())
        self.assertEqual(resumed["run"]["run_id"], "run_r")
        self.assertTrue((self.root / "studio/stderr" / (resumed["id"] + ".log")).is_file())

    def test_a_new_run_of_the_same_lane_replaces_what_was_parked(self):
        run = self.research_run(error=Failure(code="review_disagreement", message="Einwand"))
        write_json(self.root / "studio/paused_job.json", {"id": "r", "action": "research", "status": "blocked", "run": run})
        with self.started():
            self.app.start("example", {"action": "research"})
        self.assertFalse((self.root / "studio/paused_job.json").exists())

    def test_a_vanished_worker_resets_its_run_and_reports_its_last_error_output(self):
        run = RunManifest(run_id="run_v", kind="research", status="running", project_hash="p", input_hash="i",
                          stages={"dossier": StageRecord(status="running", attempts=1)})
        write_yaml(manifest_path(self.root, "run_v").parent / "run_manifest.yaml", run.model_dump(mode="json"))
        job_id = "b" * 32
        write_json(self.root / "studio/job.json", {"id": job_id, "action": "resume", "status": "running",
                   "started_at": "2026-09-01T10:00:00+00:00", "run": run.model_dump(mode="json")})
        log = self.root / "studio/stderr" / (job_id + ".log")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f'Traceback (most recent call last):\n  File "{self.root / "x.py"}", line 1\nImportError: kaputt\n',
                       encoding="utf-8")
        job = self.app.job(self.root)
        self.assertEqual(job["status"], "interrupted")
        self.assertIn("unerwartet beendet", job["message"])
        self.assertIn("ImportError: kaputt", job["stop"]["crash_detail"])
        self.assertNotIn(str(self.root), job["stop"]["crash_detail"])
        saved = RunManifest.model_validate(read_yaml(manifest_path(self.root, "run_v").parent / "run_manifest.yaml"))
        self.assertEqual((saved.status, saved.stages["dossier"].status), ("pending", "pending"))

    def test_the_heartbeat_counts_the_chapter_file_the_audio_worker_writes_per_segment(self):
        run = RunManifest(run_id="run_a", kind="episode_audio", status="running", project_hash="p", input_hash="i",
                          stages={"synthesis": StageRecord(status="running")})
        work = manifest_path(self.root, "run_a").parent
        write_yaml(work / "run_manifest.yaml", run.model_dump(mode="json"))
        nested = work / "synthesis/ch_001/tts_progress.json"
        write_json(work / "progress.json", {"status": "synthesis", "chapter": 1, "chapters": 2, "completed_segments": 0,
                   "total_segments": 10, "chapter_progress": nested.relative_to(self.root).as_posix()})
        write_json(nested, {"status": "rendering", "completed": 4, "total": 6})
        old = time.time() - 900
        os.utime(work / "progress.json", (old, old))
        write_json(self.root / "studio/job.json", {"id": "q", "action": "audio", "status": "running",
                   "started_at": "2026-01-01T00:00:00+00:00", "run": run.model_dump(mode="json")})
        worker = Mock()
        worker.poll.return_value = None
        self.app.workers[self.root] = (worker, False)
        job = self.app.job(self.root)
        self.assertLess(job["heartbeat_age_seconds"], 60)
        self.assertEqual((job["progress"]["completed_segments"], job["progress"]["tts_status"]), (4, "rendering"))

    def test_the_conversation_limit_is_raised_by_an_explicit_approval(self):
        from podcast_automate.studio import chat_limits
        # A raise must lie above the current limit, which starts at the project's research allowance.
        raised = self.config.research_limits.model_calls + 50
        self.assertEqual(self.request("/api/projects/example/approve", {"kind": "chat_calls", "model_calls": raised})[0], 200)
        self.assertEqual(chat_limits(self.root, self.config.research_limits).model_calls, raised)
        self.assertEqual(self.request("/api/projects/example/approve", {"kind": "chat_calls", "model_calls": 100})[0], 400)
        self.assertEqual(json.loads(self.request("/api/projects/example")[1])["chat_budget"]["limit"], raised)

    def test_named_diagnostics_open_as_text_and_nothing_else_does(self):
        receipt = self.root / "runs/run_x/failures/dossier_1.txt"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("Traceback: Fehler", encoding="utf-8")
        status, body, headers = self.request("/api/projects/example/file?path=runs/run_x/failures/dossier_1.txt")
        self.assertEqual((status, body.decode("utf-8")), (200, "Traceback: Fehler"))
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        for path in ("project.yaml", "runs/../project.yaml", "runs/../studio/job.json", "studio/job.json"):
            self.assertEqual(self.request("/api/projects/example/file?path=" + path)[0], 404, path)

    def test_downloads_read_past_the_lock_only_while_this_studio_runs_a_text_job(self):
        write_json(self.root / "studio/job.json", {"id": "r", "action": "research", "status": "running", "run": None})
        worker = Mock()
        worker.poll.return_value = None
        self.app.workers[self.root] = (worker, False)
        self.assertTrue(self.app.own_text_job(self.root))
        write_json(self.root / "studio/job.json", {"id": "q", "action": "audio", "status": "running", "run": None})
        self.assertFalse(self.app.own_text_job(self.root), "a Qwen run writes the exports it would read")
        worker.poll.return_value = 0
        write_json(self.root / "studio/job.json", {"id": "r", "action": "research", "status": "running", "run": None})
        self.assertFalse(self.app.own_text_job(self.root))

    def test_sending_an_unanswered_message_again_replaces_it(self):
        write_json(self.root / "studio/chat.json", [{"role": "user", "message": "Kürzer"}])
        proposal = BriefProposal(message="Gern.", topic="Titel", central_question="Warum?", prior_knowledge="",
                                 depth_request="Tief", focus_questions=[], excluded_topics=[])
        with patch("podcast_automate.studio_worker.CodexAdapter.structured", return_value=(proposal, {})):
            perform(self.root, {"action": "assistant", "message": "Kürzer", "text": {}})
        chat = json.loads((self.root / "studio/chat.json").read_text(encoding="utf-8"))
        self.assertEqual([row["role"] for row in chat], ["user", "assistant"])


class ReportMergeTests(unittest.TestCase):
    """``retained_episode_reports`` keeps an entry only while it judges the text on disk."""

    def test_stale_and_foreign_entries_are_dropped_and_legacy_entries_inherit_the_run(self):
        from podcast_automate.script_artifacts import retained_episode_reports
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        write_yaml(root / "episodes/ep_001/script.yaml", example_script().model_dump())
        current = file_hash(root / "episodes/ep_001/script.yaml")
        previous = {"run_id": "run_legacy", "episodes": {
            "ep_001": {"script_sha256": current, "model_review": {"limitations": ["kept"]}},
            "ep_002": {"script_sha256": "stale", "run_id": "run_old"},
            "ep_003": {"script_sha256": current, "run_id": "run_old"},
            "../ep_001": {"script_sha256": current}}}
        self.assertEqual(retained_episode_reports(root, previous, {"ep_003"}), {
            "ep_001": {"script_sha256": current, "model_review": {"limitations": ["kept"]}, "run_id": "run_legacy"}})
        self.assertEqual(retained_episode_reports(root, {"episodes": "not a map"}, set()), {})
        self.assertEqual(retained_episode_reports(root, None, set()), {})


class PublishedReportTests(fixtures.ScriptProjectCase):
    """``reports/script_quality.yaml`` speaks for every published episode, not only the last run's."""

    def two_episode_model(self, prompt, output_type, directory, **kwargs):
        from podcast_automate.models import EpisodeScript
        value, metadata = self.model(prompt, output_type, directory, **kwargs)
        if output_type is SeriesPlan:
            second = value.episodes[0].model_copy(deep=True)
            second.episode_id, second.title = "ep_002", "Further consequences"
            value.episodes.append(second)
        if output_type is EpisodeScript:
            payload = json.loads(prompt.splitlines()[-1])
            value.episode_id = (payload.get("episode") or payload.get("original_script"))["episode_id"]
        return value, metadata

    def test_revising_one_episode_keeps_the_other_episodes_report_entries(self):
        from podcast_automate.studio_scripts import review_notes
        report = self.root / "reports/script_quality.yaml"
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.two_episode_model):
            first = run_script(self.root)
            self.assertEqual(first.status, "completed")
            complete = read_yaml(report)
            second = run_script(self.root, revise="ep_002", feedback="Clarify the consequence")
            self.assertEqual(second.status, "completed")
        self.assertEqual({key: value["run_id"] for key, value in complete["episodes"].items()},
                         {"ep_001": first.run_id, "ep_002": first.run_id})
        partial = read_yaml(report)
        self.assertEqual(partial["run_id"], second.run_id)
        self.assertEqual(sorted(partial["episodes"]), ["ep_001", "ep_002"])
        self.assertEqual(partial["episodes"]["ep_001"], complete["episodes"]["ep_001"])
        self.assertEqual(partial["episodes"]["ep_002"]["run_id"], second.run_id)
        self.assertEqual(partial["episodes"]["ep_001"]["model_review"]["limitations"],
                         ["A fixture is not a real editorial review."])
        self.assertIn("advisories", partial["episodes"]["ep_001"])
        # The Studio's reading panel takes its notes from exactly this entry.
        notes = review_notes(partial["episodes"]["ep_001"])
        self.assertEqual(notes, review_notes(complete["episodes"]["ep_001"]))
        self.assertEqual(notes["script_review"], ["A fixture is not a real editorial review."])
        self.assertEqual(partial["episodes"]["ep_001"]["script_sha256"], file_hash(self.root / "episodes/ep_001/script.yaml"))
        # The revise run's publish record hashes the merged report it wrote.
        self.assertEqual(second.stages["publish"].outputs["reports/script_quality.yaml"], file_hash(report))

    def test_unowned_probe_rows_are_reported_at_series_level(self):
        gap = "How is the bias term of an overloaded expert updated?"
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            planned = run_script(self.root, plan_only=True)
            work = manifest_path(self.root, planned.run_id).parent
            probes = work / "gap_probes.json"
            rows = json.loads(probes.read_text(encoding="utf-8")) if probes.exists() else []
            rows.append({"gap_id": "gap_seeded", "text": gap, "status": "hits_unowned", "owner_episodes": [],
                         "hits": [{"reference": "src_unused#sec_001", "score": 2, "preview": "An unused section."}]})
            write_json(probes, rows)
            completed = run_script(self.root, resume=True, approved_plan_hash=outline_hash(work))
        self.assertEqual(completed.status, "completed")
        quality = read_yaml(self.root / "reports/script_quality.yaml")
        self.assertEqual(quality["gap_probes_unowned"], [
            {"gap_id": "gap_seeded", "text": gap, "status": "hits_unowned", "references": ["src_unused#sec_001"]}])


if __name__ == "__main__":
    unittest.main()
