import json
import unittest
from unittest.mock import patch

from podcast_automate.editorial import episode_series_context
from podcast_automate.models import EpisodeScript
from podcast_automate.script_models import SeriesPlan
from podcast_automate.scripting import run_script
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

    def test_selected_episode_keeps_series_context_through_writing_polishing_and_reviews(self):
        fixture = fixtures.script_project(self)
        plan = self.series()
        captured = {}

        def model(prompt, output_type, directory, **kwargs):
            data = json.loads(prompt.splitlines()[-1])
            value, meta = fixture.model(prompt, output_type, directory, **kwargs)
            if output_type is SeriesPlan:
                return plan, meta
            if output_type is EpisodeScript:
                value.episode_id = data['episode']['episode_id']
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
            for version in ('teaching_design.v1', 'teaching_design_review.v4-framing', 'write_episode.v6-framing',
                            'dialogue_polish.v2-framing', 'dialogue_polish_review.v2-framing',
                            'script_review.v8-evidence', 'teaching_review.v3-framing',
                            'editorial_review.v3-series-context'):
                self.assertEqual(captured[version], expected)
            self.assertFalse((fixture.root / 'audio').exists())


if __name__ == '__main__':
    unittest.main()
