import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from podcast_automate.errors import AppError
from podcast_automate.research_models import Evidence, Finding, ResearchDossier
from podcast_automate.script_models import Dependency, ScenePlan
from podcast_automate.scripting import (checked_series_plan, load_plan_checkpoint,
    plan_dependency_conflicts, validate_plan)
from podcast_automate.storage import write_json
from tests.script_fixtures import example_plan


class PlanningRepairTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        evidence = [Evidence(reference="src_fixture#sec_fixture", excerpt="Synthetic test evidence")]
        self.dossier = ResearchDossier(topic="Test topic", scope_note="Fixture", findings=[
            Finding(id="f_energy", kind="definition", statement="Energy is the score.", evidence=evidence),
            Finding(id="f_use", kind="mechanism", statement="Compare scores.", evidence=evidence)],
            coverage=[], open_questions=[])
        self.good = example_plan()
        episode = self.good.episodes[0]
        episode.finding_ids.append("f_use")
        episode.scenes.append(ScenePlan(scene_id="scene_use", title="Use the foundation", question="How?",
            purpose="explanation", finding_ids=["f_use"], explanation_steps=["Use the score to compare."]))
        self.good.dependencies = [Dependency(before="f_energy", after="f_use", reason="Define before using.")]
        self.bad = self.good.model_copy(deep=True)
        self.bad.episodes[0].scenes.reverse()

    def run_plan(self, invoke, signature="same-input", **kwargs):
        return checked_series_plan(self.work, "Original brief", invoke, self.dossier, "Test topic", signature, **kwargs)

    def test_new_error_after_first_repair_gets_targeted_second_repair(self):
        wrong_ids = self.bad.model_copy(deep=True)
        wrong_ids.dependencies = [Dependency(before="ep_001", after="ep_002", reason="Wrong ID type")]
        invoke = Mock(side_effect=[wrong_ids, self.bad, self.good])
        result = self.run_plan(invoke)
        self.assertEqual(validate_plan(result, self.dossier), [])
        self.assertEqual(invoke.call_count, 3)
        repair = json.loads(invoke.call_args.args[0].splitlines()[-1])
        conflict = repair["dependency_conflicts"][0]
        self.assertEqual(conflict["before_first_introduction"]["scene"], 2)
        self.assertEqual(conflict["after_first_introduction"]["scene"], 1)
        self.assertEqual(conflict["before_statement"], "Energy is the score.")
        self.assertEqual(json.loads((self.work / "plan_errors.json").read_text()), [])

    def test_quota_pause_resumes_latest_draft_without_regenerating(self):
        invoke = Mock(side_effect=[self.bad, self.bad, AppError("Quota", code="quota_exhausted")])
        with self.assertRaises(AppError):
            self.run_plan(invoke)
        saved = json.loads((self.work / "planning_checkpoint.json").read_text())
        self.assertEqual(saved["repairs"], 1)
        resumed = Mock(return_value=self.good)
        self.run_plan(resumed)
        resumed.assert_called_once()
        self.assertEqual(resumed.call_args.args[2], "series_plan_repair.v2")
        self.assertEqual(json.loads(resumed.call_args.args[0].splitlines()[-1])["draft"], saved["draft"])

    def test_persistent_error_does_not_reset_repair_allowance_on_resume(self):
        invoke = Mock(return_value=self.bad)
        with self.assertRaises(AppError) as error:
            self.run_plan(invoke)
        self.assertEqual(error.exception.code, "invalid_plan")
        self.assertIn("A concrete comparison", str(error.exception))
        self.assertIn("Folge 1, Abschnitt 2", str(error.exception))
        self.assertEqual(invoke.call_count, 4)
        with self.assertRaises(AppError):
            self.run_plan(invoke)
        self.assertEqual(invoke.call_count, 4)
        self.assertFalse((self.work / "series_plan.json").exists())

    def test_changed_editorial_instructions_start_a_fresh_draft(self):
        self.run_plan(Mock(return_value=self.good))
        invoke = Mock(return_value=self.good)
        self.run_plan(invoke, signature="revised-brief", allow_legacy=True)
        self.assertEqual(invoke.call_args.args[2], "series_plan.v3-framing")

    def test_legacy_run_recovers_latest_saved_draft_and_used_repairs(self):
        for number, (version, plan) in enumerate([
            ("series_plan.v3-framing", self.good), ("series_plan_repair.v1", self.bad)], 1):
            folder = self.work / "calls" / f"call_{number:03d}"
            write_json(folder / "metadata.json", {"prompt_version": version})
            write_json(folder / "response.json", plan.model_dump())
        self.assertEqual(load_plan_checkpoint(self.work, "same-input"), (None, 0))
        plan, repairs = load_plan_checkpoint(self.work, "same-input", allow_legacy=True)
        self.assertEqual(plan, self.bad)
        self.assertEqual(repairs, 1)
        invoke = Mock(return_value=self.good)
        self.run_plan(invoke, allow_legacy=True)
        invoke.assert_called_once()
        self.assertEqual(invoke.call_args.args[2], "series_plan_repair.v2")
        self.assertEqual(json.loads((self.work / "planning_checkpoint.json").read_text())["repairs"], 2)

    def test_later_recap_does_not_hide_earlier_forward_reference(self):
        plan = self.bad.model_copy(deep=True)
        recap = self.good.episodes[0].model_copy(deep=True)
        recap.episode_id = "ep_002"
        plan.episodes.append(recap)
        conflicts = plan_dependency_conflicts(plan, self.dossier)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["after_first_introduction"]["episode"], 1)
        self.assertIn("Explain f_energy before f_use.", validate_plan(plan, self.dossier))

    def test_coverage_and_central_question_are_still_checked_on_every_repair(self):
        plan = self.good.model_copy(deep=True)
        plan.central_question = "Changed question"
        plan.episodes[0].scenes[0].finding_ids = ["invented"]
        invoke = Mock(side_effect=[self.bad, plan, self.good])
        self.run_plan(invoke)
        repair = json.loads(invoke.call_args.args[0].splitlines()[-1])
        self.assertIn("Keep the project's central_question unchanged.", repair["errors"])
        self.assertTrue(any("exactly" in item for item in repair["errors"]))


if __name__ == "__main__":
    unittest.main()
