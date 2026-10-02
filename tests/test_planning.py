import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from podcast_automate.errors import AppError
from podcast_automate.research_models import Evidence, Finding, ResearchDossier
from podcast_automate.script_checks import episode_limits, research_limits
from podcast_automate.script_models import Dependency, Omission, ScenePlan, episode_findings
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
        self.assertEqual(resumed.call_args.args[2], "series_plan_repair.v3-core-limits")
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
        self.assertEqual(invoke.call_args.args[2], "series_plan.v7-core-limits")

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
        self.assertEqual(invoke.call_args.args[2], "series_plan_repair.v3-core-limits")
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


def dossier_of(*ids):
    evidence = [Evidence(reference="src_fixture#sec_fixture", excerpt="Synthetic test evidence")]
    return ResearchDossier(topic="Test topic", scope_note="Fixture", coverage=[], open_questions=[],
                           findings=[Finding(id=i, kind="definition", statement=f"Statement {i}.", evidence=evidence)
                                     for i in ids])


def two_episodes():
    plan = example_plan()
    second = plan.episodes[0].model_copy(deep=True)
    second.episode_id, second.prerequisite_episodes = "ep_002", ["ep_001"]
    second.finding_ids, second.scenes[0].finding_ids = ["f_use"], ["f_use"]
    plan.episodes.append(second)
    return plan


class TwoTierPlanTests(unittest.TestCase):
    """2026-10-02: an assembled dossier holds every verified answer (Transformer: 327 findings, about 55 per episode),
    and a plan that had to cover each one pulled the episodes into detail. finding_ids is now the core an episode must
    develop; supporting_finding_ids is detail it may cite."""

    def test_a_finding_is_core_somewhere_supporting_somewhere_or_omitted(self):
        dossier = dossier_of("f_energy", "f_use", "f_detail")
        plan = two_episodes()
        self.assertTrue(any("f_detail" in error and "supporting_finding_ids" in error
                            for error in validate_plan(plan, dossier)), "an unplaced finding is named")
        plan.episodes[1].supporting_finding_ids = ["f_detail"]
        self.assertEqual(validate_plan(plan, dossier), [])
        self.assertEqual(episode_findings(plan.episodes[1]), ["f_use", "f_detail"])
        for wrong in (["f_use"], ["f_invented"]):
            with self.subTest(supporting=wrong):
                broken = plan.model_copy(deep=True)
                broken.episodes[1].supporting_finding_ids = wrong
                self.assertTrue(any("supporting_finding_ids must be known" in e for e in validate_plan(broken, dossier)))
        omitted = plan.model_copy(deep=True)
        omitted.omitted_findings = [Omission(finding_id="f_detail", reason="Detail.")]
        self.assertTrue(any("never both" in e for e in validate_plan(omitted, dossier)), "omitted and placed at once")
        omitted.episodes[1].supporting_finding_ids = []
        self.assertEqual(validate_plan(omitted, dossier), [])

    def test_a_supporting_finding_never_satisfies_a_dependency(self):
        plan = two_episodes()
        plan.episodes[0].finding_ids = plan.episodes[0].scenes[0].finding_ids = ["f_use"]
        plan.episodes[1].finding_ids = plan.episodes[1].scenes[0].finding_ids = ["f_other"]
        plan.episodes[0].supporting_finding_ids = ["f_energy"]
        plan.dependencies = [Dependency(before="f_energy", after="f_use", reason="Define before using.")]
        self.assertIn("Explain f_energy before f_use.", validate_plan(plan, dossier_of("f_energy", "f_use", "f_other")))

    def test_a_plan_without_the_new_fields_dumps_and_validates_as_before(self):
        plan = example_plan()
        entry = plan.episodes[0].model_dump()
        self.assertFalse({"supporting_finding_ids", "research_limit_ids"} & set(entry))
        self.assertEqual(validate_plan(plan, dossier_of("f_energy")), [])
        self.assertEqual(validate_plan(plan, dossier_of("f_energy", "f_extra"))[-1],
                         "Place every finding as core (finding_ids) or supporting (supporting_finding_ids) in an episode, "
                         "or explain its omission, never both. Not placed: f_extra.")

    def test_the_finales_synthesis_scene_may_stand_in_for_its_worked_example(self):
        """2026-10-02: the final episode as a whole is the series' synthesis; a synthesis scene tracing a case through
        the assembled answer counts there as its worked example. Every other episode still needs one."""
        dossier = dossier_of("f_energy", "f_use")
        plan = two_episodes()
        plan.episodes[1].scenes[0].purpose = "synthesis"
        self.assertEqual(validate_plan(plan, dossier), [])
        plan.episodes[0].scenes[0].purpose = "synthesis"
        self.assertEqual(validate_plan(plan, dossier), [
            "ep_001: include a worked_example scene, not just definitions: one concrete case traced step by step "
            "through the idea; a qualitative case without numbers counts."])
        standalone = example_plan()
        standalone.episodes[0].scenes[0].purpose = "synthesis"
        self.assertEqual(len(validate_plan(standalone, dossier_of("f_energy"))), 1, "a standalone episode needs its own")

    def test_a_placed_research_limit_must_be_one_of_the_supplied(self):
        plan = example_plan()
        plan.episodes[0].research_limit_ids = ["limit_002"]
        dossier = dossier_of("f_energy")
        self.assertEqual(validate_plan(plan, dossier), [], "without supplied limits nothing is checked")
        self.assertEqual(validate_plan(plan, dossier, limit_ids=["limit_001", "limit_002"]), [])
        self.assertIn("ep_001: research_limit_ids may only name limit_id values from research_limits.",
                      validate_plan(plan, dossier, limit_ids=["limit_001"]))


