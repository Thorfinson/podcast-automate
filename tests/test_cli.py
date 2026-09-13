import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.errors import AppError


class CliTests(unittest.TestCase):
    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(args))
        return code, json.loads(output.getvalue())

    def test_init_status_and_exported_schemas(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = str(Path(temporary) / "Projekt ä")
            code, data = self.invoke("init", root, "--topic", "Lernen", "--json")
            self.assertEqual(code, 0)
            self.assertIsNone(data["project"]["target_total_minutes"])
            code, data = self.invoke("status", root, "--json")
            self.assertEqual(data["status"], "not_started")
            schemas = Path(temporary) / "schemas"
            code, _ = self.invoke("schemas", str(schemas), "--json")
            self.assertEqual(code, 0)
            self.assertEqual(len(list(schemas.glob("*.schema.json"))), 20)
            self.assertTrue((schemas / "teaching_plan_repair.schema.json").is_file())

    def test_missing_codex_returns_persisted_blocked_state(self):
        with tempfile.TemporaryDirectory() as root:
            self.invoke("init", root, "--topic", "Thema", "--json")
            with patch("podcast_automate.runner.CodexAdapter.probe",
                       side_effect=AppError("Codex fehlt", code="codex_missing", status="blocked")):
                code, data = self.invoke("text-probe", root, "--json")
            self.assertEqual(code, 1)
            self.assertEqual(data["status"], "blocked")
            _, data = self.invoke("status", root, "--json")
            self.assertEqual(data["run"]["stages"]["codex_probe"]["error"]["code"], "codex_missing")

    def test_invalid_user_configuration_has_no_traceback(self):
        with tempfile.TemporaryDirectory() as root:
            code, data = self.invoke("init", root, "--topic", "   ", "--json")
            self.assertEqual(code, 1)
            self.assertEqual(data["code"], "invalid_configuration")


if __name__ == "__main__":
    unittest.main()
