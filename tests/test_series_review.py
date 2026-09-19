import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.episode_audio import run_episode_audio, saved_approval
from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript, TopicBrief
from podcast_automate.script_checks import SCRIPT_REVIEW_VERSION
from podcast_automate.script_models import ScriptReview, SeriesPlan
from podcast_automate.series_review import SERIES_REVIEW_VERSION, SeriesReview, assess_series, load_series_review
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


    def test_one_bounded_repair_resolves_a_seeded_contradiction(self):
        rounds = []
        def rejected(prompt, schema, version):
            review = self.invoke(prompt, schema, version)
            rounds.append(len(rounds) + 1)
            if len(rounds) == 1:
                review.checks[2].verdict = "fail"
                review.checks[2].reason = "Episode 2 contradicts the order episode 1 established."
                review.checks[2].evidence = [e for e in review.checks[2].evidence if e.episode_id == "ep_002"]
            return review

        repaired = []
        def repair(grouped):
            repaired.append({key: [i.model_dump() for i in value] for key, value in grouped.items()})
            return self.scripts

        # Without a repair callback the first failure blocks, on one call.
        with self.assertRaises(AppError) as blocked:
            self.assess(invoke=rejected)
        self.assertEqual(blocked.exception.code, "series_review_failed")
        self.assertEqual(len(rounds), 1)
        self.assertEqual(len(repaired), 0)
        # With a repair the same failure is corrected once and the series is re-checked.
        (self.work / "series_review.json").unlink()
        rounds.clear()
        self.calls = 0
        assess_series(self.work, self.config, self.plan, self.scripts, "input", rejected, repair=repair)
        self.assertEqual(len(rounds), 2)
        self.assertEqual(list(repaired[0]), ["ep_002"])
        self.assertEqual(repaired[0]["ep_002"][0]["category"], "structure")
        self.assertIn("progression", repaired[0]["ep_002"][0]["reason"])
        report = json.loads((self.work / "series_review.json").read_text(encoding="utf-8"))["report"]
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["repairs"], 1)

    def test_a_second_failure_blocks_and_a_resume_spends_no_further_call(self):
        def rejected(prompt, schema, version):
            review = self.invoke(prompt, schema, version)
            review.checks[0].verdict = "fail"
            review.checks[0].reason = "The agreed question is still lost."
            return review
        repairs = []
        with self.assertRaises(AppError) as error:
            assess_series(self.work, self.config, self.plan, self.scripts, "input", rejected,
                          repair=lambda grouped: repairs.append(grouped) or self.scripts)
        self.assertEqual(error.exception.code, "series_review_failed")
        self.assertEqual(len(repairs), 1)
        self.assertEqual(self.calls, 2)
        with self.assertRaises(AppError):
            assess_series(self.work, self.config, self.plan, self.scripts, "input", rejected,
                          repair=lambda grouped: self.scripts)
        self.assertEqual(self.calls, 2)

    def test_a_repair_that_fails_midway_is_not_repeated_on_resume(self):
        def rejected(prompt, schema, version):
            review = self.invoke(prompt, schema, version)
            review.checks[2].verdict = "fail"
            review.checks[2].reason = "Episode 2 contradicts the order episode 1 established."
            return review
        rounds = []

        def failing_repair(grouped):
            rounds.append(list(grouped))
            # Episode 1 is already rewritten when episode 2's repair review rejects the round.
            self.scripts[0].segments[-1].text += " A corrected explanation."
            raise AppError("Die Korrektur hat die Belegprüfung nicht bestanden.",
                           code="script_review_failed", status="blocked")

        with self.assertRaises(AppError) as first:
            assess_series(self.work, self.config, self.plan, self.scripts, "input", rejected, repair=failing_repair)
        self.assertEqual(first.exception.code, "script_review_failed")
        self.assertEqual((self.calls, rounds), (1, [["ep_001", "ep_002"]]))
        receipt = json.loads((self.work / "series_repair.json").read_text(encoding="utf-8"))["receipt"]
        self.assertEqual((receipt["repairs"], receipt["episodes"], receipt["failure"]["code"]),
                         (1, ["ep_001", "ep_002"], "script_review_failed"))
        # The saved verdict binds the original texts, so it cannot vouch for the half-repaired ones...
        with self.assertRaises(AppError):
            load_series_review(self.work, self.plan, self.scripts, "input")
        # ...and a resume must neither buy a new verdict nor start a second round.
        for _ in range(2):
            with self.assertRaises(AppError) as resumed:
                assess_series(self.work, self.config, self.plan, self.scripts, "input", rejected, repair=failing_repair)
            self.assertEqual(resumed.exception.code, "script_review_failed")
            self.assertIn("Belegprüfung", str(resumed.exception))
        self.assertEqual((self.calls, len(rounds)), (1, 1))
        # A tampered receipt is refused, again without a call.
        write_json(self.work / "series_repair.json", {"receipt": {**receipt, "failure": None}, "sha256": digest(receipt)})
        with self.assertRaises(AppError) as tampered:
            assess_series(self.work, self.config, self.plan, self.scripts, "input", rejected, repair=failing_repair)
        self.assertEqual(tampered.exception.code, "invalid_series_review")
        self.assertEqual((self.calls, len(rounds)), (1, 1))
        # The receipt belongs to this plan and these inputs; other inputs start with a fresh allowance.
        write_json(self.work / "series_repair.json", {"receipt": receipt, "sha256": digest(receipt)})
        with self.assertRaises(AppError) as other:
            assess_series(self.work, self.config, self.plan, self.scripts, "other", rejected, repair=failing_repair)
        self.assertEqual(other.exception.code, "script_review_failed")
        self.assertEqual((self.calls, len(rounds)), (2, 2))

    def test_a_repair_that_changes_nothing_stops_the_round(self):
        def rejected(prompt, schema, version):
            review = self.invoke(prompt, schema, version)
            review.checks[0].verdict = "fail"
            review.checks[0].reason = "Nothing to attach the correction to."
            return review
        with self.assertRaises(AppError):
            assess_series(self.work, self.config, self.plan, self.scripts, "input", rejected,
                          repair=lambda grouped: None)
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


