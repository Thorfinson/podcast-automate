"""Synthetic scripts and a researched project shared by pipeline tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.models import Chapter, EpisodeScript, Segment, TopicBrief
from podcast_automate.polishing import DialoguePolishReview
from podcast_automate.research import run_research
from podcast_automate.research_models import DossierReview, ResearchDiscovery, ResearchDossier
from podcast_automate.research_quality import ResearchAssessment
from podcast_automate.script_models import EpisodePlan, ScenePlan, ScriptReview, SeriesPlan
from podcast_automate.series_review import SeriesReview
from podcast_automate.storage import init_project
from podcast_automate.teaching import (TeachingPlan, TeachingPlanReview, TeachingPlanRepair,
    ListenerReadback, TeachingReview, EditorialReview)
from tests.polishing_fixtures import polish_review
from tests.question_fixtures import question_response, script_checks
from tests.series_fixtures import series_response
from tests.teaching_fixtures import teaching_response
from tests.research_fixtures import HTML, discovery, dossier_from_prompt, assessment_from_prompt


def example_plan():
    return SeriesPlan(topic="Test topic", central_question="Test topic", explanation_path="Start with a concrete comparison.",
        scope_note="A bounded test plan.", dependencies=[], omitted_findings=[], episodes=[EpisodePlan(
            episode_id="ep_001", title="A model compares possibilities", central_question="How are possibilities compared?",
            target_minutes=0.12, prerequisite_episodes=[], finding_ids=["f_energy"], deferred_questions=["Training remains open."],
            scenes=[ScenePlan(scene_id="scene_example", title="A concrete comparison", question="What is scored?",
                purpose="worked_example", finding_ids=["f_energy"], explanation_steps=["Compare two possibilities.", "Explain the limit."])])])


def example_script():
    return EpisodeScript(episode_id="ep_001", title="A model compares possibilities", purpose="deep_dive",
        chapters=[Chapter(chapter_id="scene_example", title="A concrete comparison")],
        segments=[Segment(segment_id="seg_001", scene_id="scene_example", chapter_id="scene_example",
                          speaker_id="host_a", text="What does this model compare?", knowledge_refs=["f_energy"]),
                  Segment(segment_id="seg_002", scene_id="scene_example", chapter_id="scene_example",
                          speaker_id="host_b", text="It scores possibilities. Here a lower score represents a better fit.", knowledge_refs=["f_energy"])])


class ScriptProjectCase(unittest.TestCase):
    """Shared project and model setup; deliberately contains no test methods."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Projekt mit Leerzeichen"
        self.config = TopicBrief(topic="Test topic", voice_profile={"host_a": "Aiden", "host_b": "Vivian"})
        init_project(self.root, self.config)

        def research_model(prompt, output_type, directory, **kwargs):
            question = question_response(prompt, output_type)
            if question is not None:
                return question, {}
            if output_type is ResearchDiscovery:
                return discovery(), {"research_performed": True}
            if output_type is ResearchDossier:
                payload = json.loads(prompt.splitlines()[-1])
                return dossier_from_prompt(json.dumps({"topic": "Test topic", "retrieved_sources": payload["sources"]})), {}
            if output_type is ResearchAssessment:
                return assessment_from_prompt(prompt), {}
            return DossierReview(issues=[], limitations=[]), {}

        with patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")), \
             patch("podcast_automate.research.CodexAdapter.structured", side_effect=research_model):
            self.research = run_research(self.root)
        self.assertEqual(self.research.status, "completed")
        self.calls = []

    def model(self, prompt, output_type, directory, **kwargs):
        self.calls.append(output_type)
        self.assertFalse(kwargs["search"])
        if output_type is SeriesReview:
            return series_response(prompt), {}
        if output_type is DialoguePolishReview:
            return polish_review(prompt), {}
        if output_type in (TeachingPlan, TeachingPlanReview, TeachingPlanRepair, ListenerReadback, TeachingReview, EditorialReview):
            return teaching_response(prompt, output_type), {}
        if output_type is SeriesPlan:
            return example_plan(), {}
        if output_type is EpisodeScript:
            return example_script(), {}
        return ScriptReview(issues=[], limitations=["A fixture is not a real editorial review."], claim_checks=script_checks(prompt)), {}


def script_project(test_case):
    """Register teardown before setup so interrupted fixtures are cleaned up too."""
    fixture = ScriptProjectCase()
    test_case.addCleanup(fixture.doCleanups)
    fixture.setUp()
    return fixture
