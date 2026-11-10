"""Works the editor provides: stored beside the brief, listed from the blocked questions, never notes."""
import json
import tempfile
import unittest
from pathlib import Path

from podcast_automate import provided_works
from podcast_automate.errors import AppError
from podcast_automate.research_models import SourceDocument, SourceSection, is_idea


def epub(chapters, *, spine, encrypted=()):
    """A minimal EPUB: its mimetype first and uncompressed, the container, a package with title, author and reading
    order, the chapters; ``encrypted`` names the files an encryption.xml marks."""
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        archive.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                         'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                         '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                         '</rootfiles></container>')
        manifest = "".join(f'<item id="{name.split(".")[0]}" href="{name}" media-type="application/xhtml+xml"/>'
                           for name in chapters)
        archive.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                         'version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Debt</dc:title>'
                         '<dc:creator>David Graeber</dc:creator><dc:date>2011-07-12</dc:date><dc:language>en</dc:language>'
                         f'</metadata><manifest>{manifest}</manifest><spine>'
                         + "".join(f'<itemref idref="{item}"/>' for item in spine) + "</spine></package>")
        for name, body in chapters.items():
            archive.writestr(f"OEBPS/{name}", f'<html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>')
        if encrypted:
            archive.writestr("META-INF/encryption.xml", '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
                             'xmlns:enc="http://www.w3.org/2001/04/xmlenc#">' + "".join(
                                 f'<enc:EncryptedData><enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData>'
                                 '</enc:EncryptedData>' for uri in encrypted) + "</encryption>")
    return buffer.getvalue()


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

    def test_refused_addresses_of_open_questions_are_listed_for_the_editor_to_fetch(self):
        """D-199, 2026-11-10: the user asked for the refused contents as a list to click through, save in the browser and
        upload for that very address. Only refusals count (HTTP 401, 402, 403, 451 or a check page), and only for
        questions that still need them."""
        from podcast_automate.research_ledger import save_value
        state = self.root / "runs/run_1/question_research/state.json"
        state.parent.mkdir(parents=True)
        refused = "https://publisher.example/paper"
        receipt = {"candidates": [{"url": refused, "title": "The paper the question needs"}],
                   "retrieval_failures": [
                       {"source": refused, "reason": "Quellenabruf fehlgeschlagen (HTTP 403).", "code": "source_download_failed"},
                       {"source": "https://springer.example/chapter", "reason": "Quelle liefert eine Zugriffssperre statt Artikeltext.",
                        "code": "source_access_blocked"},
                       {"source": "https://gone.example/page", "reason": "Quellenabruf fehlgeschlagen (HTTP 404).",
                        "code": "source_download_failed"},
                       {"source": "https://scan.example/book.pdf", "reason": "PDF enthält zu wenig lesbaren Text.",
                        "code": "source_unreadable"}]}
        save_value(state, {"tasks": {"open": {"search_receipts": [receipt]}, "done": {"search_receipts": [
            {"retrieval_failures": [{"source": "https://other.example/x", "reason": "Quellenabruf fehlgeschlagen (HTTP 403).",
                                     "code": "source_download_failed"}]}]}}})
        ledger = {"questions": [{"id": "open", "question": "Open?", "status": "blocked", "outcome": "search_block"},
                                {"id": "done", "question": "Done?", "status": "verified"}]}
        listed = provided_works.overview(self.root, ledger, state)["refused"]
        self.assertEqual([(row["source"], row["status"], row["state"]) for row in listed],
                         [(refused, 403, "blocked"), ("https://springer.example/chapter", None, "blocked")])
        self.assertEqual((listed[0]["title"], listed[0]["tasks"], listed[0]["provided"]),
                         ("The paper the question needs", [{"id": "open", "question": "Open?"}], None))
        # The editor's copy, uploaded at its address, marks the row as provided and is asked for that question.
        row = provided_works.add(self.root, b"%PDF-1.4 the paper", citation="The paper the question needs", tasks=["open"],
                                 source=refused)
        self.assertEqual(provided_works.overview(self.root, ledger, state)["refused"][0]["provided"], row["id"])
        with self.assertRaises(AppError):
            provided_works.add(self.root, b"%PDF-1.4 x", citation="A paper (2020)", source="javascript:alert(1)")
        self.assertEqual(provided_works.overview(self.root, ledger)["refused"], [], "without the run's state nothing is listed")

    def test_an_epub_is_taken_and_read_chapter_by_chapter_but_a_copy_protected_one_is_refused(self):
        """D-198, 2026-11-10: the books a research needs are mostly sold as EPUB; an upload took only PDF, a saved page or
        text. Chapters are read in the book's own order, not the archive's."""
        from podcast_automate.sources import extract
        long = " Debts were written down long before coins were struck, on tablets and on tally sticks."
        book = epub({"ch2.xhtml": f"<p>The second chapter about debts.{long * 2}</p>",
                     "ch1.xhtml": f"<h1>One</h1><p>The first chapter.{long * 2}</p>"}, spine=["ch1", "ch2"])
        row = provided_works.add(self.root, book, citation="Graeber: Debt (2011)", tasks=["t_debt"])
        self.assertTrue(row["path"].endswith(".epub"))
        self.assertEqual(provided_works.work_path(self.root, row).read_bytes(), book)
        kind, suffix, metadata, sections = extract(book, "", "book.epub", book=True)
        self.assertEqual((kind, suffix, metadata["title"], metadata["authors"]), ("text", ".epub", "Debt", ["David Graeber"]))
        text = " ".join(section.text for section in sections)
        self.assertLess(text.index("The first chapter."), text.index("The second chapter about debts."))
        # Obfuscated fonts are common and harmless; encrypted chapters are copy protection.
        fonts = epub({"ch1.xhtml": f"<p>Readable.{long * 3}</p>"}, spine=["ch1"], encrypted=["OEBPS/fonts/a.otf"])
        self.assertIn("Readable.", " ".join(s.text for s in extract(fonts, "", "fonts.epub", book=True)[3]))
        locked = epub({"ch1.xhtml": f"<p>Locked.{long * 3}</p>"}, spine=["ch1"], encrypted=["OEBPS/ch1.xhtml"])
        with self.assertRaises(AppError) as refused:
            extract(locked, "", "locked.epub", book=True)
        self.assertEqual((refused.exception.code, refused.exception.details), ("source_unreadable", {"drm": True}))

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
