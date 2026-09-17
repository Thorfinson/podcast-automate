import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.episode_audio import run_episode_audio
from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.series_review import SeriesReview, assess_series, load_series_review
from podcast_automate.scripting import outline_hash, run_script
from podcast_automate.storage import digest, file_hash, read_yaml, write_json, write_yaml
from tests import script_fixtures as fixtures
from tests.series_fixtures import series_response


class SeriesReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.config = TopicBrief(topic="Test topic")
        self.plan = fixtures.example_plan()
        second = self.plan.episodes[0].model_copy(deep=True)
        second.episode_id = "ep_002"
        second.prerequisite_episodes = ["ep_001"]
        self.plan.episodes.append(second)
        self.scripts = [fixtures.example_script(), fixtures.example_script().model_copy(update={"episode_id": "ep_002"})]
        self.calls = 0

    def invoke(self, prompt, schema, version):
        self.calls += 1
        self.assertIs(schema, SeriesReview)
        return series_response(prompt)

    def assess(self, invoke=None, scripts=None):
        return assess_series(self.work, self.config, self.plan, self.scripts if scripts is None else scripts,
                             "input", invoke or self.invoke)

    def test_full_text_collection_is_reviewed_and_resume_reuses_verdict(self):
        self.assess()
        self.assess()
        self.assertEqual(self.calls, 1)
        report = load_series_review(self.work, self.plan, self.scripts, "input")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["episode_ids"], ["ep_001", "ep_002"])
        self.assertFalse(report["human_reviewed"])

    def test_partial_run_never_calls_reviewer_or_claims_complete_series(self):
        self.assess(scripts=self.scripts[:1])
        self.assertEqual(self.calls, 0)
        report = load_series_review(self.work, self.plan, self.scripts[:1], "input")
        self.assertFalse(report["complete"])
        self.assertEqual(report["missing_episodes"], ["ep_002"])
        self.assertEqual(report["status"], "partial")
        self.assess()
        self.assertEqual(self.calls, 1)

    def test_script_change_invalidates_verdict_and_requires_new_review(self):
        self.assess()
        self.scripts[1].segments[0].text += " A revised explanation."
        with self.assertRaises(AppError):
            load_series_review(self.work, self.plan, self.scripts, "input")
        self.assess()
        self.assertEqual(self.calls, 2)

    def test_tampered_receipt_is_blocked_without_call(self):
        self.assess()
        path = self.work / "series_review.json"
        saved = json.loads(path.read_text())
        saved["report"]["status"] = "partial"
        write_json(path, saved)
        with self.assertRaises(AppError):
            self.assess()
        self.assertEqual(self.calls, 1)

    def test_incomplete_checks_invented_quotes_and_missing_episode_evidence_are_rejected(self):
        for defect in ("criterion", "quote", "episode", "missing_evidence"):
            with self.subTest(defect=defect):
                def invalid(prompt, schema, version):
                    review = self.invoke(prompt, schema, version)
                    if defect == "criterion":
                        review.checks[-1].criterion = review.checks[0].criterion
                    elif defect == "quote":
                        review.checks[0].evidence[0].quote = "Invented text"
                    elif defect == "episode":
                        review.checked_episodes.reverse()
                    else:
                        for check in review.checks:
                            check.evidence = check.evidence[:1]
                    return review
                with self.assertRaises(AppError):
                    self.assess(invoke=invalid)
                self.assertFalse((self.work / "series_review.json").exists())

    def test_rejection_is_saved_and_resume_does_not_spend_more_calls(self):
        def rejected(prompt, schema, version):
            review = self.invoke(prompt, schema, version)
            review.checks[-1].verdict = "fail"
            review.checks[-1].reason = "The final episode loses the central question."
            return review
        for _ in range(2):
            with self.assertRaises(AppError) as error:
                self.assess(invoke=rejected)
            self.assertEqual(error.exception.code, "series_review_failed")
            self.assertIn("central question", str(error.exception))
        self.assertEqual(self.calls, 1)


class SeriesWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root

    def test_series_objection_blocks_publication_and_is_retained_on_resume(self):
        def model(*args, **kwargs):
            value, meta = self.fixture.model(*args, **kwargs)
            if isinstance(value, SeriesReview):
                value.checks[0].verdict = "fail"
                value.checks[0].reason = "The agreed question was lost."
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            first = run_script(self.root)
            calls = len(self.fixture.calls)
            second = run_script(self.root, resume=True, run_id=first.run_id)
        self.assertEqual(first.stages["review"].error.code, "series_review_failed")
        self.assertEqual(second.status, "blocked")
        self.assertEqual(len(self.fixture.calls), calls)
        self.assertEqual(second.stages["publish"].status, "pending")
        self.assertFalse((self.root / "episodes/ep_001/script.yaml").exists())

    def test_audio_rejects_missing_series_receipt(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            run = run_script(self.root)
        (self.root / "runs" / run.run_id / "series_review.json").unlink()
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("No synthesis")), self.assertRaises(AppError):
            run_episode_audio(self.root, episode="ep_001", approve_audio=True)

    def test_legacy_approved_run_keeps_its_original_policy_and_reports_no_series_review(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            run = run_script(self.root, plan_only=True)
            work = self.root / "runs" / run.run_id
            inputs = json.loads((work / "inputs.json").read_text())
            inputs.pop("series_review_version")
            write_json(work / "inputs.json", inputs)
            run.input_hash = digest(inputs)
            relative = (work / "inputs.json").relative_to(self.root).as_posix()
            run.stages["planning"].outputs[relative] = file_hash(work / "inputs.json")
            write_yaml(work / "run_manifest.yaml", run.model_dump(mode="json"))
            completed = run_script(self.root, resume=True, run_id=run.run_id, approved_plan_hash=outline_hash(work))
        self.assertEqual(completed.status, "completed")
        self.assertNotIn(SeriesReview, self.fixture.calls)
        report = read_yaml(self.root / "reports/script_quality.yaml")
        self.assertFalse(report["complete_series_review"])
