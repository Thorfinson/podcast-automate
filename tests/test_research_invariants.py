"""Regression cases for source limits, edit ownership, split IDs and call reserves."""
import json
import unittest
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.question_budget import budget_projection
from podcast_automate.question_scope import QuestionScopeReview, pending_task, scoped_plan
from podcast_automate.research_ledger import read_value, save_value
from podcast_automate.research_models import ResearchDossier
from podcast_automate.research_patches import DossierPatch, edit_dossier
from podcast_automate.research_tasks import QuestionPlan, QuestionSearch
from podcast_automate.storage import digest, write_json
from tests import test_question_research as fixtures
from tests.question_fixtures import answer_for, decision, task_value


class ResearchInvariantTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.QuestionResearchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.engine = self.fixture.engine()
        self.engine.initialise(self.fixture.discovery, self.fixture.index, None, [])
        self.spec = QuestionPlan.model_validate(self.engine.state["plan"]).tasks[0]
        self.row = self.engine.state["tasks"][self.spec.id]

    def search_for(self, *urls):
        candidates = [self.fixture.discovery.candidates[0].model_copy(update={"url": url, "primary_source": True})
                      for url in urls]
        self.fixture.hook = lambda p, s, data, kw: QuestionSearch(candidates=candidates, limitations=[]) \
            if s is QuestionSearch else None

    def seed_dossier(self):
        answer = answer_for(self.fixture.ref).model_dump()
        findings = [{**answer["findings"][0], "id": identifier} for identifier in ("f_definition", "f_empirical")]
        dossier = ResearchDossier(topic=self.fixture.config.topic, scope_note="Synthetic example.",
            findings=findings, coverage=[dict(question_id="q_energy", status="answered",
                finding_ids=[f["id"] for f in findings], gap="")], open_questions=[])
        tasks = [task_value(), task_value("task_empirical", "empirical")]
        for task, finding in zip(tasks, findings):
            task["finding_ids"] = [finding["id"]]
        self.engine.state.update(plan=QuestionPlan(tasks=tasks).model_dump(), seed_dossier=dossier.model_dump(),
            tasks={t["id"]: {**pending_task(), "status": "verified", "answer": answer} for t in tasks},
            dirty_tasks=["task_empirical"])
        return dossier

    def test_duplicates_consume_allowance_across_steps_and_resume(self):
        self.fixture.config.research_limits.sources = 2
        self.search_for("https://example.org/duplicate")
        self.engine.web_search(self.spec, self.row, ["energy"])
        self.row["step"] += 1
        self.engine.save()
        restored = self.fixture.engine()
        restored.initialise(self.fixture.discovery, self.fixture.index, None, [])
        self.search_for("https://example.org/another-duplicate")
        calls = len(self.fixture.calls)
        self.assertFalse(restored.web_search(self.spec, restored.state["tasks"][self.spec.id], ["energy"]))
        self.assertEqual(len(self.fixture.calls), calls)
        self.assertEqual(self.fixture.download.call_count, 2)
        self.assertEqual(len(restored.index.sources), 1)
        self.assertEqual(len(restored.attempts), 2)
        self.assertEqual(len(restored.index.failures), 1)

    def test_failed_and_fragment_equivalent_urls_are_not_downloaded_again(self):
        self.fixture.config.research_limits.sources = 4
        self.fixture.download.side_effect = OSError("Unavailable fixture")
        self.search_for("https://example.org/fail#first")
        self.engine.web_search(self.spec, self.row, ["energy"])
        self.row["step"] += 1
        self.search_for("https://example.org/fail#second")
        self.engine.web_search(self.spec, self.row, ["energy"])
        self.assertEqual(self.fixture.download.call_count, 2)  # Includes initial source.
        self.assertEqual(len(self.engine.attempts), 2)

    def test_restore_completed_download_when_last_source_slot_was_used(self):
        self.fixture.config.research_limits.sources = 2
        self.engine.seed(self.spec, self.row)
        self.row["pending"] = decision("search_web", web_queries=["energy"]).model_dump()
        self.engine.save()
        self.search_for("https://example.org/new")
        self.fixture.download.side_effect = lambda url: (fixtures.fixtures.HTML.replace(
            b"These sentences", b"New fixture evidence. These sentences"), "text/html", url)
        with patch.object(self.engine, "set_index", side_effect=KeyboardInterrupt("After receipt, before state commit")):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.research_task(self.spec)
        used = self.fixture.download.call_count
        restored = self.fixture.engine()
        restored.run(self.fixture.discovery, self.fixture.index)
        self.assertEqual(self.fixture.download.call_count, used)
        self.assertEqual(len(restored.index.sources), 2)
        self.assertEqual(restored.state["phase"], "completed")
        self.assertEqual(sum(s is QuestionSearch for s, _ in self.fixture.calls), 1)

    def test_legacy_duplicate_receipts_are_counted_without_changing_index(self):
        self.fixture.config.research_limits.sources = 2
        receipt = self.engine.folder / "tasks/task_old/attempt_0/step_000/downloads.json"
        save_value(receipt, {"processed": ["https://example.org/old-duplicate#fragment"],
                             "index": self.fixture.index.model_dump()})
        restored = self.fixture.engine()
        restored.initialise(self.fixture.discovery, self.fixture.index, None, [])
        self.assertEqual(len(restored.attempts), 2)
        self.assertEqual(restored.index.model_dump(), self.fixture.index.model_dump())
        self.assertFalse(restored.web_search(self.spec, restored.state["tasks"][self.spec.id], ["energy"]))

    def test_patch_cannot_edit_other_task_under_shared_discovery_question(self):
        seed = self.seed_dossier()
        def illegal(prompt, schema, payload, kwargs):
            if schema is DossierPatch:
                self.assertEqual([f["id"] for f in payload["editable_findings"]], ["f_empirical"])
                return DossierPatch(updates=[seed.findings[0].model_copy(update={"statement": "Unrelated rewrite."})],
                    additions=[], coverage_updates=[], resolved_open_questions=[], new_open_questions=[])
        self.fixture.hook = illegal
        with self.assertRaises(AppError) as caught:
            self.engine.compose()
        self.assertEqual(caught.exception.code, "invalid_research_patch")

    def test_new_finding_ownership_survives_next_composition(self):
        seed = self.seed_dossier()
        new = seed.findings[1].model_copy(update={"id": "f_new", "statement": "Energy assignment has a limited scope."})
        self.fixture.hook = lambda p, s, data, kw: DossierPatch(updates=[], additions=[new], coverage_updates=[],
            resolved_open_questions=[], new_open_questions=[]) if s is DossierPatch else None
        dossier, _, _ = self.engine.compose()
        owners = self.engine.state["composed_findings"]["owners"]
        self.assertEqual(owners["f_new"], ["task_empirical"])
        self.assertEqual(digest(dossier.findings[0].model_dump()), digest(seed.findings[0].model_dump()))
        self.engine.state.update(seed_dossier=dossier.model_dump(), finding_owners=owners, audit_round=1)
        targets = []
        def capture(*args, **kwargs):
            targets.extend(kwargs["targets"])
            return args[2]
        with patch("podcast_automate.question_synthesis.edit_dossier", side_effect=capture):
            self.engine.compose()
        self.assertEqual(set(targets), {"f_empirical", "f_new"})

    def test_shared_finding_is_protected_until_all_owners_are_dirty(self):
        self.seed_dossier()
        self.engine.state["plan"]["tasks"][0]["finding_ids"].append("f_empirical")
        seen = []
        def capture(*args, **kwargs):
            seen.append(kwargs["targets"])
            return args[2]
        with patch("podcast_automate.question_synthesis.edit_dossier", side_effect=capture):
            self.engine.compose()
            self.engine.state["dirty_tasks"] = ["task_definition", "task_empirical"]
            self.engine.compose()
        self.assertEqual(seen, [set(), {"f_definition", "f_empirical"}])

    def test_compatible_legacy_patch_replays_without_new_call(self):
        seed = self.seed_dossier()
        self.fixture.hook = lambda p, s, data, kw: DossierPatch(updates=[seed.findings[1].model_copy(
            update={"statement": "Configurations receive energies in this bounded fixture."})], additions=[], coverage_updates=[],
            resolved_open_questions=[], new_open_questions=[]) if s is DossierPatch else None
        def legacy(*args, **kwargs):
            kwargs["targets"] = kwargs.pop("legacy_targets")
            return edit_dossier(*args, **kwargs)
        with patch("podcast_automate.question_synthesis.edit_dossier", side_effect=legacy):
            self.engine.compose()
        calls = len(self.fixture.calls)
        self.engine.compose()
        self.assertEqual(len(self.fixture.calls), calls)

    def test_legacy_patch_with_unrelated_changes_is_rejected_on_replay(self):
        seed = self.seed_dossier()
        self.fixture.hook = lambda p, s, data, kw: DossierPatch(updates=[seed.findings[0].model_copy(
            update={"statement": "An unrelated rewrite."})], additions=[], coverage_updates=[],
            resolved_open_questions=[], new_open_questions=[]) if s is DossierPatch else None
        def legacy(*args, **kwargs):
            kwargs["targets"] = kwargs.pop("legacy_targets")
            return edit_dossier(*args, **kwargs)
        with patch("podcast_automate.question_synthesis.edit_dossier", side_effect=legacy):
            with self.assertRaises(AppError):
                self.engine.compose()
        calls = len(self.fixture.calls)
        with self.assertRaises(AppError) as caught:
            self.engine.compose()
        self.assertEqual(caught.exception.code, "invalid_research_patch")
        self.assertEqual(len(self.fixture.calls), calls)

    def test_reference_repair_cannot_bypass_unrelated_finding_protection(self):
        self.seed_dossier()
        def corrupt(*args, **kwargs):
            changed = args[2].model_copy(deep=True)
            changed.findings[0].statement = "Unrelated rewrite through reference repair."
            return changed
        with patch("podcast_automate.question_synthesis.repair_references", side_effect=corrupt):
            with self.assertRaises(AppError) as caught:
                self.engine.compose()
        self.assertEqual(caught.exception.code, "invalid_research_patch")

    def test_impossible_plan_blocks_before_answer_calls_and_keeps_scope(self):
        tasks = [task_value(f"task_{n}") for n in range(77)]
        self.engine.state.update(plan=QuestionPlan(tasks=tasks).model_dump(),
            tasks={t["id"]: pending_task() for t in tasks}, dirty_tasks=[t["id"] for t in tasks])
        self.engine.save()
        write_json(self.fixture.work / "budget.json", {"model_calls": 3, "search_rounds": 1})
        calls = len(self.fixture.calls)
        with self.assertRaises(AppError) as caught:
            self.engine.ensure_budget()
        self.assertEqual(caught.exception.code, "research_budget_insufficient")
        projection = read_value(self.engine.folder / "state.json")["budget_projection"]
        self.assertEqual(projection["minimum_remaining_calls"], 157)
        self.assertEqual(projection["shortfall"], 10)
        self.assertEqual(len(self.engine.state["tasks"]), 77)
        self.assertEqual(len(self.fixture.calls), calls)

    def test_seeded_plan_projection_counts_batches_and_verified_answers(self):
        self.seed_dossier()
        tasks = [task_value(f"task_{n}") for n in range(77)]
        rows = {t["id"]: pending_task() for t in tasks}
        rows["task_0"]["status"] = "verified"
        self.engine.state.update(plan=QuestionPlan(tasks=tasks).model_dump(), tasks=rows,
                                 dirty_tasks=[t["id"] for t in tasks])
        write_json(self.fixture.work / "budget.json", {"model_calls": 33})
        limits = self.fixture.config.research_limits.model_copy(update={"model_calls": 250})
        result = budget_projection(self.fixture.work, self.engine.state, limits)
        self.assertEqual((result["question_calls"], result["closing_calls"], result["headroom"]), (152, 22, 43))

    def test_exact_allowance_completes_and_optional_search_preserves_reserve(self):
        # Planning and scope review are already saved; five calls remain in the ideal path.
        self.fixture.config.research_limits.model_calls = 7
        budget_path = self.fixture.work / "budget.json"
        write_json(budget_path, {"model_calls": 2})
        def counted(*args, **kwargs):
            used = json.loads(budget_path.read_text())["model_calls"]
            write_json(budget_path, {"model_calls": used + 1})
            return self.fixture.model(*args, **kwargs)
        self.engine.invoke = counted
        self.engine.run(self.fixture.discovery, self.fixture.index)
        self.assertEqual(self.engine.state["phase"], "completed")
        self.assertEqual(self.engine.state["budget_projection"]["minimum_remaining_calls"], 0)
        self.assertEqual(json.loads(budget_path.read_text())["model_calls"], 7)
        self.engine.run(self.fixture.discovery, self.fixture.index)
        self.assertEqual(json.loads(budget_path.read_text())["model_calls"], 7)

    def test_optional_search_is_blocked_before_it_consumes_closing_calls(self):
        self.fixture.config.research_limits.model_calls = 5
        self.engine.seed(self.spec, self.row)
        calls = len(self.fixture.calls)
        with self.assertRaises(AppError) as caught:
            self.engine.web_search(self.spec, self.row, ["energy"])
        self.assertEqual(caught.exception.code, "research_budget_insufficient")
        self.assertEqual(len(self.fixture.calls), calls)
        self.assertEqual(self.engine.state["budget_projection"]["closing_calls"], 3)
        self.assertEqual(self.engine.state["budget_projection"]["shortfall"], 1)

    def test_saved_pending_answer_reduces_projection_by_one_call(self):
        self.row["pending"] = decision("answer", answer=answer_for(self.fixture.ref)).model_dump()
        result = budget_projection(self.fixture.work, self.engine.state, self.fixture.config.research_limits)
        self.assertEqual((result["question_calls"], result["closing_calls"]), (1, 3))


