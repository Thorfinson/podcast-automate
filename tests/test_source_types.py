"""Source types, idea sources, research aims and the recency rule (2026-09-30).

Both series of September came out as evidence audits: the research planned every question as a test, read critiques
instead of the thinkers' own works, treated the user's LLM-written idea notes as claims to check, and never looked at
how old a practice source was. These tests pin the rules that replace that."""
import io
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.question_research import check_aims
from podcast_automate.research_dates import research_day, run_date
from podcast_automate.research_evidence import evidence_profile
from podcast_automate.research_models import SourceCandidate, admissible, is_idea
from podcast_automate.research_quality import quality_brief
from podcast_automate.research_reader import source_facts
from podcast_automate.research_tasks import QuestionPlan, QuestionTask
from podcast_automate.sources import import_source
from tests.question_fixtures import task_value
from tests.research_fixtures import TEXT


def candidate(**fields):
    return SourceCandidate(**{"url": "https://example.org/paper.pdf", "title": "Search title", "authors": [],
                              "published_date": "", "rationale": "Test", "primary_source": False, **fields})


def pdf(created=None):
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 12 Tf 30 700 Td (" + TEXT + ") Tj ET").encode())
    page[NameObject("/Contents")] = stream
    if created:
        writer.add_metadata({"/CreationDate": created})
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class SourceTypeTests(unittest.TestCase):
    def test_a_search_may_add_any_typed_source_but_never_an_idea_source(self):
        for source_type, primary, expected in (("practice", False, True), ("critique", False, True),
                                               ("primary_work", False, True), ("idea", True, False),
                                               ("unknown", True, True), ("unknown", False, False)):
            with self.subTest(source_type=source_type, primary=primary):
                self.assertIs(admissible(candidate(source_type=source_type, primary_source=primary)), expected)

    def test_idea_sources_are_never_evidence_whatever_their_address(self):
        with tempfile.TemporaryDirectory() as temporary:
            web, _ = import_source(candidate(source_type="practice"), Path(temporary), "run_20260930_120000_000000_abcdef12",
                                   downloaded=(pdf(), "application/pdf", "https://example.org/paper.pdf"))
            post, _ = import_source(candidate(url="https://social.example/post/1", source_type="idea"), Path(temporary),
                                    "run_20260930_120000_000000_abcdef12",
                                    downloaded=(pdf(), "application/pdf", "https://social.example/post/1"))
        self.assertEqual((web.source_type, post.source_type), ("practice", "idea"))
        self.assertEqual((is_idea(web), is_idea(post)), (False, True))
        self.assertTrue(is_idea(web.model_copy(update={"url": "", "final_url": ""})), "user material without a URL")

    def test_a_pdf_names_its_own_date_and_a_search_result_date_is_marked_as_such(self):
        with tempfile.TemporaryDirectory() as temporary:
            dated, _ = import_source(candidate(published_date="2020-01-01"), Path(temporary), "run_x",
                                     downloaded=(pdf("D:20260812093000Z"), "application/pdf", "https://example.org/paper.pdf"))
            guessed, _ = import_source(candidate(url="https://example.org/b.pdf", published_date="2025-03-01"),
                                       Path(temporary), "run_x",
                                       downloaded=(pdf(), "application/pdf", "https://example.org/b.pdf"))
            undated, _ = import_source(candidate(url="https://example.org/c.pdf"), Path(temporary), "run_x",
                                       downloaded=(pdf(), "application/pdf", "https://example.org/c.pdf"))
        self.assertEqual((dated.published_date, dated.date_basis), ("2026-08-12", "document"))
        self.assertEqual((guessed.published_date, guessed.date_basis), ("2025-03-01", "search_result"))
        self.assertEqual(undated.date_basis, "unknown")
        self.assertEqual(source_facts(dated), {"published": "2026-08-12"})
        self.assertEqual(source_facts(guessed), {"published": "2025-03-01 (search result)"})
        self.assertEqual(source_facts(undated.model_copy(update={"source_type": "standard"})), {"type": "standard"})

    def test_a_primary_work_or_an_open_access_book_is_read_with_the_book_limits(self):
        # 2026-10-02: the article limit (300 pages, 1M characters) refused the OAPEN and DOAB books the search is sent to.
        from unittest.mock import patch
        from podcast_automate import sources
        seen = []

        def parse(raw, *, book=False):
            seen.append(book)
            return {"metadata": {}, "blocks": [[TEXT, 1]]}
        cases = ((candidate(source_type="primary_work"), True),
                 (candidate(url="https://library.oapen.org/bitstream/20.500/1/book.pdf", source_type="overview"), True),
                 (candidate(url="https://link.springer.com/book/10.1007/978-3-030-1", source_type="overview"), True),
                 (candidate(source_type="study"), False), (candidate(), False))
        with tempfile.TemporaryDirectory() as temporary, patch("podcast_automate.sources.extract_pdf_isolated", side_effect=parse):
            for found, book in cases:
                with self.subTest(url=found.url, source_type=found.source_type):
                    import_source(found, Path(temporary), "run_x", downloaded=(pdf(), "application/pdf", found.url))
                    self.assertIs(seen[-1], book)
        # A refusal in book mode names the book limits.
        refused = sources.pdf_failure(b'{"error": "UnreadablePdf", "reason": "too_many_pages"}', book=True)
        self.assertIn("2000 Seiten", str(refused))
        self.assertIn("300 Seiten", str(sources.pdf_failure(b'{"error": "UnreadablePdf", "reason": "too_many_pages"}')))

    def test_a_service_key_never_follows_a_redirect_to_another_host(self):
        # 2026-10-02: urllib copies the Authorization header (CORE's key) onto every redirected request.
        import urllib.request
        from unittest.mock import patch
        from podcast_automate.sources import PublicRedirect
        handler = PublicRedirect()

        def follow(new, headers=None):
            request = urllib.request.Request("https://api.core.ac.uk/v3/search/works", headers=headers or {})
            return handler.redirect_request(request, None, 302, "Found", {}, new)
        with patch("podcast_automate.sources.public_url"):
            key = {"Authorization": "Bearer secret"}
            for new in ("https://elsewhere.example/works", "http://api.core.ac.uk/v3/search/works"):
                with self.subTest(new=new), self.assertRaises(AppError) as refused:
                    follow(new, key)
                self.assertEqual(refused.exception.code, "source_download_failed")
            self.assertEqual(follow("https://api.core.ac.uk/v3/other", key).full_url, "https://api.core.ac.uk/v3/other")
            self.assertEqual(follow("https://elsewhere.example/paper.pdf").full_url, "https://elsewhere.example/paper.pdf")

    def test_a_document_or_candidate_from_before_types_dumps_exactly_as_before(self):
        legacy = candidate()
        self.assertNotIn("source_type", legacy.model_dump())
        self.assertIn("source_type", candidate(source_type="study").model_dump())
        with tempfile.TemporaryDirectory() as temporary:
            document, _ = import_source(legacy, Path(temporary), "run_x",
                                        downloaded=(pdf(), "application/pdf", "https://example.org/paper.pdf"))
        self.assertFalse({"source_type", "date_basis"} & set(document.model_dump()))