class ResearchLimitTests(unittest.TestCase):
    """2026-10-02: the research quality report promises that the script states the limits it noted ("das Skript
    benennt sie"), but the script lane read only whether the gate passed."""

    GATE = {"passed": True, "requirements": [
        {"requirement_id": "rq_001", "question": "Wie entsteht die Energie?", "finding_ids": ["f_energy", "f_unknown"],
         "passed": False, "source_limit": True, "missing": ["Keine Quelle nennt Personenstunden."]},
        {"requirement_id": "rq_002", "question": "Wozu dient sie?", "finding_ids": ["f_use"], "passed": True,
         "missing": [], "noted": ["Nur der Hersteller berichtet die Zahl."]},
        {"requirement_id": "rq_003", "question": "Offen?", "finding_ids": ["f_use"], "passed": False,
         "missing": ["Noch zu recherchieren."]}],
        "noted_limits": ["Die Gegenposition stützt sich auf eine Studie."],
        "noted_after_reworks": [{"task_id": "task_use", "objection": "Die Messung ist nicht unabhängig wiederholt."}],
        "script_notes": ["Die Gegenposition stützt sich auf eine Studie.", "Das Werk liegt nur als Abstract vor."]}

    def test_every_noted_limit_is_read_once_with_the_findings_it_concerns(self):
        dossier = dossier_of("f_energy", "f_use", "task_use__f_rate")
        limits = research_limits(self.GATE, dossier)
        self.assertEqual(limits, [
            {"limit_id": "limit_001", "text": "Keine Quelle nennt Personenstunden.", "finding_ids": ["f_energy"],
             "question": "Wie entsteht die Energie?"},
            {"limit_id": "limit_002", "text": "Nur der Hersteller berichtet die Zahl.", "finding_ids": ["f_use"],
             "question": "Wozu dient sie?"},
            {"limit_id": "limit_003", "text": "Die Gegenposition stützt sich auf eine Studie.", "finding_ids": []},
            {"limit_id": "limit_004", "text": "Die Messung ist nicht unabhängig wiederholt.",
             "finding_ids": ["task_use__f_rate"]},
            {"limit_id": "limit_005", "text": "Das Werk liegt nur als Abstract vor.", "finding_ids": []}])
        # A missing item of a requirement that is not a source limit is open research, not a limit for the script.
        self.assertNotIn("Noch zu recherchieren.", json.dumps(limits, ensure_ascii=False))
        self.assertEqual(research_limits({"passed": True, "requirements": [{"requirement_id": "rq_001"}]}, dossier), [],
                         "a gate written before these keys yields no limits")

    def test_each_limit_reaches_one_episode_where_the_plan_or_its_findings_place_it(self):
        dossier = dossier_of("f_energy", "f_use", "task_use__f_rate")
        limits = research_limits(self.GATE, dossier)
        plan = two_episodes()
        plan.episodes[1].supporting_finding_ids = ["task_use__f_rate"]
        texts = lambda entry: [row["text"] for row in episode_limits(plan, entry, limits)]
        # Unplaced: by the first episode that uses a finding, else the final episode.
        self.assertEqual(texts(plan.episodes[0]), ["Keine Quelle nennt Personenstunden."])
        self.assertEqual(texts(plan.episodes[1]), ["Nur der Hersteller berichtet die Zahl.",
                                                   "Die Gegenposition stützt sich auf eine Studie.",
                                                   "Die Messung ist nicht unabhängig wiederholt.",
                                                   "Das Werk liegt nur als Abstract vor."])
        self.assertEqual(episode_limits(plan, plan.episodes[1], limits)[2],
                         {"text": "Die Messung ist nicht unabhängig wiederholt.", "finding_ids": ["task_use__f_rate"]})
        # The plan's placement wins and keeps the limit out of the episode its findings would give it to.
        plan.episodes[0].research_limit_ids = ["limit_005", "limit_002"]
        self.assertEqual(texts(plan.episodes[0]), ["Keine Quelle nennt Personenstunden.",
                                                   "Nur der Hersteller berichtet die Zahl.",
                                                   "Das Werk liegt nur als Abstract vor."])
        self.assertEqual(episode_limits(plan, plan.episodes[0], limits)[1]["finding_ids"], [],
                         "only the affected findings this episode cites")
        self.assertEqual(len(texts(plan.episodes[1])), 2)


if __name__ == "__main__":
    unittest.main()
