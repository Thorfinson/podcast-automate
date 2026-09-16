import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from podcast_automate.models import TopicBrief
from podcast_automate.storage import init_project, load_project, project_lock

spec = importlib.util.spec_from_file_location("qwen_setup", Path(__file__).resolve().parents[1] / "scripts/setup-qwen.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class QwenSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.root = self.workspace / "projects/existing"
        init_project(self.root, TopicBrief(topic="Keep this project"))
        self.before = (self.root / "project.yaml").read_bytes()
        python = self.workspace / ".venv-tts/bin/python"
        python.parent.mkdir(parents=True)
        python.touch()
        self.mock_file = patch.object(setup, "__file__", str(self.workspace / "scripts/setup-qwen.py"))
        self.mock_file.start()
        self.addCleanup(self.mock_file.stop)

    def test_setup_checks_device_and_downloads_before_saving_without_generating_audio(self):
        with patch.object(setup.platform, "system", return_value="Darwin"), \
             patch.object(setup.subprocess, "run", return_value=SimpleNamespace(stdout="installed packages")) as run, \
             patch("builtins.print"):
            setup.main(["--python", "python3.12", "--device", "mps", "--project", "projects/existing"])
        updated = load_project(self.root)
        self.assertEqual(updated.topic, "Keep this project")
        self.assertEqual(updated.runtime.tts_device, "mps")
        self.assertEqual((self.root / "reports/project-before-qwen.yaml").read_bytes(), self.before)
        saved = json.loads((self.workspace / ".studio/tts-runtime.json").read_text())
        self.assertEqual(updated.runtime.tts_revision, saved["tts_revision"])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any("snapshot_download" in str(command) for command in commands))
        self.assertTrue(all("generate_custom_voice" not in str(command) for command in commands))

    def test_failed_device_check_does_not_change_project_or_saved_defaults(self):
        def run(command, **kwargs):
            if "snapshot_download" in str(command):
                raise setup.subprocess.CalledProcessError(1, command)
            return SimpleNamespace(stdout="")
        with patch.object(setup.platform, "system", return_value="Darwin"), \
             patch.object(setup.subprocess, "run", side_effect=run), \
             self.assertRaises(setup.subprocess.CalledProcessError):
            setup.main(["--python", "python3.12", "--device", "mps", "--project", "projects/existing"])
        self.assertEqual((self.root / "project.yaml").read_bytes(), self.before)
        self.assertFalse((self.workspace / ".studio/tts-runtime.json").exists())

    def test_setup_keeps_an_active_projects_configuration_locked(self):
        with patch.object(setup.platform, "system", return_value="Linux"), \
             patch.object(setup.subprocess, "run", return_value=SimpleNamespace(stdout="")), \
             project_lock(self.root):
            from podcast_automate.errors import AppError
            with self.assertRaises(AppError):
                setup.main(["--python", "python3.12", "--device", "cpu", "--project", "projects/existing"])
        self.assertEqual((self.root / "project.yaml").read_bytes(), self.before)
        self.assertFalse((self.workspace / ".studio/tts-runtime.json").exists())