class AimTests(unittest.TestCase):
    def plan(self, *tasks):
        return QuestionPlan(tasks=[QuestionTask(**{**task_value(id=f"task_{i}"), **task}) for i, task in enumerate(tasks)])

    def test_an_explain_task_names_the_works_it_explains_from(self):
        config = TopicBrief(topic="Psychohistory")
        with self.assertRaises(AppError) as unnamed:
            check_aims(self.plan({"aim": "explain"}), config)
        self.assertIn("primary_works", str(unnamed.exception))
        check_aims(self.plan({"aim": "explain", "primary_works": ["Dalio, Principles for Dealing with the Changing World Order"]}),
                   config)

    def test_a_series_meant_to_explain_or_apply_gets_tasks_that_do_so(self):
        understand = TopicBrief(topic="Psychohistory", series_goal={"understand": 3, "evaluate": 1, "apply": 0})
        build = TopicBrief(topic="Knowledge bases", series_goal={"understand": 2, "evaluate": 1, "apply": 3})
        with self.assertRaises(AppError):
            check_aims(self.plan({}, {"kind": "empirical"}), understand)
        check_aims(self.plan({"aim": "explain", "primary_works": ["Morris, Why the West Rules"]}, {}), understand)
        with self.assertRaises(AppError) as missing:
            check_aims(self.plan({"aim": "explain", "primary_works": ["W3C, SHACL"]}), build)
        self.assertIn("aim=build", str(missing.exception))
        check_aims(self.plan({"aim": "explain", "primary_works": ["W3C, SHACL"]}, {"aim": "build"}), build)
        # Without a goal every plan passes as before.
        check_aims(self.plan({}), TopicBrief(topic="Unset"))

    def test_each_aim_adds_its_own_completion_profile(self):
        explain = QuestionTask(**{**task_value(kind="theory"), "aim": "explain", "primary_works": ["Piketty, Capital"]})
        build = QuestionTask(**{**task_value(kind="mechanism"), "aim": "build"})
        plain = QuestionTask(**task_value())
        self.assertIn("in its own logic from its author's own work", evidence_profile(explain))
        self.assertIn("Original theoretical account", evidence_profile(explain))
        self.assertIn("date every practice claim", evidence_profile(build))
        self.assertEqual(evidence_profile(plain), "Original definition, conceptual scope and distinctions; no artificial "
                                                  "empirical test.")
        self.assertNotIn("aim", plain.model_dump(), "a task from before aims dumps as before")


