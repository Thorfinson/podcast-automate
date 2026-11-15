"""The money per call of a billed run (cost_estimate, D-146, D-148): where the projection's figure comes from, what an
attempt without a reported cost counts at, and the money block of a projection.

The measured values asserted here are the table of BUSINESS_LOGIC's money-limit section (completed runs of 2026-09-26
to 2026-10-04); the source order and the ten priced attempts are the rules stated there."""
import tempfile
import unittest
from pathlib import Path

from podcast_automate import cost_estimate
from podcast_automate.cost_estimate import cost_view, fallback_usd, per_call_usd, project_mean, spent_usd, table_usd
from podcast_automate.storage import write_json, write_yaml
from podcast_automate.text_settings import CLAUDE_MODELS, OPENROUTER_MODELS


class CostEstimateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.work = self.root / "runs" / "run_99_current"
        self.work.mkdir(parents=True)
        # A completed run's mean is read once per process; every test starts from its own folders.
        cost_estimate._run_mean.cache_clear()
        self.addCleanup(cost_estimate._run_mean.cache_clear)

    def run_with(self, name, *, kind="research", status="completed", model="claude-sonnet-5-5", costs=(),
                 key="reported_cost_usd"):
        work = self.root / "runs" / name
        write_yaml(work / "run_manifest.yaml", {"kind": kind, "status": status})
        for number, usd in enumerate(costs, start=1):
            write_json(work / "calls" / f"call_{number:03d}" / "metadata.json", {"requested_model": model, key: usd})
        return work

    def test_the_table_holds_the_documented_means_also_under_openrouters_names(self):
        self.assertEqual([table_usd(kind, model) for kind in ("research", "script")
                          for model in ("claude-sonnet-5-5", "claude-opus-5-5")], [0.37, 0.75, 0.83, 1.17])
        self.assertEqual(table_usd("research", "anthropic/claude-sonnet-5.5"), 0.37)
        self.assertEqual(table_usd("script", "anthropic/claude-opus-5.5"), 1.17)
        self.assertIsNone(table_usd("research", "claude-haiku-5-5"), "no Haiku run was measured")

    def test_the_run_decides_after_ten_priced_attempts_else_the_project_else_the_table(self):
        self.assertEqual(per_call_usd(self.root, self.work, "research", "claude-sonnet-5-5"), (0.37, "table"))
        self.run_with("run_01_done", costs=[0.5] * 10)
        self.assertEqual(per_call_usd(self.root, self.work, "research", "claude-sonnet-5-5"), (0.5, "project"))
        nine = {"priced_attempts": 9, "billed_usd": 9.0, "model_billed_usd": 9.0}
        self.assertEqual(per_call_usd(self.root, self.work, "research", "claude-sonnet-5-5", nine), (0.5, "project"))
        ten = {"priced_attempts": 10, "billed_usd": 2.5, "model_billed_usd": 2.0}
        self.assertEqual(per_call_usd(self.root, self.work, "research", "claude-sonnet-5-5", ten), (0.2, "run"),
                         "the run's mean counts model attempts only, not its search requests")
        # A budget settled before search requests were kept apart holds only billed_usd.
        older = {"priced_attempts": 10, "billed_usd": 3.0}
        self.assertEqual(per_call_usd(self.root, self.work, "research", "claude-sonnet-5-5", older), (0.3, "run"))

    def test_the_project_value_is_its_newest_completed_run_of_the_kind_and_model(self):
        # Amounts a float holds exactly, so a mean of ten is exact.
        self.run_with("run_01_old", costs=[0.25] * 10)
        self.run_with("run_02_other_model", model="claude-opus-5-5", costs=[2.0] * 10)
        self.run_with("run_03_too_few", costs=[0.875] * 9)
        self.run_with("run_04_failed", status="failed", costs=[0.625] * 10)
        self.run_with("run_05_script", kind="script", costs=[0.375] * 10)
        self.assertEqual(project_mean(self.root, "research", "claude-sonnet-5-5"), 0.25)
        self.assertEqual(project_mean(self.root, "script", "claude-sonnet-5-5"), 0.375)
        self.assertEqual(project_mean(self.root, "research", "claude-opus-5-5"), 2.0)
        self.run_with("run_06_newest", model="anthropic/claude-sonnet-5.5", key="separately_billed_cost",
                      costs=[0.75] * 10)
        cost_estimate._run_mean.cache_clear()
        self.assertEqual(project_mean(self.root, "research", "claude-sonnet-5-5"), 0.75,
                         "OpenRouter's name of the model counts, at its billed cost")
        self.assertEqual(project_mean(self.root, "research", "claude-sonnet-5-5", exclude="run_06_newest"), 0.25)
        self.assertIsNone(project_mean(self.root, "research", "claude-haiku-5-5"))
        # The newest folder, run_99_current, has no manifest yet: it is no completed run, and never a stop.
        self.assertFalse((self.work / "run_manifest.yaml").exists())

    def test_the_money_block_adds_the_expected_rest_to_what_was_spent(self):
        budget = {"billed_usd": 3.0, "model_billed_usd": 3.0, "priced_attempts": 10, "estimated_usd": 0.5,
                  "external_usd": 0.6, "unpriced_attempts": 2}
        self.assertEqual(spent_usd(budget), 4.1)
        view = cost_view(self.root, self.work, "research", "claude-sonnet-5-5", 7.0, 10, budget)
        self.assertEqual((view["per_call_usd"], view["per_call_source"], view["spent_usd"], view["expected_remaining_usd"],
                          view["expected_total_usd"], view["unpriced_attempts"]), (0.3, "run", 4.1, 3.0, 7.1, 2))
        self.assertFalse(view["feasible"], "7.10 USD expected exceed a 7 USD limit")
        self.assertTrue(cost_view(self.root, self.work, "research", "claude-sonnet-5-5", 7.1, 10, budget)["feasible"])
        self.assertTrue(cost_view(self.root, self.work, "research", "claude-sonnet-5-5", None, 10, budget)["feasible"])
        self.assertEqual(cost_view(self.root, self.work, "research", "claude-sonnet-5-5", 7.0, -3, budget)
                         ["expected_remaining_usd"], 0.0)

    def test_a_model_nobody_measured_counts_high_never_at_zero(self):
        """Review 2026-11-14: an attempt without a reported cost of Haiku 5.5 (the default since D-166), Opus 5 or most
        OpenRouter models counted at 0 USD before the run's first priced attempt, and their projection named no
        expected money."""
        measured = max(fallback_usd("claude-sonnet-5-5"), fallback_usd("claude-opus-5-5"))
        for model in [*CLAUDE_MODELS, *OPENROUTER_MODELS, "vendor/unknown-model", None]:
            with self.subTest(model=model):
                self.assertGreater(fallback_usd(model), 0)
                for kind in ("research", "script"):
                    usd, source = per_call_usd(self.root, self.work, kind, model)
                    self.assertGreater(usd, 0)
                    self.assertIn(source, {"table", "assumed"})
        # Haiku 5.5 costs a twentieth of Sonnet 5.5 per token (D-166): Sonnet's measured value bounds it from above.
        self.assertEqual(fallback_usd("claude-haiku-5-5"), fallback_usd("claude-sonnet-5-5"))
        self.assertEqual(per_call_usd(self.root, self.work, "research", "claude-haiku-5-5"), (0.37, "assumed"))
        self.assertEqual(per_call_usd(self.root, self.work, "script", "claude-haiku-5-5"), (0.83, "assumed"))
        # Any other model without a measurement counts at least as high as every measured one.
        for model in ("claude-opus-5", "openai/gpt-6-astra", "deepseek/deepseek-v4.1-flash", "vendor/unknown-model", None):
            with self.subTest(unmeasured=model):
                self.assertGreaterEqual(fallback_usd(model), measured)
                self.assertGreaterEqual(per_call_usd(self.root, self.work, "script", model)[0],
                                        per_call_usd(self.root, self.work, "script", "claude-opus-5-5")[0])
        # A measured project run still decides over the assumption.
        self.run_with("run_01_haiku", model="claude-haiku-5-5", costs=[0.02] * 10)
        usd, source = per_call_usd(self.root, self.work, "research", "claude-haiku-5-5")
        self.assertEqual((round(usd, 6), source), (0.02, "project"))


if __name__ == "__main__":
    unittest.main()
