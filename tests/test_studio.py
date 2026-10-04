import contextlib
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
from podcast_automate.storage import (digest, file_hash, file_lock, init_project, project_hash, project_lock, read_yaml,
                                      write_json, write_yaml)
from podcast_automate.studio import BriefProposal, Studio, make_server, read_json, record_interruption
from podcast_automate.studio_worker import perform, probe_key
from tests import script_fixtures as fixtures
from tests.script_fixtures import example_plan, example_script

# Studio tests start real workers; a worker keeps Windows awake unless told not to (studio_worker, 2026-10-02).
_KEEP_AWAKE = patch.dict(os.environ, {"PLA_KEEP_AWAKE": "0"})


def setUpModule():
    _KEEP_AWAKE.start()


def tearDownModule():
    _KEEP_AWAKE.stop()


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
        # Resolved as Studio() resolves it: its workers and jobs are keyed by that form (an 8.3 TEMP differs).
        self.workspace = Path(self.temp.name).resolve()
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

    def test_a_work_arrives_as_raw_bytes_and_only_as_octet_stream(self):
        # 2026-10-01: a book PDF from the library is far beyond the JSON upload's 4 MB.
        from urllib.parse import quote
        book = b"%PDF-1.4 " + b"x" * (6 * 1024 * 1024)
        path = f"/api/projects/example/work?citation={quote('Morris: Why the West Rules (2010)')}&task=unknown_task"

        def post(body, content_type, token=None):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
            connection.request("POST", path, body, headers={"X-Studio-Token": token or self.app.token, "Content-Type": content_type})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        self.assertEqual(post(book, "text/plain")[0], 400, "only a type that needs the browser's preflight is taken")
        self.assertEqual(post(book, "application/octet-stream", token="wrong")[0], 403)
        status, result = post(book, "application/octet-stream")
        self.assertEqual(status, 200)
        self.assertEqual((result["work"]["citation"], result["work"]["bytes"], result["work"]["tasks"]),
                         ("Morris: Why the West Rules (2010)", len(book), []), "a question the run does not know is dropped")
        self.assertEqual((self.root / result["work"]["path"]).read_bytes(), book)
        self.assertEqual(json.loads(self.request("/api/projects/example")[1])["works"]["provided"][0]["id"], result["work"]["id"])

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

    def test_a_stopped_teaching_design_is_named_on_the_job_and_takes_a_redesign_request(self):
        work = self.root / "runs/run_teach"
        stopped = StageRecord(status="blocked", error=Failure(code="teaching_design_failed", message="Scene 4 reads SPARQL aloud."))
        write_yaml(work / "run_manifest.yaml", RunManifest(run_id="run_teach", kind="script", project_hash="0" * 64,
                                                           input_hash="c" * 64, status="blocked",
                                                           stages={"teaching": stopped}).model_dump(mode="json"))
        write_json(work / "series_plan.json", example_plan().model_dump())
        write_json(work / "teaching/ep_001/checkpoint.json", {"focused_repair": True, "repairs": 2})
        job = Studio.readable(self.root, {"status": "blocked", "message": "Scene 4 reads SPARQL aloud.",
                                          "run": {"run_id": "run_teach", "kind": "script",
                                                  "stages": {"teaching": stopped.model_dump(mode="json")}}})
        self.assertEqual(job["teaching_failure"], {"episode_id": "ep_001", "title": example_plan().episodes[0].title})
        for payload in ({"episode_id": "ep_001"}, {"episode_id": "ep_404", "note": "Plain words."}):
            self.assertEqual(self.request("/api/projects/example/approve",
                                          {"kind": "teaching_redesign", "run_id": "run_teach", **payload})[0], 400)
        status, body, _ = self.request("/api/projects/example/approve", {"kind": "teaching_redesign", "run_id": "run_teach",
                                                                         "episode_id": "ep_001", "note": "Plain words."})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["teaching_redesign"]["note"], "Plain words.")
        saved = json.loads((work / "teaching_redesigns.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["run_id"], saved["input_hash"], [r["episode_id"] for r in saved["requests"]]),
                         ("run_teach", "c" * 64, ["ep_001"]))
        self.assertEqual(self.request("/api/projects/example/approve", {"kind": "teaching_redesign", "run_id": "run_teach",
                                      "episode_id": "ep_001", "note": "x"}, {"X-Studio-Token": "wrong"})[0], 403)

    def test_the_home_network_is_served_like_this_computer_only_when_started_for_it(self):
        """2026-09-28: the user steers the Studio from the phone in the home WLAN, with protection only against
        the outside. This computer is always served; the home network only after ``pla studio --lan``; an
        address from the internet never. A device names this computer by the address it connected to, so a
        rebound domain and a foreign page stay refused, and every mutation still needs the session token."""
        from podcast_automate.studio import client_scope
        self.assertEqual([client_scope(a, False) for a in ("127.0.0.1", "::1", "::ffff:127.0.0.1")], ["local"] * 3)
        self.assertIsNone(client_scope("192.168.178.40", False), "without --lan the home network is refused")
        for address in ("192.168.178.40", "10.0.0.5", "172.16.1.1", "169.254.3.3", "fe80::1"):
            self.assertEqual(client_scope(address, True), "lan", address)
        for address in ("8.8.8.8", "100.64.1.1", "2001:4860:4860::8888", "no address"):
            self.assertIsNone(client_scope(address, True), address)
        port = self.server.server_port
        bootstrap = json.loads(self.request("/api/bootstrap")[1])
        self.assertEqual((bootstrap["lan"], bootstrap["client"]), ({"enabled": False, "urls": []}, "local"))
        # The test client always connects from 127.0.0.1, so the phone's scope is patched; the address it
        # connected to is then 127.0.0.1 as well.
        with patch("podcast_automate.studio.client_scope", return_value=None):
            self.assertEqual(self.request("/api/bootstrap")[0], 403)
        here = f"127.0.0.1:{port}"
        with patch("podcast_automate.studio.client_scope", return_value="lan"):
            status, body, _ = self.request("/api/bootstrap", headers={"Host": here})
            self.assertEqual((status, json.loads(body)["client"]), (200, "lan"))
            self.assertEqual(self.request("/api/projects/example", headers={"Host": here, "Origin": f"http://{here}"})[0], 200)
            self.assertEqual(self.request("/api/bootstrap", headers={"Host": f"localhost:{port}"})[0], 403)
            self.assertEqual(self.request("/api/bootstrap", headers={"Host": f"evil.example:{port}"})[0], 403)
            self.assertEqual(self.request("/api/bootstrap", headers={"Host": here, "Origin": "http://evil.example"})[0], 403)
            self.assertEqual(self.request("/api/key", {"key": "secret"}, {"Host": here, "X-Studio-Token": "wrong"})[0], 403)
            self.assertEqual(self.app.key, "")
        self.app.lan = True
        with patch("podcast_automate.studio.lan_addresses", return_value=["192.168.178.75"]):
            self.assertEqual(json.loads(self.request("/api/bootstrap")[1])["lan"],
                             {"enabled": True, "urls": [f"http://192.168.178.75:{port}"]})

    def test_only_the_wlan_start_listens_beyond_this_computer(self):
        from podcast_automate.studio import serve
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertFalse(self.app.lan)
        with patch("podcast_automate.studio.ThreadingHTTPServer") as server:
            server.return_value.server_port = 8765
            made = make_server(self.workspace, 8765, lan=True)
        self.assertEqual(server.call_args[0][0], ("0.0.0.0", 8765))
        self.assertEqual((made.studio.lan, made.studio.port), (True, 8765))
        # A Studio already running only here is not silently reused by the WLAN start: it says to quit it first.
        running = json.dumps({"app": "podcast-studio", "workspace": str(self.workspace.resolve()),
                              "lan": {"enabled": False, "urls": []}}).encode("utf-8")
        output = io.StringIO()
        with patch("podcast_automate.studio.urlopen", return_value=io.BytesIO(running)), \
             patch("podcast_automate.studio.make_server") as started, \
             patch("podcast_automate.studio.webbrowser.open") as opened, \
             contextlib.redirect_stdout(output):
            serve(self.workspace, 8765, lan=True)
        started.assert_not_called()
        opened.assert_not_called()
        self.assertIn("nur auf diesem Computer", output.getvalue())

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

    def test_tags_read_with_the_script_are_shown_and_bind_the_gemini_approval(self):
        """The expression belongs in the script check (2026-09-29): the page shows the placed tags per episode, and
        an approval carries the hash of the tags read; tags placed anew afterwards are refused until read."""
        from podcast_automate.expression import EXPRESSION_VERSION
        self.publish_episode()
        folder = self.root / "episodes/ep_001"
        audio = AudioChoice(provider="openrouter_gemini_tts", voices={"host_a": "Sadaltager", "host_b": "Aoede"})
        write_json(self.root / "studio/audio.json", audio.model_dump())
        write_json(folder / "expression.json", {"version": EXPRESSION_VERSION, "script_sha256": file_hash(folder / "script.yaml"),
                                                "segments": {"seg_001": "<breath> What does this model compare?"}})
        episode = json.loads(self.request("/api/projects/example")[1])["episodes"][0]
        self.assertEqual((episode["expression"]["tags"], episode["expression"]["hash"]), (1, file_hash(folder / "expression.json")))
        expressive = AudioChoice(**{**audio.model_dump(), "expression": True})
        data = {"action": "audio", "episode": "ep_001", "approve_audio": True,
                "script_hash": file_hash(folder / "script.yaml"), "readable_hash": file_hash(folder / "script.md"),
                "config_hash": project_hash(self.config), "audio_hash": digest(expressive.model_dump()), "expression_hash": ""}
        self.app.key = "test-key"
        with patch("podcast_automate.studio.subprocess.Popen") as process:
            status, body, _ = self.request("/api/projects/example/start", data)
            self.assertEqual((status, json.loads(body)["code"]), (400, "script_edited"))
            process.return_value.stdin = io.StringIO()
            process.return_value.stdin.close = Mock()
            process.return_value.poll.return_value = None
            status, body, _ = self.request("/api/projects/example/start", {**data, "expression_hash": episode["expression"]["hash"]})
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(process.return_value.stdin.getvalue())["expression_hash"], episode["expression"]["hash"])
        # Placing tags is its own job, for named episodes with a published script or for all of them.
        self.assertEqual(self.request("/api/projects/example/start", {"action": "expression", "episodes": ["ep_404"]})[0], 400)

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

    def test_decisions_are_written_while_another_episode_is_recorded_but_not_while_their_own_is(self):
        """2026-10-02: the exclusive project lock refused every listening review and spoken form while a Gemini
        recording held the project's shared lock."""
        self.publish_episode()
        self.publish_episode("ep_002")
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml", {"audio_approved": True})
        with project_lock(self.root, shared=True), file_lock(self.root / "episodes/ep_002/.audio.lock"):
            self.assertEqual(self.request("/api/projects/example/listening_review",
                                          {"episode": "ep_001", "reviewed": True, "note": "Gut."})[0], 200)
            self.assertEqual(self.request("/api/projects/example/spoken_override",
                                          {"episode": "ep_001", "segment_id": "seg_002", "spoken": "Anders."})[0], 200)
        decision = read_yaml(self.root / "episodes/ep_001/audio_review.yaml")
        self.assertEqual((decision["listening_note"], decision["spoken_overrides"]), ("Gut.", {"seg_002": "Anders."}))
        with project_lock(self.root, shared=True), file_lock(self.root / "episodes/ep_001/.audio.lock"):
            status, body, _ = self.request("/api/projects/example/listening_review",
                                           {"episode": "ep_001", "reviewed": False, "note": ""})
        self.assertEqual((status, json.loads(body)["code"]), (400, "episode_busy"))
        self.assertTrue(read_yaml(self.root / "episodes/ep_001/audio_review.yaml")["human_listening_reviewed"])

    def test_local_sources_outside_the_project_come_only_from_disk(self):
        """2026-10-02: in LAN mode any device in the home network could point a research run at any file on this
        computer through the brief's local sources."""
        from podcast_automate.storage import load_project
        saved_elsewhere = str(self.workspace / "notes" / "eigene.txt")
        config = load_project(self.root)
        config.local_sources = [saved_elsewhere]
        write_yaml(self.root / "project.yaml", config.model_dump(mode="json"))
        detail = json.loads(self.request("/api/projects/example")[1])
        upload = "inputs/uploads/" + "a" * 64 + ".txt"
        foreign = [str(self.workspace / "secret.txt"), "../other/project.yaml", "inputs/../../escape.txt"]
        data = {"config": {**detail["config"], "local_sources": [saved_elsewhere, *foreign, upload]},
                "config_hash": detail["config_hash"], "text": detail["text"]}
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 200)
        self.assertEqual(read_yaml(self.root / "project.yaml")["local_sources"], [saved_elsewhere, upload])
        # A save of the unchanged list keeps it, and the brief's hash with it.
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(self.request("/api/projects/example/save", {"config": detail["config"],
                         "config_hash": detail["config_hash"], "text": detail["text"]})[0], 200)
        self.assertEqual(json.loads(self.request("/api/projects/example")[1])["config_hash"], detail["config_hash"])
        # A new project takes no path outside itself from the browser.
        brief = {**self.config.model_dump(mode="json"), "topic": "Neu", "local_sources": [*foreign, upload]}
        status, body, _ = self.request("/api/projects", {"config": brief, "text": {}})
        self.assertEqual(status, 200, body)
        created = self.workspace / "projects" / json.loads(body)["id"]
        self.assertEqual(read_yaml(created / "project.yaml")["local_sources"], [upload])

    def test_a_gemini_re_render_is_bound_to_the_tags_on_the_readers_page(self):
        """2026-10-02: after "Ausdruck neu setzen" a re-render spoke tags nobody had read."""
        from podcast_automate.expression import EXPRESSION_VERSION
        self.publish_episode()
        folder = self.root / "episodes/ep_001"
        audio = AudioChoice(provider="openrouter_gemini_tts", voices={"host_a": "Sadaltager", "host_b": "Aoede"},
                            expression=True)
        write_json(self.root / "studio/audio.json", audio.model_dump())
        script_hash = file_hash(folder / "script.yaml")
        write_json(folder / "expression.json", {"version": EXPRESSION_VERSION, "script_sha256": script_hash,
                                                "segments": {"seg_001": "<breath> What does this model compare?"}})
        write_yaml(folder / "audio_review.yaml", {"audio_approved": True, "scripts": {"ep_001": script_hash},
                                                  "audio_generation": audio_generation_record(audio)})
        self.app.key = "test-key"
        data = {"action": "audio", "episode": "ep_001", "rerender": True, "script_hash": script_hash,
                "readable_hash": file_hash(folder / "script.md"), "config_hash": project_hash(self.config),
                "audio_hash": digest(audio.model_dump())}
        with patch("podcast_automate.studio.subprocess.Popen") as process:
            process.return_value.stdin = io.StringIO()
            process.return_value.stdin.close = Mock()
            process.return_value.poll.return_value = None
            status, body, _ = self.request("/api/projects/example/start", data)
            self.assertEqual((status, json.loads(body)["code"]), (400, "script_edited"))
            process.assert_not_called()
            status, body, _ = self.request("/api/projects/example/start",
                                           {**data, "expression_hash": file_hash(folder / "expression.json")})
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(process.return_value.stdin.getvalue())["expression_hash"],
                             file_hash(folder / "expression.json"))

    def test_the_queue_says_when_a_recording_waits_for_the_key_and_each_episode_names_its_speech_size(self):
        self.publish_episode()
        write_json(self.root / "studio/audio_queue.json", [{"episode": "ep_001", "queued_at": "2026-10-01T10:00:00+00:00",
                                                           "data": {}}])
        write_yaml(self.root / "episodes/ep_001/audio_review.yaml", {"spoken_overrides": {"seg_001": "Kurz."}})
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            # After a restart the key is gone; the queue says so instead of waiting for a place (2026-10-02).
            self.assertEqual(json.loads(self.request("/api/projects/example")[1])["audio_queue"][0]["waiting"], "key")
            self.app.key = "test-key"
            detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["audio_queue"][0]["waiting"], "place")
        script = example_script()
        expected = len("Kurz.") + sum(len(segment.text) for segment in script.segments[1:])
        speech = detail["episodes"][0]["speech"]
        self.assertEqual(speech, {"characters": expected, "minutes": detail["episodes"][0]["metrics"]["estimated_minutes"]})

    def test_the_overview_reads_cards_and_the_page_keeps_derived_values_while_their_files_are_unchanged(self):
        """2026-10-02: each overview poll built every project's page: the 16 MB question state, the 12-16 MB
        inputs.json hashed for the outline, every script; about 65 MB and 0.6 s with the mutex held."""
        self.publish_episode()
        work = self.root / "runs/run_o"
        for name in ("series_plan.json", "knowledge_model.json", "inputs.json", "script_request.json"):
            write_json(work / name, {"episodes": [{"episode_id": "ep_001"}, {"episode_id": "ep_002"}]} if name == "series_plan.json" else {"name": name})
        write_yaml(work / "run_manifest.yaml", RunManifest(run_id="run_o", kind="script", project_hash="0" * 64, input_hash="c" * 64,
                                                           stages={"planning": StageRecord(status="completed")}).model_dump(mode="json"))
        write_json(self.root / "studio/outline.json", {"run_id": "run_o"})
        with patch.object(self.app, "detail", side_effect=AssertionError("the overview builds no project page")):
            status, body, _ = self.request("/api/projects")
        self.assertEqual(status, 200, body)
        card = json.loads(body)["projects"][0]
        self.assertEqual((card["id"], card["has_outline"], card["episode_count"], card["script_count"]), ("example", True, 2, 1))
        self.assertEqual(card["episodes"][0]["episode_id"], "ep_001")
        with patch("podcast_automate.studio.outline_hash", wraps=outline_hash) as hashed, \
                patch("podcast_automate.studio.sample_inventory", return_value={}) as samples:
            first = json.loads(self.request("/api/projects/example")[1])
            second = json.loads(self.request("/api/projects/example")[1])
            self.assertEqual(hashed.call_count, 1)
            self.assertEqual(first["outline"]["hash"], second["outline"]["hash"])
            write_json(work / "inputs.json", {"name": "changed"})
            third = json.loads(self.request("/api/projects/example")[1])
            self.assertEqual(hashed.call_count, 2)
            self.assertNotEqual(third["outline"]["hash"], first["outline"]["hash"])
            self.assertEqual(third["outline"]["hash"], outline_hash(work))
            self.assertLessEqual(samples.call_count, 1)

    def test_a_draft_being_redone_or_stopped_is_no_outline_to_show_revise_or_approve(self):
        """2026-10-02: a revision stopped at the output limit kept the plan it replaced on the page as the outline;
        its revise button then stopped at once (invalid_plan) and offered the same button again."""
        work = self.root / "runs/run_o"
        write_json(work / "series_plan.json", example_plan().model_dump())
        for name in ("knowledge_model.json", "inputs.json", "script_request.json"):
            write_json(work / name, {"name": name})
        write_json(self.root / "studio/outline.json", {"run_id": "run_o"})

        def planning(record):
            write_yaml(work / "run_manifest.yaml", RunManifest(run_id="run_o", kind="script", project_hash="0" * 64,
                       input_hash="c" * 64, stages={"planning": record}).model_dump(mode="json"))
        planning(StageRecord(status="blocked", attempts=2, error=Failure(code="claude_output_limit", message="Zu lang.")))
        self.assertIsNone(json.loads(self.request("/api/projects/example")[1])["outline"])
        self.assertFalse(json.loads(self.request("/api/projects")[1])["projects"][0]["has_outline"])
        for action in ("replan", "script"):
            status, body, _ = self.request("/api/projects/example/start", {"action": action, "message": "Kürzer.", "plan_hash": "x"})
            self.assertEqual((status, json.loads(body)["code"]), (400, "plan_required"))
        self.assertFalse((self.root / "studio/job.json").exists())
        planning(StageRecord(status="running", attempts=3))
        self.assertIsNone(json.loads(self.request("/api/projects/example")[1])["outline"])
        planning(StageRecord(status="completed", attempts=3))
        self.assertEqual(json.loads(self.request("/api/projects/example")[1])["outline"]["run_id"], "run_o")
        self.assertTrue(json.loads(self.request("/api/projects")[1])["projects"][0]["has_outline"])

    def test_an_episode_without_a_quality_report_carries_no_notes(self):
        self.publish_episode()
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["episodes"][0]["review_notes"], {})

    def test_bad_model_does_not_leave_a_half_created_project(self):
        data = {"config":self.config.model_dump(), "text":{"provider":"openrouter", "model":None}}
        self.assertEqual(self.request("/api/projects", data)[0], 400)
        self.assertEqual(len(list((self.workspace / "projects").glob("*/project.yaml"))), 1)

    def test_a_proposal_sets_the_series_goal_and_recency_and_zero_removes_the_rule(self):
        def apply(**fields):
            proposal = BriefProposal(message="So?", topic="A test project", central_question="Why?", prior_knowledge="",
                                     depth_request="Deep", focus_questions=[], excluded_topics=[], **fields)
            chat = read_json(self.root / "studio/chat.json", []) if (self.root / "studio/chat.json").exists() else []
            write_json(self.root / "studio/chat.json", [*chat, {"role": "assistant", **proposal.model_dump()}])
            detail = self.app.detail("example")
            self.assertFalse(detail["proposal_applied"])
            self.app.apply_proposal("example", {key: detail[key] for key in
                                                ("proposal_hash", "config_hash", "audio_hash", "execution_hash")})
            return read_yaml(self.root / "project.yaml")
        saved = apply(series_goal={"understand": 1, "evaluate": 1, "apply": 3}, recency_months=6)
        self.assertEqual((saved["series_goal"], saved["recency_months"]), ({"understand": 1, "evaluate": 1, "apply": 3}, 6))
        # Unset in a later proposal keeps both; recency 0 removes the rule, the goal stays.
        self.assertEqual(apply()["recency_months"], 6)
        saved = apply(recency_months=0)
        self.assertNotIn("recency_months", saved)
        self.assertEqual(saved["series_goal"]["apply"], 3)

    def test_a_new_german_project_asks_jev_by_default_and_an_english_one_does_not(self):
        for language, expected in (("de-DE", (True, True)), ("en-US", (False, False))):
            config = self.config.model_copy(update={"topic": f"Neues Thema {language}", "language": language})
            status, body, _ = self.request("/api/projects", {"config": config.model_dump(mode="json")})
            self.assertEqual(status, 200, body)
            detail = json.loads(self.request("/api/projects/" + json.loads(body)["id"])[1])
            self.assertEqual((detail["jev_probe"], detail["jev_default"]), expected, language)
        # The user's switch replaces the default, in both directions.
        project = json.loads(body)["id"]
        self.request(f"/api/projects/{project}/jev_probe", {"enabled": True})
        self.assertEqual(json.loads(self.request("/api/projects/" + project)[1])["jev_default"], False)

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

    def test_the_jev_probe_switch_keeps_the_settings_hash_and_the_key_goes_only_to_runs_that_asked(self):
        """The switch has its own file: a brief proposal applied before stays applied, and a later one cannot turn
        the probe off by accident. The worker hands the OpenRouter key only to a script run that uses the probe."""
        before = json.loads(self.request("/api/projects/example")[1])
        self.assertFalse(before["jev_probe"])
        self.assertIsNone(probe_key(self.root, {"api_key": "test-key"}))
        status, body, _ = self.request("/api/projects/example/jev_probe", {"enabled": True})
        self.assertEqual(status, 200, body)
        after = json.loads(self.request("/api/projects/example")[1])
        self.assertTrue(after["jev_probe"])
        self.assertEqual((after["execution"], after["execution_hash"]), (before["execution"], before["execution_hash"]))
        self.assertEqual(probe_key(self.root, {"api_key": "test-key"}), "test-key")
        self.assertEqual(self.request("/api/projects/example/jev_probe", {"enabled": "yes"})[0], 400)
        # Saving the settings, as a proposal does, neither needs nor changes the switch.
        data = {"config": after["config"], "config_hash": after["config_hash"], "text": after["text"],
                "execution": {**after["execution"], "jev_probe": False}, "execution_hash": after["execution_hash"]}
        self.assertEqual(self.request("/api/projects/example/save", data)[0], 200)
        self.assertTrue(json.loads(self.request("/api/projects/example")[1])["jev_probe"])
        # A run keeps what it started with: the saved request decides, not today's switch.
        write_json(self.root / "runs/run_old/script_request.json", {"execution": {"text": "sequential", "audio": "sequential"}})
        self.assertIsNone(probe_key(self.root, {"api_key": "test-key"}, "run_old"))
        write_json(self.root / "runs/run_jev/script_request.json", {"execution": {"text": "sequential", "jev_probe": True}})
        self.request("/api/projects/example/jev_probe", {"enabled": False})
        self.assertEqual(probe_key(self.root, {"api_key": "test-key"}, "run_jev"), "test-key")

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
        # Every Gemini recording gets its expression layer unless the choice says otherwise (the user, 2026-09-29).
        self.assertEqual(saved["audio_settings"], {**audio, "expression": True})
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

    def test_a_refused_request_is_answered_after_its_body_is_read(self):
        # A stale tab after a Studio restart posts with the old token. Refused before its body was read, the
        # connection was reset on Windows and the page reported "not reachable" instead of renewing its token.
        # The reset depends on timing, so the test pins the mechanism: the refusal reads the whole declared body first.
        from podcast_automate.studio import StudioHandler
        payload = {"kind": "gap", "run_id": "run_x", "task_id": "task_a", "reason": "x" * 100_000}
        real, drained = StudioHandler.drain, []

        def spy(handler):
            drained.append(handler.unread)
            real(handler)
            drained.append(handler.unread)
        with patch.object(StudioHandler, "drain", spy):
            status, body, _ = self.request("/api/projects/example/approve", payload, {"X-Studio-Token": "stale"})
        self.assertEqual((status, json.loads(body)["code"]), (403, "forbidden"))
        self.assertEqual(drained, [len(json.dumps(payload)), 0])

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


