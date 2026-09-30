"""Episode roles, recalled findings, the source spot-check and the series review's arc (2026-09-30).

The Asimov series announced its path once and never again, its finale was mostly new material, a wrong finding
about Dalio passed every review, and the series review passed "progression" and "synthesis" anyway."""
import unittest

from podcast_automate.editorial import episode_series_context
from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.script_checks import validate_plan, validate_script
from podcast_automate.script_evidence import receipt_defects, settle_receipts, validate_claim_checks
from podcast_automate.script_models import ScriptReview, episode_findings
from podcast_automate.series_review import CRITERIA, series_criteria, validate_review, SeriesReview
from tests import script_fixtures as fixtures


def two_episodes():
    plan = fixtures.example_plan()
    second = plan.episodes[0].model_copy(deep=True)
    second.episode_id, second.prerequisite_episodes = "ep_002", ["ep_001"]
    second.finding_ids, second.scenes[0].finding_ids = ["f_other"], ["f_other"]
    second.series_role = "Assembles the comparison into the answer."
    plan.episodes.append(second)
    return plan


class PlanTests(unittest.TestCase):
    def test_every_episode_states_its_role_and_recalls_only_earlier_findings(self):
        plan = two_episodes()
        known = type("Dossier", (), {"topic": plan.topic, "findings": [type("F", (), {"id": i})() for i in ("f_energy", "f_other")]})()
        self.assertEqual(validate_plan(plan, known), [])
        plan.episodes[0].series_role = " "
        self.assertTrue(any("series_role" in e for e in validate_plan(plan, known)))
        plan.episodes[0].series_role = "Shows the comparison."
        plan.episodes[1].recap_finding_ids = ["f_energy"]
        self.assertEqual(validate_plan(plan, known), [], "the finale may recall an earlier episode's finding")
        plan.episodes[0].recap_finding_ids = ["f_other"]
        self.assertTrue(any("recap_finding_ids" in e for e in validate_plan(plan, known)), "never a later one")

    def test_a_recalled_finding_may_be_cited_without_every_one_being_covered(self):
        plan = two_episodes()
        finale = plan.episodes[1]
        finale.recap_finding_ids = ["f_energy", "f_extra"]
        self.assertEqual(episode_findings(finale), ["f_other", "f_energy", "f_extra"])
        script = fixtures.example_script().model_copy(deep=True)
        script.episode_id = "ep_002"
        script.segments[0].knowledge_refs = ["f_other"]
        script.segments[1].knowledge_refs = ["f_energy"]
        self.assertEqual(validate_script(script, finale, check_duration=False), [], "f_extra need not be cited")
        script.segments[0].knowledge_refs = ["f_energy"]
        self.assertIn("The dialogue must cover every planned finding.", validate_script(script, finale, check_duration=False))

    def test_the_series_context_names_each_episodes_role_only_when_set(self):
        plan = two_episodes()
        context = episode_series_context(plan, plan.episodes[1])
        self.assertEqual(context["episode_path"][1]["series_role"], "Assembles the comparison into the answer.")
        plan.episodes[1].series_role = ""
        self.assertNotIn("series_role", episode_series_context(plan, plan.episodes[1])["episode_path"][1])
        self.assertNotIn("series_role", plan.episodes[1].model_dump(), "a plan from before roles dumps as before")


class SourceSpotCheckTests(unittest.TestCase):
    def check(self, **fields):
        return {"segment_id": "seg_002", "finding_ids": ["f_energy"], "verdict": "preserved",
                "quote": "It scores possibilities.", "reason": "Matches.", "changed_fields": [], **fields}

    def test_a_preserved_segment_names_the_supplied_sections_it_was_compared_with(self):
        segment = fixtures.example_script().segments[1]
        review = ScriptReview.model_validate({"issues": [], "limitations": [], "claim_checks": [self.check()]})
        sections = {"src_a#sec_1", "src_a#sec_2"}
        missing = receipt_defects(review.claim_checks[0], segment, {"f_energy"}, sections)
        self.assertTrue(any("source_refs" in d for d in missing))
        unknown = receipt_defects(review.claim_checks[0].model_copy(update={"source_refs": ["src_x#sec_9"]}),
                                  segment, {"f_energy"}, sections)
        self.assertTrue(any("not supplied" in d for d in unknown))
        self.assertEqual(receipt_defects(review.claim_checks[0].model_copy(update={"source_refs": ["src_a#sec_2"]}),
                                         segment, {"f_energy"}, sections), [])
        # Without supplied sections (older dossiers) nothing more is asked.
        self.assertEqual(receipt_defects(review.claim_checks[0], segment, {"f_energy"}, None), [])

    def test_a_segment_read_as_preserved_by_settling_carries_its_findings_anchors(self):
        script = fixtures.example_script()
        review = ScriptReview.model_validate({"issues": [], "limitations": [], "claim_checks": [
            self.check(segment_id="seg_001", quote="What does this model compare?"),
            self.check(verdict="no_research_claim")]})
        settled = settle_receipts(review, script, {"f_energy": ["src_a#sec_1"]})
        self.assertEqual([c.source_refs for c in settled.claim_checks], [[], ["src_a#sec_1"]])

    def test_a_finding_that_misstates_its_source_is_drift_on_the_source_field(self):
        script = fixtures.example_script()
        findings = [type("F", (), {"id": "f_energy"})()]
        review = ScriptReview.model_validate({"issues": [], "limitations": [], "claim_checks": [
            self.check(segment_id="seg_001", quote="What does this model compare?", source_refs=["src_a#sec_1"]),
            self.check(verdict="drift", changed_fields=["source"], source_refs=["src_a#sec_1"],
                       reason="The section names a typical length; the finding says it names none.")]})
        issues = validate_claim_checks(review, script, findings, sections={"src_a#sec_1"})
        self.assertEqual([(i.category, i.segment_ids) for i in issues], [("grounding", ["seg_002"])])


class SeriesArcTests(unittest.TestCase):
    def test_the_arc_is_always_checked_and_exposition_and_guidance_by_the_goal(self):
        self.assertIn("arc", CRITERIA)
        self.assertEqual(series_criteria(TopicBrief(topic="Unset")), CRITERIA)
        understand = TopicBrief(topic="Psychohistory", series_goal={"understand": 3, "evaluate": 1, "apply": 0})
        build = TopicBrief(topic="Knowledge bases", series_goal={"understand": 2, "evaluate": 1, "apply": 3})
        self.assertEqual(series_criteria(understand), (*CRITERIA, "exposition"))
        self.assertEqual(series_criteria(build), (*CRITERIA, "exposition", "guidance"))

    def test_a_review_without_a_criterion_the_goal_asks_for_is_refused(self):
        scripts = [fixtures.example_script()]
        quote = scripts[0].segments[1].text
        checks = [{"criterion": c, "verdict": "pass", "reason": "Holds.",
                   "evidence": [{"episode_id": "ep_001", "segment_id": "seg_002", "quote": quote}]} for c in CRITERIA]
        review = SeriesReview.model_validate({"checked_episodes": ["ep_001"], "checks": checks, "warnings": []})
        validate_review(review, scripts)
        understand = TopicBrief(topic="Psychohistory", series_goal={"understand": 3, "evaluate": 1, "apply": 0})
        with self.assertRaises(AppError) as missing:
            validate_review(review, scripts, series_criteria(understand))
        self.assertIn("exposition", str(missing.exception))


if __name__ == "__main__":
    unittest.main()
