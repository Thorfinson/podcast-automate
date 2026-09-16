import ctypes
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.storage import digest, init_project
from podcast_automate.studio import Studio


@contextmanager
def open_windows_directory(path):
    """A real directory handle without FILE_SHARE_DELETE, as held by some apps."""
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 1, 3, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


class StudioTrashTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.root = self.workspace / "projects/pilot"
        config = TopicBrief(topic="Pilot")
        init_project(self.root, config)
        self.request = {"confirm_id": "pilot", "config_hash": digest(config.model_dump(mode="json"))}
        self.app = Studio(self.workspace)
        (self.root / "cache/audio/a.wav").write_bytes(b"saved audio")
        (self.root / "cache/audio/z.wav").write_bytes(b"second recording")
        self.before = self.files(self.root)

    @staticmethod
    def files(root):
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")
                if path.is_file() and path.name != ".pla.lock"}

    def test_directory_rename_denied_can_still_delete_and_restore_every_file(self):
        rename = Path.rename
        def deny_directories(path, target):
            if path.is_dir():
                raise PermissionError(13, "Directory is open", str(path))
            return rename(path, target)
        rmdir = Path.rmdir
        def keep_original_shells(path):
            if path.is_relative_to(self.root):
                raise PermissionError(13, "Directory is open", str(path))
            return rmdir(path)
        with patch.object(Path, "rename", deny_directories), patch.object(Path, "rmdir", keep_original_shells):
            result = self.app.delete("pilot", self.request)
        self.assertFalse((self.root / "project.yaml").exists())
        self.assertTrue((self.root / "cache/audio").is_dir())
        self.assertEqual(self.files(self.workspace / ".studio/trash" / result["trash_id"] / "project"), self.before)
        self.app.restore({"trash_id": result["trash_id"]})
        self.assertEqual(self.files(self.root), self.before)

    def test_locked_file_rolls_back_and_names_the_file_without_a_partial_trash_entry(self):
        rename = Path.rename
        def deny_file(path, target):
            if path.is_relative_to(self.root) and (path.is_dir() or path.name == "z.wav"):
                raise PermissionError(13, "Open", str(path))
            return rename(path, target)
        with patch.object(Path, "rename", deny_file), self.assertRaises(AppError) as error:
            self.app.delete("pilot", self.request)
        self.assertEqual(error.exception.code, "project_files_locked")
        self.assertIn("z.wav", str(error.exception))
        self.assertEqual(self.files(self.root), self.before)
        self.assertEqual(list((self.workspace / ".studio/trash").iterdir()), [])

    def test_restore_does_not_overwrite_files_added_to_an_empty_shell(self):
        result = self.app.delete("pilot", self.request)
        nested = self.root / "cache/audio"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "new.wav").write_bytes(b"new work")
        with self.assertRaises(AppError) as error:
            self.app.restore({"trash_id": result["trash_id"]})
        self.assertEqual(error.exception.code, "project_exists")
        self.assertEqual((nested / "new.wav").read_bytes(), b"new work")

    @unittest.skipUnless(os.name == "nt", "Windows directory sharing")
    def test_real_windows_directory_handle_allows_delete_and_restore(self):
        with open_windows_directory(self.root / "cache/audio"):
            result = self.app.delete("pilot", self.request)
            self.assertTrue((self.root / "cache/audio").is_dir())
            self.assertFalse((self.root / "project.yaml").exists())
            self.app.restore({"trash_id": result["trash_id"]})
            self.assertEqual(self.files(self.root), self.before)


if __name__ == "__main__":
    unittest.main()
