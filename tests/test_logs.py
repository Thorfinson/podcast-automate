import logging
import tempfile
import unittest
from pathlib import Path

from podcast_automate.logs import (LOGGER, _SECRETS, add_secret, configure_logging, failure_records, log_target, logger,
                                   record_failure, release_logging)
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
    def test_a_release_through_another_spelling_of_the_path_closes_the_handler(self):
        """2026-10-02 review: the worker configured its log under the resolved project path and the caller released it
        under the unresolved one (an 8.3 temp name on Windows CI, /var for /private/var on macOS); the handler stayed
        open, and Windows refused to remove the folder."""
        root = logging.getLogger(LOGGER)
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "sub").mkdir()
            spelled = Path(folder) / "sub" / ".." / "studio.log"
            configure_logging(spelled)
            open_handlers = lambda: [h for h in root.handlers if getattr(h, "pla_target", None) == log_target(spelled)]
            self.assertEqual(len(open_handlers()), 1)
            release_logging(Path(folder).resolve() / "studio.log")
            self.assertEqual(open_handlers(), [])

    def test_file_handler_is_added_once_and_receives_records(self):
        root = logging.getLogger(LOGGER)
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "studio.log"
            configure_logging(log)
            configure_logging(log)
            handlers = [h for h in root.handlers if getattr(h, "pla_target", None) == log_target(log)]
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

    def test_the_anthropic_key_of_the_environment_never_reaches_a_log_line(self):
        import os
        from unittest.mock import patch
        from podcast_automate.logs import scrub
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "plain-anthropic-value-42"}):
            self.assertNotIn("plain-anthropic-value-42", scrub("Aufruf mit plain-anthropic-value-42 fehlgeschlagen"))

    def test_log_file_redacts_credentials_in_message_and_traceback(self):
        """2026-10-04 docs review: worker.log, studio.log and pla.log got full tracebacks unredacted; only the failure
        records were cleaned. A registered key, an OpenRouter key pattern and a bearer token must not reach the file,
        and a line that merely ends like the start of a key keeps its last character."""
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "worker.log"
            add_secret("custom-secret-42")
            configure_logging(log)
            try:
                try:
                    raise RuntimeError("upstream said Bearer hunter2 for sk-or-abcdefghijklmnopqrstu")
                except RuntimeError as exc:
                    logger("worker").error("Auftrag fehlgeschlagen mit custom-secret-42", exc_info=exc)
                logger("worker").info("Ende des Laufs c")
            finally:
                release_logging(log)
                _SECRETS.discard("custom-secret-42")
            text = log.read_text(encoding="utf-8")
        self.assertIn("RuntimeError", text)
        self.assertIn("test_logs.py", text)
        for leaked in ("custom-secret-42", "hunter2", "sk-or-abcdefghijklmnopqrstu"):
            self.assertNotIn(leaked, text)
        self.assertIn("Ende des Laufs c", text)


if __name__ == "__main__":
    unittest.main()
