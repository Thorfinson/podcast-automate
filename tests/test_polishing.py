import json
import unittest
from unittest.mock import patch

from podcast_automate.episode_audio import run_episode_audio
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript
from podcast_automate.polishing import (DEMANDING_PASSAGES, DemandingPassage, DialoguePolishReview,
                                       HOST_ROLES, Referent,
                                       POLISH_PROMPT_VERSION, POLISH_REVIEW_VERSION, compare_dialogue,
                                       polish_dialogue, validate_polish_review)
from podcast_automate.script_models import ScriptReview
from podcast_automate.scripting import run_script, validate_script
from podcast_automate.storage import digest, read_yaml, write_json, write_yaml
from tests import script_fixtures as fixtures


class PolishingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root

    def test_separate_pass_reaches_final_checks_with_original_and_preserves_voice_selection(self):
        final_checks = []
        def model(prompt, output_type, directory, **kwargs):
            value, metadata = self.fixture.model(prompt, output_type, directory, **kwargs)
            if kwargs['prompt_version'] == POLISH_PROMPT_VERSION:
                payload = json.loads(prompt.splitlines()[-1])
                self.assertEqual(payload['host_roles'], HOST_ROLES)
                value.segments[0].speaker_id = 'host_b'
                value.segments[1].speaker_id = 'host_a'
            if output_type is ScriptReview:
                final_checks.append(json.loads(prompt.splitlines()[-1]))
            return value, metadata
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(list(run.stages), ['planning', 'teaching', 'writing', 'polishing', 'review', 'publish'])
        folder = self.root / 'runs' / run.run_id / 'polishing/ep_001'
        # The before/after views name the role; the speaker swap below is what is being checked.
        self.assertIn('**Host A:** What', (folder / 'before.md').read_text(encoding='utf-8'))
        self.assertIn('**Host B:** What', (folder / 'after.md').read_text(encoding='utf-8'))
        self.assertIn('**Host B:** What', (self.root / 'episodes/ep_001/script.md').read_text(encoding='utf-8'))
        self.assertEqual(final_checks[0]['original_draft'], fixtures.example_script().model_dump())
        self.assertEqual(final_checks[0]['script']['segments'][0]['speaker_id'], 'host_b')
        self.assertEqual(final_checks[0]['host_roles'], HOST_ROLES)
        report = read_yaml(self.root / 'reports/script_quality.yaml')
        result = report['episodes']['ep_001']['dialogue_polish']
        self.assertEqual(result['status'], 'passed')
        self.assertNotEqual(result['original_digest'], result['polished_digest'])
        self.assertFalse(result['human_reviewed'])
        self.assertFalse(read_yaml(self.root / 'episodes/audio_review.yaml')['audio_approved'])
        self.assertEqual(read_yaml(self.root / 'project.yaml')['voice_profile'], self.fixture.config.voice_profile)

    def test_quota_between_polishing_and_comparison_reuses_candidate(self):
        paused = False
        def model(prompt, output_type, directory, **kwargs):
            nonlocal paused
            if output_type is DialoguePolishReview and not paused:
                paused = True
                raise AppError('Quota', code='quota_exhausted', status='waiting_for_quota')
            return self.fixture.model(prompt, output_type, directory, **kwargs)
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=model):
            first = run_script(self.root)
            self.assertEqual(first.stages['polishing'].status, 'waiting_for_quota')
            self.assertEqual(first.stages['review'].status, 'pending')
            count = self.fixture.calls.count(EpisodeScript)
            resumed = run_script(self.root, resume=True)
        self.assertEqual(resumed.status, 'completed')
        self.assertEqual(self.fixture.calls.count(EpisodeScript), count)
        self.assertEqual(count, 2)

    def test_persistent_fact_drift_blocks_export_and_keeps_repair_limit_on_resume(self):
        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.fixture.model(prompt, output_type, directory, **kwargs)
            if output_type is EpisodeScript and kwargs['prompt_version'].startswith('dialogue_polish'):
                value.segments[1].text = 'It scores exactly 1000 possibilities and always succeeds.'
            if output_type is DialoguePolishReview:
                value.checks[0].verdict = 'fail'
                value.checks[0].reason = 'The original has no count of 1000 and no success guarantee. Remove both additions.'
            return value, meta
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=model):
            run = run_script(self.root)
            count = len(self.fixture.calls)
            resumed = run_script(self.root, resume=True)
        self.assertEqual(run.stages['polishing'].error.code, 'dialogue_polish_failed')
        self.assertEqual(resumed.status, 'blocked')
        self.assertEqual(len(self.fixture.calls), count)
        self.assertNotIn(ScriptReview, self.fixture.calls)
        self.assertFalse((self.root / 'episodes/ep_001/script.yaml').exists())
        checkpoint = json.loads((self.root / 'runs' / run.run_id / 'polishing/ep_001/checkpoint.json').read_text())
        self.assertEqual(checkpoint['repairs'], 2)

    def test_missing_criteria_or_invented_comparison_evidence_cannot_pass(self):
        for corruption in ('criterion', 'before_quote', 'after_quote'):
            def model(prompt, output_type, directory, **kwargs):
                value, meta = self.fixture.model(prompt, output_type, directory, **kwargs)
                if output_type is DialoguePolishReview:
                    if corruption == 'criterion':
                        value.checks.pop()
                    elif corruption == 'before_quote':
                        value.checks[0].before[0].quote = 'This sentence does not exist.'
                    else:
                        value.checks[0].after[0].quote = 'This sentence does not exist either.'
                return value, meta
            with self.subTest(corruption=corruption), patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=model):
                run = run_script(self.root)
            self.assertEqual(run.stages['polishing'].error.code,
                             'invalid_polish_review' if corruption == 'criterion' else 'invalid_polish_evidence')
            self.assertEqual(run.stages['review'].status, 'pending')

    def test_changed_original_invalidates_cached_polish_even_when_segment_ids_match(self):
        original = fixtures.example_script()
        entry = fixtures.example_plan().episodes[0]
        versions = []
        def invoke(prompt, output_type, version):
            versions.append(version)
            if output_type is EpisodeScript:
                return original.model_copy(deep=True)
            return fixtures.polish_review(prompt)
        args = (self.fixture.config, entry)
        folder = self.root / 'isolated'
        polish_dialogue(*args, original, None, invoke, folder, validate_script)
        polish_dialogue(*args, original, None, invoke, folder, validate_script)
        self.assertEqual(versions.count(POLISH_PROMPT_VERSION), 1)
        original.segments[1].text += ' This comparison has a stated limitation.'
        polish_dialogue(*args, original, None, invoke, folder, validate_script)
        self.assertEqual(versions.count(POLISH_PROMPT_VERSION), 2)
        report = json.loads((folder / 'result.json').read_text())
        self.assertEqual(report['original_digest'], digest(original.model_dump()))

    def test_damaged_or_removed_polishing_stage_blocks_approved_audio(self):
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=self.fixture.model):
            run = run_script(self.root)
        work = self.root / 'runs' / run.run_id
        artifact = work / 'polishing/ep_001/script.json'
        before = artifact.read_bytes()
        write_json(artifact, {})
        with patch('podcast_automate.episode_audio.run_tts', side_effect=AssertionError('No audio')):
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, episode='ep_001', approve_audio=True)
            self.assertEqual(caught.exception.code, 'invalid_script')
            artifact.write_bytes(before)
            manifest = read_yaml(work / 'run_manifest.yaml')
            del manifest['stages']['polishing']
            write_yaml(work / 'run_manifest.yaml', manifest)
            with self.assertRaises(AppError) as caught:
                run_episode_audio(self.root, episode='ep_001', approve_audio=True)
            self.assertEqual(caught.exception.code, 'invalid_script')

    def test_missing_intro_or_outro_is_repaired_then_compared_again(self):
        comparisons = 0
        def model(prompt, output_type, directory, **kwargs):
            nonlocal comparisons
            result, meta = self.fixture.model(prompt, output_type, directory, **kwargs)
            if output_type is DialoguePolishReview:
                comparisons += 1
                if comparisons == 1:
                    check = next(c for c in result.checks if c.criterion == 'episode_framing')
                    check.verdict = 'fail'
                    check.reason = 'The episode stops at a technical question; add the missing spoken sign-off.'
                    check.after = []
            return result, meta
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, 'completed')
        self.assertEqual(comparisons, 2)
        report = json.loads((self.root / 'runs' / run.run_id / 'polishing/ep_001/result.json').read_text())
        self.assertEqual(report['repairs'], 1)
        self.assertFalse(report['human_reviewed'])

    def test_persistent_missing_framing_blocks_publication(self):
        def model(prompt, output_type, directory, **kwargs):
            result, meta = self.fixture.model(prompt, output_type, directory, **kwargs)
            if output_type is DialoguePolishReview:
                check = next(c for c in result.checks if c.criterion == 'episode_framing')
                check.verdict = 'fail'
                check.reason = 'There is no welcome or outro.'
                check.after = []
            return result, meta
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=model):
            run = run_script(self.root)
            calls = len(self.fixture.calls)
            resumed = run_script(self.root, resume=True)
        self.assertEqual(run.stages['polishing'].error.code, 'dialogue_polish_failed')
        self.assertEqual(resumed.status, 'blocked')
        self.assertEqual(len(self.fixture.calls), calls)
        self.assertFalse((self.root / 'episodes/ep_001/script.yaml').exists())

    def long_script(self):
        """Six segments, so three demanding passages that are neither greeting nor sign-off exist."""
        script = fixtures.example_script()
        first = script.segments[0]
        script.segments = [first.model_copy(update={"segment_id": f"seg_{i:03d}", "text": f"Satz Nummer {i}."},
                                            deep=True) for i in range(1, 7)]
        return script

    def review_for(self, script, **changes):
        review = fixtures.polish_review(json.dumps({"original": script.model_dump(),
                                                    "candidate": script.model_dump()}))
        for key, value in changes.items():
            setattr(review, key, value)
        return review

    def test_exactly_three_distinct_existing_passages_are_required(self):
        script = self.long_script()
        review = self.review_for(script)
        self.assertEqual(len(review.demanding_passages), DEMANDING_PASSAGES)
        self.assertEqual(validate_polish_review(review, script, script), [])
        for broken, code in ((review.demanding_passages[:2], "invalid_polish_review"),
                             (review.demanding_passages + [review.demanding_passages[0]], "invalid_polish_review")):
            with self.subTest(code=code), self.assertRaises(AppError) as caught:
                validate_polish_review(self.review_for(script, demanding_passages=broken), script, script)
            self.assertEqual(caught.exception.code, code)
        unknown = self.review_for(script)
        unknown.demanding_passages[0].segment_id = "seg_999"
        with self.assertRaises(AppError) as caught:
            validate_polish_review(unknown, script, script)
        self.assertEqual(caught.exception.code, "invalid_polish_evidence")

    def test_three_named_passages_with_a_repeated_id_are_rejected_for_distinctness(self):
        script = self.long_script()
        review = self.review_for(script)
        # Three passages are named, so the count rule is met; only the repeated id can reject.
        review.demanding_passages[2].segment_id = review.demanding_passages[1].segment_id
        self.assertEqual(len(review.demanding_passages), DEMANDING_PASSAGES)
        with self.assertRaises(AppError) as caught:
            validate_polish_review(review, script, script)
        self.assertEqual(caught.exception.code, "invalid_polish_review")
        self.assertIn("unterschiedliche", str(caught.exception))

    def test_greeting_and_sign_off_never_count_as_the_densest_passage(self):
        script = self.long_script()
        for position in (0, -1):
            review = self.review_for(script)
            review.demanding_passages[0].segment_id = script.segments[position].segment_id
            with self.subTest(position=position), self.assertRaises(AppError) as caught:
                validate_polish_review(review, script, script)
            self.assertEqual(caught.exception.code, "invalid_polish_review")

    def test_a_referent_must_be_resolved_by_an_earlier_segment(self):
        script = self.long_script()
        review = self.review_for(script)
        review.demanding_passages[0].referents[0].resolved_by = script.segments[-1].segment_id
        with self.assertRaises(AppError) as caught:
            validate_polish_review(review, script, script)
        self.assertEqual(caught.exception.code, "invalid_polish_evidence")

    def test_an_unresolved_referent_becomes_a_spoken_language_issue_instead_of_a_rejection(self):
        script = self.long_script()
        review = self.review_for(script)
        review.demanding_passages[1].referents[0].resolved_by = None
        issues = validate_polish_review(review, script, script)
        self.assertEqual(len(issues), 1)
        self.assertTrue(issues[0].startswith("spoken_language: "))
        self.assertIn(review.demanding_passages[1].segment_id, issues[0])
        failing = self.review_for(script)
        failing.demanding_passages[1].referents[0].resolved_by = None
        next(c for c in failing.checks if c.criterion == "spoken_language").verdict = "fail"
        self.assertEqual(validate_polish_review(failing, script, script), [])

    def test_an_unresolved_referent_drives_the_existing_repair_loop(self):
        original, comparisons = self.long_script(), 0
        entry = fixtures.example_plan().episodes[0]

        def invoke(prompt, output_type, version):
            nonlocal comparisons
            if output_type is EpisodeScript:
                return original.model_copy(deep=True)
            review = fixtures.polish_review(prompt)
            comparisons += 1
            if comparisons == 1:
                review.demanding_passages[0].referents[0] = Referent(expression="dieser Wert", resolved_by=None)
            return review

        polish_dialogue(self.fixture.config, entry, original, None, invoke,
                        self.root / "density", lambda *_: [])
        self.assertEqual(comparisons, 2)
        issues = json.loads((self.root / "density/issues.json").read_text(encoding="utf-8"))
        self.assertEqual(len(issues), 1)
        self.assertIn("dieser Wert", issues[0])

    def test_the_prerequisite_context_reaches_the_polish_pass_and_its_comparison(self):
        seen = []
        script = fixtures.example_script()
        entry = fixtures.example_plan().episodes[0]
        context = [{"episode_id": "ep_001", "status": "reviewed_teaching_plan",
                    "established_terms": ["Kandidat"]}]
        def invoke(prompt, output_type, version):
            seen.append((version, json.loads(prompt.splitlines()[-1])))
            if output_type is EpisodeScript:
                return script.model_copy(deep=True)
            return fixtures.polish_review(prompt)
        polish_dialogue(self.fixture.config, entry, script, None, invoke, self.root / "context",
                        validate_script, prerequisite_context=context)
        self.assertEqual([version for version, _ in seen], [POLISH_PROMPT_VERSION, POLISH_REVIEW_VERSION])
        self.assertTrue(all(payload["prerequisite_context"] == context for _, payload in seen))

    def test_the_comparison_call_can_be_made_on_its_own(self):
        script = fixtures.example_script()
        entry = fixtures.example_plan().episodes[0]
        captured = {}
        def invoke(prompt, output_type, version):
            captured["version"], captured["payload"] = version, json.loads(prompt.splitlines()[-1])
            return fixtures.polish_review(prompt)
        review = compare_dialogue({"language": "de-DE"}, entry, script, script, invoke)
        self.assertEqual(captured["version"], POLISH_REVIEW_VERSION)
        self.assertEqual(captured["payload"]["candidate"], script.model_dump())
        self.assertEqual(validate_polish_review(review, script, script), [])

    def test_framing_pass_needs_quotes_from_both_boundary_chapters(self):
        script = fixtures.example_script()
        script.chapters.append(Chapter(chapter_id='closing', title='Closing'))
        script.segments[-1].chapter_id = script.segments[-1].scene_id = 'closing'
        review = fixtures.polish_review(json.dumps({'original': script.model_dump(), 'candidate': script.model_dump()}))
        framing = next(c for c in review.checks if c.criterion == 'episode_framing')
        framing.after = framing.after[:1]
        with self.assertRaises(AppError) as caught:
            validate_polish_review(review, script, script)
        self.assertEqual(caught.exception.code, 'invalid_polish_evidence')
        from podcast_automate.teaching import Passage
        framing.after.append(Passage(segment_id=script.segments[-1].segment_id, quote=script.segments[-1].text))
        validate_polish_review(review, script, script)


if __name__ == '__main__':
    unittest.main()
