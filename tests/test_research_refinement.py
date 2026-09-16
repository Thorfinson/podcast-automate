"""Regression cases for lost passages, targeted edits and checkpoint replay (no live providers)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.research import run_research, validate_dossier
from podcast_automate.research_models import Evidence, Finding, ResearchDossier, SourceDocument, SourceIndex, SourceSection
from podcast_automate.research_patches import (DossierPatch, apply_patch, cached_call, edit_dossier,
                                             repair_references)
from podcast_automate.research_quality import ResearchAssessment, incremental_round, research_priority
from podcast_automate.research_retrieval import gap_queries, merge_context, references, retrieve_saved
from podcast_automate.research_review import SourceReview, SourceReviewIssue
from podcast_automate.storage import write_yaml
from tests import test_research as fixtures


def empty_patch(**changes):
    return DossierPatch(**{"updates": [], "additions": [], "coverage_updates": [],
                           "resolved_open_questions": [], "new_open_questions": [], **changes})


def document(sections):
    return SourceDocument(id="src_book", type="text", title="Synthetic book", authors=[], published_date="",
        imported_at="2026-01-01", url="https://example.org/book", final_url="https://example.org/book",
        reliability_note="Synthetic test material", uncertainties=[], raw_path="book.txt", raw_hash="raw",
        text_hash="text", sections=sections)


class RetrievalTests(unittest.TestCase):
    def test_definition_hidden_among_187_sections_is_found_with_neighbors_and_original_refs(self):
        sections = [SourceSection(id=f"sec_{i}", text="Human needs and development. General discussion. " * 25)
                    for i in range(187)]
        sections[94] = SourceSection(id="sec_definition", page=24,
            text="Singular satisfiers address one need. Synergic satisfiers also assist other needs.")
        source = document(sections)
        seen = [{"source_id": source.id, "title": source.title, "sections": [
            {"reference": f"{source.id}#{s.id}", "text": s.text, "page": s.page} for s in sections[:4]]}]
        index = SourceIndex(sources=[source], failures=[])
        context, decisions = retrieve_saved(index, ["Max Neef singular synergic satisfiers definition"], seen)
        self.assertIn("src_book#sec_definition", references(context))
        self.assertIn("src_book#sec_93", references(context))
        self.assertIn("src_book#sec_95", references(context))
        self.assertEqual(next(s for s in context[0]["sections"] if s["reference"].endswith("#sec_definition"))["page"], 24)
        self.assertIn("src_book#sec_definition", decisions[0]["new_direct_matches"])
        merged = merge_context(seen, context)
        self.assertTrue(references(seen) <= references(merged))
        again, _ = retrieve_saved(index, ["Max Neef singular synergic satisfiers definition"], merged)
        self.assertEqual(again, [])
        german, _ = retrieve_saved(index, ["Welche Originalpassagen erklären die singulären und synergischen Satisfier-Typen?"], seen)
        self.assertIn("src_book#sec_definition", references(german))

    def test_each_query_gets_its_own_ranking_and_unrelated_text_is_not_a_hit(self):
        source = document([SourceSection(id="sec_a", text="Alpha mechanism involves rotation."),
                           SourceSection(id="sec_b", text="Beta limitation prevents rotation.")])
        index = SourceIndex(sources=[source], failures=[])
        context, decisions = retrieve_saved(index, ["Alpha mechanism", "Beta limitation", "unrelated galaxy"], [])
        self.assertEqual(len(context[0]["sections"]), 2)
        self.assertEqual(decisions[0]["best_matches"], ["src_book#sec_a"])
        self.assertEqual(decisions[1]["best_matches"], ["src_book#sec_b"])
        self.assertEqual(decisions[2]["best_matches"], [])
        bounded, _ = retrieve_saved(index, ["Alpha mechanism"], [], max_chars=1)
        self.assertEqual(bounded, [])


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.context = [{"source_id": "src_a", "title": "Test", "url": "https://example.org/a", "sections": [
            {"reference": "src_a#sec_a", "text": fixtures.TEXT, "page": None}]}]
        self.dossier = fixtures.dossier_from_prompt(json.dumps({"retrieved_sources": self.context, "topic": "Test topic"}))
        self.other = self.dossier.findings[0].model_copy(deep=True, update={"id": "f_untouched", "statement": "Learning differs from inference."})
        self.dossier.findings.append(self.other)
        self.config = fixtures.TopicBrief(topic="Test topic")

    def test_targeted_edit_preserves_every_other_field_and_rejects_scope_changes(self):
        changed = self.dossier.findings[0].model_copy(update={"statement": "Energy scores configurations."})
        result = apply_patch(self.dossier, empty_patch(updates=[changed]), {"f_energy"})
        self.assertEqual(result.findings[1].model_dump(), self.other.model_dump())
        self.assertEqual(result.topic, self.dossier.topic)
        self.assertEqual(result.scope_note, self.dossier.scope_note)
        self.assertEqual(result.coverage, self.dossier.coverage)
        for bad in [empty_patch(updates=[self.other]), empty_patch(updates=[changed, changed]),
                    empty_patch(additions=[changed]), empty_patch(resolved_open_questions=["invented"]),
                    empty_patch(coverage_updates=[self.dossier.coverage[0].model_copy(update={"question_id": "q_new"})])]:
            with self.assertRaises(AppError):
                apply_patch(self.dossier, bad, {"f_energy"})

    def test_reference_repair_only_sends_affected_findings_and_rechecks_evidence(self):
        draft = self.dossier.model_copy(deep=True)
        draft.findings[0].evidence[0].excerpt = "Not a real quotation"
        calls = []
        def generate(prompt, schema):
            payload = json.loads(prompt.splitlines()[-1])
            calls.append(payload)
            self.assertEqual(schema, DossierPatch)
            self.assertEqual([f["id"] for f in payload["editable_findings"]], ["f_energy"])
            self.assertNotIn("draft", payload)
            self.assertFalse(payload["allow_additions"])
            return empty_patch(updates=[self.dossier.findings[0]])
        result = repair_references(self.folder, "repair", draft, fixtures.discovery(), self.context, self.config, generate)
        self.assertEqual(validate_dossier(result, fixtures.discovery(), self.context), [])
        self.assertEqual(result.findings[1], self.other)
        again = repair_references(self.folder, "repair", draft, fixtures.discovery(), self.context, self.config, generate)
        self.assertEqual(result, again)
        self.assertEqual(len(calls), 1)
        receipt = self.folder / "repair.json"
        data = json.loads(receipt.read_text())
        data["value"]["updates"][0]["statement"] = "Tampered"
        receipt.write_text(json.dumps(data))
        with self.assertRaises(AppError) as raised:
            repair_references(self.folder, "repair", draft, fixtures.discovery(), self.context, self.config, generate)
        self.assertEqual(raised.exception.code, "invalid_research_checkpoint")

    def test_valid_reference_does_not_make_an_invented_quote_valid(self):
        draft = self.dossier.model_copy(deep=True)
        draft.findings[0].evidence[0].excerpt = "Invented"
        with self.assertRaises(AppError) as raised:
            repair_references(self.folder, "bad", draft, fixtures.discovery(), self.context, self.config,
                              lambda p, s: empty_patch())
        self.assertEqual(raised.exception.code, "invalid_evidence")

    def test_changed_base_cannot_reuse_a_patch(self):
        generate = lambda p, s: empty_patch()
        kwargs = dict(targets={"f_energy"}, instructions="Clarify the explanation")
        edit_dossier(self.folder, "same", self.dossier, fixtures.discovery(), self.context, self.config, generate, **kwargs)
        changed = self.dossier.model_copy(update={"scope_note": "Changed scope"})
        with self.assertRaises(AppError):
            edit_dossier(self.folder, "same", changed, fixtures.discovery(), self.context, self.config, generate, **kwargs)

    def test_round_strategy_preserves_existing_legacy_receipts(self):
        old = self.folder / "old"
        old.mkdir()
        (old / "search.json").write_text("{}")
        (old / "dossier.json").write_text("{}")
        self.assertFalse(incremental_round(old))
        self.assertTrue(incremental_round(self.folder / "new"))
        interrupted = self.folder / "interrupted"
        interrupted.mkdir()
        (interrupted / "search.json").write_text("{}")
        (interrupted / "retrieval.json").write_text("{}")
        self.assertTrue(incremental_round(interrupted))
        self.assertTrue(incremental_round(self.folder / "new"))

    def test_new_source_review_replaces_obsolete_objections_but_preserves_open_questions(self):
        def review(reason):
            return SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason=reason,
                resolution="research", search_queries=[reason])], limitations=[])
        first = research_priority(self.config, self.dossier, review("Old definition missing"), None, 0)
        first["requirements"][0]["missing"] = ["Independent validation still missing"]
        current = research_priority(self.config, self.dossier, review("New causal step missing"), first, 1,
                                    current_objections_only=True)
        self.assertNotIn("Old definition missing", current["blocking_gaps"])
        self.assertIn("New causal step missing", current["blocking_gaps"])
        self.assertIn("Independent validation still missing", current["blocking_gaps"])
        self.assertFalse(current["passed"])
        self.assertEqual(gap_queries(current), ["New causal step missing"])


class RefinementPipelineTests(unittest.TestCase):
    setUp = fixtures.ResearchTests.setUp
    model = fixtures.ResearchTests.model

    def test_old_run_stopped_before_full_synthesis_resumes_with_a_patch_and_reuses_search(self):
        assessments, patches = 0, 0
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"<h1>Fixture", ("<h1>" + url).encode()), "text/html", url)
        def model(prompt, schema, directory, **kwargs):
            nonlocal assessments, patches
            version = kwargs["prompt_version"]
            if schema is ResearchAssessment:
                assessments += 1
                assessment = fixtures.assessment_from_prompt(prompt)
                if assessments == 1:
                    assessment.requirements[0].explanation = False
                    assessment.requirements[0].missing = ["Explain the energy comparison."]
                    assessment.requirements[0].search_queries = ["energy configurations comparison"]
                return assessment, {}
            if version == "research_quality.v1.search":
                extra = fixtures.discovery()
                extra.candidates[0].url = "https://example.org/additional"
                return extra, {"research_performed": True}
            if version == "research_quality.v1.dossier":
                raise AppError("Stopped", code="interrupted", status="blocked")
            if schema is DossierPatch:
                patches += 1
            return self.model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            with patch("podcast_automate.research_quality.incremental_round", return_value=False):
                stopped = run_research(self.root)
            self.assertEqual(stopped.status, "blocked")
            downloads = self.download.call_count
            resumed = run_research(self.root, resume=True)
        self.assertEqual(resumed.status, "completed", str(resumed.stages["completeness"].error))
        self.assertEqual(self.download.call_count, downloads)
        self.assertEqual(patches, 1)

    def prepare_hidden_passage(self):
        # One real imported HTML paragraph is deliberately omitted from the initial model context.
        extra = b"<p>Singular satisfiers address one need. Synergic satisfiers also assist other needs.</p>"
        self.download.return_value = (fixtures.HTML.replace(b"</main>", extra + b"</main>"),
                                      "text/html", "https://example.org/paper0")
        self.config.research_limits.search_rounds = 1
        self.config.research_limits.sources = 1
        write_yaml(self.root / "project.yaml", self.config.model_dump())
        from podcast_automate.research import source_context
        def initial_context(*args, **kwargs):
            context = source_context(*args, **kwargs)
            for source in context:
                source["sections"] = [s for s in source["sections"] if "Singular satisfiers" not in s["text"]]
            return context
        selector = patch("podcast_automate.research.source_context", side_effect=initial_context)
        selector.start()
        self.addCleanup(selector.stop)

    def local_model(self, prompt, schema, directory, **kwargs):
        if schema is SourceReview and kwargs["prompt_version"] == "research_review.v6":
            return SourceReview(issues=[SourceReviewIssue(finding_id="f_energy",
                reason="Define singular and synergic satisfiers.", resolution="research",
                search_queries=["singular synergic satisfiers definition"])], limitations=[]), {}
        if schema is DossierPatch:
            payload = json.loads(prompt.splitlines()[-1])
            section = next(s for source in payload["sources"] for s in source["sections"] if "Singular satisfiers" in s["text"])
            self.assertNotIn("previous_dossier", payload)
            return empty_patch(updates=[Finding(id="f_energy", kind="definition",
                statement="Singular satisfiers meet one need; synergic satisfiers also assist others.",
                evidence=[Evidence(reference=section["reference"], excerpt="Singular satisfiers")])]), {}
        return self.model(prompt, schema, directory, **kwargs)

    def test_saved_passages_close_gap_even_when_web_and_source_budgets_are_exhausted(self):
        self.prepare_hidden_passage()
        events = []
        def model(*args, **kwargs):
            events.append(kwargs["prompt_version"])
            return self.local_model(*args, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(self.root)
            count = len(events)
            resumed = run_research(self.root, resume=True)
        self.assertEqual(run.status, "completed", str(run.stages["completeness"].error))
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(len(events), count)
        self.assertEqual(self.download.call_count, 1)
        self.assertIn("research_patch.v1.evidence", events)
        self.assertNotIn("research_quality.v1.search", events)
        self.assertEqual(events.count("research_dossier.v4"), 1)

    def test_pause_after_local_patch_replays_it_without_model_or_network_repetition(self):
        self.prepare_hidden_passage()
        patch_calls, paused = 0, False
        def model(prompt, schema, directory, **kwargs):
            nonlocal patch_calls, paused
            if schema is DossierPatch:
                patch_calls += 1
            if kwargs["prompt_version"].endswith("grounding_0_routed") and not paused:
                paused = True
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.local_model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            first = run_research(self.root)
            self.assertEqual(first.status, "waiting_for_quota")
            self.assertFalse((self.root / "research/latest.json").exists())
            resumed = run_research(self.root, resume=True)
        self.assertEqual(resumed.status, "completed", str(resumed.stages["completeness"].error))
        self.assertEqual(patch_calls, 1)
        self.assertEqual(self.download.call_count, 1)

    def test_local_hit_cannot_bypass_an_unresolved_independent_review(self):
        self.prepare_hidden_passage()
        def model(prompt, schema, directory, **kwargs):
            if schema is SourceReview:
                return SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason="Independent test missing.",
                    resolution="research", search_queries=["singular synergic satisfiers independent test"])], limitations=[]), {}
            return self.local_model(prompt, schema, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(self.root)
        self.assertEqual(run.status, "blocked")
        self.assertFalse((self.root / "research/latest.json").exists())


if __name__ == "__main__":
    unittest.main()
