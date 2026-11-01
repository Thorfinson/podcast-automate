"""Synthetic scripts and a researched project shared by pipeline tests."""
import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.models import Chapter, EpisodeScript, Segment, TopicBrief
from podcast_automate.research import run_research
from podcast_automate.research_models import DossierReview, ResearchDiscovery, ResearchDossier
from podcast_automate.research_quality import ResearchAssessment
from podcast_automate.script_models import EpisodePlan, ScenePlan, ScriptReview, SeriesPlan
from podcast_automate.series_review import SeriesReview
from podcast_automate.storage import init_project
from tests.narration_fixtures import narration_response
from tests.question_fixtures import question_response, script_checks
from tests.series_fixtures import series_response
from tests.research_fixtures import HTML, discovery, dossier_from_prompt, assessment_from_prompt, composed_generation


# A stand-in for the Claude Code CLI: it answers the login check, then acts as the agent would, reading material.json,
# writing plan.md and draft.json, running check.py and critique.py, and printing the session's JSON result. Its draft
# is the fixture's own (fake_draft.json beside it, example_script) where that fits the episode's chapters, else words
# that meet its length.
FAKE_AGENT = r'''
import json, os, subprocess, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    sys.exit(0)
assert "-p" in args, args
assert args[args.index("--tools") + 1] == "Read,Write,Edit,Glob,Grep,Bash", args
allowed = args[args.index("--allowedTools") + 1]
assert "Bash(python check.py:*)" in allowed and "WebSearch" not in allowed and "Bash(*)" not in allowed, allowed
material = json.load(open("material.json", encoding="utf-8"))
length = material["length"]
# The agent writes its target: host_b's seven words per chapter, host_a the rest.
scenes = material["episode"]["scenes"]
words = max(3, -(-(length["target_words"] - 7 * len(scenes)) // len(scenes)))
filler = ["Kerze", "Fenster", "Brücke", "Wolke", "Garten", "Lampe", "Hafen", "Stufe", "Mantel", "Faden"]
segments = []
for number, scene in enumerate(scenes, 1):
    text = " ".join(filler[i % len(filler)] for i in range(words))
    segments.append({"segment_id": f"{scene['scene_id']}_a", "scene_id": scene["scene_id"],
                     "chapter_id": scene["scene_id"], "speaker_id": "host_a", "text": text + ".",
                     "knowledge_refs": scene["finding_ids"]})
    segments.append({"segment_id": f"{scene['scene_id']}_b", "scene_id": scene["scene_id"],
                     "chapter_id": scene["scene_id"], "speaker_id": "host_b",
                     "text": "Darauf wäre ich nie gekommen, erzähl weiter.", "knowledge_refs": []})
draft = {"schema_version": "1.0", "episode_id": material["episode"]["episode_id"], "title": "Eine Probe",
         "purpose": "deep_dive", "chapters": [{"chapter_id": s["scene_id"], "title": s["title"]}
                                              for s in material["episode"]["scenes"]],
         "segments": segments}
given = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_draft.json")
if os.path.isfile(given):
    fixed = json.load(open(given, encoding="utf-8"))
    if fixed["episode_id"] == draft["episode_id"] and [c["chapter_id"] for c in fixed["chapters"]] == [
            c["chapter_id"] for c in draft["chapters"]]:
        draft = fixed
open("plan.md", "w", encoding="utf-8").write("# Plan\n\nThe turn: a probe.\n")
json.dump(draft, open("draft.json", "w", encoding="utf-8"), ensure_ascii=False)
check = subprocess.run([sys.executable, "check.py"], capture_output=True, text=True, encoding="utf-8")
critique = subprocess.run([sys.executable, "critique.py"], capture_output=True, text=True, encoding="utf-8")
json.dump({"check": check.stdout, "critique": critique.stdout}, open("agent_log.json", "w", encoding="utf-8"))
print(json.dumps({"type": "result", "result": "Finished the episode.", "is_error": False, "num_turns": 7,
                  "duration_ms": 1234, "total_cost_usd": 1.5, "usage": {"input_tokens": 10, "output_tokens": 20}}))
'''


def fake_agent(folder, draft=None):
    """The fake CLI in ``folder``; ``draft`` (an EpisodeScript) is what it writes for an episode with its chapters."""
    script = Path(folder) / "fake_claude.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    if draft is not None:
        (Path(folder) / "fake_draft.json").write_text(draft.model_dump_json(), encoding="utf-8")
    return [sys.executable, str(script)]


def example_plan():
    return SeriesPlan(topic="Test topic", central_question="Test topic", explanation_path="Start with a concrete comparison.",
        scope_note="A bounded test plan.", dependencies=[], omitted_findings=[], episodes=[EpisodePlan(
            episode_id="ep_001", title="A model compares possibilities", central_question="How are possibilities compared?",
            target_minutes=0.12, prerequisite_episodes=[], finding_ids=["f_energy"], deferred_questions=["Training remains open."],
            series_role="Shows how a model compares possibilities, the base of the answer.",
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
    """Shared project and model setup; deliberately contains no test methods. Every script run is written by the fake
    story agent (FAKE_AGENT), the only writer since D-184."""
    # The script tests are written against a composed dossier (findings f_energy and so on); a case that sets this
    # researches as a run started now does and plans with the assembled dossier's ids.
    assembled = False

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
             patch("podcast_automate.research.CodexAdapter.structured", side_effect=research_model), \
             (contextlib.nullcontext() if self.assembled else composed_generation()):
            self.research = run_research(self.root)
        self.assertEqual(self.research.status, "completed")
        self.calls = []
        # The agent writes example_script for the fixture's episode, which the audio and export tests read.
        agent = patch("podcast_automate.claude_code.claude_command",
                      return_value=fake_agent(self.root.parent, example_script()))
        agent.start()
        self.addCleanup(agent.stop)

    def model(self, prompt, output_type, directory, **kwargs):
        self.calls.append(output_type)
        self.assertFalse(kwargs["search"])
        if output_type is SeriesReview:
            return series_response(prompt), {}
        if output_type is SeriesPlan:
            return example_plan(), {}
        if output_type is EpisodeScript:
            return example_script(), {}
        narration = narration_response(output_type)
        if narration is not None:
            return narration, {}
        return ScriptReview(issues=[], limitations=["A fixture is not a real editorial review."], claim_checks=script_checks(prompt)), {}


def script_project(test_case):
    """Register teardown before setup so interrupted fixtures are cleaned up too."""
    fixture = ScriptProjectCase()
    test_case.addCleanup(fixture.doCleanups)
    fixture.setUp()
    return fixture