class ScopeIdentityTests(unittest.TestCase):
    def split(self, tasks):
        plan = QuestionPlan(tasks=tasks)
        review = QuestionScopeReview(decisions=[dict(task_id=t.id, reason="Separate obligations", parts=[
            dict(question=title, criterion_indices=[0], acceptance=[title], queries=[title], key_terms=[])
            for title in ("First obligation", "Second obligation")]) for t in plan.tasks])
        return scoped_plan(plan, review)

    def test_long_siblings_and_recursive_splits_have_stable_unique_ids(self):
        prefix = "task_" + "x" * 24
        tasks = [task_value(prefix + "_a"), task_value(prefix + "_b")]
        plan, groups = self.split(tasks)
        again, reordered_groups = self.split(list(reversed(tasks)))
        self.assertEqual(groups, reordered_groups)
        self.assertEqual({t.id for t in plan.tasks}, {t.id for t in again.tasks})
        descendants, _ = self.split([t.model_dump() for t in plan.tasks])
        self.assertEqual(len({t.id for t in descendants.tasks}), 8)
        self.assertTrue(all(len(t.id) <= 32 for t in descendants.tasks))

    def test_child_cannot_take_an_existing_parent_id(self):
        plan, groups = self.split([task_value("task_short"), task_value("task_short_a")])
        self.assertNotIn("task_short_a", groups["task_short"])
        self.assertEqual(len({t.id for t in plan.tasks}), 4)
