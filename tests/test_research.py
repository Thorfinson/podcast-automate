import io
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.research import DISCOVERY_VERSION, run_research, validate_dossier
from podcast_automate.research_dates import run_date
from podcast_automate.research_patches import DossierPatch
from podcast_automate.research_review import SourceReview, SourceReviewIssue
from podcast_automate.research_models import (Evidence, Finding, QuestionCoverage, ResearchDiscovery, ResearchDossier,
                                              SourceCandidate, SourceIndex, SourceSection)
from podcast_automate.research_quality import requirements_for
from podcast_automate.research_tasks import QuestionPlan, ReopenPlan, ResearchDecision
from podcast_automate.run_budget import approve_research_gap
from podcast_automate.runner import status
from podcast_automate.sources import (blocked_sources, canonical_url, extract, import_source, public_url,
                                      PublicRedirect)
from podcast_automate.storage import read_yaml, write_json, write_yaml
from tests import research_fixtures as fixtures
from tests.research_fixtures import TEXT, HTML, discovery, dossier_from_prompt
from tests.research_fixtures import composed_generation
from tests.question_fixtures import complete_fixture_response, decision, task_value


def setUpModule():
    # These tests follow a dossier a model composes and audits (prompt generation 2), as runs started before
    # 2026-10-01 still do; the assembled dossier of a run started now has its own tests in test_question_research.
    composed = composed_generation()
    composed.start()
    unittest.addModuleCleanup(composed.stop)


