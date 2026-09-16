import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from podcast_automate.episode_audio import run_episode_audio
from podcast_automate.errors import AppError
from podcast_automate.execution import run_episode_stage
from podcast_automate.models import EpisodeScript, ResearchLimits, TopicBrief
from podcast_automate.parallel_speech import LockedGeminiSpeech
from podcast_automate.research import reserve_call
from podcast_automate.runner import manifest_path, outputs_valid
from podcast_automate.script_models import SeriesPlan
from podcast_automate.scripting import run_script
from podcast_automate.storage import digest, file_hash, init_project, project_lock, read_yaml, write_json, write_yaml
from podcast_automate.studio import BriefProposal, Studio, audio_job_path, record_interruption
from tests import test_scripting as fixtures
from tests.test_scripting import example_script
from tests.test_speech import response


REMOTE = {"provider": "openrouter_gemini_tts", "voices": {"host_a": "Sadaltager", "host_b": "Aoede"}}


class LockAndBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_shared_audio_locks_block_writers_across_processes(self):
        code = "from pathlib import Path; from podcast_automate.storage import project_lock; import sys\nwith project_lock(Path(sys.argv[1]), shared=sys.argv[2]=='shared'): print('locked')"
        with project_lock(self.root, shared=True):
            shared = subprocess.run([sys.executable, "-c", code, str(self.root), "shared"], capture_output=True, timeout=10)
            writer = subprocess.run([sys.executable, "-c", code, str(self.root), "exclusive"], capture_output=True, timeout=10)
            self.assertEqual(shared.returncode, 0, shared.stderr)
            self.assertNotEqual(writer.returncode, 0)
        with project_lock(self.root):
            pass

    def test_parallel_reservations_never_reuse_call_numbers_or_exceed_limit(self):
        def reserve(_):
            try:
                return reserve_call(self.root, ResearchLimits(model_calls=5))
            except AppError as error:
                self.assertEqual(error.code, "research_budget_exhausted")
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            numbers = list(pool.map(reserve, range(16)))
        self.assertEqual(sorted(n for n in numbers if n is not None), [1, 2, 3, 4, 5])
        self.assertEqual(json.loads((self.root / "budget.json").read_text())["model_calls"], 5)

    def test_stage_parallelism_is_bounded_and_result_order_is_stable(self):
        active = peak = 0
        mutex, barrier = threading.Lock(), threading.Barrier(3)
        entries = [SimpleNamespace(episode_id=f"ep_{i:03}") for i in range(6)]
        def action(entry):
            nonlocal active, peak
            with mutex:
                active += 1
                peak = max(peak, active)
            if entry in entries[:3]:
                barrier.wait(timeout=5)
            time.sleep(0.02)
            with mutex:
                active -= 1
            return [entry.episode_id]
        result = run_episode_stage(entries, action, workers=3, work=self.root, stage="writing")
        self.assertEqual(peak, 3)
        self.assertEqual(result, [e.episode_id for e in entries])

    def test_sequential_setting_keeps_one_task_at_a_time(self):
        seen = []
        entries = [SimpleNamespace(episode_id=f"ep_{i:03}") for i in range(3)]
        result = run_episode_stage(entries, lambda entry: seen.append(entry.episode_id) or [entry.episode_id],
                                   workers=1, work=self.root, stage="review")
        self.assertEqual(result, seen)
        self.assertEqual(seen, [e.episode_id for e in entries])

    def test_identical_concurrent_speech_requests_share_one_cached_result(self):
        def respond(*args, **kwargs):
            time.sleep(0.05)
            return response()
        with patch("podcast_automate.speech.build_opener") as build:
            build.return_value.open.side_effect = respond
            with ThreadPoolExecutor(max_workers=3) as pool:
                paths = list(pool.map(lambda _: LockedGeminiSpeech("test-key").synthesize(
                    "A shared example.", "Aoede", "de-DE", self.root), range(3)))
            self.assertEqual(build.return_value.open.call_count, 1)
        self.assertEqual(len(set(paths)), 1)
        self.assertEqual(json.loads(paths[0].with_suffix(".json").read_text())["sha256"], file_hash(paths[0]))


class StudioParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.root = self.workspace / "projects/example"
        self.config = TopicBrief(topic="Example", voice_profile={"host_a": "Aiden", "host_b": "Vivian"})
        init_project(self.root, self.config)
        self.app = Studio(self.workspace)
        write_json(self.root / "studio/audio.json", REMOTE)
        write_json(self.root / "studio/execution.json", {"text": "parallel", "audio": "parallel"})
        for number in range(1, 5):
            folder = self.root / f"episodes/ep_{number:03}"
            script = example_script().model_copy(update={"episode_id": folder.name})
            write_yaml(folder / "script.yaml", script.model_dump())
            (folder / "script.md").write_text("Read this script.")

    def approval(self, episode):
        folder = self.root / "episodes" / episode
        return {"action": "audio", "episode": episode, "approve_audio": True,
            "script_hash": file_hash(folder / "script.yaml"), "readable_hash": file_hash(folder / "script.md"),
            "config_hash": digest(self.config.model_dump(mode="json")), "audio_hash": digest(REMOTE)}

    def process(self, *args, **kwargs):
        pipe = io.StringIO()
        pipe.close = Mock()
        process = Mock(stdin=pipe)
        process.poll.return_value = None
        return process

    def test_three_distinct_jobs_have_independent_receipts_and_key_only_in_stdin(self):
        self.app.key = "secret-for-test"
        with patch("podcast_automate.studio.subprocess.Popen", side_effect=self.process) as launch:
            jobs = [self.app.start("example", self.approval(f"ep_{n:03}")) for n in range(1, 4)]
            for episode, code in [("ep_001", "episode_busy"), ("ep_004", "audio_capacity")]:
                with self.assertRaises(AppError) as denied:
                    self.app.start("example", self.approval(episode))
                self.assertEqual(denied.exception.code, code)
            self.assertEqual(launch.call_count, 3)
            self.assertNotIn(self.app.key, str(launch.call_args))
        detail = self.app.detail("example")
        self.assertEqual(len(detail["audio_jobs"]), 3)
        self.assertEqual(detail["audio_capacity"]["available"], 0)
        for job in jobs:
            payload = json.loads(self.app.audio_processes[job["id"]][0].stdin.getvalue())
            self.assertEqual(payload["audio_job_id"], job["id"])
            self.assertTrue(payload["parallel_remote"])
            self.assertEqual(payload["api_key"], self.app.key)
            self.assertEqual(json.loads(audio_job_path(self.root, job["id"]).read_text())["episode"], job["episode"])
        for path in self.root.rglob("*.json"):
            self.assertNotIn(self.app.key, path.read_text())

    def test_sequential_mode_and_local_work_keep_the_single_worker_guard(self):
        write_json(self.root / "studio/execution.json", {"text": "parallel", "audio": "sequential"})
        with patch("podcast_automate.studio.subprocess.Popen", side_effect=self.process):
            self.app.start("example", self.approval("ep_001"))
            with self.assertRaises(AppError) as denied:
                self.app.start("example", self.approval("ep_002"))
            self.assertEqual(denied.exception.code, "audio_capacity")
            with self.assertRaises(AppError):
                self.app.start("example", {"action": "research"})
            with self.assertRaises(AppError):
                self.app.idle(self.root)

    def test_stopping_one_remote_job_does_not_stop_its_neighbor(self):
        with patch("podcast_automate.studio.subprocess.Popen", side_effect=self.process):
            first = self.app.start("example", self.approval("ep_001"))
            second = self.app.start("example", self.approval("ep_002"))
        process = self.app.audio_processes[first["id"]][0]
        process.wait.side_effect = lambda **kwargs: setattr(process.poll, "return_value", 0)
        def stopped(worker):
            worker.wait(timeout=1)
        with patch("podcast_automate.studio.stop_process_tree", side_effect=stopped), project_lock(self.root, shared=True):
            self.app.stop("example", first["id"])
        self.assertEqual(self.app.job(self.root, audio_job_id=first["id"])["status"], "interrupted")
        self.assertEqual(self.app.job(self.root, audio_job_id=second["id"])["status"], "running")
        self.app.audio_processes[second["id"]][0].wait.assert_not_called()

    def test_overview_delete_and_restore_preserve_every_project_artifact(self):
        marker = self.root / "episodes/ep_001/script.md"
        before = marker.read_bytes()
        self.assertEqual(self.app.overview()["projects"][0]["id"], "example")
        with self.assertRaises(AppError):
            self.app.delete("example", {})
        with project_lock(self.root, shared=True), self.assertRaises(AppError):
            self.app.delete("example", {"confirm_id": "example", "config_hash": digest(self.config.model_dump(mode="json"))})
        result = self.app.delete("example", {"confirm_id": "example", "config_hash": digest(self.config.model_dump(mode="json"))})
        self.assertFalse(marker.exists())
        self.assertEqual(self.app.overview()["projects"], [])
        self.assertEqual(len(self.app.overview()["trash"]), 1)
        self.app.restore({"trash_id": result["trash_id"]})
        self.assertEqual(marker.read_bytes(), before)
        self.assertEqual(self.app.overview()["trash"], [])

    def test_chat_settings_require_applying_the_current_proposal(self):
        proposal = BriefProposal(message="Use this?", topic="Changed", central_question="Why?", prior_knowledge="",
            depth_request="Deep", focus_questions=[], excluded_topics=[], execution={"text": "parallel", "audio": "sequential"},
            text={"model": "gpt-6-astra", "reasoning_effort": "xhigh"}, audio_settings=REMOTE)
        row = {"role": "assistant", **proposal.model_dump()}
        write_json(self.root / "studio/chat.json", [row])
        detail = self.app.detail("example")
        request = {key: detail[key] for key in ("proposal_hash", "config_hash", "audio_hash", "execution_hash")}
        with self.assertRaises(AppError):
            self.app.apply_proposal("example", {**request, "proposal_hash": "stale"})
        self.assertEqual(read_yaml(self.root / "project.yaml")["topic"], "Example")
        self.app.apply_proposal("example", request)
        result = self.app.detail("example")
        self.assertEqual(result["execution"], {"text": "parallel", "audio": "sequential"})
        self.assertEqual(result["text"]["reasoning_effort"], "xhigh")
        self.assertTrue(result["proposal_applied"])
        self.assertFalse((self.root / "studio/job.json").exists())


class ParallelPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ScriptingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def model(self, prompt, output_type, directory, **kwargs):
        result, metadata = self.fixture.model(prompt, output_type, directory, **kwargs)
        if output_type is SeriesPlan:
            second = result.episodes[0].model_copy(deep=True)
            second.episode_id = "ep_002"
            result.episodes.append(second)
        if output_type is EpisodeScript:
            payload = json.loads(prompt.splitlines()[-1])
            result.episode_id = payload.get("episode", payload.get("original_script"))["episode_id"]
            if result.episode_id == "ep_002":
                result.segments[0].text = result.segments[0].text.replace("model", "device")
        return result, metadata

    def test_codex_calls_overlap_within_stage_and_resume_keeps_saved_mode(self):
        write_json(self.root / "studio/execution.json", {"text": "parallel", "audio": "sequential"})
        barrier = threading.Barrier(2)
        def model(*args, **kwargs):
            if kwargs["prompt_version"] == "write_episode.v6-framing":
                barrier.wait(timeout=5)
            return self.model(*args, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        work = manifest_path(self.root, run.run_id).parent
        self.assertEqual(json.loads((work / "script_request.json").read_text())["execution"]["text"], "parallel")
        self.assertEqual(len(list((work / "reviewed").glob("*.json"))), 2)
        write_json(self.root / "studio/execution.json", {"text": "sequential", "audio": "sequential"})
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("No repeat calls")):
            resumed = run_script(self.root, resume=True, run_id=run.run_id)
        self.assertEqual(resumed.status, "completed")
        self.assertTrue(all(outputs_valid(self.root, stage) for stage in resumed.stages.values()))

    def test_two_audio_pipelines_overlap_keep_approvals_and_resume_without_calls(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            self.assertEqual(run_script(self.root).status, "completed")
        barrier = threading.Barrier(2)
        def audio(request, **kwargs):
            if json.loads(request.data)["voice"] == "Sadaltager":
                barrier.wait(timeout=5)
            return response()
        def assemble(script, paths, folder, **kwargs):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "audio.mp3").write_bytes(b"test-audio")
            write_json(folder / "audio_report.json", {"duration_seconds": 3})
            return [folder / "audio.mp3", folder / "audio_report.json"]
        with patch("podcast_automate.speech.build_opener") as build, patch("podcast_automate.episode_audio.assemble", side_effect=assemble):
            build.return_value.open.side_effect = audio
            with ThreadPoolExecutor(max_workers=2) as pool:
                runs = list(pool.map(lambda episode: run_episode_audio(self.root, episode=episode, approve_audio=True,
                    audio_choice=REMOTE, api_key="test-key", parallel_remote=True), ["ep_001", "ep_002"]))
            self.assertEqual([run.status for run in runs], ["completed", "completed"])
            self.assertEqual(build.return_value.open.call_count, 3)  # shared second utterance reused
            build.return_value.open.side_effect = AssertionError("No new API call on resume")
            for run in runs:
                self.assertEqual(run_episode_audio(self.root, resume=True, run_id=run.run_id, parallel_remote=True).status, "completed")
        for episode in ("ep_001", "ep_002"):
            self.assertEqual(set(read_yaml(self.root / "episodes" / episode / "audio_review.yaml")["scripts"]), {episode})
            self.assertTrue((self.root / "episodes" / episode / "audio_latest.json").exists())


if __name__ == "__main__":
    unittest.main()
