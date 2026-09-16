import json
import unittest
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.research import run_research, source_context
from podcast_automate.research_models import DossierReview, ReviewIssue, ResearchDiscovery, ResearchDossier, SourceIndex, SourceSection
from podcast_automate.research_quality import QUALITY_VERSION, ResearchAssessment, quality_report, requirements_for
from podcast_automate.research_review import (ROUTING_INSTRUCTIONS, IssueRoute, ReviewRoutes, SourceReview,
    SourceReviewIssue, classify_legacy_review)
from podcast_automate.runner import outputs_valid
from podcast_automate.scripting import load_research
from podcast_automate.storage import digest, read_yaml, write_json, write_yaml
from tests import test_research as fixtures


class ResearchQualityTests(unittest.TestCase):
    def setUp(self):
        fixtures.ResearchTests.setUp(self)
        if self._testMethodName.startswith("test_legacy_") or self._testMethodName == "test_unfinished_legacy_reference_repair_routes_missing_evidence_first":
            # Build actual v1 receipts to verify their replay after an upgrade.
            old_strategy = patch("podcast_automate.research_quality.incremental_round", return_value=False)
            old_strategy.start()
            self.addCleanup(old_strategy.stop)
    model = fixtures.ResearchTests.model

    def test_new_research_checks_every_original_question_not_only_discovery(self):
        self.config.central_question = "The central question"
        self.config.focus_questions = ["An overlooked foundation", "A competing explanation"]
        write_yaml(self.root / "project.yaml", self.config.model_dump())
        seen = []
        def model(prompt, schema, directory, **kwargs):
            if schema is ResearchAssessment:
                seen.extend(json.loads(prompt.splitlines()[-1])["brief"]["requirements"])
            return self.model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual([r["question"] for r in seen], [self.config.central_question, *self.config.focus_questions])
        self.assertEqual(len(seen), 3)
        self.assertTrue(outputs_valid(self.root, run.stages["completeness"]))

    def closing_model(self, prompt, schema, directory, **kwargs):
        if schema is ResearchAssessment:
            result = fixtures.assessment_from_prompt(prompt)
            self.assessments += 1
            if self.assessments == 1:
                result.requirements[0].explanation = False
                result.requirements[0].missing = ["The actual mechanism is missing."]
                result.requirements[0].search_queries = ["energy configurations comparison"]
            return result, {}
        if schema is ResearchDiscovery and kwargs["prompt_version"].endswith(".search"):
            extra = fixtures.discovery()
            extra.candidates[0].url = "https://example.org/second"
            return extra, {"research_performed": True}
        if schema is ResearchDossier and "previous_dossier" in json.loads(prompt.splitlines()[-1]):
            return ResearchDossier.model_validate(json.loads(prompt.splitlines()[-1])["previous_dossier"]), {}
        return self.model(prompt, schema, directory, **kwargs)

    def test_gap_triggers_search_read_synthesis_grounding_and_independent_recheck(self):
        self.assessments = 0
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"<h1>Fixture", ("<h1>" + url + " Fixture").encode()), "text/html", url)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.closing_model) as model:
            run = run_research(self.root)
            used = model.call_count
            resumed = run_research(self.root, resume=True)
        self.assertEqual(run.status, "completed")
        self.assertEqual(resumed.run_id, run.run_id)
        self.assertEqual(model.call_count, used)
        self.assertEqual(self.assessments, 2)
        self.assertEqual(self.download.call_count, 2)
        _, dossier, _, sources, _ = load_research(self.root, self.config)
        self.assertEqual(len(sources.sources), 2)
        report = json.loads((self.root / "reports/research_quality.json").read_text())
        self.assertTrue(report["quality_gate"]["passed"])
        self.assertEqual(report["budget"], {"model_calls": 8, "search_rounds": 2})
        self.assertEqual(report["quality_gate"]["dossier_hash"], digest(dossier.model_dump()))

    def missing_review(self, *, mixed=False):
        issues = [SourceReviewIssue(finding_id="f_energy", reason="The original causal comparison is absent.",
                                   resolution="research", search_queries=["energy original causal comparison full text"])]
        if mixed:
            issues.append(SourceReviewIssue(finding_id="f_energy", reason="Correct the attribution.",
                                            resolution="revise", search_queries=[]))
        return SourceReview(issues=issues, limitations=[])

    def distinct_downloads(self):
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"<h1>Fixture",
            ("<h1>" + url + " Fixture").encode()), "text/html", url)

    def test_initial_missing_evidence_goes_to_search_before_rewrite_or_assessment(self):
        self.assessments = 1  # The final, evidence-based assessment passes.
        self.distinct_downloads()
        events = []
        def model(prompt, schema, directory, **kwargs):
            version = kwargs["prompt_version"]
            events.append(version)
            if version == "research_review.v6":
                return self.missing_review(mixed=True), {}
            if version.endswith(".search"):
                payload = json.loads(prompt.splitlines()[-1])
                self.assertIn("Correct the attribution.", payload["quality_review"]["blocking_gaps"])
                self.assertEqual(payload["quality_review"]["assessment_status"], "pending_after_source_review")
            return self.closing_model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(events[events.index("research_review.v6") + 1], "research_quality.v1.search")
        self.assertEqual(events.count("research_quality.v1.assessment"), 1)
        self.assertFalse(any("repair" in event for event in events))
        report = json.loads((self.root / "reports/research_quality.json").read_text())
        self.assertEqual(report["review"]["issues"], [])
        self.assertEqual(len(report["initial_review"]["issues"]), 2)

    def test_followup_mixed_review_searches_immediately_and_retains_text_corrections(self):
        self.assessments = 0
        self.distinct_downloads()
        events, searches = [], 0
        def model(prompt, schema, directory, **kwargs):
            nonlocal searches
            version = kwargs["prompt_version"]
            events.append(version)
            if version.endswith(".search"):
                searches += 1
                if searches == 2:
                    report = json.loads(prompt.splitlines()[-1])["quality_review"]
                    self.assertEqual(len(report["source_review"]["issues"]), 2)
                    self.assertEqual(report["assessment_round"], 0)
                    extra = fixtures.discovery()
                    extra.candidates[0].url = "https://example.org/third"
                    return extra, {"research_performed": True}
            if version.endswith("grounding_0_routed") and searches == 1:
                return self.missing_review(mixed=True), {}
            return self.closing_model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(self.root)
            calls = len(events)
            resumed = run_research(self.root, resume=True)
        self.assertEqual((run.status, resumed.status), ("completed", "completed"))
        self.assertEqual(len(events), calls)
        self.assertEqual(events[events.index("research_quality.v1.grounding_0_routed") + 1], "research_quality.v1.search")
        self.assertEqual(self.assessments, 2)
        self.assertEqual(self.download.call_count, 3)
        self.assertFalse(any("repair" in event for event in events))

    def test_wording_only_is_repaired_without_unrelated_search(self):
        self.assessments = 0
        self.distinct_downloads()
        events = []
        def model(prompt, schema, directory, **kwargs):
            version = kwargs["prompt_version"]
            events.append(version)
            if version.endswith("grounding_0_routed"):
                return SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason="Correct attribution.",
                    resolution="revise", search_queries=[])], limitations=[]), {}
            return self.closing_model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(events[events.index("research_quality.v1.grounding_0_routed") + 1],
                         "research_patch.v1.grounding")
        self.assertEqual(events.count("research_quality.v1.search"), 1)

    def test_duplicate_search_does_not_rewrite_identical_evidence(self):
        self.assessments = 1
        self.distinct_downloads()
        events, searches = [], 0
        def model(prompt, schema, directory, **kwargs):
            nonlocal searches
            version = kwargs["prompt_version"]
            events.append(version)
            if version == "research_review.v6":
                return self.missing_review(), {}
            if version.endswith(".search"):
                searches += 1
                if searches == 1:
                    return fixtures.discovery(), {"research_performed": True}  # already read URL
            return self.closing_model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(events[3:5], ["research_quality.v1.search", "research_quality.v1.search"])
        self.assertEqual(events.count("research_patch.v1.evidence"), 1)
        self.assertTrue((self.root / "runs" / run.run_id / "completeness/round_001/no_new_evidence.json").exists())

    def test_legacy_checkpoint_routes_once_and_resumes_without_repeating_completed_work(self):
        self._legacy_resume(completed_repair=False)

    def test_legacy_completed_repair_is_preserved_before_routing_latest_review(self):
        self._legacy_resume(completed_repair=True)

    def test_unfinished_legacy_reference_repair_routes_missing_evidence_first(self):
        self._legacy_resume(completed_repair=True, invalid_repair=True)

    def _legacy_resume(self, *, completed_repair, invalid_repair=False):
        self.assessments = 0
        self.distinct_downloads()
        events, captured = [], {}
        pause = True
        def model(prompt, schema, directory, **kwargs):
            nonlocal pause
            version = kwargs["prompt_version"]
            events.append(version)
            if version.endswith(".dossier"):
                captured["dossier_prompt"] = prompt
            if version.endswith("grounding_0_routed") and pause:
                pause = False
                captured["review_prompt"] = prompt.removeprefix(ROUTING_INSTRUCTIONS + "\n")
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            if schema is ReviewRoutes:
                return ReviewRoutes(issues=[IssueRoute(issue_index=0, resolution="research",
                    search_queries=["energy original causal comparison full text"])]), {}
            if version.endswith("grounding_1_routed"):
                return self.missing_review(), {}
            if version.endswith(".search") and "source_review" in json.loads(prompt.splitlines()[-1])["quality_review"]:
                extra = fixtures.discovery()
                extra.candidates[0].url = "https://example.org/third"
                return extra, {"research_performed": True}
            return self.closing_model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            paused = run_research(self.root)
        self.assertEqual(paused.status, "waiting_for_quota")
        folder = self.root / "runs" / paused.run_id / "completeness/round_001"
        legacy = DossierReview(issues=[ReviewIssue(finding_id="f_energy", reason="The original causal comparison is absent.")], limitations=[])
        def checkpoint(name, schema, prompt, value):
            write_json(folder / (name + ".json"), {"input_hash": digest({"prompt": prompt,
                "schema": schema.model_json_schema(), "version": QUALITY_VERSION}),
                "sha256": digest(value), "value": value})
        checkpoint("grounding_0", DossierReview, captured["review_prompt"], legacy.model_dump())
        if completed_repair:
            draft = json.loads((folder / "dossier.json").read_text())["value"]
            prompt = captured["dossier_prompt"] + "\nCorrect the current draft without hiding unresolved evidence gaps:\n" + json.dumps(
                {"draft": draft, "review": legacy.model_dump()}, ensure_ascii=False)
            if invalid_repair:
                draft["findings"][0]["evidence"][0]["excerpt"] = "An invalid quote needing another correction"
            checkpoint("grounding_repair_0", ResearchDossier, prompt, draft)
        before = len(events)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            resumed = run_research(self.root, resume=True)
            calls = len(events)
            self.assertEqual(run_research(self.root, resume=True).status, "completed")
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(len(events), calls)
        suffix = events[before:]
        expected = "research_quality.v1.grounding_1_routed" if completed_repair and not invalid_repair else "research_quality.v1.grounding_routing_0"
        self.assertEqual(suffix[:2], [expected, "research_quality.v1.search"])
        self.assertEqual(self.download.call_count, 3)
        self.assertEqual(events.count("research_quality.v1.dossier"), 2)

    def test_legacy_reference_error_order_migrates_without_regenerating_draft(self):
        self.assessments = 0
        self.distinct_downloads()
        captured = {}
        def model(prompt, schema, directory, **kwargs):
            version = kwargs["prompt_version"]
            if version == "research_quality.v1.dossier":
                payload = json.loads(prompt.splitlines()[-1])
                good = ResearchDossier.model_validate(payload["previous_dossier"])
                captured["good"] = good.model_dump()
                draft = good.model_copy(deep=True)
                second = draft.findings[0].model_copy(deep=True)
                second.id = "f_other"
                sources = json.loads(prompt.splitlines()[-3])["retrieved_sources"]
                other = next(s for s in sources if s["source_id"] != second.evidence[0].reference.split("#")[0])
                second.evidence[0].reference = next(s["reference"] for s in other["sections"] if "Models assign an energy" in s["text"])
                draft.findings.append(second)
                for finding in draft.findings:
                    finding.statement = ("energy " * 160).strip()
                return draft, {}
            if version.endswith(".dossier_references"):
                captured["prompt"] = prompt
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.closing_model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model) as calls:
            paused = run_research(self.root)
            self.assertEqual(paused.status, "waiting_for_quota")
            prefix, payload_text = captured["prompt"].rsplit("\n", 1)
            payload = json.loads(payload_text)
            self.assertEqual(len(payload["errors"]), 2)
            payload["errors"].reverse()
            old_prompt = prefix + "\n" + json.dumps(payload, ensure_ascii=False)
            path = self.root / "runs" / paused.run_id / "completeness/round_001/dossier_references.json"
            old_hash = digest({"prompt": old_prompt, "schema": ResearchDossier.model_json_schema(), "version": QUALITY_VERSION})
            write_json(path, {"input_hash": old_hash, "value": captured["good"], "sha256": digest(captured["good"])})
            resumed = run_research(self.root, resume=True)
        self.assertEqual(resumed.status, "completed", str(resumed.stages["completeness"].error))
        saved = json.loads(path.read_text())
        self.assertEqual(saved["previous_input_hash"], old_hash)
        self.assertEqual(sum(c.kwargs["prompt_version"].endswith(".dossier_references") for c in calls.call_args_list), 1)

    def test_source_gaps_cannot_bypass_search_budget_or_quality_gate(self):
        self.config.research_limits.search_rounds = 1
        write_yaml(self.root / "project.yaml", self.config.model_dump())
        def model(prompt, schema, directory, **kwargs):
            if schema is SourceReview:
                return self.missing_review(), {}
            return self.model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model) as calls:
            run = run_research(self.root)
            count = calls.call_count
            run = run_research(self.root, resume=True)
        self.assertEqual(calls.call_count, count)
        self.assertEqual(run.stages["completeness"].error.code, "research_budget_exhausted")
        self.assertFalse((self.root / "research/latest.json").exists())
        gate = json.loads((self.root / "runs" / run.run_id / "research_quality_gate.json").read_text())
        self.assertFalse(gate["passed"])
        self.assertIn("causal comparison", gate["blocking_gaps"][0])

    def test_legacy_routing_must_account_for_every_original_objection(self):
        _, dossier, _, _, context, _ = self.completed_data()
        review = DossierReview(issues=[ReviewIssue(finding_id="f_energy", reason="First"),
                                      ReviewIssue(finding_id="f_energy", reason="Second")], limitations=[])
        with self.assertRaises(AppError):
            classify_legacy_review(review, dossier, context, lambda p, s: ReviewRoutes(issues=[
                IssueRoute(issue_index=0, resolution="revise", search_queries=[])]))

    def test_pause_after_new_sources_resumes_without_repeating_search_or_download(self):
        self.assessments = 0
        self.distinct_downloads()
        paused = False
        def model(prompt, schema, directory, **kwargs):
            nonlocal paused
            if kwargs["prompt_version"] == "research_patch.v1.evidence" and not paused:
                paused = True
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.closing_model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(self.root)
            self.assertEqual(run.status, "waiting_for_quota")
            self.assertFalse((self.root / "research/latest.json").exists())
            downloads = self.download.call_count
            resumed = run_research(self.root, resume=True)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(self.download.call_count, downloads)
        budget = json.loads((self.root / "runs" / run.run_id / "budget.json").read_text())
        self.assertEqual(budget["search_rounds"], 2)

    def test_limit_keeps_missing_research_blocked_and_resume_preserves_counts(self):
        self.config.research_limits.search_rounds = 1
        write_yaml(self.root / "project.yaml", self.config.model_dump())
        self.assessments = 0
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.closing_model) as model:
            run = run_research(self.root)
            calls = model.call_count
            run = run_research(self.root, resume=True)
        self.assertEqual(run.status, "blocked")
        self.assertEqual(run.stages["completeness"].error.code, "research_budget_exhausted")
        self.assertEqual(model.call_count, calls)
        self.assertFalse((self.root / "research/latest.json").exists())
        self.assertFalse((self.root / "research/research_briefing.md").exists())
        report = json.loads((self.root / "runs" / run.run_id / "research_quality_gate.json").read_text())
        self.assertIn("actual mechanism", report["requirements"][0]["missing"][0])

    def completed_data(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            run = run_research(self.root)
        _, dossier, discovery, index, context = load_research(self.root, self.config)
        assessment = fixtures.assessment_from_prompt(json.dumps({"brief": {"requirements": requirements_for(self.config)}}))
        return run, dossier, discovery, index, context, assessment

    def test_green_review_cannot_hide_partial_coverage_or_unanswered_work(self):
        _, dossier, discovery, index, _, assessment = self.completed_data()
        dossier.coverage[0].status = "partial"
        dossier.coverage[0].gap = "Missing actual source text."
        dossier.open_questions = ["Missing another approved topic."]
        result = quality_report(self.config, dossier, discovery, index, assessment)
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["blocking_gaps"]), 2)

    def test_missing_requirement_or_unknown_finding_cannot_pass(self):
        _, dossier, discovery, index, _, assessment = self.completed_data()
        for broken in (assessment.model_copy(update={"requirements": []}), assessment.model_copy(deep=True)):
            if broken.requirements:
                broken.requirements[0].finding_ids = ["invented"]
            with self.assertRaises(AppError):
                quality_report(self.config, dossier, discovery, index, broken)

    def test_limitation_only_and_upload_only_answers_cannot_pass(self):
        _, dossier, discovery, index, _, assessment = self.completed_data()
        dossier.findings[0].kind = "limitation"
        self.assertFalse(quality_report(self.config, dossier, discovery, index, assessment)["passed"])
        dossier.findings[0].kind = "definition"
        index.sources[0].url = index.sources[0].final_url = ""
        self.assertFalse(quality_report(self.config, dossier, discovery, index, assessment)["passed"])

    def test_preserves_cited_passages_when_selecting_context_for_new_gaps(self):
        _, dossier, discovery, index, _, _ = self.completed_data()
        source = index.sources[0]
        source.sections += [SourceSection(id=f"sec_new_{i}", text=("new gap irrelevant " * 80)) for i in range(120)]
        selected = source_context(index, discovery, retained_dossier=dossier, extra_queries=["new gap irrelevant"])
        self.assertIn(dossier.findings[0].evidence[0].reference, [s["reference"] for d in selected for s in d["sections"]])

    def test_legacy_research_without_scope_gate_cannot_start_plan(self):
        run, *_ = self.completed_data()
        path = self.root / "runs" / run.run_id / "run_manifest.yaml"
        data = read_yaml(path)
        del data["stages"]["completeness"]
        write_yaml(path, data)
        with self.assertRaises(AppError) as error:
            load_research(self.root, self.config)
        self.assertEqual(error.exception.code, "research_coverage_incomplete")

    def test_modified_completed_evidence_blocks_plan(self):
        run, *_ = self.completed_data()
        path = self.root / "runs" / run.run_id / "complete_research/dossier.json"
        path.write_text("changed", encoding="utf-8")
        with self.assertRaises(AppError) as error:
            load_research(self.root, self.config)
        self.assertEqual(error.exception.code, "invalid_research")

    def test_new_incomplete_run_cannot_fall_back_to_previous_passed_dossier(self):
        original, *_ = self.completed_data()
        original_briefing = (self.root / "research/research_briefing.md").read_bytes()
        self.config.research_limits.search_rounds = 1
        write_yaml(self.root / "project.yaml", self.config.model_dump())
        self.assessments = 0
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.closing_model):
            new = run_research(self.root)
        self.assertEqual(new.status, "blocked")
        self.assertEqual(json.loads((self.root / "research/latest.json").read_text())["run_id"], original.run_id)
        self.assertEqual((self.root / "research/research_briefing.md").read_bytes(), original_briefing)
        with self.assertRaises(AppError) as error:
            load_research(self.root, self.config)
        self.assertEqual(error.exception.code, "research_coverage_incomplete")

    def test_extra_focus_question_requires_a_new_quality_assessment(self):
        self.completed_data()
        self.config.focus_questions = ["A new core requirement"]
        with self.assertRaises(AppError) as error:
            load_research(self.root, self.config)
        self.assertEqual(error.exception.code, "inputs_changed")


if __name__ == "__main__":
    unittest.main()