class SourceTests(unittest.TestCase):
    def test_html_extracts_article_metadata_and_ignores_scripts(self):
        kind, suffix, metadata, sections = extract(HTML, "text/html; charset=utf-8", "page")
        text = " ".join(s.text for s in sections)
        self.assertEqual((kind, suffix), ("html", ".html"))
        self.assertEqual(metadata["authors"], ["Test Author"])
        self.assertIn(TEXT, text)
        self.assertNotIn("invent sources", text)
        self.assertNotIn("Navigation", text)
        self.assertEqual(sections, extract(HTML, "text/html", "page")[3])

    def test_a_provided_book_is_read_beyond_the_page_limit_of_a_downloaded_pdf(self):
        from podcast_automate import pdf_text
        writer, buffer = PdfWriter(), io.BytesIO()
        for _ in range(pdf_text.MAX_PAGES + 1):
            writer.add_blank_page(72, 72)
        writer.write(buffer)
        with self.assertRaises(pdf_text.UnreadablePdf):
            pdf_text.extract_pdf(buffer.getvalue())
        self.assertEqual(pdf_text.extract_pdf(buffer.getvalue(), book=True)["metadata"]["extraction_coverage"]["pages_total"],
                         pdf_text.MAX_PAGES + 1)

    def test_real_pdf_extraction_preserves_page_reference(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(("BT /F1 12 Tf 30 700 Td (" + TEXT + ") Tj ET").encode())
        page[NameObject("/Contents")] = stream
        buffer = io.BytesIO()
        writer.write(buffer)
        kind, _, _, sections = extract(buffer.getvalue(), "application/pdf", "paper.pdf")
        self.assertEqual(kind, "pdf")
        self.assertIn("Models assign an energy", sections[0].text)
        self.assertEqual(sections[0].page, 1)
        # A readable PDF whose own title field holds only a space is imported under the search result's title.
        writer.add_metadata({"/Title": " "})
        buffer = io.BytesIO()
        writer.write(buffer)
        candidate = SourceCandidate(url="https://example.org/paper.pdf", title="Search title", authors=[],
                                    published_date="", rationale="Test", primary_source=False)
        with tempfile.TemporaryDirectory() as temporary:
            document, _ = import_source(candidate, Path(temporary), "run_test",
                                        downloaded=(buffer.getvalue(), "application/pdf", candidate.url))
        self.assertEqual(document.title, "Search title")
        self.assertIn("Models assign an energy", document.sections[0].text)

    def open_access_case(self, url, title, answers, headers=None, env=None):
        """Import one candidate while ``download`` answers from ``answers``; returns the document and the fetched URLs.
        An address a case does not answer is not found, as a free service without the work answers. Unpaywall's
        contact address and CORE's key are set only by ``env``, whatever the machine running the tests has."""
        fetched = []
        def fetch(address, sent=None):
            fetched.append(address)
            if headers is not None and sent:
                headers[address] = sent
            answer = answers.get(address, AppError("Quellenabruf fehlgeschlagen (HTTP 404).", code="source_download_failed",
                                                   details={"http_status": 404}))
            if isinstance(answer, Exception):
                raise answer
            return answer
        candidate = SourceCandidate(url=url, title=title, authors=[], published_date="", rationale="Test", primary_source=False)
        environment = {key: value for key, value in os.environ.items()
                       if key not in {"PLA_UNPAYWALL_EMAIL", "PLA_CORE_API_KEY"}} | (env or {})
        with patch.dict(os.environ, environment, clear=True), patch("podcast_automate.sources.download", side_effect=fetch), \
                tempfile.TemporaryDirectory() as temporary:
            try:
                return import_source(candidate, Path(temporary), "run_test")[0], fetched
            except AppError as exc:
                return exc, fetched

    def test_a_refused_download_is_replaced_by_a_free_copy_of_the_same_work(self):
        blocked = AppError("Quellenabruf fehlgeschlagen (HTTP 403).", code="source_download_failed", details={"http_status": 403})
        work = {"best_oa_location": {"is_oa": True, "pdf_url": None, "landing_page_url": "https://publisher.example/abstract"},
                "locations": [{"is_oa": True, "pdf_url": "https://repository.example/paper.pdf"},
                              {"is_oa": False, "pdf_url": "https://paywall.example/paper.pdf"}]}
        url = "https://www.pnas.org/doi/10.1073/pnas.1915006117"
        document, fetched = self.open_access_case(url, "Measuring the predictability", {
            url: blocked,
            "https://api.openalex.org/works/doi:10.1073/pnas.1915006117": (json.dumps(work).encode(), "application/json", ""),
            "https://repository.example/paper.pdf": (HTML, "text/html", "https://repository.example/paper.pdf")})
        # The copy keeps the address it was found under as its identity and names where it was read.
        self.assertEqual((document.url, document.final_url), (url, "https://repository.example/paper.pdf"))
        self.assertIn("Open-access copy of the same work found via OpenAlex", document.reliability_note)
        self.assertIn(TEXT, " ".join(s.text for s in document.sections))
        # An abstract-only landing page and a closed location are never fetched.
        self.assertEqual(fetched, [url, "https://api.openalex.org/works/doi:10.1073/pnas.1915006117", "https://repository.example/paper.pdf"])

    def test_without_a_doi_only_an_exactly_matching_title_counts_and_a_closed_work_keeps_its_refusal(self):
        unreachable = AppError("Quellenabruf fehlgeschlagen (URLError).", code="source_download_failed",
                               details={"network_error": "URLError"})
        url = "https://www.uu.nl/sites/default/files/scheffer_science_2012.pdf"
        search = "https://api.openalex.org/works?search=Anticipating%20Critical%20Transitions&per_page=3"
        results = {"results": [{"title": "Anticipating critical transitions in finance", "locations": [{"is_oa": True, "pdf_url": "https://other.example/a.pdf"}]},
                               {"title": "Anticipating Critical Transitions", "locations": [{"is_oa": True, "pdf_url": "https://pmc.example/scheffer.pdf"}]}]}
        document, fetched = self.open_access_case(url, "Anticipating Critical Transitions", {
            url: unreachable, search: (json.dumps(results).encode(), "application/json", ""),
            "https://pmc.example/scheffer.pdf": (HTML, "text/html", "https://pmc.example/scheffer.pdf")})
        self.assertEqual(document.final_url, "https://pmc.example/scheffer.pdf")
        self.assertNotIn("https://other.example/a.pdf", fetched)
        closed_url = "https://pubs.aeaweb.org/doi/pdfplus/10.1257/jep.14.3.137"
        refused = AppError("Quellenabruf fehlgeschlagen (HTTP 403).", code="source_download_failed", details={"http_status": 403})
        error, _ = self.open_access_case(closed_url, "Collective Action", {
            closed_url: refused,
            "https://api.openalex.org/works/doi:10.1257/jep.14.3.137": (json.dumps({"locations": []}).encode(), "application/json", "")})
        self.assertIs(error, refused)

    REFUSED = AppError("Quellenabruf fehlgeschlagen (HTTP 403).", code="source_download_failed", details={"http_status": 403})

    def test_without_an_openalex_copy_the_next_free_services_are_asked_in_turn(self):
        # 2026-10-01: for 26 of 34 addresses Asimov's run could not read, OpenAlex alone had no copy.
        url = "https://www.sciencedirect.com/science/article/pii/10.1016/j.joep.2024.102700"
        doi = "10.1016/j.joep.2024.102700"
        scholar = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=openAccessPdf"
        document, fetched = self.open_access_case(url, "Fairness in the field", {
            url: self.REFUSED,
            f"https://api.openalex.org/works/doi:{doi}": (json.dumps({"locations": []}).encode(), "application/json", ""),
            scholar: (json.dumps({"openAccessPdf": {"url": "https://repository.example/fair.pdf"}}).encode(), "application/json", ""),
            "https://repository.example/fair.pdf": (HTML, "text/html", "https://repository.example/fair.pdf")})
        self.assertIn("Open-access copy of the same work found via Semantic Scholar", document.reliability_note)
        # Unpaywall and CORE need the user's contact address or key and are not asked without them.
        self.assertFalse(any("unpaywall" in address or "core.ac.uk" in address for address in fetched))
        self.assertEqual(fetched[-2:], [scholar, "https://repository.example/fair.pdf"])

    def test_a_pmc_article_behind_a_captcha_is_read_from_europe_pmc(self):
        url = "https://pmc.ncbi.nlm.nih.gov/articles/PMC3735133/"
        xml = (b"<?xml version='1.0'?><article><front><article-title>The Oregon experiment</article-title></front><body>"
               b"<sec><p>" + TEXT.encode() + b"</p></sec></body></article>")
        full_text = "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC3735133/fullTextXML"
        document, _ = self.open_access_case(url, "The Oregon experiment", {
            url: (self.CHALLENGE, "text/html", url), full_text: (xml, "application/xml", full_text)})
        self.assertIn("Open-access copy of the same work found via Europe PMC", document.reliability_note)
        self.assertIn(TEXT, " ".join(s.text for s in document.sections))

    def test_unpaywall_and_core_are_asked_with_the_users_address_and_key(self):
        url = "https://www.cambridge.org/core/journals/x/article/10.1017/S0003055419000018"
        core = "https://api.core.ac.uk/v3/search/works?q=doi%3A%2210.1017/S0003055419000018%22&limit=3"
        sent, temporary = {}, tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        store = Path(temporary.name) / "core_usage.json"
        answers = {
            url: self.REFUSED,
            core: (json.dumps({"results": [{"downloadUrl": "https://core.example/ostrom.pdf"}]}).encode(), "application/json", ""),
            "https://core.example/ostrom.pdf": (HTML, "text/html", "https://core.example/ostrom.pdf")}
        env = {"PLA_UNPAYWALL_EMAIL": "reader@example.org", "PLA_CORE_API_KEY": "core-test-key", "PLA_CORE_USAGE_STORE": str(store)}
        document, fetched = self.open_access_case(url, "Governing the commons", answers, headers=sent, env=env)
        self.assertIn("found via CORE", document.reliability_note)
        self.assertEqual(sent[core], {"Authorization": "Bearer core-test-key"})
        unpaywall = next(address for address in fetched if "api.unpaywall.org" in address)
        self.assertIn("email=reader%40example.org", unpaywall)
        self.assertLess(fetched.index(unpaywall), fetched.index(core))
        # Every CORE search counts against the day's allowance in one shared file (2026-10-01: about 1000 a day).
        self.assertEqual(json.loads(store.read_text(encoding="utf-8"))["calls"], 1)
        # With the day's calls used up, CORE is skipped and the refusal stands.
        refused, skipped = self.open_access_case(url, "Governing the commons", answers, env={**env, "PLA_CORE_DAILY_LIMIT": "1"})
        self.assertIs(refused, self.REFUSED)
        self.assertNotIn(core, skipped)
        self.assertEqual(json.loads(store.read_text(encoding="utf-8"))["calls"], 1)
        # A Studio worker gets the key from the Studio's settings instead of the variable (D-167).
        from podcast_automate.sources import use_core_key
        use_core_key("core-studio-key")
        self.addCleanup(use_core_key, None)
        handed, _ = self.open_access_case(url, "Governing the commons", answers, headers=sent,
                                          env={"PLA_CORE_USAGE_STORE": str(store)})
        self.assertIn("found via CORE", handed.reliability_note)
        self.assertEqual(sent[core], {"Authorization": "Bearer core-studio-key"})
        # And Unpaywall's contact address from the workspace settings (studio_settings.unpaywall_email, 2026-11-03).
        from podcast_automate.sources import use_unpaywall_email
        use_unpaywall_email("studio@example.org")
        self.addCleanup(use_unpaywall_email, None)
        _, asked = self.open_access_case(url, "Governing the commons", answers, env={"PLA_CORE_USAGE_STORE": str(store)})
        self.assertIn("email=studio%40example.org", next(address for address in asked if "api.unpaywall.org" in address))

    def test_a_scan_without_a_text_layer_brings_a_free_copy(self):
        url = "https://example.org/max-neef-scan.pdf"
        work = {"title": "Human Scale Development", "locations": [{"is_oa": True, "pdf_url": "https://repository.example/hsd.pdf"}]}
        search = "https://api.openalex.org/works?search=Human%20Scale%20Development&per_page=3"
        scan = {"blocks": [], "metadata": {"extraction_coverage": {"pages_total": 12, "pages_with_text": 0}}}
        with patch("podcast_automate.sources.extract_pdf_isolated", return_value=scan):
            document, _ = self.open_access_case(url, "Human Scale Development", {
                url: (b"%PDF-1.4 scanned", "application/pdf", url),
                search: (json.dumps({"results": [work]}).encode(), "application/json", ""),
                "https://repository.example/hsd.pdf": (HTML, "text/html", "https://repository.example/hsd.pdf")})
        self.assertEqual(document.final_url, "https://repository.example/hsd.pdf")
        self.assertIn("found via OpenAlex", document.reliability_note)

    def test_other_failures_ask_for_no_copy(self):
        url = "https://example.org/paper.pdf"
        for failure in (AppError("Unavailable", code="source_download_failed"),
                        AppError("Quelle überschreitet 20 MiB.", code="source_too_large")):
            with self.subTest(failure=str(failure)):
                error, fetched = self.open_access_case(url, "A paper", {url: failure})
                self.assertIs(error, failure)
                self.assertEqual(fetched, [url])

    WAYBACK = "https://archive.org/wayback/available?url=https%3A%2F%2Fexample.org%2Fgone.html"

    def test_a_page_gone_from_its_address_is_read_from_the_internet_archive_but_a_refusal_never(self):
        """D-197, 2026-11-10: seven sources of the projects were gone (HTTP 404) and the archive keeps many such pages.
        Until then a 404 asked for no copy at all. A refusal (403) is the site's decision and asks no archive."""
        url = "https://example.org/gone.html"
        gone = AppError("Quellenabruf fehlgeschlagen (HTTP 404).", code="source_download_failed", details={"http_status": 404})
        capture = "https://web.archive.org/web/20210503101500id_/https://example.org/gone.html"
        snapshot = {"archived_snapshots": {"closest": {"available": True, "status": "200", "timestamp": "20210503101500",
                                                       "url": "http://web.archive.org/web/20210503101500/https://example.org/gone.html"}}}
        answers = {url: gone, self.WAYBACK: (json.dumps(snapshot).encode(), "application/json", ""),
                   capture: (HTML, "text/html", capture)}
        document, fetched = self.open_access_case(url, "A page that is gone", answers)
        self.assertEqual((document.url, document.final_url), (url, capture))
        self.assertIn("Archived copy of this address from the Internet Archive (captured 2021-05-03)", document.reliability_note)
        self.assertEqual(fetched, [url, self.WAYBACK, capture], "a 404 asks the archive alone, no repository")
        # Without a capture, or with only a capture of an error page, the 404 stands.
        for missing in ({"archived_snapshots": {}}, {"archived_snapshots": {"closest": {**snapshot["archived_snapshots"]["closest"], "status": "404"}}}):
            with self.subTest(missing=missing):
                error, _ = self.open_access_case(url, "A page that is gone",
                                                 {url: gone, self.WAYBACK: (json.dumps(missing).encode(), "application/json", "")})
                self.assertIs(error, gone)
        # A server that does not answer asks the free repositories first, then the archive.
        unreachable = AppError("Quellenabruf fehlgeschlagen (URLError).", code="source_download_failed",
                               details={"network_error": "URLError"})
        document, fetched = self.open_access_case(url, "A page that is gone", {**answers, url: unreachable})
        self.assertEqual(document.final_url, capture)
        self.assertLess(fetched.index("https://api.openalex.org/works?search=A%20page%20that%20is%20gone&per_page=3"),
                        fetched.index(self.WAYBACK))
        # A refusal never reaches the archive.
        error, fetched = self.open_access_case(url, "A page that is gone", {**answers, url: self.REFUSED})
        self.assertIs(error, self.REFUSED)
        self.assertNotIn(self.WAYBACK, fetched)

    # Springer's bot check as the Ontologies run received it: a noscript notice and one loading error, 300 characters.
    CHALLENGE = (b"<!DOCTYPE html><html><head><title>Client Challenge</title></head><body><noscript>JavaScript is "
                 b"disabled in your browser. Please enable JavaScript to proceed.</noscript><div id=\"loading-error\">"
                 b"A required part of this site couldn't load. This may be due to a browser extension, network issues, "
                 b"or browser settings. Please check your connection, disable any ad blockers, or try using a different "
                 b"browser.</div><script>loadScript('/_fs-ch/script.js')</script></body></html>")

    def test_a_bot_check_counts_as_a_refused_download_and_brings_the_free_copy(self):
        with self.assertRaises(AppError) as refused:
            extract(self.CHALLENGE, "text/html", "page")
        self.assertEqual((refused.exception.code, refused.exception.details), ("source_access_blocked", {"interstitial": True}))
        url = "https://link.springer.com/chapter/10.1007/978-3-031-77847-6_18"
        work = {"locations": [{"is_oa": True, "pdf_url": "https://repository.example/chapter.pdf"}]}
        document, fetched = self.open_access_case(url, "Increasing the accuracy", {
            url: (self.CHALLENGE, "text/html", url),
            "https://api.openalex.org/works/doi:10.1007/978-3-031-77847-6_18": (json.dumps(work).encode(), "application/json", ""),
            "https://repository.example/chapter.pdf": (HTML, "text/html", "https://repository.example/chapter.pdf")})
        self.assertEqual((document.url, document.final_url), (url, "https://repository.example/chapter.pdf"))
        self.assertIn("Open-access copy of the same work found via OpenAlex", document.reliability_note)
        # Without a free copy the refusal itself is the failure, and an ordinary page never gets the copy note.
        closed, _ = self.open_access_case(url, "Increasing the accuracy", {
            url: (self.CHALLENGE, "text/html", url),
            "https://api.openalex.org/works/doi:10.1007/978-3-031-77847-6_18": (json.dumps({"locations": []}).encode(),
                                                                               "application/json", "")})
        self.assertEqual(closed.code, "source_access_blocked")
        plain, _ = self.open_access_case("https://example.org/paper", "Paper",
                                         {"https://example.org/paper": (HTML, "text/html", "https://example.org/paper")})
        self.assertNotIn("Open-access copy", plain.reliability_note)

    def test_a_script_banner_above_a_real_article_is_not_a_bot_check(self):
        article = (b"<html><head><title>A real article</title></head><body><div>JavaScript is disabled in your browser."
                   b"</div><article>" + b"".join(b"<p>Paragraph %d: ontologies constrain what a model may state about "
                                                 b"a schema.</p>" % n for n in range(60)) + b"</article></body></html>")
        kind, _, _, sections = extract(article, "text/html", "page")
        self.assertEqual(kind, "html")
        self.assertTrue(sections)

    def test_only_refused_addresses_count_as_blocked_sources(self):
        with patch("podcast_automate.sources.download", return_value=(self.CHALLENGE + b" " * 10, "text/html", "")), \
                patch("podcast_automate.sources.extract", side_effect=lambda raw, kind, name: (
                    "html", ".html", {"title": "Client Challenge"},
                    [SourceSection(id="sec_1", text="A required part of this site couldn't load. " * 6, page=None)])), \
                tempfile.TemporaryDirectory() as temporary:
            # A check imported before such pages were recognised still stands in older indexes.
            imported, _ = import_source(SourceCandidate(url="https://link.springer.com/chapter/x", title="Chapter",
                                                        authors=[], published_date="", rationale="Test", primary_source=True),
                                        Path(temporary), "run_test")
        index = SourceIndex(sources=[imported], failures=[
            {"source": "https://publisher.example/a", "reason": "Quellenabruf fehlgeschlagen (HTTP 403).", "code": "source_download_failed"},
            {"source": "https://publisher.example/b", "reason": "Quellenabruf fehlgeschlagen (HTTP 404).", "code": "source_download_failed"},
            {"source": "https://publisher.example/c", "reason": "Quellenabruf fehlgeschlagen (TimeoutError).", "code": "source_download_failed"},
            {"source": "https://publisher.example/d", "reason": "Quelle liefert eine Zugriffssperre statt Artikeltext.",
             "code": "source_access_blocked"}])
        self.assertEqual([row["url"] for row in blocked_sources(index)],
                         ["https://publisher.example/a", "https://publisher.example/d", "https://link.springer.com/chapter/x"])
        self.assertIn("Client Challenge", blocked_sources(index)[-1]["evidence"])

    def test_unreadable_and_challenge_pages_are_rejected(self):
        for raw, content_type in ((b"short", "text/plain"), (b"\0" * 400, "text/plain"),
            (b"<html><body>Just a moment..." + b"verify you are human " * 30 + b"</body></html>", "text/html")):
            with self.subTest(content_type=content_type), self.assertRaises(AppError):
                extract(raw, content_type, "page")

    def test_unsafe_urls_and_redirects_cannot_fetch_private_hosts(self):
        for url in ("file:///secret", "https://user:password@example.org", "http://example.org:8080"):
            with self.subTest(url=url), self.assertRaises(AppError):
                canonical_url(url)
        local = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        with patch("podcast_automate.sources.socket.getaddrinfo", return_value=local):
            with self.assertRaises(AppError):
                public_url("http://localhost/secret")
            with self.assertRaises(AppError):
                PublicRedirect().redirect_request(None, None, 302, "Found", {}, "http://localhost/secret")
        self.assertEqual(canonical_url("HTTPS://EXAMPLE.ORG/paper#section"), "https://example.org/paper")


class SourceServer:
    """A source server for ``sources.download`` without a network: every connection the download opens is one end of
    a socketpair whose other end reads the request and sends the next of ``answers`` (bytes, or a function of the
    socket and ``release``, an event set when the test ends). It records the address each connection was opened to."""

    def __init__(self, *answers):
        import threading
        self.answers, self.requests, self.connected = list(answers), [], []
        self.release = threading.Event()

    @staticmethod
    def pair():
        """Two connected TCP sockets on the loopback interface, which is what ``socket.socketpair`` gives on Windows.
        On Linux and macOS it gives Unix sockets, on which http.client's TCP_NODELAY fails with EOPNOTSUPP, so every
        download failed there before the server answered (CI, 2026-11-15). ``socket.create_connection`` is patched
        while the server runs, so the client connects by hand."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            ours = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            ours.connect(listener.getsockname())
            theirs, _ = listener.accept()
        return ours, theirs

    def create_connection(self, address, timeout=None, source_address=None, **kwargs):
        import threading
        self.connected.append(tuple(address))
        ours, theirs = self.pair()
        ours.settimeout(timeout if isinstance(timeout, (int, float)) else None)
        threading.Thread(target=self.answer, args=(theirs, self.answers.pop(0)), daemon=True).start()
        return ours

    def answer(self, sock, payload):
        with sock:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = sock.recv(65536)
                if not chunk:
                    return
                data += chunk
            self.requests.append(data.decode("latin-1"))
            if callable(payload):
                payload(sock, self.release)
            else:
                sock.sendall(payload)

    def __enter__(self):
        self.patches = [patch("socket.create_connection", self.create_connection)]
        for active in self.patches:
            active.start()
        return self

    def __exit__(self, *exc):
        self.release.set()
        for active in self.patches:
            active.stop()
        return False


def public(*addresses):
    """``sources.resolve`` answering every host with these public addresses, or by host from a dict."""
    def answer(host):
        found = addresses[0].get(host, ()) if addresses and isinstance(addresses[0], dict) else addresses
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0)) for address in found]
    return patch("podcast_automate.sources.resolve", side_effect=answer)


EPUB_CONTAINER = (b'<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" '
                  b'version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" '
                  b'media-type="application/oebps-package+xml"/></rootfiles></container>')


def epub(*chapters, compress=None):
    """An EPUB of ``chapters`` (bytes of XHTML), in reading order."""
    import zipfile
    items = "".join(f'<item id="c{n}" href="c{n}.xhtml" media-type="application/xhtml+xml"/>' for n in range(len(chapters)))
    spine = "".join(f'<itemref idref="c{n}"/>' for n in range(len(chapters)))
    package = (f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata '
               f'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Book</dc:title></metadata><manifest>{items}'
               f'</manifest><spine>{spine}</spine></package>').encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compress or zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip")
        archive.writestr("META-INF/container.xml", EPUB_CONTAINER)
        archive.writestr("OEBPS/content.opf", package)
        for n, chapter in enumerate(chapters):
            archive.writestr(f"OEBPS/c{n}.xhtml", chapter)
    return buffer.getvalue()


def damaged(raw, name="OEBPS/c0.xhtml", *, corrupt=False, encrypted=False, method=None):
    """``raw`` with the entry ``name`` damaged the way real files are: a broken deflate stream, the zip's own
    encryption flag, or a compression method Python cannot read (Deflate64 is 9)."""
    raw = bytearray(raw)
    local = raw.find(name.encode()) - 30
    central = raw.rfind(name.encode()) - 46
    assert raw[local:local + 4] == b"PK\x03\x04" and raw[central:central + 4] == b"PK\x01\x02"
    if corrupt:
        start = local + 30 + len(name)
        for offset in range(start + 5, start + 25):
            raw[offset] ^= 0x5A
    if encrypted:
        raw[local + 6] |= 1
        raw[central + 8] |= 1
    if method is not None:
        raw[local + 8:local + 10] = raw[central + 10:central + 12] = method.to_bytes(2, "little")
    return bytes(raw)


class LegacyArticleParser:
    """The page reader as it was until 2026-11-14, kept to show that the linear one reads every page alike."""

    @staticmethod
    def text(page):
        from html.parser import HTMLParser
        from podcast_automate.sources import ArticleParser

        class Legacy(HTMLParser):
            OMIT, BLOCK = ArticleParser.OMIT, ArticleParser.BLOCK

            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack, self.text, self.main_text, self.title, self.metadata = [], [], [], [], {}

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == "meta":
                    key = attributes.get("name", attributes.get("property", "")).lower()
                    if key and attributes.get("content"):
                        self.metadata.setdefault(key, []).append(attributes["content"])
                if tag == "html" and attributes.get("lang"):
                    self.metadata["language"] = [attributes["lang"]]
                if tag in self.BLOCK:
                    self.add_text("\n\n")
                if tag not in {"meta", "link", "br", "hr", "img", "input", "source", "wbr"}:
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if tag in self.BLOCK:
                    self.add_text("\n\n")
                if tag in self.stack:
                    self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]

            def add_text(self, value):
                if not any(tag in self.OMIT for tag in self.stack):
                    self.text.append(value)
                    if "main" in self.stack or "article" in self.stack:
                        self.main_text.append(value)

            def handle_data(self, data):
                if "title" in self.stack:
                    self.title.append(data)
                if "head" not in self.stack:
                    self.add_text(data)
        parser = Legacy()
        parser.feed(page)
        return "".join(parser.text), "".join(parser.main_text), "".join(parser.title), parser.metadata


class SourceRetrievalTests(unittest.TestCase):
    """The review of 2026-11-14: what a source server or file can do to a research run."""

    def test_a_broken_server_answer_is_a_failed_download_not_a_crash(self):
        # An exception class the import loops did not catch stopped every task of the run, and each resume fetched
        # the reserved address again and stopped the same way.
        from podcast_automate.sources import download
        answers = {"a garbled status line": b"SSH-2.0-OpenSSH_9.6\r\n\r\n",
                   "more than 100 headers": b"HTTP/1.1 200 OK\r\n" + b"".join(b"X-H%d: v\r\n" % n for n in range(101))
                   + b"\r\n",
                   "a chunked body cut short": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n400\r\n" + b"a" * 100,
                   "a body shorter than its length": b"HTTP/1.1 200 OK\r\nContent-Length: 5000\r\n\r\n" + b"a" * 100}
        for case, answer in answers.items():
            with self.subTest(case), public("8.8.8.8"), SourceServer(answer), self.assertRaises(AppError) as failed:
                download("http://example.org/paper")
            self.assertEqual(failed.exception.code, "source_download_failed")
            self.assertTrue(failed.exception.details.get("network_error"), "so the free copies and the archive are asked")

    def test_the_time_limit_of_a_download_holds_while_a_server_sends_byte_by_byte(self):
        from podcast_automate import sources

        def trickle(sock, release):
            sock.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\nx")
            release.wait(30)
        with public("8.8.8.8"), SourceServer(trickle), patch("podcast_automate.sources.RECEIVE_TIMEOUT_SECONDS", 3), \
                patch("podcast_automate.sources.DOWNLOAD_SECONDS", -1), self.assertRaises(AppError) as stopped:
            sources.download("http://example.org/paper")
        self.assertEqual(stopped.exception.code, "source_too_large", "the limit is checked after every receive")

    def test_a_download_connects_to_the_address_it_checked(self):
        # The address was checked, then the connection asked the name server again, which could answer with a
        # private address the second time (DNS rebinding).
        from podcast_automate import sources
        page = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nhello"
        with public("8.8.8.8"), SourceServer(page) as server:
            self.assertEqual(sources.download("http://example.org/paper")[0], b"hello")
        self.assertEqual(server.connected, [("8.8.8.8", 80)])
        self.assertIn("Host: example.org\r\n", server.requests[0])

        class TLS:
            names = []

            def wrap_socket(self, sock, server_hostname=None, **kwargs):
                self.names.append(server_hostname)
                return sock
        with public("8.8.8.8"), SourceServer(page) as server, patch("podcast_automate.sources.SOURCE_TLS", TLS()):
            self.assertEqual(sources.download("https://example.org/paper")[0], b"hello")
        self.assertEqual((server.connected, TLS.names), ([("8.8.8.8", 443)], ["example.org"]),
                         "the certificate is checked for the name, the connection goes to the checked address")
        moved = b"HTTP/1.1 302 Found\r\nLocation: http://other.example/next\r\nContent-Length: 0\r\n\r\n"
        with public({"example.org": ["8.8.8.8"], "other.example": ["8.8.4.4"]}), SourceServer(moved, page) as server:
            self.assertEqual(sources.download("http://example.org/paper")[2], "http://other.example/next")
        self.assertEqual(server.connected, [("8.8.8.8", 80), ("8.8.4.4", 80)])
        with public({"example.org": ["8.8.8.8"], "other.example": ["127.0.0.1"]}), SourceServer(moved) as server, \
                self.assertRaises(AppError) as refused:
            sources.download("http://example.org/paper")
        self.assertEqual((refused.exception.code, server.connected), ("invalid_source_url", [("8.8.8.8", 80)]))

    def test_a_meta_tag_with_an_empty_attribute_is_read_like_any_page(self):
        page = (b'<html><head><meta name content="x"><meta property content="y"><title>T</title></head><body><p>'
                + TEXT.encode() + b"</p></body></html>")
        kind, _, metadata, sections = extract(page, "text/html", "https://example.org/a")
        self.assertEqual((kind, metadata["title"]), ("html", "T"))
        self.assertIn(TEXT, " ".join(section.text for section in sections))

    def test_a_damaged_epub_is_an_unreadable_source(self):
        chapter = b"<html><body><p>" + TEXT.encode() + b"</p></body></html>"
        book = epub(chapter)
        self.assertEqual(extract(book, "application/epub+zip", "book.epub")[:2], ("text", ".epub"))
        for case, raw in {"a broken deflate stream": damaged(book, corrupt=True),
                          "the zip's own encryption": damaged(book, encrypted=True),
                          "Deflate64": damaged(book, method=9)}.items():
            with self.subTest(case), self.assertRaises(AppError) as refused:
                extract(raw, "application/epub+zip", "book.epub")
            self.assertEqual(refused.exception.code, "source_unreadable")

    def test_any_parser_failure_is_an_unreadable_source(self):
        candidate = SourceCandidate(url="https://example.org/a", title="A page", authors=[], published_date="",
                                    rationale="Test", primary_source=True)
        with tempfile.TemporaryDirectory() as temporary, \
                patch("podcast_automate.sources.ArticleParser.feed", side_effect=AssertionError("parser")), \
                self.assertRaises(AppError) as refused:
            import_source(candidate, Path(temporary), "run_x", downloaded=(HTML, "text/html", candidate.url))
        self.assertEqual((refused.exception.code, refused.exception.details.get("parser_error")),
                         ("source_unreadable", "AssertionError"))

    def test_a_free_copy_service_that_answers_oddly_names_no_copy(self):
        from podcast_automate import sources
        candidate = SourceCandidate(url="https://doi.org/10.1234/abcd.5678", title="A paper", authors=[],
                                    published_date="", rationale="Test", primary_source=True)
        for body in (b"[1, 2]", b'{"best_oa_location": {"is_oa": true, "pdf_url": 5}, "locations": "x"}'):
            with self.subTest(body=body), patch("podcast_automate.sources.download", return_value=(body, "", "")):
                self.assertEqual(sources.open_access_copies(candidate), [])
        with patch("podcast_automate.sources.download", return_value=(b"[]", "", "")):
            blocked = AppError("Quellenabruf fehlgeschlagen (HTTP 403).", code="source_download_failed",
                               details={"http_status": 403})
            with self.assertRaises(AppError) as kept:
                sources.open_access_copy(candidate, blocked)
        self.assertIs(kept.exception, blocked)

    def test_the_page_reader_reads_pages_without_closing_tags_alike_and_in_linear_time(self):
        import time
        from podcast_automate.sources import ArticleParser
        pages = [HTML.decode(), "<p>one<p>two<li>three<li>four</ul><div>five</div>",
                 "<html><head><title>T</title><style>x</style></head><body><nav>menu<p>n</nav><main><p>m1<p>m2"
                 "<script>s</script>after</main><article><p>a</article><p>out</body></html>",
                 "<body><b><i>x</b> y</i> z<form>f<p>q</form> w<svg><text>t</text></svg> v<template>u</template>",
                 "<head><title>a<title>b</title>c</head><p>d</p></p></div><title>in body</title><p>e"]
        for page in pages:
            parser = ArticleParser()
            parser.feed(page)
            with self.subTest(page=page[:40]):
                self.assertEqual(("".join(parser.text), "".join(parser.main_text), "".join(parser.title),
                                  parser.metadata), LegacyArticleParser.text(page))
        # HTML may leave out </p>; 60,000 such paragraphs took minutes, quadratic in the open tags.
        page = "<html><body>" + "".join(f"<p>Paragraph {n} of an ordinary page." for n in range(60_000)) + "</body></html>"
        started = time.monotonic()
        ArticleParser().feed(page)
        self.assertLess(time.monotonic() - started, 20)

    def test_an_epub_stops_reading_once_its_text_passes_the_limit(self):
        # A 39 KiB e-book whose one chapter expanded to 19 MiB of markup took a minute and 400 MiB before the text
        # limit refused it, since the limit was checked only after every chapter was read.
        from podcast_automate import sources
        fed = []
        original = sources.ArticleParser.feed

        def feed(parser, data):
            fed.append(len(data))
            return original(parser, data)
        chapter = b"<html><body>" + b"<p>Readable words of the book.</p>" * 100_000 + b"</body></html>"
        with patch("podcast_automate.sources.ArticleParser.feed", feed), self.assertRaises(AppError) as refused:
            extract(epub(chapter), "application/epub+zip", "book.epub")
        self.assertEqual(refused.exception.code, "source_too_large")
        self.assertLess(sum(fed), 2 * sources.MAX_TEXT, "the chapter is read only until its text passes the limit")
        # Markup without text is refused by its size before it is read at all.
        fed.clear()
        empty = b"<html><body>" + b"<span></span>" * 500_000 + b"</body></html>"
        with patch("podcast_automate.sources.ArticleParser.feed", feed), self.assertRaises(AppError) as refused:
            extract(epub(empty, empty), "application/epub+zip", "book.epub")
        self.assertEqual((refused.exception.code, fed), ("source_too_large", []))

    def test_a_chapter_read_in_steps_reads_as_read_at_once(self):
        import random
        import re
        from podcast_automate import sources
        pieces = ["<p>", "</p>", "Text &amp; more ", "&eacute;t&eacute; ", "<!-- a < b -->", "<b>bold</b>", "<li>item",
                  "<script>if (a < b) {x}</script>", "<br/>", "word " * 30, "<div class='x'>", "</div>", "&nbsp;",
                  "<style>p { a: b }</style>", "<nav>menu</nav>", "<main>", "</main>", "\n\n  ", "<h2>Head</h2>", "&am"]
        generator = random.Random(7)
        markup = "<html><body>" + "".join(generator.choice(pieces) for _ in range(60_000)) + "</body></html>"
        self.assertGreater(len(markup), 2 * sources.EPUB_FEED_CHARS)
        parser = sources.ArticleParser()
        parser.feed(markup)
        self.assertEqual(sources.chapter_blocks(markup, 0, 10 ** 9, AppError("x", code="source_too_large")),
                         re.split(r"\n\s*\n", "".join(parser.text)))


class ResearchTests(fixtures.ResearchProjectCase):
    def test_an_openrouter_model_researches_through_perplexity_without_any_subscription(self):
        """D-151: with the Perplexity search an OpenRouter run needs no subscription; the run binds the search, the
        discovery's candidates come from the search results, and the requests count toward the money limit."""
        from podcast_automate.web_search import SearchPlan
        config = json.loads(json.dumps(self.config.model_dump(mode="json")))
        config["research_limits"]["cost_usd"] = 50.0
        write_yaml(self.root / "project.yaml", config)

        def openrouter(adapter, prompt, output_type, directory, **kwargs):
            if output_type is SearchPlan:
                return SearchPlan(queries=["energy model definition"], languages=[]), {"separately_billed_cost": 0.01}
            payload = json.loads(prompt.splitlines()[-1])
            searched = isinstance(payload, dict) and "results" in payload and "task" in payload
            output, _ = self.model(payload["task"] if searched else prompt, output_type, directory,
                                   **{**kwargs, "search": searched})
            return output, {"separately_billed_cost": 0.01}
        found = {"queries": ["energy model definition"], "request_id": "r1", "usd": 0.005, "elapsed_seconds": 0.1,
                 "results": [{"title": "Paper 0", "url": "https://example.org/paper0", "snippet": "s", "date": None,
                              "last_updated": None}]}
        with patch("podcast_automate.openrouter.OpenRouterAdapter.structured", autospec=True, side_effect=openrouter), \
                patch("podcast_automate.web_search.PerplexitySearch.search", return_value=found) as search, \
                patch("podcast_automate.research.CodexAdapter.structured", side_effect=AssertionError("no Codex")), \
                patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", side_effect=AssertionError("no Claude")):
            run = run_research(self.root, backend="openrouter", model="vendor/test-model", api_key="or-key",
                               web_search="perplexity", perplexity_key="pplx-test-key-0123456789", plan_review="auto")
        self.assertEqual(run.status, "completed")
        self.assertGreaterEqual(search.call_count, 1)
        work = self.root / "runs" / run.run_id
        request = json.loads((work / "research_request.json").read_text(encoding="utf-8"))
        self.assertEqual((request["text_generation"]["provider"], request["text_generation"]["web_search"]),
                         ("openrouter", "perplexity"))
        budget = json.loads((work / "budget.json").read_text(encoding="utf-8"))
        self.assertGreater(budget["billed_usd"], 0)
        self.assertTrue(any(path.name == "search_results.json" for path in (work / "calls").rglob("*.json")))
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"pplx-test-key-0123456789", path.read_bytes(), path)
        # Without the Perplexity search an OpenRouter model cannot research.
        with self.assertRaises(AppError) as refused:
            run_research(self.root, backend="openrouter", model="vendor/test-model", api_key="or-key")
        self.assertEqual(refused.exception.code, "invalid_backend")

    def test_an_earlier_runs_sources_are_offered_as_a_library_and_reused_without_a_download(self):
        """2026-09-30: the rebuilt series research anew, with the stored corpus as a starting library."""
        seen = []

        def model(prompt, output_type, directory, **kwargs):
            if output_type is ResearchDiscovery:
                seen.append(json.loads(prompt.splitlines()[-1]))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            first = run_research(self.root)
        self.assertEqual(first.status, "completed")
        self.assertNotIn("library", seen[0])
        downloads = self.download.call_count
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            second = run_research(self.root, seed_corpus=first.run_id)
        self.assertEqual(second.status, "completed")
        self.assertEqual([row["url"] for row in seen[1]["library"]], ["https://example.org/paper0"])
        self.assertEqual(self.download.call_count, downloads, "the stored copy is reused, not fetched again")
        work = self.root / "runs" / second.run_id
        index = json.loads((work / "source_index.json").read_text(encoding="utf-8"))
        self.assertTrue(index["sources"][0]["raw_path"].startswith(f"sources/raw/{second.run_id}/"))
        self.assertEqual(json.loads((work / "research_request.json").read_text(encoding="utf-8"))["seed_corpus"], first.run_id)
        # A resume keeps its library; naming one again is refused, and a script run is no library.
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            self.assertEqual(run_research(self.root, resume=True, run_id=second.run_id).status, "completed")
            with self.assertRaises(AppError):
                run_research(self.root, resume=True, run_id=second.run_id, seed_corpus=first.run_id)

    def test_a_new_version_researches_with_the_old_versions_sources_as_its_library(self):
        """D-168: a new version brings the newest completed research run's sources along; its first run offers them."""
        from podcast_automate.project_versions import create_version
        from podcast_automate.research import latest_research_run
        seen = []

        def model(prompt, output_type, directory, **kwargs):
            if output_type is ResearchDiscovery:
                seen.append(json.loads(prompt.splitlines()[-1]))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            first = run_research(self.root)
        version = create_version(self.root)
        downloads = self.download.call_count
        self.assertEqual(latest_research_run(version), first.run_id)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(version, seed_corpus=latest_research_run(version))
        self.assertEqual(run.status, "completed")
        self.assertEqual([row["url"] for row in seen[-1]["library"]], ["https://example.org/paper0"])
        self.assertEqual(self.download.call_count, downloads, "the old version's copy is reused, not fetched again")
        index = json.loads((version / "runs" / run.run_id / "source_index.json").read_text(encoding="utf-8"))
        self.assertTrue(index["sources"][0]["raw_path"].startswith(f"sources/raw/{run.run_id}/"))
        # Another run id is still refused as a library when it is not a research run of this version.
        with self.assertRaises(AppError):
            run_research(version, seed_corpus="run_20200101_000000_000000_00000000")

    def test_a_first_search_over_its_limit_is_trimmed_and_a_paid_one_survives_a_stop(self):
        """Asimov, 2026-09-30: 27 candidates against a limit of 26 threw the whole paid search away."""
        from podcast_automate import research as research_module
        calls = []

        def model(prompt, output_type, directory, **kwargs):
            if output_type is ResearchDiscovery:
                calls.append(directory.name)
                found = fixtures.discovery(count=14)
                metadata = {"research_performed": True, "web_search_events": 1, "prompt_version": kwargs["prompt_version"]}
                # What the real adapters keep in every call folder.
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "response.json").write_text(found.model_dump_json(), encoding="utf-8")
                (directory / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
                return found, metadata
            return self.model(prompt, output_type, directory, **kwargs)
        real_write = research_module.write_json
        stopped = []

        def stop_once(path, data):
            if path.name == "discovery.json" and not stopped:
                stopped.append(path)
                raise KeyboardInterrupt  # the worker stopped right after the paid search answered
            return real_write(path, data)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model), \
                patch("podcast_automate.research.write_json", side_effect=stop_once), \
                self.assertRaises(KeyboardInterrupt):
            run_research(self.root)
        run_id = next(p.name for p in (self.root / "runs").iterdir() if p.name.startswith("run_"))
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            resumed = run_research(self.root, resume=True, run_id=run_id)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(len(calls), 1, "the resume adopts the saved answer instead of searching again")
        saved = json.loads((self.root / "runs" / run_id / "discovery.json").read_text(encoding="utf-8"))
        # No focus questions: a limit of 12; the two over it are named, not silently lost.
        self.assertEqual(len(saved["candidates"]), 12)
        self.assertIn("Über dem Quellenlimit der ersten Suche", saved["limitations"][-1])
        metadata = json.loads((self.root / "runs" / run_id / "discovery_metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["adopted_from"], calls[0])

    def test_every_failed_import_is_recorded_with_its_cause_and_code(self):
        """The retrieval report distinguishes an unreadable file from a duplicate, by code and by wording."""
        from podcast_automate.research_models import ResearchDiscovery

        def model(prompt, output_type, directory, **kwargs):
            if output_type is ResearchDiscovery:
                self.calls.append(output_type)
                return fixtures.discovery(count=3), {"research_performed": True, "web_search_events": 1}
            return self.model(prompt, output_type, directory, **kwargs)
        self.download.side_effect = [
            (fixtures.HTML, "text/html", "https://example.org/paper0"),
            AppError("PDF enthält zu wenig lesbaren Text: 0 von 12 Seiten haben eine Textebene; vermutlich gescannt, OCR nötig.",
                     code="source_unreadable", details={"pages_total": 12, "pages_with_text": 0}),
            (fixtures.HTML, "text/html", "https://example.org/paper2")]
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            manifest = run_research(self.root)
        self.assertEqual(manifest.status, "completed")
        index = json.loads((self.root / "runs" / manifest.run_id / "source_index.json").read_text(encoding="utf-8"))
        self.assertEqual([s["url"] for s in index["sources"]], ["https://example.org/paper0"])
        self.assertEqual(index["failures"], [
            {"source": "https://example.org/paper1", "code": "source_unreadable",
             "reason": "PDF enthält zu wenig lesbaren Text: 0 von 12 Seiten haben eine Textebene; vermutlich gescannt, OCR nötig."},
            {"source": "https://example.org/paper2", "code": "duplicate_source",
             "reason": "Identischer Quellentext bereits eingelesen."}])
        # The briefing's access-problem list keeps the wording a reader needs to act on.
        briefing = (self.root / "runs" / manifest.run_id / "research_briefing.md").read_text(encoding="utf-8")
        problems = briefing.split("## Zugriffsprobleme")[1].split("## Quellenverzeichnis")[0]
        self.assertIn("- https://example.org/paper1: PDF enthält zu wenig lesbaren Text: 0 von 12 Seiten", problems)
        self.assertIn("- https://example.org/paper2: Identischer Quellentext bereits eingelesen.", problems)

    def test_topic_to_downloaded_sources_and_dossier_then_resume(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            first = run_research(self.root)
            second = run_research(self.root, resume=True)
        self.assertEqual(first.status, "completed")
        self.assertEqual(first.run_id, second.run_id)
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(self.download.call_count, 1)
        self.assertTrue(all(stage.attempts == 1 for stage in second.stages.values()))
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])

    def test_answered_calls_record_their_wall_clock_and_the_publish_writes_the_calibration(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed")
        work = self.root / "runs" / run.run_id
        # The simulated adapter writes no response receipts; the run's own timing receipt marks each answered call.
        timings = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(work.glob("calls/call_*/timing.json"))]
        self.assertEqual([t["schema"] for t in timings], [s.__name__ for s in self.calls])
        for timing in timings:
            self.assertGreaterEqual(timing["seconds"], 0)
            self.assertLessEqual(timing["started_at"], timing["finished_at"])
        self.assertEqual([t["search"] for t in timings], [True] + [False] * 7)
        state = json.loads((work / "question_research/state.json").read_text(encoding="utf-8"))["value"]
        # Discovery precedes the ledger and still counts; the task's two calls are attributed to it.
        self.assertEqual([(row["name"], row["task"]) for row in state["call_timings"]][:5],
                         [("ResearchDiscovery", None), ("plan", None), ("scope_0", None),
                          ("reader", "task_definition"), ("review_001", "task_definition")])
        calibration = json.loads((self.root / "research/calibration.json").read_text(encoding="utf-8"))
        self.assertEqual((calibration["run_id"], calibration["tasks"], calibration["verified_tasks"], calibration["calls_per_task"]),
                         (run.run_id, 1, 1, 2))
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])

    def test_discovery_asks_for_independent_sources_under_its_own_version(self):
        seen = []
        def model(prompt, output_type, directory, **kwargs):
            if output_type is ResearchDiscovery:
                seen.append((kwargs["prompt_version"], prompt))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            self.assertEqual(run_research(self.root).status, "completed")
        # v5 (2026-09-30) types every candidate and treats attachments as maps; independence stays required.
        self.assertEqual([version for version, _ in seen], [DISCOVERY_VERSION])
        self.assertIn("For every empirical or performance claim family, include at least one source not "
                      "authored by the organisation making the claim, or state in limitations that none "
                      "was found.", seen[0][1])
        self.assertIn("Give every candidate its source_type", seen[0][1])
        self.assertIn("Attachments are maps, not evidence", seen[0][1])
        payload = json.loads(seen[0][1].splitlines()[-1])
        self.assertEqual(payload["research_date"], run_date(next(p for p in (self.root / "runs").iterdir()
                                                                  if p.name.startswith("run_"))))
        self.assertNotIn("recency_months", payload, "no rule unless the brief sets one")

    def test_open_questions_carry_what_the_corpus_probe_found(self):
        """An open question reaches ``open_questions.md`` only through an accepted gap: one task
        blocks, the user accepts it, and the resumed run finishes with the question on record.
        The composed dossier declares the gap; the probe finds its section, which the definition
        task had already read, so the line says the hits were read and the gap confirmed."""
        gap = "The rule that assigns an energy to each configuration is missing."

        def model(prompt, schema, directory, **kwargs):
            payload = json.loads(prompt.splitlines()[-1])
            if schema is QuestionPlan:
                plan = QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
                plan.tasks[0].requirement_ids = [r["id"] for r in requirements_for(self.config)]
                return plan, {}
            if schema is ResearchDecision and payload["task"]["kind"] == "empirical":
                return decision("blocked"), {}
            if schema is ReopenPlan:
                routes = ReopenPlan(routes=[dict(index=i, task_ids=["task_empirical"], reason="Only the accepted gap.")
                                            for i in range(len(payload["objections"]))])
                return complete_fixture_response(routes, payload), {}
            value, meta = self.model(prompt, schema, directory, **kwargs)
            if isinstance(value, ResearchDossier) and not value.open_questions:
                value.open_questions = [gap]
            return value, meta

        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            first = run_research(self.root)
        self.assertEqual((first.status, first.stages["dossier"].error.code), ("blocked", "research_questions_blocked"))
        approve_research_gap(self.root, first.run_id, "task_empirical", "Out of scope for this series")
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            run = run_research(self.root, resume=True)
        self.assertEqual((run.status, run.run_id), ("completed", first.run_id))
        probes = json.loads((self.root / "runs" / run.run_id /
                             "question_research/gap_probes.json").read_text(encoding="utf-8"))
        self.assertEqual([row["text"] for row in probes], [gap])
        self.assertTrue(probes[0]["hits"])
        self.assertEqual(probes[0]["status"], "hits_read_confirmed")
        text = (self.root / "research/open_questions.md").read_text(encoding="utf-8")
        references = ", ".join(hit["reference"] for hit in probes[0]["hits"])
        self.assertIn(f"- {gap} Korpusprobe: gelesen und bestätigt trotz Treffern in {references}.", text)
        self.assertIn("- Akzeptierte Lücke: What is energy? (Out of scope for this series)", text)

    def test_selected_research_model_is_bound_to_run_and_preserved_on_resume(self):
        original = (self.root / "project.yaml").read_bytes()
        selections = []

        def selected(adapter, *args, **kwargs):
            selections.append((adapter.settings.codex_model, adapter.reasoning_effort))
            return self.model(*args, **kwargs)

        with patch("podcast_automate.research.CodexAdapter.structured", autospec=True, side_effect=selected):
            first = run_research(self.root, model="gpt-6-astra", reasoning_effort="xhigh")
            second = run_research(self.root, resume=True, run_id=first.run_id)
            with self.assertRaises(AppError) as changed:
                run_research(self.root, resume=True, run_id=first.run_id, reasoning_effort="low")
        self.assertEqual(changed.exception.code, "inputs_changed")
        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "completed")
        self.assertEqual(selections, [("gpt-6-astra", "xhigh")] * 8)
        self.assertEqual((self.root / "project.yaml").read_bytes(), original)
        report = json.loads((self.root / "reports/research_quality.json").read_text())
        self.assertEqual(report["sources"], 1)
        self.assertFalse(report["human_reviewed"])
        self.assertTrue(report["complete_topic_coverage"])
        self.assertEqual(report["coverage_scope"], "agreed_brief")
        self.assertIn("Models assign an energy", (self.root / "research/research_briefing.md").read_text(encoding="utf-8"))

    def test_style_revision_reuses_verified_sources_without_search_or_download(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            original = run_research(self.root)
            self.config.depth_request = "Use familiar mental pictures and explain their limits."
            write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
            revised = run_research(self.root, reuse_sources=original.run_id)
            resumed = run_research(self.root, resume=True)
        self.assertEqual(revised.status, "completed")
        self.assertEqual(revised.run_id, resumed.run_id)
        self.assertNotEqual(original.run_id, revised.run_id)
        self.assertEqual(self.calls.count(ResearchDiscovery), 1)
        self.assertEqual(self.calls.count(ResearchDossier), 2)
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual(revised.stages["retrieval"].attempts, 0)
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])
        report = json.loads((self.root / "reports/research_quality.json").read_text())
        self.assertEqual(report["source_provenance"]["reused_from_run"], original.run_id)
        self.assertFalse(report["source_provenance"]["new_web_search"])
        self.assertEqual(report["budget"], {"model_calls": 7, "search_rounds": 0, "sequence": 7})

    def test_source_reuse_rejects_changed_scope(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            original = run_research(self.root)
            self.config.central_question = "A different research question"
            write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
            with self.assertRaises(AppError) as raised:
                run_research(self.root, reuse_sources=original.run_id)
        self.assertEqual(raised.exception.code, "inputs_changed")
        self.assertEqual(len(self.calls), 8)

    def test_source_reuse_rejects_corrupt_snapshot(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            original = run_research(self.root)
            raw = next((self.root / "sources/raw" / original.run_id).iterdir())
            raw.write_bytes(b"broken snapshot")
            with self.assertRaises(AppError) as raised:
                run_research(self.root, reuse_sources=original.run_id)
        self.assertEqual(raised.exception.code, "invalid_source_snapshot")

    def test_source_reuse_rejects_changed_local_file(self):
        local = self.root / "reading.txt"
        local.write_text(TEXT, encoding="utf-8")
        self.config.local_sources = ["reading.txt"]
        write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            original = run_research(self.root)
            local.write_text(TEXT + " Changed.", encoding="utf-8")
            with self.assertRaises(AppError) as raised:
                run_research(self.root, reuse_sources=original.run_id)
        self.assertEqual(raised.exception.code, "inputs_changed")

    def test_illustration_requires_explained_limits(self):
        # Since 2026-10-02 a lone half is dropped instead of refused (research_models.Finding; the rule's own test is
        # test_evidence_contracts): an illustration still never reaches a script without its limits.
        lone = Finding.model_validate({"id": "f_picture", "kind": "example", "statement": "A supported idea.",
                                       "illustration": "Picture a landscape.",
                                       "evidence": [{"reference": "source#section", "excerpt": "anchor"}]})
        self.assertEqual((lone.illustration, lone.illustration_limit), ("", ""))

    def test_quota_after_retrieval_preserves_sources_and_resumes_through_cli(self):
        from contextlib import redirect_stdout
        blocked = False

        def model(prompt, output_type, directory, **kwargs):
            nonlocal blocked
            if output_type is ResearchDossier and not blocked:
                blocked = True
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.model(prompt, output_type, directory, **kwargs)

        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            first = run_research(self.root)
            self.assertEqual(first.status, "waiting_for_quota")
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["resume", str(self.root), "--json"])
        self.assertEqual(code, 0)
        second = json.loads(output.getvalue())["run"]
        self.assertEqual(second["run_id"], first.run_id)
        self.assertEqual(second["stages"]["dossier"]["attempts"], 2)
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual(self.calls.count(ResearchDiscovery), 1)

    def test_corrupt_raw_source_is_downloaded_again(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            run_research(self.root)
            raw = next((self.root / "sources/raw").rglob("*.html"))
            raw.write_bytes(b"corrupted")
            self.assertIn("retrieval", status(self.root)["invalid_completed_stages"])
            result = run_research(self.root, resume=True)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stages["discovery"].attempts, 1)
        self.assertEqual(result.stages["retrieval"].attempts, 2)
        self.assertEqual(self.download.call_count, 2)

    def test_timeout_can_resume_with_longer_deadline_without_researching_again(self):
        self.config.runtime.text_timeout_seconds = 600
        write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
        deadlines = []

        def interrupted(adapter, prompt, output_type, directory, **kwargs):
            deadlines.append(adapter.settings.text_timeout_seconds)
            if output_type is ResearchDossier and adapter.settings.text_timeout_seconds == 600:
                raise AppError("Timeout", code="timeout")
            return self.model(prompt, output_type, directory, **kwargs)

        with patch("podcast_automate.research.CodexAdapter.structured", autospec=True, side_effect=interrupted):
            first = run_research(self.root, model="gpt-6-astra", reasoning_effort="xhigh")
            self.assertEqual(first.status, "failed")
            self.config.runtime.text_timeout_seconds = 1800
            # A timeout change must not mask a changed research scope.
            changed = self.config.model_dump(mode="json")
            changed["focus_questions"] = ["An added topic"]
            write_yaml(self.root / "project.yaml", changed)
            with self.assertRaises(AppError) as error:
                run_research(self.root, resume=True, run_id=first.run_id)
            self.assertEqual(error.exception.code, "inputs_changed")
            write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
            resumed = run_research(self.root, resume=True, run_id=first.run_id)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed.run_id, first.run_id)
        self.assertEqual(resumed.input_hash, first.input_hash)
        self.assertEqual(resumed.stages["discovery"].attempts, 1)
        self.assertEqual(resumed.stages["retrieval"].attempts, 1)
        self.assertEqual(resumed.stages["dossier"].attempts, 2)
        self.assertEqual(self.download.call_count, 1)
        self.assertEqual(deadlines, [600] * 6 + [1800] * 3)
        work = self.root / "runs" / first.run_id
        # The timed-out call produced no response: its reservation is refunded, while its
        # directory number stays unique so the repeated call gets a fresh receipt folder.
        budget = json.loads((work / "budget.json").read_text())
        self.assertEqual((budget["model_calls"], budget["sequence"], budget["refunded"]), (8, 9, [6]))
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])

    def test_operational_edits_while_paused_keep_the_run_resumable(self):
        """2026-10-02: the Studio saves the brief while a run waits for quota; a voice, host-name, deadline or limit
        edit gave inputs_changed and the run could never resume. Content edits still invalidate it."""
        blocked = False

        def model(prompt, output_type, directory, **kwargs):
            nonlocal blocked
            if output_type is ResearchDossier and not blocked:
                blocked = True
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.model(prompt, output_type, directory, **kwargs)

        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            first = run_research(self.root)
            self.assertEqual(first.status, "waiting_for_quota")
            edited = self.config.model_dump(mode="json")
            edited["runtime"]["text_timeout_seconds"] = 3600
            edited["research_limits"]["model_calls"] = 900
            edited["voice_profile"] = {"host_a": "Aiden", "host_b": "Vivian"}
            edited["host_names"] = {"host_a": "Anna", "host_b": "Ben"}
            content = {**edited, "central_question": "A different question"}
            write_yaml(self.root / "project.yaml", content)
            with self.assertRaises(AppError) as changed:
                run_research(self.root, resume=True, run_id=first.run_id)
            self.assertEqual(changed.exception.code, "inputs_changed")
            write_yaml(self.root / "project.yaml", edited)
            self.assertFalse(status(self.root, first.run_id)["project_changed"])
            resumed = run_research(self.root, resume=True, run_id=first.run_id)
        self.assertEqual((resumed.status, resumed.run_id, resumed.input_hash), ("completed", first.run_id, first.input_hash))
        activity = json.loads((self.root / "runs" / first.run_id / "research_activity.json").read_text(encoding="utf-8"))
        self.assertEqual(activity["model_call_limit"], 900, "the raised limit applies to the resumed run")

    def test_local_sources_outside_the_project_are_refused_before_anything_starts(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere) / "private.txt"
            outside.write_text(TEXT, encoding="utf-8")
            for value in (str(outside), "../" + outside.name):
                with self.subTest(value=value):
                    data = self.config.model_dump(mode="json")
                    data["local_sources"] = [value]
                    write_yaml(self.root / "project.yaml", data)
                    with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model), \
                            self.assertRaises(AppError) as refused:
                        run_research(self.root)
                    self.assertEqual((refused.exception.code, refused.exception.status), ("local_source_outside", "blocked"))
                    self.assertIn(value, str(refused.exception))
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / "runs/latest.json").exists())

    def test_an_unchanged_progress_report_is_not_written_again(self):
        """Both envelopes carry the whole question ledger (2 to 2.6 MB in the September runs); a report that says
        nothing new is skipped, and the activity file keeps the time of its last real change."""
        from podcast_automate import research
        writes, seen = [], []
        original = research.write_json

        def counting(path, data):
            if path.name == "research_activity.json":
                writes.append(data.get("activity"))
            return original(path, data)

        def questions(root, work, config, discovery, index, invoke, progress, **kwargs):
            before = len(writes)
            progress("Gleich")
            stamp = json.loads((work / "research_activity.json").read_text(encoding="utf-8"))["updated_at"]
            progress("Gleich")
            seen.append((len(writes) - before, json.loads((work / "research_activity.json").read_text(encoding="utf-8"))["updated_at"] == stamp))
            progress("Anders")
            seen.append(len(writes) - before)
            raise AppError("Halt für den Test", code="test_stop", status="blocked")

        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model), \
                patch.object(research, "write_json", side_effect=counting), \
                patch.object(research, "run_question_research", side_effect=questions):
            run = run_research(self.root)
        self.assertEqual(run.stages["dossier"].error.code, "test_stop")
        self.assertEqual(seen, [(1, True), 2])

    def test_a_run_with_an_open_question_is_never_published(self):
        """Transformer, 2026-10-02: published as completed with two rows still researching."""
        from podcast_automate.research import unanswered_questions
        from podcast_automate.research_ledger import save_value
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model), \
                patch("podcast_automate.research.unanswered_questions", return_value=["task_open"]):
            run = run_research(self.root)
        self.assertEqual((run.status, run.stages["publish"].error.code), ("blocked", "research_questions_open"))
        self.assertIn("task_open", run.stages["publish"].error.message)
        self.assertFalse((self.root / "research/latest.json").exists())
        work = self.root / "runs" / run.run_id
        self.assertEqual(unanswered_questions(work), [], "the fixture run answered every question")
        state = {"plan": {"tasks": [{"id": "t2"}, {"id": "t1"}, {"id": "t3"}]},
                 "tasks": {"t1": {"status": "verified"}, "t2": {"status": "researching"}, "t3": {"status": "blocked"}}}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            self.assertEqual(unanswered_questions(folder), [])
            write_json(folder / "research_questions.json", {"questions": [{"id": "t1", "status": "verified"},
                                                                          {"id": "t2", "status": "pending"}]})
            self.assertEqual(unanswered_questions(folder), ["t2"])
            save_value(folder / "question_research/state.json", state)
            self.assertEqual(unanswered_questions(folder), ["t2"])

    def test_parser_upgrade_re_extracts_only_a_run_whose_questions_are_not_planned_yet(self):
        """Review 2026-11-14: after a parser upgrade every resume read the raw files again. The question ledger binds a
        run's sources by their text (question_research.initialise), so a changed text stopped such a run for good with
        inputs_changed, and a completed run was recalculated. Both keep their stored extraction now. A run stopped before
        its questions were planned reuses its download and is read again with the new parser, as before."""
        from podcast_automate.research import EXTRACTION_VERSION
        from podcast_automate.storage import file_hash

        def upgraded(model):
            return (patch("podcast_automate.research.EXTRACTION_VERSION", "test-upgrade"),
                    patch("podcast_automate.sources.EXTRACTION_VERSION", "test-upgrade"),
                    patch("podcast_automate.research.CodexAdapter.structured", side_effect=model))

        def stopping_at(schema):
            stopped = []

            def model(prompt, output_type, directory, **kwargs):
                if output_type is schema and not stopped:
                    stopped.append(True)
                    raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
                return self.model(prompt, output_type, directory, **kwargs)
            return model
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            completed = run_research(self.root)
        work = self.root / "runs" / completed.run_id
        request = json.loads((work / "research_request.json").read_text(encoding="utf-8"))
        self.assertEqual(request["extraction_version"], EXTRACTION_VERSION, "a new run records the parser of its sources")
        volatile = {"budget.json", "budget_projection.json", "run_manifest.yaml"}

        def files():
            return {p.relative_to(self.root).as_posix(): file_hash(p) for p in sorted([*work.rglob("*"),
                    *(self.root / "sources").rglob("*")]) if p.is_file() and p.name not in volatile}
        before, calls = files(), len(self.calls)
        first, second, third = upgraded(self.model)
        with first, second, third:
            again = run_research(self.root, resume=True, run_id=completed.run_id)
        self.assertEqual((again.status, again.stages["retrieval"].attempts, again.stages["dossier"].attempts),
                         ("completed", 1, 1), "a completed run is not recalculated")
        self.assertEqual((files(), len(self.calls)), (before, calls))
        # A run past its plan keeps the extraction its ledger was bound to.
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=stopping_at(ResearchDecision)):
            planned = run_research(self.root)
        self.assertTrue((self.root / "runs" / planned.run_id / "question_research/state.json").exists())
        first, second, third = upgraded(self.model)
        with first, second, third:
            resumed = run_research(self.root, resume=True, run_id=planned.run_id)
        self.assertEqual((resumed.status, resumed.stages["retrieval"].attempts), ("completed", 1))
        # A run stopped before its questions were planned is read again with the new parser, from its stored download.
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=stopping_at(QuestionPlan)):
            unplanned = run_research(self.root)
        self.assertEqual(unplanned.status, "waiting_for_quota")
        self.assertFalse((self.root / "runs" / unplanned.run_id / "question_research/state.json").exists())
        downloads = self.download.call_count
        first, second, third = upgraded(self.model)
        with first, second, third:
            rebuilt = run_research(self.root, resume=True, run_id=unplanned.run_id)
        self.assertEqual((rebuilt.status, rebuilt.stages["retrieval"].attempts, rebuilt.stages["discovery"].attempts),
                         ("completed", 2, 1))
        self.assertEqual(self.download.call_count, downloads)

    def test_the_advisor_of_an_openrouter_run_is_asked_with_the_runs_key(self):
        """Review 2026-11-14: the advisor's pool got the Anthropic key only. An OpenRouter run whose key came from the
        Studio or --api-key stopped at its first blocked question with openrouter_key_required, one charged call per
        resume."""
        from podcast_automate.research_advisor import BlockAdvice
        from podcast_automate.web_search import SearchPlan
        config = json.loads(json.dumps(self.config.model_dump(mode="json")))
        config["research_limits"]["cost_usd"] = 50.0
        write_yaml(self.root / "project.yaml", config)
        keys = []

        def openrouter(adapter, prompt, output_type, directory, **kwargs):
            keys.append((output_type, adapter._key.get_secret_value()))
            if output_type is SearchPlan:
                return SearchPlan(queries=["energy model definition"], languages=[]), {"separately_billed_cost": 0.01}
            if output_type is ResearchDecision:
                return decision("blocked"), {"separately_billed_cost": 0.01}
            payload = json.loads(prompt.splitlines()[-1])
            searched = isinstance(payload, dict) and "results" in payload and "task" in payload
            output, _ = self.model(payload["task"] if searched else prompt, output_type, directory,
                                   **{**kwargs, "search": searched})
            return output, {"separately_billed_cost": 0.01}
        found = {"queries": ["energy model definition"], "request_id": "r1", "usd": 0.005, "elapsed_seconds": 0.1,
                 "results": [{"title": "Paper 0", "url": "https://example.org/paper0", "snippet": "s", "date": None,
                              "last_updated": None}]}
        environment = {name: value for name, value in os.environ.items() if name != "OPENROUTER_API_KEY"}
        with patch.dict(os.environ, environment, clear=True), \
                patch("podcast_automate.openrouter.OpenRouterAdapter.structured", autospec=True, side_effect=openrouter), \
                patch("podcast_automate.web_search.PerplexitySearch.search", return_value=found), \
                patch("podcast_automate.research.CodexAdapter.structured", side_effect=AssertionError("no Codex")), \
                patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", side_effect=AssertionError("no Claude")):
            run = run_research(self.root, backend="openrouter", model="vendor/test-model", api_key="or-key-0123456789",
                               web_search="perplexity", perplexity_key="pplx-test-key-0123456789", plan_review="auto")
        self.assertEqual(run.stages["dossier"].error.code, "research_questions_blocked")
        self.assertIn((BlockAdvice, "or-key-0123456789"), keys)

    def test_a_call_refused_for_a_missing_key_before_any_model_work_is_not_charged(self):
        """Review 2026-11-14: a call the OpenRouter adapter refused for a missing key, before any request, stayed charged
        to the run's call and search limits, and a resume did not give it back."""
        config = json.loads(json.dumps(self.config.model_dump(mode="json")))
        config["research_limits"]["cost_usd"] = 50.0
        write_yaml(self.root / "project.yaml", config)
        environment = {name: value for name, value in os.environ.items() if name != "OPENROUTER_API_KEY"}
        with patch.dict(os.environ, environment, clear=True), \
                patch("podcast_automate.research.CodexAdapter.structured", side_effect=AssertionError("no Codex")):
            run = run_research(self.root, backend="openrouter", model="vendor/test-model", web_search="perplexity",
                               perplexity_key="pplx-test-key-0123456789", plan_review="auto")
        self.assertEqual(run.stages["discovery"].error.code, "openrouter_key_required")
        budget = json.loads((self.root / "runs" / run.run_id / "budget.json").read_text(encoding="utf-8"))
        self.assertEqual((budget["model_calls"], budget["search_rounds"]), (0, 0))

    def test_a_run_on_codex_s_default_model_keeps_it_when_the_setting_changes_before_a_resume(self):
        """Review 2026-11-14: a run started without --model stored no text choice and resumed on whatever
        runtime.codex_model said then, though the model choice stays binding. New runs record the model they started
        with; a run started before keeps reading the setting."""
        data = self.config.model_dump(mode="json")
        data["runtime"]["codex_model"] = "gpt-6-astra"
        write_yaml(self.root / "project.yaml", data)
        models, stopped = [], []

        def model(adapter, prompt, output_type, directory, **kwargs):
            models.append(adapter.settings.codex_model)
            if output_type is ResearchDecision and not stopped:
                stopped.append(True)
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.research.CodexAdapter.structured", autospec=True, side_effect=model):
            first = run_research(self.root)
            self.assertEqual(first.status, "waiting_for_quota")
            data["runtime"]["codex_model"] = "gpt-6-other"
            write_yaml(self.root / "project.yaml", data)
            count = len(models)
            second = run_research(self.root, resume=True, run_id=first.run_id)
        self.assertEqual(second.status, "completed")
        self.assertEqual(set(models[count:]), {"gpt-6-astra"})
        work = self.root / "runs" / first.run_id
        self.assertEqual(json.loads((work / "research_request.json").read_text(encoding="utf-8"))["codex_model"],
                         "gpt-6-astra")

    def test_source_limit_trims_excess_search_results_and_never_loads_more(self):
        """Since 2026-09-30 an excess is trimmed to the limit and named instead of discarding the paid search;
        the limit itself still holds: never more downloads than approved sources."""
        data = self.config.model_dump(mode="json")
        data["research_limits"]["sources"] = 1
        write_yaml(self.root / "project.yaml", data)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=lambda prompt, output_type, *a, **k:
                   (discovery(count=2), {}) if output_type is ResearchDiscovery else self.model(prompt, output_type, *a, **k)):
            run = run_research(self.root)
        saved = json.loads((self.root / "runs" / run.run_id / "discovery.json").read_text(encoding="utf-8"))
        self.assertEqual([c["url"] for c in saved["candidates"]], ["https://example.org/paper0"])
        self.assertIn("Paper 1", saved["limitations"][-1])
        self.assertEqual(self.download.call_count, 1)

    def test_failed_rebuild_marks_downstream_stages_pending(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            run_research(self.root)
        raw = next((self.root / "sources/raw").rglob("*.html"))
        raw.write_bytes(b"corrupted")
        self.download.side_effect = AppError("Unavailable", code="source_download_failed")
        second = run_research(self.root, resume=True)
        self.assertEqual(second.status, "blocked")
        self.assertEqual(second.stages["dossier"].status, "pending")
        self.assertEqual(second.stages["publish"].status, "pending")
        self.assertEqual(second.stages["publish"].outputs, {})

    def test_no_readable_source_blocks_dossier_creation(self):
        self.download.side_effect = AppError("HTTP unavailable", code="source_download_failed")
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            run = run_research(self.root)
        self.assertEqual(run.status, "blocked")
        self.assertEqual(run.stages["retrieval"].error.code, "no_readable_sources")
        self.assertFalse((self.root / "research/research_briefing.md").exists())
        self.assertEqual(self.calls, [ResearchDiscovery])

    def test_call_budget_is_enforced_across_resume(self):
        data = self.config.model_dump(mode="json")
        data["research_limits"]["model_calls"] = 1
        write_yaml(self.root / "project.yaml", data)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            first = run_research(self.root)
            second = run_research(self.root, resume=True)
        self.assertEqual(first.stages["dossier"].error.code, "research_budget_exhausted")
        self.assertEqual(second.status, "blocked")
        self.assertEqual(self.calls, [ResearchDiscovery])
        # The manifest names the spent limit, so the Studio need not read the German message (D-152).
        self.assertEqual(first.stages["dossier"].error.details, {"limit": "model_calls"})
        saved = read_yaml(self.root / "runs" / second.run_id / "run_manifest.yaml")
        self.assertEqual(saved["stages"]["dossier"]["error"]["details"], {"limit": "model_calls"})

    def test_changed_local_source_requires_new_run(self):
        local = self.root / "notes.txt"
        local.write_text(TEXT)
        data = self.config.model_dump(mode="json")
        data["local_sources"] = ["notes.txt"]
        write_yaml(self.root / "project.yaml", data)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            self.assertEqual(run_research(self.root).status, "completed")
            local.write_text(TEXT + " An additional fact.")
            with self.assertRaises(AppError) as error:
                run_research(self.root, resume=True)
        self.assertEqual(error.exception.code, "inputs_changed")

    def test_a_copied_project_still_finds_its_local_source_by_content(self):
        """D-178, 2026-10-19: a local source's id comes from its absolute path, so the copy „Orlagau (ElevenLabs)“ named
        its upload anew and every script and recording stopped with „Lokale Quelle seit der Recherche geändert“. The
        content the research read counts; a changed file in the copy is still refused."""
        import shutil
        from podcast_automate.scripting import load_research
        from podcast_automate.storage import load_project
        local = self.root / "notes.txt"
        local.write_text(TEXT, encoding="utf-8")
        data = self.config.model_dump(mode="json")
        data["local_sources"] = ["notes.txt"]
        write_yaml(self.root / "project.yaml", data)
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            self.assertEqual(run_research(self.root).status, "completed")
        copy = self.root.parent / (self.root.name + "-copy")
        shutil.copytree(self.root, copy)
        run_id, *_ = load_research(copy, load_project(copy))
        self.assertTrue((copy / "runs" / run_id).is_dir())
        (copy / "notes.txt").write_text(TEXT + " Changed in the copy.", encoding="utf-8")
        with self.assertRaises(AppError) as changed:
            load_research(copy, load_project(copy))
        self.assertEqual(changed.exception.code, "inputs_changed")

    def test_reference_check_rejects_invented_or_unseen_evidence(self):
        context = [{"sections": [{"reference": "src_a#sec_a", "text": TEXT}]}]
        draft = ResearchDossier(topic="Test topic", scope_note="Test", findings=[Finding(
            id="f_a", kind="claim", statement="Test claim", evidence=[Evidence(reference="src_a#sec_a", excerpt="Models assign an energy")])],
            coverage=[QuestionCoverage(question_id="q_energy", status="answered", finding_ids=["f_a"], gap="")], open_questions=[])
        self.assertEqual(validate_dossier(draft, discovery(), context), [])
        draft.findings[0].evidence[0].excerpt = "An invented quote"
        self.assertTrue(any("not verbatim" in error for error in validate_dossier(draft, discovery(), context)))
        draft.findings[0].evidence[0].reference = "src_a#unseen"
        self.assertTrue(any("unknown or unseen" in error for error in validate_dossier(draft, discovery(), context)))
        draft.coverage = []
        self.assertTrue(any("each research question" in error for error in validate_dossier(draft, discovery(), context)))

    def test_persistent_review_issue_prevents_publication(self):
        original_dossier = None

        def model(prompt, output_type, directory, **kwargs):
            nonlocal original_dossier
            if output_type is SourceReview:
                return complete_fixture_response(SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason="Claim is overstated",
                    resolution="revise", search_queries=[])], limitations=[]), json.loads(prompt.splitlines()[-1])), {}
            if output_type is ResearchDossier and original_dossier is not None:
                return original_dossier, {}
            result = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ResearchDossier:
                original_dossier = result[0]
            return result

        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model) as backend:
            run = run_research(self.root)
            calls_before = backend.call_count
            resumed = run_research(self.root, resume=True)
            self.assertEqual(backend.call_count, calls_before)
        self.assertEqual(run.status, "blocked")
        self.assertEqual(resumed.status, "blocked")
        self.assertEqual(run.stages["dossier"].error.code, "research_questions_blocked")
        self.assertFalse((self.root / "research/research_briefing.md").exists())

    def test_review_resume_preserves_revised_draft_after_quota(self):
        draft = None
        review_calls = 0
        synthesis_calls = 0

        def model(prompt, output_type, directory, **kwargs):
            nonlocal draft, review_calls, synthesis_calls
            if output_type is ResearchDossier:
                synthesis_calls += 1
                if draft is None:
                    draft = dossier_from_prompt(prompt)
                else:
                    draft = draft.model_copy(deep=True)
                    draft.findings[0].statement = "The model scores possible configurations."
                return draft, {}
            if output_type is DossierPatch:
                draft = draft.model_copy(deep=True)
                draft.findings[0].statement = "The model scores possible configurations."
                return DossierPatch(updates=draft.findings, additions=[], coverage_updates=[],
                                    resolved_open_questions=[], new_open_questions=[]), {}
            if output_type is SourceReview:
                review_calls += 1
                if review_calls == 1:
                    return complete_fixture_response(SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason="Clarify the idea",
                        resolution="revise", search_queries=[])], limitations=[]), json.loads(prompt.splitlines()[-1])), {}
                payload = json.loads(prompt.splitlines()[-1])
                self.assertEqual(payload["dossier"]["findings"][0]["statement"], "The model scores possible configurations.")
                if review_calls == 2:
                    raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
                return complete_fixture_response(SourceReview(issues=[], limitations=[]), payload), {}
            return self.model(prompt, output_type, directory, **kwargs)

        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=model):
            interrupted = run_research(self.root)
            self.assertEqual(interrupted.status, "waiting_for_quota")
            finished = run_research(self.root, resume=True)
        self.assertEqual(finished.status, "completed")
        self.assertEqual(synthesis_calls, 1)
        self.assertEqual(review_calls, 3)
        self.assertEqual(self.download.call_count, 1)
        self.assertIn("The model scores possible configurations.",
                      (self.root / "research/research_briefing.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