class RecencyTests(unittest.TestCase):
    def test_the_research_date_is_the_runs_own_and_only_sent_with_a_recency_rule(self):
        work = Path("runs/run_20260930_101500_000000_abcdef12")
        self.assertEqual(run_date(work), "2026-09-30")
        self.assertEqual(run_date(Path("runs/latest")), "")
        self.assertEqual(research_day(TopicBrief(topic="History"), work), {})
        self.assertEqual(research_day(TopicBrief(topic="AI practice", recency_months=6), work),
                         {"research_date": "2026-09-30"})

    def test_the_quality_brief_carries_goal_and_recency_only_when_set(self):
        plain = TopicBrief(topic="Unset")
        self.assertFalse({"series_goal", "recency_months"} & set(quality_brief(plain)))
        aimed = plain.model_copy(update={"series_goal": {"understand": 1, "evaluate": 1, "apply": 3}, "recency_months": 3})
        self.assertEqual((quality_brief(aimed)["series_goal"]["apply"], quality_brief(aimed)["recency_months"]), (3, 3))



class LibraryChoiceTests(unittest.TestCase):
    def test_the_library_comes_from_the_newest_completed_research_not_a_run_stopped_at_its_start(self):
        from podcast_automate.research import latest_research_run
        from podcast_automate.storage import write_yaml
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(latest_research_run(root))
            for run_id, kind, state in (("run_20260913_100000_000000_aaaaaaaa", "research", "completed"),
                                        ("run_20260920_100000_000000_bbbbbbbb", "script", "completed"),
                                        ("run_20260930_100000_000000_cccccccc", "research", "pending")):
                write_yaml(root / "runs" / run_id / "run_manifest.yaml", {"kind": kind, "status": state})
            self.assertEqual(latest_research_run(root), "run_20260913_100000_000000_aaaaaaaa")
            write_yaml(root / "runs/run_20260913_100000_000000_aaaaaaaa/run_manifest.yaml",
                       {"kind": "research", "status": "blocked"})
            self.assertEqual(latest_research_run(root), "run_20260930_100000_000000_cccccccc", "else the newest")

if __name__ == "__main__":
    unittest.main()
