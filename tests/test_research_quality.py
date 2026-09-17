import json
import unittest
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.research import run_research, source_context
from podcast_automate.research_models import SourceSection
from podcast_automate.research_quality import ResearchAssessment, quality_report, requirements_for
from podcast_automate.runner import outputs_valid
from podcast_automate.scripting import load_research
from podcast_automate.storage import read_yaml, write_yaml
from tests import research_fixtures as fixtures


class ResearchQualityTests(fixtures.ResearchProjectCase):

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
        def blocked(*args, **kwargs):
            raise AppError("Missing sources", code="research_coverage_incomplete", status="blocked")
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=blocked):
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
