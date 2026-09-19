import logging
import tempfile
import unittest
from pathlib import Path

from podcast_automate.logs import LOGGER, configure_logging, failure_records, logger, record_failure
from podcast_automate.models import RunManifest, StageRecord
from podcast_automate.runner import execute_stages, status
from podcast_automate.storage import init_project
from podcast_automate.models import TopicBrief


class FailureRecordTests(unittest.TestCase):
    def test_traceback_is_persisted_without_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            try:
                raise ValueError("Bearer hunter2 with sk-or-abcdefghijklmnopqrstu and key=secretvalue")
            except ValueError as exc:
                path = record_failure(root, "writing_1", exc, secrets=("secretvalue",))
            text = path.read_text(encoding="utf-8")
            self.assertIn("ValueError", text)
            self.assertIn("test_logs.py", text)
            for leaked in ("hunter2", "sk-or-abcdefghijklmnopqrstu", "secretvalue"):
                self.assertNotIn(leaked, text)
            self.assertEqual(failure_records(root), [path.name])
            self.assertTrue(path.name.startswith("writing_1_"))

    def test_unexpected_stage_error_keeps_user_message_and_points_to_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            init_project(root, TopicBrief(topic="Thema"))
            manifest = RunManifest(run_id="run_x", kind="text_probe", project_hash="a", input_hash="b",
                                   stages={"codex_probe": StageRecord()})
            path = root / "runs/run_x/run_manifest.yaml"
            path.parent.mkdir(parents=True)

            def broken():
                raise KeyError("missing_field")

            result = execute_stages(root, manifest, path, {"codex_probe": broken})
            record = result.stages["codex_probe"]
            self.assertEqual(record.error.code, "invalid_local_data")
            self.assertIn("Technische Details: runs/run_x/failures/codex_probe_1_", record.error.message)
            records = failure_records(root / "runs/run_x")
            self.assertEqual(len(records), 1)
            self.assertIn("KeyError", (root / "runs/run_x/failures" / records[0]).read_text(encoding="utf-8"))
            (root / "runs/latest.json").write_text('{"run_id": "run_x"}', encoding="utf-8")
            self.assertEqual(status(root)["failure_records"], [f"runs/run_x/failures/{records[0]}"])


class ConfigureLoggingTests(unittest.TestCase):
    def test_file_handler_is_added_once_and_receives_records(self):
        root = logging.getLogger(LOGGER)
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "studio.log"
            configure_logging(log)
            configure_logging(log)
            handlers = [h for h in root.handlers if getattr(h, "pla_target", None) == str(log)]
            self.assertEqual(len(handlers), 1)
            try:
                logger("test").warning("Hallo Protokoll")
                for handler in handlers:
                    handler.flush()
                self.assertIn("WARNING podcast_automate.test: Hallo Protokoll", log.read_text(encoding="utf-8"))
            finally:
                for handler in handlers:
                    root.removeHandler(handler)
                    handler.close()


if __name__ == "__main__":
    unittest.main()
