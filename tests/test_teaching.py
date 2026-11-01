"""What a script run's review knows of the episodes before an episode (teaching.prerequisite_context).

Until D-184 this module also tested the teaching design and the teaching assessment of a draft; both went with the
writing prompt. What an earlier episode actually said, the story agent reads in its published text (test_story_agent).
"""
import tempfile
import unittest
from pathlib import Path

from podcast_automate.teaching import prerequisite_context, project_terminology
from podcast_automate.storage import write_json
from tests import script_fixtures as fixtures


class PrerequisiteContextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)

    def series(self, prerequisites):
        plan = fixtures.example_plan()
        for index in range(2, len(prerequisites) + 2):
            entry = plan.episodes[0].model_copy(deep=True)
            entry.episode_id, entry.series_role = f"ep_{index:03d}", f"Episode {index} adds its part."
            entry.prerequisite_episodes = prerequisites[index - 2]
            plan.episodes.append(entry)
        return plan

    def test_context_uses_transitive_earlier_episodes_and_never_a_future_one(self):
        plan = self.series([["ep_001"], ["ep_002"], ["ep_003"]])
        rows = prerequisite_context(plan, plan.episodes[2], self.work)
        self.assertEqual([r["episode_id"] for r in rows], ["ep_001", "ep_002"])
        self.assertEqual({r["status"] for r in rows}, {"outline_only"})
        self.assertEqual(prerequisite_context(plan, plan.episodes[0], self.work), [])

    def test_only_the_nearest_prerequisite_comes_in_full(self):
        """Asimov, 2026-10-02: the finale built on every earlier episode and got 474,000 characters of prerequisite
        context. Only the nearest prerequisite comes in full; every other one brings its question, series_role and
        findings. A saved design in the run folder (a run begun before D-184) is not read."""
        plan = self.series([["ep_001"], ["ep_001", "ep_002"], ["ep_001", "ep_002", "ep_003"]])
        write_json(self.work / "teaching/ep_001/plan.json", {"destination": "An old design."})
        first, finale = plan.episodes[0], plan.episodes[3]
        rows = prerequisite_context(plan, finale, self.work)
        self.assertEqual([r["episode_id"] for r in rows], ["ep_001", "ep_002", "ep_003"])
        self.assertEqual(["outline" in r for r in rows], [False, False, True])
        self.assertEqual(rows[0], {"episode_id": "ep_001", "title": first.title, "status": "outline_only",
                                   "context": "summary", "central_question": first.central_question,
                                   "series_role": first.series_role, "finding_ids": first.finding_ids})
        self.assertEqual(rows[2]["outline"], plan.episodes[2].model_dump())

    def test_the_terminology_follows_the_project_topic(self):
        history = type("Config", (), {"language": "de-DE", "topic": "Psychohistorie", "central_question": ""})()
        machine = type("Config", (), {"language": "de-DE", "topic": "Transformer und Sprachmodelle",
                                      "central_question": ""})()
        self.assertNotIn("Query", project_terminology(history))
        self.assertIn("Query", project_terminology(machine))


if __name__ == "__main__":
    unittest.main()
