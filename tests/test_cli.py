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
            self.assertEqual(len(list(schemas.glob("*.schema.json"))), 21)
            self.assertTrue((schemas / "series_review.schema.json").is_file())
            self.assertTrue((schemas / "teaching_plan_repair.schema.json").is_file())

    def test_doctor_can_skip_optional_local_tts_for_remote_audio(self):
        with patch("podcast_automate.cli.inspect", return_value={"ready": True, "checks": []}) as inspect:
            code, _ = self.invoke("doctor", "--skip-tts", "--json")
        self.assertEqual(code, 0)
        self.assertFalse(inspect.call_args.kwargs["include_tts"])

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

    def test_series_review_runs_on_its_own_without_touching_the_reviewed_run(self):
        from podcast_automate.scripting import run_script
        from podcast_automate.storage import file_hash, read_yaml
        from tests import script_fixtures as fixtures
        fixture = fixtures.script_project(self)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=fixture.model):
            script = run_script(fixture.root)
        self.assertEqual(script.status, "completed")
        work = fixture.root / "runs" / script.run_id
        before = {path.relative_to(work).as_posix(): file_hash(path)
                  for path in sorted(work.rglob("*")) if path.is_file()}
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=fixture.model):
            code, data = self.invoke("series-review", str(fixture.root), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(data["status"], "completed")
        self.assertNotEqual(data["run_id"], script.run_id)
        # The reviewed run stays byte-identical; the verdict lands in its own run folder.
        self.assertEqual({path.relative_to(work).as_posix(): file_hash(path)
                          for path in sorted(work.rglob("*")) if path.is_file()}, before)
        report = json.loads((fixture.root / "runs" / data["run_id"] /
                             "series_review.json").read_text(encoding="utf-8"))["report"]
        self.assertEqual(report["status"], "passed")
        quality = read_yaml(fixture.root / "reports/script_quality.yaml")
        self.assertEqual(quality["series_review_run_id"], data["run_id"])
        self.assertTrue(quality["complete_series_review"])
        self.assertEqual(read_yaml(fixture.root / "runs" / data["run_id"] / "run_manifest.yaml")["kind"],
                         "series_review")
        self.assertEqual(json.loads((fixture.root / "runs/latest.json").read_text(encoding="utf-8")),
                         {"run_id": script.run_id})

    def test_series_review_keeps_the_latest_pointer_and_mirrors_only_the_published_run(self):
        from podcast_automate.scripting import run_script
        from podcast_automate.storage import read_yaml
        from tests import script_fixtures as fixtures
        fixture = fixtures.script_project(self)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=fixture.model):
            first = run_script(fixture.root)
            second = run_script(fixture.root, revise="ep_001", feedback="Tighter.")
        self.assertEqual((first.status, second.status), ("completed", "completed"))
        report = fixture.root / "reports/script_quality.yaml"
        before = report.read_bytes()
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=fixture.model):
            code, data = self.invoke("series-review", str(fixture.root), "--run", first.run_id, "--json")
        self.assertEqual((code, data["status"], data["report_mirrored"], data["script_run_id"]),
                         (0, "completed", False, first.run_id))
        self.assertIn("nicht der veröffentlichte Stand", data["message"])
        self.assertEqual(report.read_bytes(), before)
        review_run = fixture.root / "runs" / data["run_id"]
        verdict = json.loads((review_run / "series_review.json").read_text(encoding="utf-8"))["report"]
        self.assertEqual(verdict["status"], "passed")
        # The one call is charged to the review run's own budget.
        self.assertEqual(json.loads((review_run / "budget.json").read_text(encoding="utf-8"))["model_calls"], 1)
        # The latest-run pointer still names the script run, so status and resume dispatch on it.
        self.assertEqual(json.loads((fixture.root / "runs/latest.json").read_text(encoding="utf-8")),
                         {"run_id": second.run_id})
        code, state = self.invoke("status", str(fixture.root), "--json")
        self.assertEqual((code, state["run"]["run_id"], state["run"]["kind"]), (0, second.run_id, "script"))
        calls = len(fixture.calls)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=fixture.model):
            code, resumed = self.invoke("resume", str(fixture.root), "--json")
        self.assertEqual((code, resumed["run"]["run_id"], resumed["run"]["status"]), (0, second.run_id, "completed"))
        self.assertEqual(len(fixture.calls), calls)
        # Without --run the published run is reviewed and that verdict reaches the report.
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=fixture.model):
            code, data = self.invoke("series-review", str(fixture.root), "--json")
        self.assertEqual((code, data["report_mirrored"], data["script_run_id"]), (0, True, second.run_id))
        self.assertIn("reports/script_quality.yaml", data["message"])
        quality = read_yaml(report)
        self.assertEqual((quality["series_review_run_id"], quality["run_id"]), (data["run_id"], second.run_id))
        self.assertEqual(json.loads((fixture.root / "runs/latest.json").read_text(encoding="utf-8")),
                         {"run_id": second.run_id})
        # A review run has nothing to resume; naming it explicitly is refused, not treated as a probe.
        calls = len(fixture.calls)
        code, refused = self.invoke("resume", str(fixture.root), "--run-id", data["run_id"], "--json")
        self.assertEqual((code, refused["code"]), (1, "invalid_run"))
        self.assertEqual(len(fixture.calls), calls)

    def test_research_and_resume_gate_the_plan_unless_the_flag_waives_it_at_the_start(self):
        from unittest.mock import Mock
        from podcast_automate.models import RunManifest
        from podcast_automate.storage import write_json, write_yaml
        with tempfile.TemporaryDirectory() as temporary:
            root = str(Path(temporary) / "Projekt")
            self.invoke("init", root, "--topic", "Thema", "--json")
            manifest = Mock(status="completed")
            manifest.model_dump.return_value = {"run_id": "run_r", "kind": "research", "status": "completed", "stages": {}}
            with patch("podcast_automate.cli.run_research", return_value=manifest) as research:
                self.assertEqual(self.invoke("research", root, "--json")[0], 0)
                self.assertEqual(research.call_args.kwargs["plan_review"], "required")
                self.assertEqual(self.invoke("research", root, "--approve-plan", "--json")[0], 0)
                self.assertEqual(research.call_args.kwargs["plan_review"], "auto")
                write_yaml(Path(root) / "runs/run_r/run_manifest.yaml", RunManifest(
                    run_id="run_r", kind="research", project_hash="0" * 64, input_hash="a" * 64, stages={}).model_dump(mode="json"))
                write_json(Path(root) / "runs/latest.json", {"run_id": "run_r"})
                self.assertEqual(self.invoke("resume", root, "--json")[0], 0)
                # A resume has no flag: only the request saved at the start can waive the gate.
                self.assertEqual((research.call_args.kwargs["resume"], research.call_args.kwargs["plan_review"]), (True, "required"))
            code, data = self.invoke("approve", root, "--research-plan", "--json")
            self.assertEqual((code, data["code"]), (1, "invalid_plan_approval"))
            self.assertIn("noch keinen Rechercheplan", data["message"])
            code, data = self.invoke("approve", root, "--max-tasks", "2", "--json")
            self.assertEqual((code, data["code"]), (1, "invalid_request"))
            # A higher source limit is its own explicit approval for the run.
            code, data = self.invoke("approve", root, "--sources", "200", "--json")
            self.assertEqual((code, data["budget_approval"]["sources"]), (0, 200))

    def test_invalid_user_configuration_has_no_traceback(self):
        with tempfile.TemporaryDirectory() as root:
            code, data = self.invoke("init", root, "--topic", "   ", "--json")
            self.assertEqual(code, 1)
            self.assertEqual(data["code"], "invalid_configuration")


if __name__ == "__main__":
    unittest.main()
