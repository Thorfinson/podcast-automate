"""The two CI helpers (D-205): which changes may skip the test legs, and how the suite is split into shards."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


changes = load("ci_changes")
shard_script = load("test_shards")


class ChangesTests(unittest.TestCase):
    def test_docs_pages_and_templates_skip_the_tests(self):
        self.assertFalse(changes.needs_tests(["README.md", "CHANGELOG.md", "docs/STUDIO.md", "docs/media/x.mp3",
                                              "docs/specs/plan.md", "site/index.html", "evals/web_search/run.py",
                                              ".github/ISSUE_TEMPLATE/bug_report.yml", ".github/CODEOWNERS",
                                              ".github/PULL_REQUEST_TEMPLATE.md", ".github/dependabot.yml",
                                              "LICENSE", ".gitignore"]))

    def test_everything_a_test_can_read_runs_them(self):
        # test_third_host reads examples/, test_platforms the launchers; a workflow can change how tests run.
        for path in ["src/podcast_automate/studio.py", "src/podcast_automate/prompts/README.md",
                     "src/podcast_automate/data/story_agent_task.md", "tests/test_audio.py", "examples/x/project.yaml",
                     "Podcast-Studio.sh", "pyproject.toml", "scripts/setup-qwen.py", ".github/workflows/tests.yml",
                     ".gitattributes", "docs.py"]:
            with self.subTest(path=path):
                self.assertTrue(changes.needs_tests(["README.md", path]))

    def test_an_unknown_or_empty_change_runs_them(self):
        self.assertTrue(changes.needs_tests(None))
        self.assertTrue(changes.needs_tests([]))
        self.assertIsNone(changes.changed_files("0000000000000000000000000000000000000000", "abc"))
        self.assertIsNone(changes.changed_files("", "abc"))


class ShardTests(unittest.TestCase):
    def test_every_module_lands_in_exactly_one_shard(self):
        names = shard_script.modules()
        self.assertIn("test_ci_scripts", names)
        for count in (1, 2, 3, 4):
            with self.subTest(count=count):
                groups = shard_script.shards(names, count, shard_script.load_weights())
                self.assertEqual(len(groups), count)
                self.assertEqual(sorted(name for group in groups for name in group), names)

    def test_the_split_is_balanced_and_the_same_on_every_run(self):
        weights = {"test_a": 10.0, "test_b": 6.0, "test_c": 5.0, "test_d": 4.0, "test_e": 1.0}
        groups = shard_script.shards(list(weights), 2, weights)
        self.assertEqual(groups, shard_script.shards(list(reversed(list(weights))), 2, weights))
        totals = sorted(sum(weights[name] for name in group) for group in groups)
        # By hand, longest first into the lightest shard: 10 | 6, 5 → 11, 4 → 14 (10 < 11), 1 → 12. No subset of these
        # weights sums to 13, so 12 and 14 is also the best split there is.
        self.assertEqual(totals, [12.0, 14.0])
        # The greedy split's bound: no shard is heavier than another by more than the largest single module.
        many = {f"test_{i:02d}": float((i * 37) % 23 + 1) for i in range(30)}
        loads = [sum(many[name] for name in group) for group in shard_script.shards(list(many), 3, many)]
        self.assertLessEqual(max(loads) - min(loads), max(many.values()))

    def test_a_module_without_a_measured_time_counts_as_the_median(self):
        groups = shard_script.shards(["test_a", "test_b", "test_c", "test_new"], 2,
                                     {"test_a": 9.0, "test_b": 3.0, "test_c": 1.0})
        self.assertIn("test_new", [name for group in groups for name in group])
        self.assertEqual(groups, [["test_a"], ["test_b", "test_c", "test_new"]])

    def test_weights_come_from_the_durations_list_of_unittest(self):
        log = ("Slowest test durations\n----\n"
               "12.500s     test_one (test_audio.AudioTests.test_one)\n"
               "0.500s      test_two (test_audio.AudioTests.test_two)\n"
               "3.000s      test_x (test_studio.StudioHttpTests.test_x)\n"
               "Ran 3 tests in 16.0s\n")
        self.assertEqual(shard_script.weights_from_log(log), {"test_audio": 13.0, "test_studio": 3.0})
        # PowerShell's redirection wraps long lines at its width, also inside a name (seen on 2026-11-10).
        wrapped = ("Slowest test durations\n----\n"
                   "151.744s   test_a_stop_at_any_model_call (test_series_review.SeriesRepa\n"
                   "irWorkflowTests.test_a_stop_at_any_model_call)\n"
                   "13.748s    test_selected_episode_keeps_series_context \n"
                   "(test_episode_framing.EpisodeFramingTests.test_selected_episode_keeps_series_context)\n"
                   "Ran 2 tests in 165.5s\n")
        self.assertEqual(shard_script.weights_from_log(wrapped),
                         {"test_episode_framing": 13.7, "test_series_review": 151.7})

    def test_the_stored_weights_name_only_existing_modules(self):
        names = set(shard_script.modules())
        stale = sorted(set(shard_script.load_weights()) - names)
        self.assertEqual(stale, [], "rerun scripts/test_shards.py --weights after removing or renaming a module")

    def test_unreadable_weights_split_by_count(self):
        with tempfile.TemporaryDirectory() as folder:
            broken = Path(folder) / "weights.json"
            broken.write_text("not json", encoding="utf-8")
            self.assertEqual(shard_script.load_weights(broken), {})
            broken.write_text(json.dumps({"test_a": 2}), encoding="utf-8")
            self.assertEqual(shard_script.load_weights(broken), {"test_a": 2.0})


if __name__ == "__main__":
    unittest.main()
