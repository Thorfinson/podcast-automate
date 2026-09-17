"""Regression cases for lost passages, targeted edits and checkpoint replay (no live providers)."""
import json
import tempfile
import unittest
from pathlib import Path

from podcast_automate.errors import AppError
from podcast_automate.research import validate_dossier
from podcast_automate.research_models import SourceDocument, SourceIndex, SourceSection
from podcast_automate.research_patches import DossierPatch, apply_patch, edit_dossier, repair_references
from podcast_automate.research_reader import SourceReader
from podcast_automate.research_tasks import ReaderWindow
from podcast_automate.research_retrieval import merge_context, references
from tests import research_fixtures as fixtures


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
        reader = SourceReader(index)
        result = reader.search("Max Neef singular synergic satisfiers definition")
        self.assertEqual(result["candidates"][0]["reference"], "src_book#sec_definition")
        context = reader.read([ReaderWindow(reference=result["candidates"][0]["reference"], before=1, after=1)])["context"]
        self.assertIn("src_book#sec_definition", references(context))
        self.assertIn("src_book#sec_93", references(context))
        self.assertIn("src_book#sec_95", references(context))
        self.assertEqual(next(s for s in context[0]["sections"] if s["reference"].endswith("#sec_definition"))["page"], 24)
        merged = merge_context(seen, context)
        self.assertTrue(references(seen) <= references(merged))
        self.assertEqual(merge_context(merged, context), merged)
        german = reader.search("Welche Originalpassagen erklären die singulären und synergischen Satisfier-Typen?")
        self.assertEqual(german["candidates"][0]["reference"], "src_book#sec_definition")

    def test_each_query_gets_its_own_ranking_and_unrelated_text_is_not_a_hit(self):
        source = document([SourceSection(id="sec_a", text="Alpha mechanism involves rotation."),
                           SourceSection(id="sec_b", text="Beta limitation prevents rotation.")])
        index = SourceIndex(sources=[source], failures=[])
        reader = SourceReader(index)
        for query, expected in (("Alpha mechanism", "src_book#sec_a"), ("Beta limitation", "src_book#sec_b")):
            self.assertEqual(reader.search(query)["candidates"][0]["reference"], expected)
        self.assertEqual(reader.search("unrelated galaxy")["candidates"], [])
        windows = [ReaderWindow(reference=f"src_book#sec_{name}", before=0, after=0) for name in ("a", "b")]
        self.assertEqual(len(reader.read(windows)["context"][0]["sections"]), 2)
        bounded = reader.read(windows, max_chars=1)
        self.assertEqual(bounded["context"], [])
        self.assertEqual(bounded["deferred"], [window.reference for window in windows])


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


if __name__ == "__main__":
    unittest.main()
