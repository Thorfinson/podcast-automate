"""``storage.replace_file``, the rename behind every JSON and YAML write, and ``storage.read_text``, the read
of a file another worker may be renaming onto: both outlast a momentary Windows sharing violation, raise at
once on other platforms, and give up after the deadline with bounded backoff. ``os.replace``, ``time.sleep``
and ``time.monotonic`` are patched, so no file is renamed and nothing sleeps."""
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate import storage


class ReplaceFileTests(unittest.TestCase):
    def test_a_momentary_sharing_violation_on_windows_is_retried_once_the_reader_is_gone(self):
        sleeps = []
        with patch.object(storage.os, "name", "nt"), \
                patch.object(storage.os, "replace", side_effect=[PermissionError(13, "sharing violation"), None]) as replace, \
                patch.object(storage.time, "sleep", side_effect=sleeps.append):
            storage.replace_file("tmp", "target")
        self.assertEqual(replace.call_count, 2)
        self.assertEqual(sleeps, [0.001])

    def test_other_platforms_raise_at_once_without_sleeping(self):
        with patch.object(storage.os, "name", "posix"), \
                patch.object(storage.os, "replace", side_effect=PermissionError(13, "denied")) as replace, \
                patch.object(storage.time, "sleep", side_effect=AssertionError("no retry outside Windows")):
            with self.assertRaises(PermissionError):
                storage.replace_file("tmp", "target")
        self.assertEqual(replace.call_count, 1)

    def test_a_persistent_lock_on_windows_gives_up_after_the_deadline_with_the_backoff_capped(self):
        # The deadline is read at 0.0 s; eight retries fall inside the two seconds, the ninth check is past it.
        clock = iter([0.0, *(0.1 * n for n in range(1, 9)), 2.5])
        sleeps = []
        with patch.object(storage.os, "name", "nt"), \
                patch.object(storage.os, "replace", side_effect=PermissionError(13, "locked")) as replace, \
                patch.object(storage.time, "sleep", side_effect=sleeps.append), \
                patch.object(storage.time, "monotonic", side_effect=lambda: next(clock)):
            with self.assertRaises(PermissionError):
                storage.replace_file("tmp", "target")
        self.assertEqual(replace.call_count, 9)
        # Doubling from one millisecond, never above fifty: a reader that stays is not waited on for long.
        self.assertEqual(sleeps, [0.001, 0.002, 0.004, 0.008, 0.016, 0.032, 0.05, 0.05])


class FlakyPath:
    """A path whose first ``failures`` reads land on the instant of a concurrent rename."""

    def __init__(self, failures, text="{}"):
        self.failures, self.text, self.reads = failures, text, 0

    def read_text(self, encoding):
        self.reads += 1
        if self.reads <= self.failures:
            raise PermissionError(13, "sharing violation")
        return self.text


class ReadTextTests(unittest.TestCase):
    def test_a_read_during_a_concurrent_rename_on_windows_is_retried(self):
        path, sleeps = FlakyPath(1, '{"model_calls": 3}'), []
        with patch.object(storage.os, "name", "nt"), patch.object(storage.time, "sleep", side_effect=sleeps.append):
            self.assertEqual(storage.read_text(path), '{"model_calls": 3}')
        self.assertEqual((path.reads, sleeps), (2, [0.001]))

    def test_other_platforms_and_missing_files_raise_at_once(self):
        path = FlakyPath(1)
        with patch.object(storage.os, "name", "posix"), \
                patch.object(storage.time, "sleep", side_effect=AssertionError("no retry outside Windows")):
            with self.assertRaises(PermissionError):
                storage.read_text(path)
        self.assertEqual(path.reads, 1)
        with patch.object(storage.time, "sleep", side_effect=AssertionError("a missing file is not a lock")):
            with self.assertRaises(FileNotFoundError):
                storage.read_text(Path(__file__).parent / "does-not-exist.json")


if __name__ == "__main__":
    unittest.main()
