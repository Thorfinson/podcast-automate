"""Works the editor provides: stored beside the brief, listed from the blocked questions, never notes."""
import json
import tempfile
import unittest
from pathlib import Path

from podcast_automate import provided_works
from podcast_automate.errors import AppError
from podcast_automate.research_models import SourceDocument, SourceSection, is_idea


class ProvidedWorksTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # Resolved, as Studio.root() hands it to add() (an 8.3 TEMP differs from its long form).
        self.root = Path(temp.name).resolve()

    def test_a_work_is_stored_whole_with_its_citation_and_questions(self):
        pdf = b"%PDF-1.4 a scanned book"
        row = provided_works.add(self.root, pdf, citation="  Kuran:  Private Truths, Public Lies (1995) ", tasks=["t_kuran"])
        self.assertEqual(row["citation"], "Kuran: Private Truths, Public Lies (1995)")
        self.assertEqual(provided_works.work_path(self.root, row).read_bytes(), pdf)
        self.assertEqual(provided_works.inventory(self.root), [row])
        # The same file for one more question joins that question instead of a second copy.
        again = provided_works.add(self.root, pdf, citation="Kuran: Private Truths, Public Lies (1995)", tasks=["t_other"])
        self.assertEqual((len(provided_works.inventory(self.root)), again["tasks"]), (1, ["t_kuran", "t_other"]))
        self.assertEqual(provided_works.candidate(row).published_date, "1995")
        for raw, citation in ((pdf, "x"), (b"\x00\x01binary", "Kuran (1995)"), (b"", "Kuran (1995)")):
            with self.subTest(citation=citation, raw=raw[:8]), self.assertRaises(AppError):
                provided_works.add(self.root, raw, citation=citation)

    def test_the_list_names_works_of_blocked_and_retried_questions_only(self):
        row = provided_works.add(self.root, b"Plain text of the paper.", citation="Merton: The Self-Fulfilling Prophecy (1948)")
        question = lambda id, status, **extra: {"id": id, "question": f"{id}?", "status": status, **extra}
        ledger = {"questions": [
            question("blocked", "blocked", outcome="search_block", primary_works=["Merton: The Self-Fulfilling Prophecy (1948)"]),
            question("retry", "researching", advice={"recommendation": "retry"}, primary_works=["Kuran (1995)", "Merton: The Self-Fulfilling Prophecy (1948)"]),
            question("fresh", "researching", primary_works=["Never tried (2020)"]),
            question("waits", "blocked", outcome="prerequisite_block", primary_works=["Prerequisite (2001)"]),
            question("gap", "blocked", outcome="search_block", accepted_gap=True, primary_works=["Accepted (1999)"]),
            question("done", "verified", primary_works=["Read (1990)"])]}
        listed = {entry["work"]: entry for entry in provided_works.overview(self.root, ledger)["missing"]}
        self.assertEqual(set(listed), {"Merton: The Self-Fulfilling Prophecy (1948)", "Kuran (1995)"})
        merton = listed["Merton: The Self-Fulfilling Prophecy (1948)"]
        self.assertEqual((merton["state"], merton["provided"], [t["id"] for t in merton["tasks"]]), ("blocked", row["id"], ["blocked", "retry"]))
        self.assertEqual((listed["Kuran (1995)"]["state"], listed["Kuran (1995)"]["provided"]), ("retrying", None))

    def test_a_provided_copy_is_a_published_work_and_a_note_stays_an_idea(self):
        document = lambda **fields: SourceDocument(**{
            "id": "src_a", "type": "text", "title": "Kuran (1995)", "authors": [], "published_date": "1995",
            "imported_at": "x", "url": "", "final_url": "", "reliability_note": "x", "uncertainties": [], "raw_path": "r",
            "raw_hash": "h", "text_hash": "t", "sections": [SourceSection(id="sec_a", text="Text.")], **fields})
        self.assertFalse(is_idea(document(citation="Kuran: Private Truths, Public Lies (1995)", source_type="primary_work")))
        self.assertTrue(is_idea(document(source_type="idea")))
        self.assertTrue(is_idea(document()))
        # Saved documents without a citation keep their old form, so their hashes stay valid.
        self.assertNotIn("citation", json.loads(document().model_dump_json()))

    def test_a_provided_copy_reaches_the_evidence_check_as_the_work_it_cites(self):
        # 2026-10-02: the read passages carried no citation, and the check took the copy without an address for notes.
        from podcast_automate.research_evidence import user_notes
        from podcast_automate.research_models import SourceIndex
        from podcast_automate.research_reader import SourceReader
        from podcast_automate.research_tasks import ReaderWindow
        section = SourceSection(id="sec_a", text="Text of the provided work.")
        base = {"type": "text", "title": "Kuran (1995)", "authors": [], "published_date": "1995", "imported_at": "x",
                "url": "", "final_url": "", "reliability_note": "x", "uncertainties": [], "raw_path": "r", "raw_hash": "h",
                "sections": [section]}
        work = SourceDocument(id="src_work", text_hash="w", citation="Kuran: Private Truths, Public Lies (1995)",
                              source_type="primary_work", **base)
        note = SourceDocument(id="src_note", text_hash="n", **base)
        reader = SourceReader(SourceIndex(sources=[work, note], failures=[]))
        context = {row["source_id"]: row for row in reader.read(
            [ReaderWindow(reference=f"{sid}#sec_a", before=0, after=0) for sid in ("src_work", "src_note")])["context"]}
        self.assertEqual(context["src_work"]["citation"], "Kuran: Private Truths, Public Lies (1995)")
        self.assertNotIn("citation", context["src_note"], "every other source is read as before")
        self.assertFalse(user_notes(context["src_work"]))
        self.assertTrue(user_notes(context["src_note"]))


if __name__ == "__main__":
    unittest.main()