class SeriesRepairWorkflowTests(unittest.TestCase):
    """The bounded cross-episode repair through the real ``ScriptRun`` on a two-episode plan."""

    SENTENCE = " Episode one established this order first."

    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        self.versions = []

    def two_episode_model(self, *, on_series=None, on_review=None, on_repair=None):
        """The fixture model with a second planned episode; each hook adjusts one kind of call."""
        def model(prompt, output_type, directory, **kwargs):
            self.versions.append(kwargs["prompt_version"])
            value, meta = self.fixture.model(prompt, output_type, directory, **kwargs)
            payload = json.loads(prompt.splitlines()[-1])
            if output_type is SeriesPlan:
                second = value.episodes[0].model_copy(deep=True)
                second.episode_id, second.title = "ep_002", "Further consequences"
                value.episodes.append(second)
            elif output_type is EpisodeScript:
                # Writing, polishing and repairs each carry the episode under a different key.
                episode = payload.get("episode") or payload.get("original_script") or payload.get("draft")
                value.episode_id = episode["episode_id"]
                if kwargs["prompt_version"] == "script_review_repair.v1" and on_repair:
                    on_repair(value, payload)
            elif output_type is ScriptReview and on_review:
                on_review(value, payload)
            elif output_type is SeriesReview and on_series:
                on_series(value, self.versions.count(SERIES_REVIEW_VERSION), directory.parent.parent)
            return value, meta
        return model

    def contradiction(self, review):
        """A seeded progression failure whose evidence names one segment of episode 2 only."""
        review.checks[2].verdict = "fail"
        review.checks[2].reason = "Episode 2 contradicts the order episode 1 established."
        review.checks[2].evidence = [e for e in review.checks[2].evidence if e.episode_id == "ep_002"]

    def test_a_series_repair_publishes_the_repaired_text_with_its_own_review(self):
        before, pre_repair = {}, {}

        def seed(review, call, work):
            if call != 1:
                return
            self.contradiction(review)
            before.update({name: (work / name).read_bytes() for name in
                           ("reviewed/ep_001.json", "reviews/ep_001.json", "reviewed/ep_002.json", "reviews/ep_002.json")})
            # An approval of the text as it stands now must not survive the repair.
            scratch = self.root.parent / "pre_repair_script.yaml"
            write_yaml(scratch, json.loads(before["reviewed/ep_002.json"]))
            pre_repair["hash"] = file_hash(scratch)
            write_yaml(self.root / "episodes/ep_002/audio_review.yaml",
                       {"audio_approved": True, "scripts": {"ep_002": pre_repair["hash"]}})

        def repair(script, payload):
            self.assertEqual(script.episode_id, "ep_002")
            self.assertEqual([i["segment_ids"] for i in payload["review"]["issues"]], [["seg_002"]])
            self.assertTrue(payload["review"]["issues"][0]["reason"].startswith("progression:"))
            script.segments[-1].text += self.SENTENCE

        with patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.two_episode_model(on_series=seed, on_repair=repair)):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        # One repair, one extra episode review and one series re-check; nothing else is repeated.
        self.assertEqual(self.versions.count("script_review_repair.v1"), 1)
        self.assertEqual(self.versions.count(SCRIPT_REVIEW_VERSION), 3)
        self.assertEqual(self.versions.count(SERIES_REVIEW_VERSION), 2)
        work = self.root / "runs" / run.run_id
        published = read_yaml(self.root / "episodes/ep_002/script.yaml")
        self.assertTrue(published["segments"][-1]["text"].endswith(self.SENTENCE))
        quality = read_yaml(self.root / "reports/script_quality.yaml")
        entry = quality["episodes"]["ep_002"]
        self.assertEqual(entry["script_sha256"], file_hash(self.root / "episodes/ep_002/script.yaml"))
        self.assertEqual(entry["model_review"], json.loads((work / "reviews/ep_002.json").read_text(encoding="utf-8")))
        self.assertTrue(entry["model_review"]["claim_checks"][-1]["quote"].endswith(self.SENTENCE))
        self.assertEqual((work / "reviews/ep_002_before_series_repair.json").read_bytes(), before["reviews/ep_002.json"])
        self.assertEqual((quality["series_review"]["status"], quality["series_review"]["repairs"]), ("passed", 1))
        receipt = json.loads((work / "series_repair.json").read_text(encoding="utf-8"))["receipt"]
        self.assertEqual((receipt["repairs"], receipt["episodes"], receipt["failure"]), (1, ["ep_002"], None))
        # The audio decision names the repaired hash; the approval of the earlier text no longer applies.
        self.assertNotEqual(pre_repair["hash"], entry["script_sha256"])
        decision = read_yaml(self.root / "episodes/audio_review.yaml")
        self.assertEqual((decision["audio_approved"], decision["scripts"]["ep_002"]), (False, entry["script_sha256"]))
        self.assertFalse(saved_approval(self.root, "ep_002", entry["script_sha256"], None))
        # The untouched episode keeps its files and its published text.
        for name in ("reviewed/ep_001.json", "reviews/ep_001.json"):
            self.assertEqual((work / name).read_bytes(), before[name])
        self.assertFalse((work / "reviews/ep_001_before_series_repair.json").exists())
        self.assertEqual(read_yaml(self.root / "episodes/ep_001/script.yaml"), fixtures.example_script().model_dump())
        self.assertEqual(quality["episodes"]["ep_001"]["model_review"],
                         json.loads(before["reviews/ep_001.json"]))
        # A resume reuses every verdict.
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=AssertionError("No call on resume")):
            resumed = run_script(self.root, resume=True, run_id=run.run_id)
        self.assertEqual(resumed.status, "completed")

    def test_a_series_repair_with_claim_drift_is_rejected_and_a_resume_spends_nothing(self):
        before = {}

        def seed(review, call, work):
            self.assertEqual(call, 1)
            self.contradiction(review)
            before.update({name: (work / name).read_bytes() for name in ("reviewed/ep_002.json", "reviews/ep_002.json")})

        def drift(review, payload):
            if payload["script"]["segments"][-1]["text"].endswith(self.SENTENCE):
                check = review.claim_checks[-1]
                check.verdict, check.changed_fields = "drift", ["scope"]
                check.reason = "The repaired segment claims more than the finding supports."

        def repair(script, payload):
            script.segments[-1].text += self.SENTENCE

        model = self.two_episode_model(on_series=seed, on_review=drift, on_repair=repair)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual((run.status, run.stages["review"].error.code), ("blocked", "script_review_failed"))
        work = self.root / "runs" / run.run_id
        rejected = work / "reviews/ep_002_series_repair_rejected.json"
        self.assertIn(rejected.relative_to(self.root).as_posix(), run.stages["review"].error.message)
        saved = json.loads(rejected.read_text(encoding="utf-8"))
        self.assertEqual([i["category"] for i in saved["review"]["issues"]], ["grounding"])
        self.assertTrue(saved["draft"]["segments"][-1]["text"].endswith(self.SENTENCE))
        # Neither the reviewed text nor its review changed, and nothing was published.
        for name, content in before.items():
            self.assertEqual((work / name).read_bytes(), content)
        self.assertFalse((work / "reviews/ep_002_before_series_repair.json").exists())
        self.assertFalse((self.root / "episodes/ep_002/script.yaml").exists())
        self.assertEqual((self.versions.count("script_review_repair.v1"), self.versions.count(SERIES_REVIEW_VERSION)), (1, 1))
        receipt = json.loads((work / "series_repair.json").read_text(encoding="utf-8"))["receipt"]
        self.assertEqual((receipt["repairs"], receipt["failure"]["code"]), (1, "script_review_failed"))
        calls = len(self.versions)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            resumed = run_script(self.root, resume=True, run_id=run.run_id)
        self.assertEqual((resumed.status, resumed.stages["review"].error.code), ("blocked", "script_review_failed"))
        self.assertEqual(len(self.versions), calls)
