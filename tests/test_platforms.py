import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psutil

from podcast_automate.models import RuntimeSettings
from podcast_automate.platforms import configure_path, venv_python
from podcast_automate.process import stop_process_tree
from podcast_automate.qwen_worker import model_dtype, select_device
from podcast_automate.storage import write_json
from podcast_automate.studio import Studio


class PlatformTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "Studio ä with spaces"
        self.root.mkdir()

    def test_studio_selects_only_the_host_venv_and_applies_local_qwen_settings(self):
        for system, relative in [("Windows", "Scripts/python.exe"), ("Darwin", "bin/python"), ("Linux", "bin/python")]:
            with self.subTest(system=system), patch("podcast_automate.platforms.platform.system", return_value=system):
                python = self.root / ".venv-tts" / relative
                python.parent.mkdir(parents=True, exist_ok=True)
                python.touch()
                self.assertEqual(venv_python(self.root / ".venv-tts"), python)
                write_json(self.root / ".studio/tts-runtime.json", {"tts_device": "cpu", "tts_revision": "a" * 40})
                runtime = Studio(self.root).runtime()
                self.assertEqual(runtime.tts_python, str(python))
                self.assertEqual(runtime.tts_device, "cpu")
                self.assertEqual(runtime.tts_revision, "a" * 40)

    def test_local_binary_path_is_added_once(self):
        binaries = self.root / "tools/ffmpeg/bin"
        binaries.mkdir(parents=True)
        with patch.dict(os.environ, {"PATH": "test-original-path"}):
            configure_path(self.root)
            configure_path(self.root)
            paths = os.environ["PATH"].split(os.pathsep)
            self.assertEqual(paths[0], str(binaries))
            self.assertEqual(paths.count(str(binaries)), 1)
            self.assertIn("test-original-path", paths)

    def torch(self, *, cuda=False, mps=False, bf16=True):
        return SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: cuda, is_bf16_supported=Mock(return_value=bf16)),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
            float32="float32", float16="float16", bfloat16="bfloat16")

    def test_qwen_auto_supports_cuda_mps_and_cpu_and_explicit_devices_do_not_fall_back(self):
        for cuda, mps, expected in [(True, False, "cuda:0"), (False, True, "mps"), (False, False, "cpu")]:
            with self.subTest(expected=expected):
                torch = self.torch(cuda=cuda, mps=mps)
                self.assertEqual(select_device(torch, "auto"), expected)
                self.assertEqual(select_device(torch, "cpu"), "cpu")
                RuntimeSettings(tts_device=expected)
        for device in ("mps", "cuda:0"):
            with self.assertRaises(RuntimeError):
                select_device(self.torch(), device)

    def test_mps_and_cpu_never_use_cuda_precision_checks(self):
        torch = self.torch(mps=True)
        for device in ("mps", "cpu"):
            self.assertEqual(model_dtype(torch, device), "float32")
        torch.cuda.is_bf16_supported.assert_not_called()
        self.assertEqual(model_dtype(self.torch(cuda=True, bf16=True), "cuda:0"), "bfloat16")
        self.assertEqual(model_dtype(self.torch(cuda=True, bf16=False), "cuda:0"), "float16")

    @unittest.skipIf(os.name == "nt", "Unix shell launcher")
    def test_launchers_preserve_workspace_with_spaces_and_arguments(self):
        repository = Path(__file__).resolve().parents[1]
        python = self.root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
        python.chmod(0o755)
        for name in ("Podcast-Studio.sh", "Podcast-Studio.command"):
            shutil.copyfile(repository / name, self.root / name)
        for name in ("Podcast-Studio.sh", "Podcast-Studio.command"):
            result = subprocess.run(["sh", str(self.root / name), "--no-browser", "--port", "8766"],
                                    cwd=self.root.parent, capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(result.stdout.splitlines(), ["-m", "podcast_automate", "studio", str(self.root),
                                                          "--no-browser", "--port", "8766"])

    def test_stop_includes_detached_descendants_but_leaves_other_jobs_running(self):
        sleep = "import time; time.sleep(60)"
        parent_code = (
            "import subprocess, sys, os, time\n"
            "child = subprocess.Popen([sys.executable, '-c', sys.argv[1]], start_new_session=os.name != 'nt')\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(60)\n")
        parent = subprocess.Popen([sys.executable, "-c", parent_code, sleep], stdout=subprocess.PIPE,
                                  text=True, start_new_session=os.name != "nt")
        neighbor = subprocess.Popen([sys.executable, "-c", sleep], start_new_session=os.name != "nt")
        self.addCleanup(lambda: stop_process_tree(neighbor) if neighbor.poll() is None else None)
        self.addCleanup(lambda: stop_process_tree(parent) if parent.poll() is None else None)
        self.addCleanup(parent.stdout.close)
        child = psutil.Process(int(parent.stdout.readline()))
        self.addCleanup(lambda: child.kill() if child.is_running() and child.status() != psutil.STATUS_ZOMBIE else None)
        stop_process_tree(parent)
        self.assertIsNotNone(parent.poll())
        self.assertTrue(not child.is_running() or child.status() == psutil.STATUS_ZOMBIE)
        self.assertIsNone(neighbor.poll())

    def test_posix_stop_freezes_ancestors_before_listing_children(self):
        order = []
        parent, child, grandchild = [Mock() for _ in range(3)]
        for name, proc, children in [("parent", parent, [child]), ("child", child, [grandchild]),
                                     ("grandchild", grandchild, [])]:
            proc.suspend.side_effect = lambda name=name: order.append((name, "suspend"))
            proc.children.side_effect = lambda name=name, children=children: order.append((name, "children")) or children
            proc.kill.side_effect = lambda name=name: order.append((name, "kill"))
        owner = Mock(pid=123)
        owner.poll.return_value = None
        with patch("podcast_automate.process.os", SimpleNamespace(name="posix")), \
             patch("psutil.Process", return_value=parent), patch("psutil.wait_procs"):
            stop_process_tree(owner)
        self.assertEqual(order, [("parent", "suspend"), ("parent", "children"),
            ("child", "suspend"), ("child", "children"), ("grandchild", "suspend"),
            ("grandchild", "children"), ("grandchild", "kill"), ("child", "kill"), ("parent", "kill")])
        owner.wait.assert_called_once()
