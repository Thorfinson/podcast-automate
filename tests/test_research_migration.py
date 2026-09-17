"""Historical disk formats must remain readable after retiring their execution engine."""
import json

from podcast_automate.errors import AppError
from podcast_automate.research import source_context
from podcast_automate.research_ledger import bootstrap_legacy, save_value
from podcast_automate.research_models import SourceIndex
from podcast_automate.sources import import_source
from podcast_automate.storage import digest, write_json
from tests import research_fixtures as fixtures


class LegacyResearchTests(fixtures.ResearchProjectCase):
    def setUp(self):
        super().setUp()
        self.work = self.root / "runs/legacy"
        self.discovery = fixtures.discovery()
        source, _ = import_source(self.discovery.candidates[0], self.root, self.work.name)
        self.index = SourceIndex(sources=[source], failures=[])
        self.context = source_context(self.index, self.discovery)
        self.dossier = fixtures.dossier_from_prompt(json.dumps({
            "topic": self.config.topic, "retrieved_sources": self.context}))

    def restore(self, work=None):
        return bootstrap_legacy(self.root, work or self.work, self.discovery, self.index, None, self.context)

    def save_draft(self, folder, name, dossier):
        value = dossier.model_dump()
        if name.endswith("_applied.json"):
            write_json(folder / name, {"dossier": value, "dossier_hash": digest(value)})
        else:
            save_value(folder / name, value)

    def test_all_historical_draft_layouts_import_without_running_old_models(self):
        names = ["dossier.json", "dossier_references.json", "dossier_patch_applied.json",
                 "dossier_patch_references_applied.json"]
        names += [f"grounding_repair_{n}{suffix}.json" for n in range(3) for suffix in ("", "_references")]
        names += [f"grounding_patch_{n}{suffix}_applied.json" for n in range(3) for suffix in ("", "_references")]
        for name in names:
            with self.subTest(receipt=name):
                work = self.work / name.removesuffix(".json")
                folder = work / "completeness/round_001"
                self.save_draft(folder, name, self.dossier)
                _, index, dossier, _, migration = self.restore(work)
                self.assertEqual(dossier, self.dossier)
                self.assertEqual(index, self.index)
                self.assertEqual(migration["drafts"], [str((folder / name).relative_to(work))])
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual(self.calls, [])

    def test_later_valid_repairs_win_and_invalid_quotes_do_not_replace_them(self):
        folder = self.work / "completeness/round_001"
        self.save_draft(folder, "dossier.json", self.dossier)
        repaired = self.dossier.model_copy(deep=True)
        repaired.findings[0].statement = "The fixture assigns energies to configurations."
        self.save_draft(folder, "grounding_repair_0.json", repaired)
        invalid = repaired.model_copy(deep=True)
        invalid.findings[0].evidence[0].excerpt = "An invented quotation"
        self.save_draft(folder, "grounding_repair_0_references.json", invalid)
        _, _, restored, _, migration = self.restore()
        self.assertEqual(restored, repaired)
        self.assertEqual(len(migration["drafts"]), 2)

    def test_saved_search_and_downloads_extend_history_without_new_requests(self):
        extra = fixtures.discovery(count=2)
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"These sentences", url.encode()), "text/html", url)
        source, _ = import_source(extra.candidates[1], self.root, self.work.name)
        index = SourceIndex(sources=[*self.index.sources, source], failures=[])
        folder = self.work / "completeness/round_001"
        save_value(folder / "search.json", extra.model_dump())
        save_value(folder / "retrieval.json", {"index": index.model_dump(), "processed_urls": [source.url]})
        context = source_context(index, self.discovery)
        write_json(folder / "source_context.json", context)
        review = {"issues": [{"finding_id": "f_energy", "reason": "Missing independent evidence",
                              "resolution": "research", "search_queries": ["independent energy test"]}], "limitations": []}
        save_value(folder / "grounding_0_routed.json", review)
        discovery, restored, _, selected, migration = self.restore()
        self.assertEqual(discovery.candidates, extra.candidates)
        self.assertEqual(restored, index)
        self.assertEqual(selected, context)
        self.assertEqual(migration["last_review"], review)
        self.assertEqual(self.download.call_count, 2)

    def test_changed_receipt_hashes_are_rejected_before_migration(self):
        for name, value in (("search.json", self.discovery.model_dump()),
                            ("retrieval.json", {"index": self.index.model_dump()}),
                            ("dossier.json", self.dossier.model_dump())):
            with self.subTest(receipt=name):
                work = self.work / name.removesuffix(".json")
                write_json(work / "completeness/round_001" / name, {"value": value, "sha256": "changed"})
                with self.assertRaises(AppError) as error:
                    self.restore(work)
                self.assertEqual(error.exception.code, "invalid_research_checkpoint")
        write_json(self.work / "completeness/round_001/dossier_patch_applied.json",
                   {"dossier": self.dossier.model_dump(), "dossier_hash": "changed"})
        with self.assertRaises(AppError) as error:
            self.restore()
        self.assertEqual(error.exception.code, "invalid_research_checkpoint")

    def test_missing_sources_changed_passages_and_changed_originals_are_rejected(self):
        missing = self.work / "missing"
        save_value(missing / "completeness/round_001/retrieval.json", {"index": {"sources": [], "failures": []}})
        changed = self.work / "changed"
        context = json.loads(json.dumps(self.context))
        context[0]["sections"][0]["text"] = "Changed source passage"
        write_json(changed / "completeness/round_001/source_context.json", context)
        for work in (missing, changed):
            with self.subTest(work=work.name), self.assertRaises(AppError) as error:
                self.restore(work)
            self.assertEqual(error.exception.code, "invalid_source_snapshot")
        (self.root / self.index.sources[0].raw_path).write_bytes(b"Changed original")
        with self.assertRaises(AppError) as error:
            self.restore()
        self.assertEqual(error.exception.code, "invalid_source_snapshot")
