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


class BoundBriefTests(unittest.TestCase):
    """``storage.bound_brief``: a resumed run hashes its operational fields as its snapshot recorded them."""

    def test_operational_edits_keep_the_hash_and_content_edits_change_it(self):
        from podcast_automate.models import TopicBrief
        started = TopicBrief(topic="Thema", focus_questions=["Warum?"])
        snapshot = started.model_dump(mode="json")
        recorded = storage.project_hash(started)
        operational = started.model_copy(update={
            "runtime": started.runtime.model_copy(update={"text_timeout_seconds": 3600, "codex_model": "gpt-6-astra"}),
            "research_limits": started.research_limits.model_copy(update={"model_calls": 2000})})
        voices = started.model_copy(update={"voice_profile": {"host_a": "Aiden", "host_b": "Vivian"},
                                            "host_names": {"host_a": "Anna", "host_b": "Ben"}})
        content = operational.model_copy(update={"focus_questions": ["Warum?", "Wie?"]})
        for lane in ("research", "script"):
            with self.subTest(lane=lane):
                self.assertEqual(storage.project_hash(storage.bound_brief(started, snapshot, lane)), recorded)
                self.assertEqual(storage.project_hash(storage.bound_brief(operational, snapshot, lane)), recorded)
                self.assertNotEqual(storage.project_hash(storage.bound_brief(content, snapshot, lane)), recorded)
        # Voices and host names are operational only for research, which never reads them; a script speaks them.
        self.assertEqual(storage.project_hash(storage.bound_brief(voices, snapshot, "research")), recorded)
        self.assertNotEqual(storage.project_hash(storage.bound_brief(voices, snapshot, "script")), recorded)
        # The run itself works with today's settings; only the hash is bound.
        self.assertEqual(storage.bound_brief(operational, snapshot, "research").focus_questions, ["Warum?"])
        self.assertEqual(operational.runtime.text_timeout_seconds, 3600)

    def test_snapshots_from_before_host_names_and_missing_snapshots(self):
        from podcast_automate.models import TopicBrief
        started = TopicBrief(topic="Thema")
        old_snapshot = {key: value for key, value in started.model_dump(mode="json").items() if key != "host_names"}
        named = started.model_copy(update={"host_names": {"host_a": "Anna", "host_b": "Ben"}})
        self.assertEqual(storage.project_hash(storage.bound_brief(named, old_snapshot, "research")),
                         storage.project_hash(started))
        self.assertIs(storage.bound_brief(named, None, "research"), named)


if __name__ == "__main__":
    unittest.main()
