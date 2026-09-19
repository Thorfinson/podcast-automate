import io
import json
import socket
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.research import run_research, validate_dossier
from podcast_automate.research_patches import DossierPatch
from podcast_automate.research_review import SourceReview, SourceReviewIssue
from podcast_automate.research_models import Evidence, Finding, QuestionCoverage, ResearchDiscovery, ResearchDossier
from podcast_automate.research_quality import requirements_for
from podcast_automate.research_tasks import QuestionPlan, ReopenPlan, ResearchDecision
from podcast_automate.run_budget import approve_research_gap
from podcast_automate.runner import status
from podcast_automate.sources import canonical_url, extract, public_url, PublicRedirect
from podcast_automate.storage import write_yaml
from tests import research_fixtures as fixtures
from tests.research_fixtures import TEXT, HTML, discovery, dossier_from_prompt
from tests.question_fixtures import complete_fixture_response, decision, task_value


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


class ResearchTests(fixtures.ResearchProjectCase):
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
        self.assertEqual([version for version, _ in seen], ["research_discovery.v4-independence"])
        self.assertIn("For every empirical or performance claim family, include at least one source not "
                      "authored by the organisation making the claim, or state in limitations that none "
                      "was found.", seen[0][1])

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
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            Finding(id="f_picture", kind="example", statement="A supported idea.",
                    illustration="Picture a landscape.",
                    evidence=[Evidence(reference="source#section", excerpt="anchor")])

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
            first = run_research(self.root)
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

    def test_parser_upgrade_reuses_download_and_rebuilds_dossier(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            first = run_research(self.root)
            with patch("podcast_automate.research.EXTRACTION_VERSION", "test-upgrade"), \
                 patch("podcast_automate.sources.EXTRACTION_VERSION", "test-upgrade"):
                second = run_research(self.root, resume=True)
        self.assertEqual(second.status, "completed")
        self.assertEqual(second.stages["retrieval"].attempts, 2)
        self.assertEqual(second.stages["dossier"].attempts, 2)
        self.assertEqual(second.stages["discovery"].attempts, 1)
        self.assertEqual(self.download.call_count, 1)

    def test_source_limit_rejects_excess_search_results(self):
        data = self.config.model_dump(mode="json")
        data["research_limits"]["sources"] = 1
        write_yaml(self.root / "project.yaml", data)
        with patch("podcast_automate.research.CodexAdapter.structured", return_value=(discovery(count=2), {})):
            run = run_research(self.root)
        self.assertEqual(run.stages["discovery"].error.code, "invalid_model_output")
        self.assertEqual(self.download.call_count, 0)

    def test_failed_rebuild_marks_downstream_stages_pending(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            first = run_research(self.root)
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
