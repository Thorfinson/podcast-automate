import json
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.episode_audio import reviewed_episode, run_episode_audio, saved_approval
from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript, TopicBrief
from podcast_automate.script_checkpoints import finished
from podcast_automate.script_checks import SCRIPT_REVIEW_VERSION
from podcast_automate.script_models import ScriptReview, SeriesPlan
from podcast_automate.script_pipeline import REVIEW_REPAIR_VERSION
from podcast_automate.series_review import (SERIES_REVIEW_PROMPT, SERIES_REVIEW_VERSION, SeriesReview, assess_series,
                                            load_series_review, require_passing_series, series_criteria)
from podcast_automate.run_budget import approve_fresh_attempts
from podcast_automate.scripting import outline_hash, run_script
from podcast_automate.storage import digest, file_hash, load_project, read_yaml, write_json, write_yaml
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

    def test_a_malformed_review_is_asked_again_with_its_defect_named(self):
        prompts = []
        def invoke(prompt, schema, version):
            prompts.append(prompt)
            review = self.invoke(prompt, schema, version)
            if len(prompts) == 1:
                review.checked_episodes.reverse()
            return review
        self.assess(invoke=invoke)
        self.assertEqual(self.calls, 2)
        self.assertIn("checked_episodes genau in dieser Reihenfolge: ep_001, ep_002", prompts[1])
        self.assertEqual(load_series_review(self.work, self.plan, self.scripts, "input")["status"], "passed")

    def test_a_review_that_stays_malformed_stops_after_two_corrections(self):
        def invalid(prompt, schema, version):
            review = self.invoke(prompt, schema, version)
            review.checked_episodes.reverse()
            return review
        with self.assertRaises(AppError) as caught:
            self.assess(invoke=invalid)
        self.assertEqual((caught.exception.code, caught.exception.status), ("invalid_series_review", "blocked"))
        self.assertEqual(self.calls, 3)
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

    def test_a_round_stopped_before_its_re_check_resumes_instead_of_counting_as_spent(self):
        """Ontologies, 2026-09-29: the user stopped the run while episode 2 was being corrected; the resume only
        repeated the verdict, because a round counted as spent the moment it began."""
        def rejected(prompt, schema, version):
            review = self.invoke(prompt, schema, version)
            if self.calls == 1:
                review.checks[2].verdict = "fail"
                review.checks[2].reason = "Episode 2 contradicts the order episode 1 established."
            return review
        rounds = []

        def stopped(grouped):
            rounds.append(list(grouped))
            raise KeyboardInterrupt  # the worker stopped mid-round: no failure is recorded

        def repaired(grouped):
            rounds.append(list(grouped))
            self.scripts[1].segments[-1].text += " In the order episode 1 established."
            return self.scripts
        with self.assertRaises(KeyboardInterrupt):
            assess_series(self.work, self.config, self.plan, self.scripts, "input", rejected, repair=stopped)
        receipt = json.loads((self.work / "series_repair.json").read_text(encoding="utf-8"))["receipt"]
        self.assertEqual((receipt["repairs"], receipt["failure"]), (1, None))
        # The resume corrects against the saved verdict, without buying it again, and then re-checks.
        assess_series(self.work, self.config, self.plan, self.scripts, "input", rejected, repair=repaired)
        self.assertEqual((len(rounds), self.calls), (2, 2))
        self.assertEqual(load_series_review(self.work, self.plan, self.scripts, "input")["status"], "passed")

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

    def by_criteria(self, prompt, schema, version):
        """The fixture verdict with one passing check for every criterion the call was asked for."""
        review = self.invoke(prompt, schema, version)
        template = review.checks[0]
        review.checks = [template.model_copy(update={"criterion": name}, deep=True)
                         for name in json.loads(prompt.splitlines()[-1])["criteria"]]
        return review

    def saved_report(self, work=None):
        return json.loads(((work or self.work) / "series_review.json").read_text(encoding="utf-8"))["report"]

    def test_a_verdict_with_the_goal_criteria_loads_by_the_criteria_it_was_asked(self):
        """Finding of 2026-10-02: a verdict whose series goal added exposition or guidance passed at generation and was
        refused as invalid_series_review at publish, on every resume and at the audio gate (all three live projects)."""
        for goal, count in (({"understand": 3, "evaluate": 1, "apply": 0}, 7),
                            ({"understand": 2, "evaluate": 1, "apply": 3}, 8)):
            with self.subTest(criteria=count):
                config = TopicBrief(topic="Test topic", series_goal=goal)
                work, self.calls = self.work / str(count), 0
                for _ in range(2):
                    assess_series(work, config, self.plan, self.scripts, "input", self.by_criteria)
                self.assertEqual(self.calls, 1, "the resume reuses the saved verdict")
                report = load_series_review(work, self.plan, self.scripts, "input")
                self.assertEqual((report["status"], report["criteria"]), ("passed", list(series_criteria(config))))
                self.assertEqual(len(report["criteria"]), count)
                require_passing_series(report)
                # A report of 30 September to 2 October has the same checks but does not name its criteria.
                legacy = {key: value for key, value in report.items() if key not in {"criteria", "advisories"}}
                write_json(work / "series_review.json", {"report": legacy, "sha256": digest(legacy)})
                self.assertEqual(load_series_review(work, self.plan, self.scripts, "input")["status"], "passed")
                assess_series(work, config, self.plan, self.scripts, "input", self.by_criteria)
                self.assertEqual(self.calls, 1)

    def test_a_saved_verdict_must_keep_the_five_base_criteria(self):
        self.assess()
        report = self.saved_report()
        for key in ("criteria", None):
            with self.subTest(recorded=key is not None):
                changed = {**report, "review": {**report["review"], "checks": [
                    c for c in report["review"]["checks"] if c["criterion"] != "synthesis"]}}
                if key:
                    changed[key] = [c for c in report["criteria"] if c != "synthesis"]
                else:
                    changed.pop("criteria")
                write_json(self.work / "series_review.json", {"report": changed, "sha256": digest(changed)})
                with self.assertRaises(AppError) as caught:
                    load_series_review(self.work, self.plan, self.scripts, "input")
                self.assertEqual(caught.exception.code, "invalid_series_review")

    def contradiction(self, review, episodes=("ep_002",)):
        review.checks[2].verdict = "fail"
        review.checks[2].reason = "Episode 2 contradicts the order episode 1 established."
        review.checks[2].evidence = [e for e in review.checks[2].evidence if e.episode_id in episodes]

    def test_a_round_that_fails_without_a_verdict_is_not_recorded_and_the_resume_goes_on_with_it(self):
        """Finding of 2026-10-02: any failure inside the correction round was replayed on every resume, so a timeout, a
        quota pause or a provider failure became a permanent block."""
        for error in (AppError("Zeitlimit erreicht.", code="timeout"),
                      AppError("Kontingent erschöpft.", code="quota_exhausted", status="waiting_for_quota"),
                      AppError("Codex ist abgebrochen.", code="codex_failed"),
                      AppError("Modellaufruf angehalten.", code="interrupted", status="interrupted")):
            with self.subTest(code=error.code):
                work, self.calls, rounds = self.work / error.code, 0, []
                scripts = [s.model_copy(deep=True) for s in self.scripts]

                def rejected(prompt, schema, version):
                    review = self.invoke(prompt, schema, version)
                    if self.calls == 1:
                        self.contradiction(review, ("ep_001", "ep_002"))
                    return review

                def failing(grouped):
                    rounds.append(list(grouped))
                    raise error

                def repaired(grouped):
                    rounds.append(list(grouped))
                    scripts[1].segments[-1].text += " In the order episode 1 established."
                    return scripts
                with self.assertRaises(AppError) as caught:
                    assess_series(work, self.config, self.plan, scripts, "input", rejected, repair=failing)
                self.assertIs(caught.exception, error)
                receipt = json.loads((work / "series_repair.json").read_text(encoding="utf-8"))["receipt"]
                self.assertEqual((receipt["repairs"], receipt["failure"], receipt["finished"]), (1, None, False))
                # The resume corrects against the saved verdict without buying it again, then re-checks.
                assess_series(work, self.config, self.plan, scripts, "input", rejected, repair=repaired)
                self.assertEqual((rounds, self.calls), ([["ep_001", "ep_002"], ["ep_001", "ep_002"]], 2))
                self.assertEqual(load_series_review(work, self.plan, scripts, "input")["status"], "passed")

    def test_a_round_partly_adopted_before_a_timeout_goes_on_for_the_uncorrected_episodes_only(self):
        """A parallel round adopts the corrections that passed and then raises the timeout of another episode; the
        resume must neither correct the adopted episode again nor buy a verdict on the half-corrected series."""
        prompts, rounds = [], []

        def answer(prompt, schema, version):
            prompts.append(json.loads(prompt.splitlines()[-1]))
            review = self.invoke(prompt, schema, version)
            if self.calls == 1:
                self.contradiction(review, ("ep_001", "ep_002"))
            return review

        def partly(grouped):
            rounds.append(list(grouped))
            self.scripts[0].segments[-1].text += " Corrected in episode 1."
            raise AppError("Zeitlimit erreicht.", code="timeout")

        def rest(grouped):
            rounds.append(list(grouped))
            self.scripts[1].segments[-1].text += " Corrected in episode 2."
            return self.scripts
        with self.assertRaises(AppError):
            assess_series(self.work, self.config, self.plan, self.scripts, "input", answer, repair=partly)
        assess_series(self.work, self.config, self.plan, self.scripts, "input", answer, repair=rest)
        self.assertEqual(rounds, [["ep_001", "ep_002"], ["ep_002"]])
        self.assertEqual(self.calls, 2)
        self.assertEqual(prompts[1]["changed_episodes"], ["ep_001", "ep_002"])
        progression = next(row for row in prompts[1]["previous_checks"] if row["criterion"] == "progression")
        self.assertEqual((progression["verdict"], progression["outcome"]), ("fail", "corrected"))
        self.assertEqual(load_series_review(self.work, self.plan, self.scripts, "input")["status"], "passed")

    def test_a_failing_check_must_name_its_episodes_and_is_routed_to_them(self):
        """Finding of 2026-10-02: a failing check without quotes gave the correction nothing to work on, and the run
        stopped at once. Such an answer is now asked again until it names the episodes in episode_ids."""
        prompts, repaired = [], []

        def answer(prompt, schema, version):
            prompts.append(prompt)
            review = self.invoke(prompt, schema, version)
            if len(prompts) <= 2:
                check = review.checks[0]
                check.verdict, check.reason, check.evidence = "fail", "The central question is never answered.", []
                if len(prompts) == 2:
                    check.episode_ids = ["ep_002"]
            return review

        def repair(grouped):
            repaired.append({key: [issue.model_dump() for issue in value] for key, value in grouped.items()})
            return self.scripts
        assess_series(self.work, self.config, self.plan, self.scripts, "input", answer, repair=repair)
        self.assertIn("Jede nicht bestandene Serienprüfung muss in episode_ids die Folgen nennen, deren Skript sich "
                      "ändern muss: coverage.", prompts[1])
        self.assertEqual(repaired, [{"ep_002": [{"category": "structure", "segment_ids": [],
                                                 "reason": "coverage: The central question is never answered."}]}])
        self.assertEqual(self.calls, 3, "the rejected answer, the corrected one and the re-check")

    def test_a_source_limit_is_reported_and_never_blocks(self):
        def answer(prompt, schema, version):
            review = self.invoke(prompt, schema, version)
            check = review.checks[1]
            check.verdict, check.evidence, check.episode_ids = "fail", [], ["ep_002"]
            check.reason, check.source_limit = "The sources never define the prior they use.", True
            return review
        assess_series(self.work, self.config, self.plan, self.scripts, "input", answer,
                      repair=lambda grouped: self.fail("a source limit is no correction for the scripts"))
        report = load_series_review(self.work, self.plan, self.scripts, "input")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["advisories"], [{"criterion": "prerequisites", "reason": "The sources never define the "
                                                 "prior they use.", "episode_ids": ["ep_002"], "basis": "source_limit"}])
        self.assertEqual(self.calls, 1)

    def test_the_re_check_knows_the_last_checks_and_blocks_only_on_open_or_changed_points(self):
        """The user's rule for repeated reviews: from the second review on, the scope is set by code and saved before
        the call, and only an earlier objection still open or a new defect in a changed episode blocks."""
        # The first verdict objects to both episodes; the round changes only episode 2.
        cases = (("a new point on an untouched episode", "coverage", "ep_001", "passed"),
                 ("the earlier objection still open on an untouched episode", "progression", "ep_001", "blocked"),
                 ("a new point in the changed episode", "coverage", "ep_002", "blocked"))
        for name, criterion, episode, status in cases:
            with self.subTest(name):
                work, self.calls, payloads = self.work / criterion / episode, 0, []
                scripts = [s.model_copy(deep=True) for s in self.scripts]

                def answer(prompt, schema, version):
                    payloads.append(json.loads(prompt.splitlines()[-1]))
                    review = self.invoke(prompt, schema, version)
                    if self.calls == 1:
                        self.contradiction(review, ("ep_001", "ep_002"))
                    else:
                        check = next(c for c in review.checks if c.criterion == criterion)
                        check.verdict, check.reason = "fail", f"Still unclear in {episode}."
                        check.evidence = [e for e in check.evidence if e.episode_id == episode]
                    return review

                def repair(grouped):
                    self.assertEqual(list(grouped), ["ep_001", "ep_002"])
                    scripts[1].segments[-1].text += " In the order episode 1 established."
                    return scripts
                if status == "passed":
                    assess_series(work, self.config, self.plan, scripts, "input", answer, repair=repair)
                else:
                    with self.assertRaises(AppError) as caught:
                        assess_series(work, self.config, self.plan, scripts, "input", answer, repair=repair)
                    self.assertEqual(caught.exception.code, "series_review_failed")
                    self.assertIn(f"Still unclear in {episode}.", str(caught.exception))
                self.assertNotIn("previous_checks", payloads[0])
                self.assertEqual(payloads[1]["changed_episodes"], ["ep_002"])
                self.assertEqual([(row["criterion"], row.get("outcome")) for row in payloads[1]["previous_checks"]
                                  if row["verdict"] == "fail"], [("progression", "corrected")])
                report = load_series_review(work, self.plan, scripts, "input")
                self.assertEqual(report["status"], status)
                receipt = json.loads((work / "series_repair.json").read_text(encoding="utf-8"))["receipt"]
                self.assertEqual(report["scope"], receipt["scope"])
                if status == "passed":
                    self.assertEqual([(row["criterion"], row["episode_ids"], row["basis"]) for row in report["advisories"]],
                                     [(criterion, [episode], "unchanged")])
                # A resume replays the saved verdict without a call.
                calls = self.calls
                try:
                    assess_series(work, self.config, self.plan, scripts, "input", answer, repair=repair)
                except AppError as error:
                    self.assertEqual(error.code, "series_review_failed")
                self.assertEqual(self.calls, calls)

    def test_a_stop_during_the_re_check_asks_it_again_with_the_saved_scope(self):
        prompts = []

        def answer(prompt, schema, version):
            prompts.append(prompt)
            if len(prompts) == 2:
                raise KeyboardInterrupt  # the worker stopped while the re-check was out
            review = self.invoke(prompt, schema, version)
            if len(prompts) == 1:
                self.contradiction(review)
            return review

        def repair(grouped):
            self.scripts[1].segments[-1].text += " In the order episode 1 established."
            return self.scripts
        with self.assertRaises(KeyboardInterrupt):
            assess_series(self.work, self.config, self.plan, self.scripts, "input", answer, repair=repair)
        self.assertIn("scope", json.loads((self.work / "series_repair.json").read_text(encoding="utf-8"))["receipt"])
        assess_series(self.work, self.config, self.plan, self.scripts, "input", answer,
                      repair=lambda grouped: self.fail("the finished round is not repeated"))
        self.assertEqual(len(prompts), 3)
        self.assertEqual(prompts[2], prompts[1], "the resume asks the re-check the same question")
        self.assertIn("previous_checks", json.loads(prompts[2].splitlines()[-1]))
        self.assertEqual(load_series_review(self.work, self.plan, self.scripts, "input")["status"], "passed")


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
        # Only the user's explicit "Mit neuen Anläufen fortsetzen" grants the objected series another round.
        self.assertTrue(approve_fresh_attempts(self.root, first.run_id)["series_repair"])
        self.assertFalse((self.root / "runs" / first.run_id / "series_repair.json").exists())

    def test_audio_rejects_missing_series_receipt(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            run = run_script(self.root)
        (self.root / "runs" / run.run_id / "series_review.json").unlink()
        with patch("podcast_automate.episode_audio.run_tts", side_effect=AssertionError("No synthesis")), self.assertRaises(AppError):
            run_episode_audio(self.root, episode="ep_001", approve_audio=True)

    def test_a_published_verdict_from_before_the_arc_still_opens_the_audio_gate(self):
        """The Asimov and Ontologies series published on 2026-09-29 carry a v1 verdict with the five earlier criteria and
        no criteria key; validating it against today's six refused their recording (finding of 2026-10-02)."""
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            run = run_script(self.root)
        path = self.root / "runs" / run.run_id / "series_review.json"
        report = json.loads(path.read_text(encoding="utf-8"))["report"]
        legacy = {key: value for key, value in report.items() if key not in {"criteria", "advisories"}}
        legacy["review"] = {**report["review"], "checks": [c for c in report["review"]["checks"] if c["criterion"] != "arc"]}

        def save(saved):
            # Written as that run wrote it: the stage record names the file's hash.
            write_json(path, {"report": saved, "sha256": digest(saved)})
            manifest_file = self.root / "runs" / run.run_id / "run_manifest.yaml"
            manifest = read_yaml(manifest_file)
            relative = path.relative_to(self.root).as_posix()
            for stage in manifest["stages"].values():
                if relative in (stage.get("outputs") or {}):
                    stage["outputs"][relative] = file_hash(path)
            write_yaml(manifest_file, manifest)
        save(legacy)
        config = load_project(self.root)
        _, script, _ = reviewed_episode(self.root, config, "ep_001")
        self.assertEqual(script.episode_id, "ep_001")
        # A verdict without one of the five base criteria is refused.
        broken = {**legacy, "review": {**legacy["review"], "checks": legacy["review"]["checks"][1:]}}
        save(broken)
        with self.assertRaises(AppError) as caught:
            reviewed_episode(self.root, config, "ep_001")
        self.assertEqual(caught.exception.code, "invalid_series_review")

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
                if kwargs["prompt_version"] == REVIEW_REPAIR_VERSION and on_repair:
                    on_repair(value, payload)
            elif output_type is ScriptReview and on_review:
                on_review(value, payload)
            elif output_type is SeriesReview and on_series:
                on_series(value, self.versions.count(SERIES_REVIEW_PROMPT), directory.parent.parent)
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
        self.assertEqual(self.versions.count(REVIEW_REPAIR_VERSION), 1)
        # The review of the correction is scoped to the series issues and the changed segments (+followup).
        self.assertEqual(self.versions.count(SCRIPT_REVIEW_VERSION), 2)
        self.assertEqual(self.versions.count(SCRIPT_REVIEW_VERSION + "+followup"), 1)
        self.assertEqual(self.versions.count(SERIES_REVIEW_PROMPT), 2)
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

    def test_a_resume_after_a_stop_in_the_series_round_keeps_the_adopted_correction(self):
        """2026-09-29: a resume of the review stage rewrote ``reviewed/`` from the episode checkpoints, so the
        series review saw the uncorrected text again and corrected the same episode a second time."""
        def seed(review, call, work):
            if call == 1:
                self.contradiction(review)
            elif call == 2:
                raise KeyboardInterrupt  # stopped while the corrected series was being re-checked

        def repair(script, payload):
            script.segments[-1].text += self.SENTENCE

        with patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.two_episode_model(on_series=seed, on_repair=repair)):
            with self.assertRaises(KeyboardInterrupt):
                run_script(self.root)
        run_id = next(path.parent.name for path in (self.root / "runs").glob("run_*/series_plan.json"))
        work = self.root / "runs" / run_id
        adopted = json.loads((work / "reviewed/ep_002.json").read_text(encoding="utf-8"))
        self.assertTrue(adopted["segments"][-1]["text"].endswith(self.SENTENCE))
        self.assertTrue(finished(work, "ep_002", "review"))
        self.versions.clear()
        with patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.two_episode_model()):
            resumed = run_script(self.root, resume=True, run_id=run_id)
        self.assertEqual(resumed.status, "completed")
        # Only the new verdict on the corrected series: no episode review, no second correction.
        self.assertEqual(self.versions, [SERIES_REVIEW_PROMPT])
        self.assertEqual(json.loads((work / "reviewed/ep_002.json").read_text(encoding="utf-8")), adopted)
        self.assertTrue(read_yaml(self.root / "episodes/ep_002/script.yaml")["segments"][-1]["text"].endswith(self.SENTENCE))

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
        # Two attempts, the second against the check's own objections, before the correction is rejected.
        self.assertEqual((self.versions.count(REVIEW_REPAIR_VERSION), self.versions.count(SERIES_REVIEW_PROMPT)), (2, 1))
        receipt = json.loads((work / "series_repair.json").read_text(encoding="utf-8"))["receipt"]
        self.assertEqual((receipt["repairs"], receipt["failure"]["code"]), (1, "script_review_failed"))
        calls = len(self.versions)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            resumed = run_script(self.root, resume=True, run_id=run.run_id)
        self.assertEqual((resumed.status, resumed.stages["review"].error.code), ("blocked", "script_review_failed"))
        self.assertEqual(len(self.versions), calls)
        # Only the user's "Mit neuen Anläufen fortsetzen" sets the failed round aside; the series is judged anew.
        record = approve_fresh_attempts(self.root, run.run_id)
        self.assertTrue(record["series_repair"])
        self.assertTrue((work / "series_repair_superseded_01.json").exists())
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.two_episode_model()):
            finished = run_script(self.root, resume=True, run_id=run.run_id)
        self.assertEqual(finished.status, "completed", finished.stages["review"].error)
        self.assertEqual(self.versions.count(SERIES_REVIEW_PROMPT), 2)
        self.assertTrue((self.root / "episodes/ep_002/script.yaml").exists())

    def test_a_stop_at_any_model_call_resumes_to_the_same_series_without_asking_again(self):
        """Every stage, stopped at each of its calls: the resume publishes the same two scripts, including the series
        correction, and asks no call again whose answer was saved (the stop of 2026-09-29 that reverted an adopted
        correction was one such point)."""
        def seed(review, call, work):
            if call == 1:
                self.contradiction(review)

        def repair(script, payload):
            script.segments[-1].text += self.SENTENCE

        with patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.two_episode_model(on_series=seed, on_repair=repair)):
            self.assertEqual(run_script(self.root).status, "completed")
        expected = {ep: read_yaml(self.root / "episodes" / ep / "script.yaml") for ep in ("ep_001", "ep_002")}
        self.assertTrue(expected["ep_002"]["segments"][-1]["text"].endswith(self.SENTENCE))
        baseline = list(self.versions)
        for stop_at in range(1, len(baseline) + 1):
            with self.subTest(stop_at=stop_at, version=baseline[stop_at - 1]):
                self.fixture = fixtures.script_project(self)
                root, self.versions, calls = self.fixture.root, [], [0]
                answer = self.two_episode_model(on_series=seed, on_repair=repair)

                def stopping(*args, **kwargs):
                    calls[0] += 1
                    if calls[0] == stop_at:
                        raise KeyboardInterrupt  # the worker stopped while this call was out
                    return answer(*args, **kwargs)
                with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=stopping),                         self.assertRaises(KeyboardInterrupt):
                    run_script(root)
                run_id = next(path.parent.name for path in (root / "runs").glob("run_*/run_manifest.yaml")
                              if read_yaml(path)["kind"] == "script")
                with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=answer):
                    resumed = run_script(root, resume=True, run_id=run_id)
                self.assertEqual(resumed.status, "completed")
                self.assertEqual({ep: read_yaml(root / "episodes" / ep / "script.yaml") for ep in expected}, expected)
                self.assertEqual(self.versions, baseline, "each saved answer is kept; only the stopped call is asked again")

    def test_a_parallel_run_corrects_a_round_at_once_and_adopts_what_passes(self):
        write_json(self.root / "studio/execution.json", {"text": "parallel", "audio": "sequential"})
        # Both first attempts must be in flight together; one after another they never meet at the barrier.
        meeting, first = threading.Barrier(2, timeout=10), set()

        def seed(review, call, work):
            if call == 1:
                review.checks[2].verdict = "fail"
                review.checks[2].reason = "Both episodes contradict the order the series established."
                self.assertEqual({e.episode_id for e in review.checks[2].evidence}, {"ep_001", "ep_002"})

        def repair(script, payload):
            if script.episode_id not in first:
                first.add(script.episode_id)
                meeting.wait()
            script.segments[-1].text += self.SENTENCE

        def drift(review, payload):
            if payload["script"]["episode_id"] == "ep_002" and payload["script"]["segments"][-1]["text"].endswith(self.SENTENCE):
                check = review.claim_checks[-1]
                check.verdict, check.changed_fields = "drift", ["scope"]
                check.reason = "The repaired segment claims more than the finding supports."

        model = self.two_episode_model(on_series=seed, on_review=drift, on_repair=repair)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual((run.status, run.stages["review"].error.code), ("blocked", "script_review_failed"))
        work = self.root / "runs" / run.run_id
        # Episode 1's correction passed and stands; episode 2's was rejected and its report is named.
        self.assertTrue(json.loads((work / "reviewed/ep_001.json").read_text(encoding="utf-8"))
                        ["segments"][-1]["text"].endswith(self.SENTENCE))
        self.assertTrue((work / "reviews/ep_001_series_adopted.json").exists())
        self.assertFalse(json.loads((work / "reviewed/ep_002.json").read_text(encoding="utf-8"))
                         ["segments"][-1]["text"].endswith(self.SENTENCE))
        self.assertIn("reviews/ep_002_series_repair_rejected.json", run.stages["review"].error.message)
        self.assertEqual(self.versions.count(REVIEW_REPAIR_VERSION), 3)

    def test_a_series_correction_the_check_rejects_once_is_adopted_on_its_second_attempt(self):
        """Ontologies ep_008, 2026-09-29: the only attempt restated an absence claim, the check named the fix, and
        the whole series stopped; a second attempt against the check's objections now gets there."""
        attempts = []

        def seed(review, call, work):
            if call == 1:
                self.contradiction(review)

        def repair(script, payload):
            attempts.append(payload["review"]["issues"][0]["reason"])
            script.segments[-1].text += self.SENTENCE

        def first_attempt_drifts(review, payload):
            if payload["script"]["segments"][-1]["text"].endswith(self.SENTENCE) and len(attempts) == 1:
                check = review.claim_checks[-1]
                check.verdict, check.changed_fields = "drift", ["scope"]
                check.reason = "The repaired segment claims more than the finding supports."

        model = self.two_episode_model(on_series=seed, on_review=first_attempt_drifts, on_repair=repair)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(attempts[0].startswith("progression:"))
        self.assertIn("claims more than the finding supports", attempts[1], "the second attempt answers the check")
        work = self.root / "runs" / run.run_id
        self.assertFalse((work / "reviews/ep_002_series_repair_rejected.json").exists())
        self.assertTrue((work / "reviews/ep_002_before_series_repair.json").exists())
