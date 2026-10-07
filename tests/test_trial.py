"""Trial project („Probelauf“, D-157): small limits that the workspace settings never lift, one short episode, and
every other project exactly as before."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from podcast_automate.cli import main
from podcast_automate.models import TopicBrief
from podcast_automate.question_budget import affordable_tasks
from podcast_automate.script_budget import STAGE_CALLS
from podcast_automate.storage import digest, init_project, load_project, project_hash, read_yaml, write_json, write_yaml
from podcast_automate.trial import TRIAL_LIMITS, TRIAL_MINUTES, TRIAL_SUB_QUESTIONS, TRIAL_TOPICS, trial_brief

GENEROUS = {"model_calls": 1500, "search_rounds": 100, "sources": 400, "cost_usd": 200.0}


class TrialProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.projects = Path(self.temp.name).resolve() / "projects"

    def workspace_limits(self, limits):
        write_json(self.projects / ".studio-settings.json", {"research_limits": limits})

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(args))
        return code, json.loads(output.getvalue())

    def test_a_trial_keeps_its_small_limits_whatever_the_workspace_allows(self):
        root = self.projects / "trial"
        init_project(root, trial_brief(TopicBrief(topic="Ebbe und Flut")))
        # Without workspace settings the trial caps the project's own defaults; no money limit appears from nowhere.
        limits = load_project(root).research_limits
        self.assertEqual((limits.model_calls, limits.search_rounds, limits.sources, limits.cost_usd), (110, 16, 40, None))
        self.workspace_limits(GENEROUS)
        self.assertEqual(load_project(root).research_limits, TRIAL_LIMITS)
        self.assertEqual(load_project(root).research_limits.cost_usd, 45.0)
        # A workspace limit below the trial's stays: each limit is the lower one.
        self.workspace_limits({"model_calls": 50, "search_rounds": 10, "sources": 20, "cost_usd": 5.0})
        limits = load_project(root).research_limits
        self.assertEqual((limits.model_calls, limits.search_rounds, limits.sources, limits.cost_usd), (50, 10, 20, 5.0))
        # Unset, the money limit refuses every billed call; the trial never turns that into an allowance (D-146).
        self.workspace_limits({key: value for key, value in GENEROUS.items() if key != "cost_usd"})
        self.assertIsNone(load_project(root).research_limits.cost_usd)
        # The settings page offers a project's limits as every project's; it reads them without the trial's cap.
        self.workspace_limits(GENEROUS)
        self.assertEqual(load_project(root, trial_caps=False).research_limits.model_calls, 1500)

    def test_a_normal_project_is_unchanged_byte_for_byte(self):
        root = self.projects / "normal"
        init_project(root, TopicBrief(topic="Ebbe und Flut", trial=False))
        raw = (root / "project.yaml").read_bytes()
        data = read_yaml(root / "project.yaml")
        self.assertNotIn("trial", data)
        # The hash is that of exactly what the file holds (unset host names left out, as always), and writing the
        # loaded brief back gives the same bytes.
        self.assertEqual(project_hash(load_project(root)),
                         digest({key: value for key, value in data.items() if not (key == "host_names" and value is None)}))
        write_yaml(root / "project.yaml", load_project(root).model_dump(mode="json"))
        self.assertEqual((root / "project.yaml").read_bytes(), raw)
        # The workspace limits hold as they did before trials existed.
        self.workspace_limits(GENEROUS)
        self.assertEqual(load_project(root).research_limits.model_dump(mode="json"), GENEROUS)

    def test_a_trial_plans_three_sub_questions_with_room_in_its_call_limit(self):
        """The trial's own cap sets the plan (trial.plan_cap): at most three sub-questions, below any requested cap; other
        briefs keep theirs. The call limit carries three at the highest measured mean of 23.4 calls each, plus about 15
        for discovery, planning, advice and closing, so a trial does not stop on it."""
        from podcast_automate.trial import plan_cap
        trial, other = trial_brief(TopicBrief(topic="Ebbe und Flut")), TopicBrief(topic="Ebbe und Flut")
        self.assertEqual((plan_cap(trial), plan_cap(trial, 2), plan_cap(trial, 8)), (3, 2, 3))
        self.assertEqual((plan_cap(other), plan_cap(other, 8)), (None, 8))
        self.assertGreaterEqual(TRIAL_LIMITS.model_calls, round(TRIAL_SUB_QUESTIONS * 23.4) + 15)
        self.assertGreaterEqual(affordable_tasks(15, TRIAL_LIMITS.model_calls), TRIAL_SUB_QUESTIONS)
        # A script run's lower bound for one episode: table of contents, the episode's stages, series review.
        self.assertLessEqual(1 + sum(STAGE_CALLS.values()) + 1, TRIAL_LIMITS.model_calls)

    def test_pla_init_trial_creates_a_small_project_with_a_sample_topic(self):
        code, data = self.invoke("init", str(self.projects / "sample"), "--trial", "--json")
        self.assertEqual(code, 0)
        saved = read_yaml(self.projects / "sample/project.yaml")
        self.assertEqual((saved["trial"], saved["target_total_minutes"], saved["topic"], saved["central_question"]),
                         (True, TRIAL_MINUTES, TRIAL_TOPICS["de-DE"], TRIAL_TOPICS["de-DE"]))
        self.assertEqual(data["project"]["trial"], True)
        self.assertEqual(data["trial"]["limits"]["model_calls"], TRIAL_LIMITS.model_calls)
        self.assertEqual(data["trial"]["sub_questions"], TRIAL_SUB_QUESTIONS)
        self.assertIn("Trial project", data["message"])
        # An own topic stays; a longer wish is cut to one short episode, a shorter one kept.
        _, data = self.invoke("init", str(self.projects / "tides"), "--topic", "Ebbe und Flut", "--total-minutes", "45",
                              "--trial", "--json")
        self.assertEqual((data["project"]["topic"], data["project"]["target_total_minutes"]), ("Ebbe und Flut", 20.0))
        _, data = self.invoke("init", str(self.projects / "short"), "--topic", "Ebbe", "--total-minutes", "12",
                              "--trial", "--json")
        self.assertEqual(data["project"]["target_total_minutes"], 12.0)
        # Without --trial the topic is required, as before.
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as refused:
            main(["init", str(self.projects / "none"), "--json"])
        self.assertEqual(refused.exception.code, 2)
        self.assertFalse((self.projects / "none").exists())
        # A plain init writes no trial key.
        _, data = self.invoke("init", str(self.projects / "plain"), "--topic", "Ebbe", "--json")
        self.assertNotIn("trial", data["project"])
        self.assertNotIn("trial", data)

    def test_the_studio_brief_takes_the_sample_topic_of_its_language_and_the_flag_is_strict(self):
        brief = trial_brief(TopicBrief(topic="Neues Podcast-Projekt", language="en-US"), sample_topic=True)
        self.assertEqual((brief.topic, brief.trial, brief.target_total_minutes),
                         (TRIAL_TOPICS["en-US"], True, TRIAL_MINUTES))
        self.assertEqual(TopicBrief.model_validate(brief.model_dump(mode="json")), brief)
        with self.assertRaises(ValidationError):
            TopicBrief(topic="x", trial="yes")


if __name__ == "__main__":
    unittest.main()
