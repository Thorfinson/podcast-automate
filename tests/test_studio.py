import http.client
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from podcast_automate.errors import AppError
from podcast_automate.models import RunManifest, StageRecord, TopicBrief
from podcast_automate.runner import manifest_path, outputs_valid
from podcast_automate.script_models import SeriesPlan
from podcast_automate.scripting import outline_hash, run_script
from podcast_automate.storage import digest, file_hash, init_project, read_yaml, write_json, write_yaml
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
            research.assert_called_once_with(self.root, model="gpt-5.6-sol", reasoning_effort="high")
        proposal = BriefProposal(message="A proposal.", topic="Title", central_question="Why?", prior_knowledge="",
                                 depth_request="Deep", focus_questions=[], excluded_topics=[])
        def reply(adapter, *args, **kwargs):
            self.assertEqual(adapter.settings.codex_model, "gpt-5.6-sol")
            self.assertEqual(adapter.reasoning_effort, "high")
            return proposal, {}
        with patch("podcast_automate.studio_worker.CodexAdapter.structured", autospec=True, side_effect=reply):
            perform(self.root, {"action": "assistant", "message": "Help", "text": selection})

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
        self.app.process_root = self.root
        self.app.process = Mock()
        self.app.process.poll.return_value = None
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
        self.app.process_root = self.root
        self.app.process = Mock()
        self.app.process.poll.return_value = None
        detail = json.loads(self.request("/api/projects/example")[1])
        self.assertEqual(detail["episodes"], [])
        self.assertEqual(detail["script_previews"][0]["state"], "draft")
        self.assertEqual(detail["job"]["progress"]["script_previews"], detail["script_previews"])
        # Even after an interruption, a preview has no publish record or audio approval.
        self.app.process.poll.return_value = 0
        with patch("podcast_automate.studio.subprocess.Popen") as launch:
            status, _, _ = self.request("/api/projects/example/start", {"action": "audio", "episode": "ep_001",
                "approve_audio": True, "script_hash": detail["script_previews"][0]["hash"]})
        self.assertEqual(status, 400)
        launch.assert_not_called()
        self.assertFalse((self.root / "episodes/ep_001/script.yaml").exists())

    def test_bad_model_does_not_leave_a_half_created_project(self):
        data = {"config":self.config.model_dump(), "text":{"provider":"openrouter", "model":None}}
        self.assertEqual(self.request("/api/projects", data)[0], 400)
        self.assertEqual(len(list((self.workspace / "projects").glob("*/project.yaml"))), 1)

    def test_audio_requires_checked_box_current_text_and_current_voices(self):
        folder = self.root / "episodes/ep_001"
        write_yaml(folder / "script.yaml", example_script().model_dump())
        (folder / "script.md").write_text("Text to read", encoding="utf-8")
        data = {"action":"audio", "episode":"ep_001", "script_hash":file_hash(folder / "script.yaml"),
                "readable_hash":file_hash(folder / "script.md"), "config_hash":digest(self.config.model_dump(mode="json"))}
        with patch("podcast_automate.studio.subprocess.Popen") as process:
            self.assertEqual(self.request("/api/projects/example/start", data)[0], 400)
            data["approve_audio"] = True
            data["config_hash"] = "old voices"
            self.assertEqual(self.request("/api/projects/example/start", data)[0], 400)
            data["config_hash"] = digest(self.config.model_dump(mode="json"))
            data["script_hash"] = "old text"
            self.assertEqual(self.request("/api/projects/example/start", data)[0], 400)
            process.assert_not_called()

    def test_gemini_audio_can_be_selected_without_changing_script_inputs(self):
        detail = json.loads(self.request("/api/projects/example")[1])
        before = (self.root / "project.yaml").read_bytes()
        audio = {"provider":"openrouter_gemini_tts", "voices":{"host_a":"Sadaltager","host_b":"Aoede"}}
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
                "config_hash":digest(self.config.model_dump(mode="json")), "audio_hash":"old provider"}
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
        self.app.process.wait(timeout=10)
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
        self.app.process, self.app.process_root = process, self.root
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


if __name__ == "__main__":
    unittest.main()
