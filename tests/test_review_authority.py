"""Roles, issue identity, the issue record and the dismissable rule of the review-loop plan (docs/specs/2026-10-04),
and the call baseline of its step 0 (scripts/call-baseline.py)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from podcast_automate.review_authority import gate_holds, issue_id, record_round
from podcast_automate.storage import write_json
from podcast_automate.script_evidence import DRIFT_PREFIX
from podcast_automate.script_models import ScriptIssue, ScriptReview
from podcast_automate.script_pipeline import dismissable
from podcast_automate.text_settings import call_role, stage_effort


class ReviewAuthorityTests(unittest.TestCase):
    def test_an_issue_is_known_by_stage_episode_category_and_segments_not_by_its_wording(self):
        base = issue_id("script_review", "ep_001", "clarity", ["seg_002", "seg_001"])
        self.assertEqual(base, issue_id("script_review", "ep_001", "clarity", ["seg_001", "seg_002", "seg_001"]))
        for other in (("dialogue_polish_review", "ep_001", "clarity", ["seg_001", "seg_002"]),
                      ("script_review", "ep_002", "clarity", ["seg_001", "seg_002"]),
                      ("script_review", "ep_001", "depth", ["seg_001", "seg_002"]),
                      ("script_review", "ep_001", "clarity", ["seg_001"]),
                      ("script_review", "ep_001", "clarity", [])):
            with self.subTest(other=other):
                self.assertNotEqual(base, issue_id(*other))

    def test_the_record_only_grows_and_a_resume_deciding_a_round_again_adds_nothing(self):
        point = {"category": "clarity", "segment_ids": ["seg_001"], "dismissable": True, "status": "blocking",
                 "decided_by": {"role": "A2"}}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "issues.jsonl"
            record_round(path, stage="script_review", episode="ep_001", round_=0, points=[point])
            first = path.read_bytes()
            record_round(path, stage="script_review", episode="ep_001", round_=0, points=[point])
            self.assertEqual(path.read_bytes(), first)
            record_round(path, stage="script_review", episode="ep_001", round_=1,
                         points=[{**point, "status": "note", "decided_by": {"role": "G", "gate": "cap"}}])
            self.assertTrue(path.read_bytes().startswith(first))
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_a_dismissable_point_blocks_only_before_its_loops_first_repair(self):
        self.assertEqual((gate_holds(0), gate_holds(1), gate_holds(3)), (True, False, False))

    def test_only_a_follow_up_point_without_factual_basis_in_a_noted_category_is_dismissable(self):
        """A point that blocks wherever it points today is never dismissable (plan §7.1)."""
        def review(category, basis, reason="r"):
            issue = ScriptIssue(category=category, segment_ids=["seg_001"], reason=reason)
            return ScriptReview(issues=[issue], limitations=[], issue_basis=[basis] if basis else [])
        self.assertTrue(dismissable(review("clarity", "changed"), 0))
        self.assertTrue(dismissable(review("dialogue", "previous"), 0))
        for case in (review("grounding", "changed"), review("scope", "previous"), review("structure", "changed"),
                     review("depth", "factual_error"), review("clarity", "source_contradiction"),
                     review("clarity", None), review("depth", "changed", DRIFT_PREFIX + " seg_001")):
            with self.subTest(issue=case.issues[0], basis=case.issue_basis):
                self.assertFalse(dismissable(case, 0))

    def test_the_role_of_a_call_follows_its_prompt_version(self):
        self.assertEqual([call_role(v) for v in ("listener_readback.v3-terms", "script_review.v12+followup",
                                                  "script_review_repair.v4-limits+a3", "write_episode.v9", None)],
                         ["A1", "A2", "A3", "A2", "A2"])
        # A1 asks at most at medium, as the listener did through STAGE_EFFORT_CAPS; A3 keeps the level it is given.
        self.assertEqual([stage_effort(v, "xhigh") for v in ("listener_readback.v3", "audio_expression.v1",
                                                             "script_review.v12+a3", "teaching_review.v5")],
                         ["medium", "medium", "xhigh", "xhigh"])
        self.assertEqual(stage_effort("listener_readback.v3", "low"), "low")

    def test_the_baseline_counts_calls_by_role_and_review_rounds_by_category(self):
        spec = importlib.util.spec_from_file_location("call_baseline", Path(__file__).resolve().parents[1] /
                                                      "scripts/call-baseline.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        calls = [("script_review.v12", None, {"issues": [{"category": "clarity"}, {"category": "clarity"}],
                                              "advisories": [{"category": "depth"}]}),
                 ("script_review_repair.v4-limits", "A2", {}),
                 ("script_review.v12+followup", "A2", {"issues": [], "advisories": [{"category": "clarity"}]}),
                 ("dialogue_polish_review.v4", "A2", {"checks": [{"criterion": "spoken_language", "verdict": "fail"},
                                                                 {"criterion": "meaning", "verdict": "pass"}]})]
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / "runs" / "run_1"
            for number, (version, role, answer) in enumerate(calls):
                folder = work / "calls" / f"call_{number:03d}"
                write_json(folder / "provider_choice.json", {"prompt_version": version, "provider": "codex_cli",
                                                             **({"role": role} if role else {})})
                write_json(folder / "response.json", answer)
            (row,) = [script.baseline(path) for path in script.run_folders([temp])]
        self.assertEqual((row["calls"], row["repair_calls"]), (4, 1))
        self.assertEqual(row["stages"]["script_review"]["roles"], {"unbekannt": 1, "A2": 1})
        self.assertEqual(row["review_rounds_by_category"], {
            "script_review": {"(rounds)": 2, "clarity": 2, "depth": 1},
            "dialogue_polish_review": {"(rounds)": 1, "spoken_language": 1}})


if __name__ == "__main__":
    unittest.main()