class WorkspaceSettingsTests(unittest.TestCase):
    """The settings page (studio_settings): one text model, audio, execution, pre-approvals, research limits and time
    limit for every project, the user's choice of 2026-10-03. Before, each project kept its own."""
    request = StudioHttpTests.request

    def setUp(self):
        StudioHttpTests.setUp(self)
        patcher = patch.dict(os.environ, {"PLA_SUBSCRIPTIONS_STORE": str(self.workspace / "subscriptions.json")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.other = self.workspace / "projects" / "second"
        init_project(self.other, TopicBrief(topic="A second project", voice_profile={"host_a": "Aiden", "host_b": "Vivian"}))
        # The first project was changed last, so a first visit shows its values.
        write_json(self.root / "studio/text.json", {"provider": "codex_cli", "model": "gpt-6-astra", "reasoning_effort": "xhigh",
                                                    "max_output_tokens": 32768})
        later = time.time() + 60
        os.utime(self.root / "project.yaml", (later, later))

    def view(self):
        return json.loads(self.request("/api/settings")[1])

    def test_saved_settings_hold_for_every_project_and_runs_take_them(self):
        from podcast_automate import studio_allowances
        from podcast_automate.execution import selected_execution
        from podcast_automate.speech import selected_audio
        from podcast_automate.storage import load_project
        first = self.view()
        self.assertEqual((first["global"], first["source_project"], first["settings"]["text"]["model"]),
                         (False, "example", "gpt-6-astra"))
        values = {**first["settings"],
                  "text": {"provider": "openrouter", "model": "anthropic/claude-opus-5.5", "reasoning_effort": "medium",
                           "max_output_tokens": 65536},
                  "audio": {"provider": "openrouter_gemini_tts", "voices": {"host_a": "Sadaltager", "host_b": "Aoede"},
                            "pauses": {"same_speaker_ms": 300, "speaker_change_ms": 500, "chapter_break_ms": 1000}},
                  "execution": {"text": "parallel", "audio": "parallel"},
                  "allowances": {"fresh_attempts": 2, "extra_calls": 250},
                  "research_limits": {"model_calls": 1200, "sources": 150, "search_rounds": 48},
                  "text_timeout_seconds": 5400}
        status, body, _ = self.request("/api/settings", {"settings": values, "hash": first["hash"], "claude_extra_usage": True})
        self.assertEqual(status, 200, body)
        saved = json.loads(body)
        self.assertEqual((saved["global"], saved["source_project"], saved["claude_extra_usage"]), (True, None, True))
        for root in (self.root, self.other):
            with self.subTest(project=root.name):
                detail = json.loads(self.request(f"/api/projects/{root.name}")[1])
                self.assertTrue(detail["settings_global"])
                self.assertEqual((detail["text"]["model"], detail["text"]["max_output_tokens"]), ("anthropic/claude-opus-5.5", 65536))
                config = load_project(root)
                audio = selected_audio(root, config)
                self.assertEqual((audio.provider, audio.voices["host_a"], audio.pauses.chapter_break_ms), ("openrouter_gemini_tts", "Sadaltager", 1000))
                self.assertEqual((selected_execution(root).text, selected_execution(root).audio), ("parallel", "parallel"))
                self.assertEqual(studio_allowances.allowances(root), {"fresh_attempts": 2, "extra_calls": 250})
                self.assertEqual((config.research_limits.model_calls, config.runtime.text_timeout_seconds), (1200, 5400))
        self.assertEqual(read_json(self.root / "studio/text.json")["model"], "gpt-6-astra", "the project's own file is left as it was")
        # A project save keeps to the brief and leaves the settings to their page.
        detail = json.loads(self.request("/api/projects/example")[1])
        status, body, _ = self.request("/api/projects/example/save", {"config": detail["config"], "config_hash": detail["config_hash"],
            "text": {"provider": "codex_cli"}, "audio_settings": detail["audio_settings"], "audio_hash": detail["audio_hash"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(self.request("/api/projects/example")[1])["text"]["model"], "anthropic/claude-opus-5.5")
        self.assertEqual(read_json(self.root / "studio/text.json")["model"], "gpt-6-astra")

    def test_a_stale_page_or_a_time_limit_out_of_range_saves_nothing(self):
        first = self.view()
        self.assertEqual(self.request("/api/settings", {"settings": first["settings"], "hash": "stale", "claude_extra_usage": False})[0], 400)
        for minutes in (1, 600):
            with self.subTest(minutes=minutes):
                status, _, _ = self.request("/api/settings", {"settings": {**first["settings"], "text_timeout_seconds": minutes * 60},
                                                              "hash": first["hash"], "claude_extra_usage": False})
                self.assertEqual(status, 400)
        self.assertEqual(self.request("/api/settings", {"settings": first["settings"], "hash": first["hash"]})[0], 400,
                         "Claude's switch is part of every save")
        self.assertFalse((self.workspace / "projects" / ".studio-settings.json").exists())
        self.assertFalse(self.view()["global"])


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
        # Each lane parks in its own file (2026-10-02); paused_job.json is the single slot of older Studios.
        self.assertEqual(json.loads((self.root / "studio/paused_research.json").read_text(encoding="utf-8"))["id"], "r")
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
        self.assertFalse((self.root / "studio/paused_research.json").exists())
        self.assertEqual(resumed["run"]["run_id"], "run_r")
        self.assertTrue((self.root / "studio/stderr" / (resumed["id"] + ".log")).is_file())

    def test_a_new_run_of_the_same_lane_replaces_what_was_parked(self):
        run = self.research_run(error=Failure(code="review_disagreement", message="Einwand"))
        write_json(self.root / "studio/paused_job.json", {"id": "r", "action": "research", "status": "blocked", "run": run})
        with self.started():
            self.app.start("example", {"action": "research"})
        self.assertFalse((self.root / "studio/paused_job.json").exists())

    def script_run(self, run_id="run_s", status="blocked", error=None):
        run = RunManifest(run_id=run_id, kind="script", status=status, project_hash="p", input_hash="i",
                          stages={"review": StageRecord(status=status, attempts=1, error=error)})
        write_yaml(manifest_path(self.root, run_id).parent / "run_manifest.yaml", run.model_dump(mode="json"))
        return run.model_dump(mode="json")

    def test_a_parked_run_of_one_lane_is_never_displaced_by_another_lanes_stop(self):
        """2026-10-02: one paused_job.json slot; a later stop of another lane parked over a stopped research run,
        whose stop card and scheduled resume then disappeared."""
        research = self.research_run(error=Failure(code="research_questions_blocked", message="offen"))
        write_json(self.root / "studio/job.json", {"id": "r", "action": "research", "status": "blocked",
                   "finished_at": "2026-09-01T10:00:00+00:00", "run": research})
        with self.started():
            self.app.start("example", {"action": "assistant", "message": "Kürzer bitte"})
        script = self.script_run(error=Failure(code="script_review_failed", message="Einwände"))
        write_json(self.root / "studio/job.json", {"id": "s", "action": "script", "status": "blocked",
                   "finished_at": "2026-09-02T10:00:00+00:00", "run": script})
        self.app.workers.clear()
        with self.started():
            check = self.app.start("example", {"action": "check"})
        parked = {path.name: json.loads(path.read_text(encoding="utf-8"))["id"]
                  for path in (self.root / "studio").glob("paused_*.json")}
        self.assertEqual(parked, {"paused_research.json": "r", "paused_script.json": "s"})
        write_json(self.root / "studio/job.json", {**check, "status": "completed"})
        self.app.workers.clear()
        detail = self.app.detail("example")
        self.assertEqual(detail["job"]["id"], "s", "the newest stop is the project's job")
        self.assertEqual(sorted(job["id"] for job in detail["parked_jobs"]), ["r", "s"])
        # The single slot of older Studios is still read.
        old = self.research_run(run_id="run_old", status="waiting_for_quota",
                                error=Failure(code="subscriptions_exhausted", message="leer"))
        (self.root / "studio/paused_research.json").unlink()
        write_json(self.root / "studio/paused_job.json", {"id": "o", "action": "research", "status": "waiting_for_quota",
                   "retry_at": "2026-01-01T00:00:00+00:00", "run": old})
        self.assertEqual(sorted(job["id"] for job in self.app.detail("example")["parked_jobs"]), ["o", "s"])
        self.assertEqual(self.app.due_resumes(), [("example", "run_old", 0)])

    def test_a_transient_technical_stop_resumes_by_itself_after_a_pause_three_times_at_most(self):
        """2026-10-02: about 186 resumes by hand against 8 automatic ones; only quota waits resumed by themselves."""
        from podcast_automate.subscriptions import parse_iso
        stopped = "2026-09-01T10:00:00+00:00"
        base = parse_iso(stopped).timestamp()
        job = {"id": "t", "action": "resume", "status": "failed", "finished_at": stopped,
               "run": self.research_run(status="failed", error=Failure(code="timeout", message="Zeitlimit"))}
        write_json(self.root / "studio/job.json", job)
        shown = self.app.job(self.root)
        self.assertEqual((shown["auto_resume_kind"], shown["auto_resume_at"]), ("transient", "2026-09-01T10:10:00+00:00"))
        self.assertEqual(self.app.due_resumes(base + 599), [])
        self.assertEqual(self.app.due_resumes(base + 600), [("example", "run_r", 0)])
        write_json(self.root / "studio/job.json", {**job, "auto_resume_count": 2})
        self.assertEqual(self.app.job(self.root)["auto_resume_at"], "2026-09-01T11:30:00+00:00")
        write_json(self.root / "studio/job.json", {**job, "auto_resume_count": 3})
        self.assertTrue(self.app.job(self.root)["auto_resume_exhausted"])
        self.assertEqual(self.app.due_resumes(base + 86400), [])
        # Never for a decision, a limit, a login or a stop the user made.
        for code, status, manifest in (("research_questions_blocked", "blocked", "blocked"),
                                       ("research_budget_exhausted", "blocked", "blocked"),
                                       ("authentication_required", "failed", "failed"),
                                       ("interrupted", "interrupted", "pending")):
            with self.subTest(code=code):
                run = self.research_run(status=manifest, error=Failure(code=code, message="x"))
                write_json(self.root / "studio/job.json", {**job, "status": status, "run": run})
                self.assertNotIn("auto_resume_at", self.app.job(self.root))
                self.assertEqual(self.app.due_resumes(base + 86400), [])
        # A due one resumes once the project is free, and the attempt is counted.
        write_json(self.root / "studio/job.json", {**job, "run": self.research_run(
            status="failed", error=Failure(code="stall", message="still"))})
        with project_lock(self.root):
            self.assertEqual(self.app.resume_due(base + 600), [])
        with self.started():
            self.assertEqual(self.app.resume_due(base + 600), ["example"])
        self.assertEqual(json.loads((self.root / "studio/job.json").read_text(encoding="utf-8"))["auto_resume_count"], 1)

    def running_research(self, job_id):
        run = RunManifest(run_id="run_e", kind="research", status="running", project_hash="p", input_hash="i",
                          stages={"dossier": StageRecord(status="running", attempts=1)})
        write_yaml(manifest_path(self.root, "run_e").parent / "run_manifest.yaml", run.model_dump(mode="json"))
        write_json(self.root / "studio/job.json", {"id": job_id, "action": "resume", "status": "running",
                   "started_at": "2026-09-01T10:00:00+00:00", "run": run.model_dump(mode="json")})

    def test_a_worker_that_outlived_its_studio_runs_elsewhere_and_is_stopped_by_its_recorded_identity(self):
        """2026-10-02: a busy lock was written as "interrupted" on every poll; "Fortsetzen" met the busy project and
        "Anhalten" found no job."""
        from podcast_automate.studio import record_worker
        job_id = "c" * 32
        self.running_research(job_id)
        before = (self.root / "studio/job.json").read_bytes()
        with project_lock(self.root):
            # Without a record of its worker, the held project lock is the sign that it runs.
            shown = self.app.job(self.root)
            self.assertEqual((shown["status"], shown["external"], shown["external_stoppable"]), ("running", True, False))
            self.assertEqual(self.request("/api/projects/example/stop", {})[0], 400)
        self.assertEqual((self.root / "studio/job.json").read_bytes(), before)
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        record_worker(self.root, job_id, process)
        shown = self.app.job(self.root)
        self.assertEqual((shown["status"], shown["external_stoppable"]), ("running", True))
        self.assertEqual((self.root / "studio/job.json").read_bytes(), before)
        status, body, _ = self.request("/api/projects/example/stop", {})
        self.assertEqual(status, 200, body)
        process.wait(timeout=10)
        self.assertEqual(self.app.job(self.root)["status"], "interrupted")
        saved = RunManifest.model_validate(read_yaml(manifest_path(self.root, "run_e").parent / "run_manifest.yaml"))
        self.assertEqual(saved.status, "pending")

    def test_a_recorded_worker_that_ended_is_interrupted_once_the_project_is_free(self):
        from podcast_automate.studio import record_worker
        job_id = "d" * 32
        self.running_research(job_id)
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        record_worker(self.root, job_id, process)
        process.wait(timeout=10)
        with project_lock(self.root):
            # Another process holds the project: shown as interrupted, written once the lock is free.
            self.assertEqual(self.app.job(self.root)["status"], "interrupted")
            self.assertEqual(json.loads((self.root / "studio/job.json").read_text(encoding="utf-8"))["status"], "running")
        self.assertEqual(self.app.job(self.root)["status"], "interrupted")
        self.assertEqual(json.loads((self.root / "studio/job.json").read_text(encoding="utf-8"))["status"], "interrupted")

    def test_old_status_locks_and_worker_records_are_swept_with_the_logs(self):
        studio = self.root / "studio"
        current, finished = "e" * 32, "f" * 32
        write_json(studio / "job.json", {"id": current, "status": "running"})
        old = time.time() - 7200
        files = [studio / f".status-{current}.lock", studio / f".status-{finished}.lock",
                 studio / "workers" / f"{finished}.json", studio / "stderr" / f"{finished}.log"]
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
            os.utime(path, (old, old))
        self.app.stderr_file(self.root, "0" * 32)
        self.assertEqual([path.exists() for path in files], [True, False, False, False])

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


class OverviewJobTests(unittest.TestCase):
    def test_a_project_card_gets_short_question_rows_with_what_its_decisions_count(self):
        # 2026-10-01: the full rows made the project list 2 MB, read again on every poll.
        from podcast_automate.studio import overview_job
        row = {"id": "t1", "question": "Q?", "kind": "synthesis", "status": "blocked", "outcome": "prerequisite_block",
               "depends_on": ["t0"], "accepted_gap": None, "retries": 1, "auto_retries": 2, "retry_requested": True,
               "findings": [{"id": "f", "statement": "x" * 5000}], "sources": [{"id": "s"}], "support": [{}],
               "advice": {"key": "1.2", "recommendation": "retry", "diagnosis": "x" * 2000, "sources": [{}]}}
        job = {"status": "blocked", "progress": {"phase": "research", "research_questions": {"phase": "blocked", "closed": 3,
                                                                                                "questions": [row]}}}
        card = overview_job(job)["progress"]["research_questions"]
        self.assertEqual(card["closed"], 3)
        self.assertEqual(card["questions"][0], {key: row[key] for key in (
            "id", "question", "kind", "status", "outcome", "depends_on", "accepted_gap", "retries", "auto_retries",
            "retry_requested")} | {"advice": {"key": "1.2", "recommendation": "retry"}})
        self.assertIn("findings", job["progress"]["research_questions"]["questions"][0], "the project page keeps whole rows")
        self.assertIsNone(overview_job(None))


if __name__ == "__main__":
    unittest.main()
