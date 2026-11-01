import json
import unittest
from unittest.mock import patch

from podcast_automate.editorial import episode_series_context
from podcast_automate.prompts import fragment
from podcast_automate.script_models import SeriesPlan
from podcast_automate.script_checks import SCRIPT_REVIEW_VERSION
from podcast_automate.script_pipeline import STORY_REVIEW_SUFFIX
from podcast_automate.scripting import run_script
from podcast_automate.story_agent import task_text
from tests import script_fixtures as fixtures


class EpisodeFramingTests(unittest.TestCase):
    def series(self):
        plan = fixtures.example_plan()
        first = plan.episodes[0]
        plan.episodes += [first.model_copy(update={"episode_id": f"ep_00{i}", "title": f"Part {i}",
                                                  "central_question": f"Question {i}?"}, deep=True)
                          for i in (2, 3)]
        return plan

    def test_first_middle_final_and_standalone_use_approved_series_order(self):
        plan = self.series()
        before = plan.model_dump()
        for index, entry in enumerate(plan.episodes):
            context = episode_series_context(plan, entry)
            self.assertEqual(context['episode_number'], index + 1)
            self.assertEqual(context['episode_count'], 3)
            self.assertEqual(context['is_first'], index == 0)
            self.assertEqual(context['is_last'], index == 2)
            self.assertEqual(len(context['episode_path']), 3)
            self.assertEqual(context['central_question'], plan.central_question)
            if index == 2:
                self.assertIsNone(context['next_episode'])
            else:
                self.assertEqual(context['next_episode']['episode_id'], plan.episodes[index + 1].episode_id)
        self.assertEqual(plan.model_dump(), before)
        standalone = fixtures.example_plan()
        context = episode_series_context(standalone, standalone.episodes[0])
        self.assertTrue(context['is_first'] and context['is_last'])
        self.assertEqual(context['episode_count'], 1)
        self.assertIsNone(context['next_episode'])

    def test_the_prompts_agree_on_the_finale_and_keep_the_framing_duties_in_one_place(self):
        """Finding of 2026-10-02: the planner asked for at least half a synthesis, the framing for one at the end,
        and the writer for an outlook on the next episode even in the finale; each restated the framing duties. The
        writer is the story agent since D-184: its task defers to the framing material.json gives it."""
        framing, plan, review = (fragment(name) for name in ("episode_framing", "series_plan", "script_review"))
        writer = task_text("de-DE", 10)
        self.assertIn("The final episode of a series is as a whole the series' synthesis", framing)
        self.assertIn("The final episode as a whole is the series' synthesis", plan)
        self.assertIn("A final or standalone episode closes the subject without promising another episode", framing)
        self.assertNotIn("at least half", plan)
        self.assertNotIn("At the end of the final episode", framing)
        # The writer and the review defer to episode_framing, which the review composes and the agent's material
        # carries, instead of restating it.
        for duties in (writer, review):
            self.assertNotIn("what the next episode takes up", duties)
            self.assertNotIn("series_role", duties)
            self.assertNotIn("in the final episode require", duties)
        self.assertIn("material.json's `framing`", writer)
        self.assertIn("framing rules above", review)
        # A recall of earlier content is cited through recap findings; naming an earlier question is framing.
        self.assertIn("recap_finding_ids, cited in knowledge_refs", framing)
        self.assertIn("name the earlier episode's question", framing)

    def test_configured_host_names_reach_generation_and_every_readable_view(self):
        fixture = fixtures.script_project(self)
        config = fixture.config.model_copy(update={'host_names': {'host_a': 'Mara', 'host_b': 'Jonas'}})
        from podcast_automate.storage import write_yaml
        write_yaml(fixture.root / 'project.yaml', config.model_dump(mode='json'))
        briefs = []

        def model(prompt, output_type, directory, **kwargs):
            payload = json.loads(prompt.splitlines()[-1])
            if 'host_names' in (payload.get('brief') or {}):
                briefs.append(payload['brief']['host_names'])
            return fixture.model(prompt, output_type, directory, **kwargs)

        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=model):
            run = run_script(fixture.root, episode='ep_001')
        self.assertEqual(run.status, 'completed')
        # The story agent writes with the names (its material's brief); any model call that is given them gets them.
        material = json.loads((fixture.root / 'runs' / run.run_id / 'story/ep_001/material.json').read_text(encoding='utf-8'))
        self.assertEqual(material['brief']['host_names'], {'host_a': 'Mara', 'host_b': 'Jonas'})
        self.assertTrue(all(row == {'host_a': 'Mara', 'host_b': 'Jonas'} for row in briefs))
        text = (fixture.root / 'episodes/ep_001/script.md').read_text(encoding='utf-8')
        self.assertIn('**Mara:**', text)
        self.assertIn('**Jonas:**', text)
        self.assertNotIn('**Host A:**', text)

    def test_selected_episode_keeps_series_context_through_the_agent_and_the_review(self):
        fixture = fixtures.script_project(self)
        plan = self.series()
        captured = {}

        def model(prompt, output_type, directory, **kwargs):
            data = json.loads(prompt.splitlines()[-1])
            value, meta = fixture.model(prompt, output_type, directory, **kwargs)
            if output_type is SeriesPlan:
                return plan, meta
            if 'series_context' in data:
                captured[kwargs['prompt_version']] = data['series_context']
            return value, meta

        for episode_id in ('ep_001', 'ep_002', 'ep_003'):
            captured.clear()
            with self.subTest(episode=episode_id), patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=model):
                run = run_script(fixture.root, episode=episode_id)
            self.assertEqual(run.status, 'completed')
            selected = next(entry for entry in plan.episodes if entry.episode_id == episode_id)
            expected = episode_series_context(plan, selected)
            # The story agent's material and the script review, by the review's own tag, so a prompt bump does not
            # hide a lost series_context here.
            material = json.loads((fixture.root / 'runs' / run.run_id / 'story' / episode_id / 'material.json')
                                  .read_text(encoding='utf-8'))
            self.assertEqual(material['series_context'], expected)
            self.assertEqual(captured, {SCRIPT_REVIEW_VERSION + STORY_REVIEW_SUFFIX: expected})
            self.assertFalse((fixture.root / 'audio').exists())


if __name__ == '__main__':
    unittest.main()
