"""The research step neither dead-ends nor hangs: accepted gaps, re-asked receipts, refunds, stalls, isolation."""
import contextlib
import http.client
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from podcast_automate.claude_code import ClaudeCodeAdapter
from podcast_automate.cli import main
from podcast_automate.codex import CodexAdapter
from podcast_automate.errors import AppError
from podcast_automate.models import ResearchLimits, RunManifest, RuntimeSettings, TextProbeOutput, TopicBrief, now
from podcast_automate.provider_pool import AdapterPool
from podcast_automate.question_research import QuestionResearch
from podcast_automate.research import reconcile_budget, reserve_call
from podcast_automate.research_ledger import load_index, read_value, save_value
from podcast_automate.research_quality import ResearchAssessment
from podcast_automate.research_tasks import AnswerReview, QuestionPlan, QuestionSearch, ReopenPlan, ResearchDecision
from podcast_automate.run_budget import accepted_gaps, approve_research_gap, effective_limits
from podcast_automate.sources import download, extract, import_failure, public_url
from podcast_automate.storage import digest, init_project, write_json, write_yaml
from podcast_automate.studio import make_server, read_json
from podcast_automate.text_settings import auto_candidates
from tests import research_fixtures as fixtures
from tests.question_fixtures import answer_for, decision, task_value
from tests.test_codex_stream import SERVER, Result
from tests.test_provider_pool import QuotaFakes
from tests.test_question_research import QuestionResearchTests

INPUT_HASH = "a" * 64


class WorkflowCase(unittest.TestCase):
    """The question-workflow harness plus a run manifest, so run-bound approvals can be written."""

    def setUp(self):
        self.fixture = QuestionResearchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.work = self.fixture.root, self.fixture.work
        write_yaml(self.work / "run_manifest.yaml", RunManifest(run_id="run_test", kind="research", project_hash="0" * 64,
                                                                input_hash=INPUT_HASH, stages={}).model_dump(mode="json"))

    def engine(self):
        f = self.fixture
        return QuestionResearch(f.root, f.work, f.config, f.model,
                                lambda activity: write_json(f.work / "research_activity.json", {"activity": activity}),
                                accepted=lambda: accepted_gaps(f.work, INPUT_HASH))

    def run_engine(self):
        engine = self.engine()
        engine.run(self.fixture.discovery, self.fixture.index)
        return engine

    def calls(self, schema):
        return sum(c[0] is schema for c in self.fixture.calls)

    def block_empirical_task(self):
        def separate(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchDecision and payload["task"]["kind"] == "empirical":
                return decision("blocked")
        self.fixture.hook = separate
        with self.assertRaises(AppError) as blocked:
            self.run_engine()
        self.assertEqual(blocked.exception.code, "research_questions_blocked")


class AcceptedGapTests(WorkflowCase):
    def test_blocked_task_can_be_accepted_as_gap_and_the_dossier_completes_without_it(self):
        self.block_empirical_task()
        with self.assertRaises(AppError):
            approve_research_gap(self.root, "run_test", "task_definition")  # verified, never skipped
        with self.assertRaises(AppError):
            approve_research_gap(self.root, "run_test", "task_unknown")
        decisions = self.calls(ResearchDecision)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["approve", str(self.root), "--run-id", "run_test", "--accept-gap", "task_empirical",
                         "--reason", "Not needed for this episode", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["gap_approval"]["task_id"], "task_empirical")
        engine = self.run_engine()
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(self.calls(ResearchDecision), decisions, "an accepted gap is never researched again")
        row = engine.state["tasks"]["task_empirical"]
        self.assertEqual((row["status"], row["outcome"], row["accepted_gap"]["reason"]),
                         ("blocked", "accepted_gap", "Not needed for this episode"))
        report = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
        self.assertTrue(report["passed"])
        self.assertEqual([g["task_id"] for g in report["accepted_gaps"]], ["task_empirical"])
        saved = json.loads((self.work / "complete_research/accepted_gaps.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["gaps"][0]["task_id"], "task_empirical")
        self.assertIn("Akzeptierte Lücken", (self.work / "research_quality.md").read_text(encoding="utf-8"))
        public = json.loads((self.work / "research_questions.json").read_text(encoding="utf-8"))
        self.assertEqual((public["blocked"], public["accepted"], public["phase"]), (0, 1, "completed"))
        # Repeating the approval is harmless; a resumed run stays complete without new calls.
        approve_research_gap(self.root, "run_test", "task_empirical")
        calls = len(self.fixture.calls)
        self.assertEqual(self.run_engine().state["phase"], "completed")
        self.assertEqual(len(self.fixture.calls), calls)

    def test_an_evidence_block_after_a_failed_review_takes_the_same_gap_approval_path(self):
        def flow(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchDecision and payload["task"]["kind"] == "empirical":
                if "answer" in payload["allowed_actions"]:
                    return decision("answer", answer=answer_for(self.fixture.ref))
                return decision("search_web", web_queries=["independent empirical test"])
            if schema is AnswerReview and payload["task"]["id"] == "task_empirical":
                return AnswerReview(criteria=[dict(index=0, passed=False, reason="No empirical test among the passages.")],
                                    supported=True, source_adequacy=True, issues=[])
        self.fixture.hook = flow
        with self.assertRaises(AppError) as blocked:
            self.run_engine()
        self.assertEqual(blocked.exception.code, "research_questions_blocked")
        state = read_value(self.work / "question_research/state.json")
        self.assertEqual((state["tasks"]["task_empirical"]["status"], state["tasks"]["task_empirical"]["outcome"]),
                         ("blocked", "evidence_block"))
        self.assertEqual(state["tasks"]["task_definition"]["status"], "verified")
        decisions = self.calls(ResearchDecision)
        approve_research_gap(self.root, "run_test", "task_empirical", "No empirical test exists for this fixture.")
        engine = self.run_engine()
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(self.calls(ResearchDecision), decisions)
        self.assertEqual(engine.state["tasks"]["task_empirical"]["outcome"], "accepted_gap")
        public = json.loads((self.work / "research_questions.json").read_text(encoding="utf-8"))
        self.assertEqual((public["blocked"], public["accepted"], public["phase"]), (0, 1, "completed"))

    def test_objections_that_only_concern_an_accepted_gap_finish_with_them_on_record(self):
        self.block_empirical_task()
        approve_research_gap(self.root, "run_test", "task_empirical", "Out of scope for now")
        decisions = self.calls(ResearchDecision)

        def object_to_gap(prompt, schema, payload, kwargs):
            if schema is ResearchAssessment:
                report = fixtures.assessment_from_prompt(prompt)
                report.requirements[0].explanation = False
                report.requirements[0].reason = "The empirical check is still absent."
                return report
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_empirical"], reason="Only the accepted gap.")
                                          for i in range(len(payload["objections"]))])
        self.fixture.hook = object_to_gap
        engine = self.run_engine()
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(engine.state["tasks"]["task_definition"]["status"], "verified")
        self.assertEqual(self.calls(ResearchDecision), decisions)
        report = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
        self.assertTrue(report["passed"] and report["passed_with_accepted_gaps"])
        self.assertIn("empirical check", " ".join(report["residual_objections"]))
        self.assertTrue(engine.state["accepted_gap_objections"])
        self.assertIn("Verbliebene Prüfeinwände", (self.work / "research_quality.md").read_text(encoding="utf-8"))

    def test_objections_against_a_verified_task_still_reopen_it_beside_an_accepted_gap(self):
        self.block_empirical_task()
        approve_research_gap(self.root, "run_test", "task_empirical")
        drafts = []

        def object_to_definition(prompt, schema, payload, kwargs):
            if schema is ResearchAssessment:
                report = fixtures.assessment_from_prompt(prompt)
                report.requirements[0].explanation = False
                report.requirements[0].reason = "The definition lacks its stated scope."
                return report
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_definition"], reason="Scope missing.")
                                          for i in range(len(payload["objections"]))])
            if schema is ResearchDecision and payload["task"]["kind"] == "definition":
                drafts.append(1)
                answer = answer_for(self.fixture.ref)
                answer.summary += f" Revision {len(drafts)}."
                return decision("answer", answer=answer)
        self.fixture.hook = object_to_definition
        with self.assertRaises(AppError) as blocked:
            self.run_engine()
        self.assertEqual(blocked.exception.code, "research_questions_blocked")
        self.assertEqual(len(self.engine_state()["tasks"]["task_definition"]["reopenings"]), 2)
        self.assertGreater(len(drafts), 0)

    def engine_state(self):
        return read_value(self.work / "question_research/state.json")


class RejectedReceiptTests(WorkflowCase):
    def setUp(self):
        super().setUp()
        self.searches = []

    def searching(self, responses):
        """Block the first reader step so the recovery searches the web; answer the searches in order."""
        def hook(prompt, schema, payload, kwargs):
            if schema is ResearchDecision and not self.searches:
                return decision("blocked")
            if schema is QuestionSearch:
                self.searches.append(prompt)
                return responses[min(len(self.searches), len(responses)) - 1]
        self.fixture.hook = hook

    def bad_search(self):
        candidate = fixtures.discovery(count=2).candidates[1].model_copy(update={"primary_source": False})
        return QuestionSearch(candidates=[candidate], limitations=["fixture"])

    def test_malformed_search_receipt_is_re_asked_with_the_rejection_named(self):
        self.searching([self.bad_search(), QuestionSearch(candidates=fixtures.discovery(count=2).candidates[1:], limitations=[])])
        self.fixture.download.side_effect = lambda url: (fixtures.HTML.replace(
            b"These sentences", b"Additional independent evidence. These sentences"), "text/html", url)
        engine = self.run_engine()
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(len(self.searches), 2)
        first_payload = self.searches[0].rsplit("\n", 1)[1]
        self.assertIn("Rejections:", self.searches[1])
        self.assertIn("Quellenauftrag", self.searches[1])
        self.assertTrue(self.searches[1].endswith("\n" + first_payload), "the JSON payload stays the last line")
        rejected = next((self.work / "question_research/tasks").glob("*/attempt_0/step_*/search_rejected_00.json"))
        self.assertEqual(json.loads(rejected.read_text(encoding="utf-8"))["code"], "invalid_model_output")
        self.assertTrue(rejected.with_name("search.json").exists())
        self.assertFalse(rejected.with_name("search_rejected_01.json").exists())

    def test_reader_payload_mismatch_is_re_asked_with_the_defect_named(self):
        readers = []

        def hook(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                readers.append(prompt)
                if len(readers) == 1:
                    return decision("read")  # reads nothing: the payload contradicts the action
        self.fixture.hook = hook
        engine = self.run_engine()
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(len(readers), 2)
        self.assertIn("Rejections:", readers[1])
        self.assertIn("Action 'read' takes only 'windows'; supplied: none", readers[1])
        self.assertTrue(readers[1].endswith("\n" + readers[0].rsplit("\n", 1)[1]), "the JSON payload stays the last line")
        rejected = next((self.work / "question_research/tasks").glob("*/attempt_0/step_*/reader_rejected_00.json"))
        saved = json.loads(rejected.read_text(encoding="utf-8"))
        self.assertEqual((saved["code"], saved["value"]["action"]), ("invalid_model_output", "read"))
        self.assertTrue(rejected.with_name("reader.json").exists())

    def test_persistent_reader_payload_mismatch_blocks_after_named_retries(self):
        self.fixture.hook = lambda prompt, schema, payload, kwargs: (
            decision("answer", answer=answer_for(self.fixture.ref), web_queries=["energy"])
            if schema is ResearchDecision else None)
        with self.assertRaises(AppError) as caught:
            self.run_engine()
        self.assertEqual((caught.exception.code, caught.exception.status), ("invalid_model_output", "blocked"))
        self.assertIn("supplied: 'web_queries', 'answer'", str(caught.exception))
        self.assertIn("wiederholt", str(caught.exception))
        self.assertEqual(self.calls(ResearchDecision), 3)

    def test_decision_schema_reports_a_payload_mismatch_instead_of_failing(self):
        window = {"reference": self.fixture.ref, "before": 0, "after": 1}
        self.assertIsNone(decision("blocked").payload_error())
        self.assertIsNone(decision("read", windows=[window]).payload_error())
        self.assertIn("Action 'blocked' takes no payload; supplied: 'windows'",
                      decision("blocked", windows=[window]).payload_error())

    def test_exhausted_rejections_block_and_resume_makes_no_further_call(self):
        self.searching([self.bad_search()])
        with self.assertRaises(AppError) as caught:
            self.run_engine()
        self.assertEqual(caught.exception.code, "invalid_model_output")
        self.assertIn("wiederholt", str(caught.exception))
        self.assertEqual(len(self.searches), 3)
        calls = len(self.fixture.calls)
        with self.assertRaises(AppError) as again:
            self.run_engine()
        self.assertEqual(again.exception.code, "invalid_model_output")
        self.assertEqual(len(self.fixture.calls), calls)

    def test_saved_receipt_that_fails_todays_check_is_retired_and_re_asked(self):
        self.run_engine()
        path = self.work / "question_research/synthesis/audit_00/assessment.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["value"]["requirements"][0]["finding_ids"] = ["f_unknown"]
        saved["sha256"] = digest(saved["value"])
        write_json(path, saved)
        assessments = self.calls(ResearchAssessment)
        again = self.run_engine()
        self.assertEqual(again.state["phase"], "completed")
        self.assertEqual(self.calls(ResearchAssessment), assessments + 1)
        self.assertTrue(path.with_name("assessment_rejected_00.json").exists())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["value"]["requirements"][0]["finding_ids"], ["f_energy"])

    def test_plan_beyond_the_affordable_task_count_is_re_asked_then_accepted(self):
        # Nothing spent yet and the default of 8 calls per task (no calibration history in a fresh project):
        # affordable = (20 - 0 - 4) // 8 = 2 tasks, so a three-task plan still exceeds the allowance.
        self.fixture.config.research_limits.model_calls = 20
        plans = []

        def oversized(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                plans.append(json.loads(prompt.splitlines()[-1]))
                return QuestionPlan(tasks=[task_value(f"task_{n}") for n in range(3)])
        self.fixture.hook = oversized
        engine = self.engine()
        engine.initialise(self.fixture.discovery, self.fixture.index, None, [])
        self.assertEqual(len(plans), 3)
        self.assertEqual((plans[0]["planning_budget"]["max_tasks"], plans[0]["planning_budget"]["expected_calls_per_task"]), (2, 8))
        self.assertEqual(plans[0]["planning_budget"]["expected_calls_source"], "default")
        self.assertNotIn("Rejections:", plans[0].get("brief", {}).get("topic", ""))
        rejected = json.loads((self.work / "question_research/plan_rejected_01.json").read_text(encoding="utf-8"))
        self.assertEqual(rejected["code"], "plan_exceeds_allowance")
        self.assertEqual(len(engine.state["plan"]["tasks"]), 3, "the final attempt keeps the plan and lets the projection decide")
        # The scope reviewer may not be blamed for a plan it cannot shrink.
        self.assertEqual(sum(c[0].__name__ == "QuestionScopeReview" for c in self.fixture.calls), 1)


class StateGrowthTests(WorkflowCase):
    def test_receipts_and_index_copies_stay_small_and_reload_exactly(self):
        decisions = []

        def recover(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                decisions.append(1)
                if len(decisions) == 1:
                    return decision("blocked")
            if schema is QuestionSearch:
                return QuestionSearch(candidates=fixtures.discovery(count=2).candidates[1:], limitations=[])
        self.fixture.hook = recover
        self.fixture.download.side_effect = lambda url: (fixtures.HTML.replace(
            b"These sentences", b"Additional independent evidence. These sentences"), "text/html", url)
        engine = self.run_engine()
        self.assertEqual(len(engine.index.sources), 2)
        receipts = engine.state["tasks"]["task_definition"]["search_receipts"]
        local = [r for r in receipts if r["lane"] == "local"]
        self.assertTrue(local)
        self.assertTrue(all("result" not in r and r["candidate_refs"] for r in local))
        manifests = sorted((self.work / "question_research/indexes").glob("*.json"))
        self.assertEqual(len(manifests), 2)
        for path in manifests:
            text = path.read_text(encoding="utf-8")
            self.assertIn("index_manifest.v1", text)
            self.assertNotIn("Models assign an energy", text)
            self.assertEqual(digest(load_index(self.root, path).model_dump()), path.stem)
        legacy = self.work / "question_research/indexes/legacy.json"
        save_value(legacy, engine.index.model_dump())
        self.assertEqual(load_index(self.root, legacy), engine.index)
        receipt = next((self.work / "question_research/tasks").glob("*/attempt_0/step_*/downloads.json"))
        saved = read_value(receipt)
        self.assertNotIn("index", saved)
        self.assertEqual(len(saved["added"]), 1)
        self.assertEqual(saved["base"], manifests[0].stem if digest(self.fixture.index.model_dump()) == manifests[0].stem else manifests[1].stem)
        public = json.loads((self.work / "research_questions.json").read_text(encoding="utf-8"))
        self.assertNotIn("search_receipts", public["questions"][0])
        self.assertGreater(public["questions"][0]["search_count"], 0)


class BudgetRefundTests(unittest.TestCase):
    def test_unfinished_calls_of_a_stopped_worker_are_refunded_once(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / "runs/run_x"
            write_json(work / "budget.json", {"model_calls": 3, "search_rounds": 1, "sequence": 3})
            write_json(work / "calls/call_001/response.json", {"ok": True})
            write_json(work / "calls/call_002/output_schema.json", {"title": "X"})
            write_json(work / "calls/call_002/failure.json", {"code": "invalid_model_output"})
            write_json(work / "calls/call_003/activity.json", {"status": "running"})
            write_json(work / "calls/call_003/provider_choice.json", {"provider": "codex_cli", "search": True})
            self.assertEqual(reconcile_budget(work), [3])
            budget = json.loads((work / "budget.json").read_text(encoding="utf-8"))
            self.assertEqual((budget["model_calls"], budget["search_rounds"], budget["refunded"]), (2, 0, [3]))
            self.assertEqual(reconcile_budget(work), [])
            # The next reservation never reuses a refunded directory number.
            self.assertEqual(reserve_call(work, ResearchLimits(model_calls=10)), 4)
            self.assertEqual(json.loads((work / "budget.json").read_text(encoding="utf-8"))["model_calls"], 3)


def text_pdf(text, title=None):
    """A one-page PDF whose text layer holds ``text``, built like the fixture in test_research."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 12 Tf 30 700 Td (" + text + ") Tj ET").encode())
    page[NameObject("/Contents")] = stream
    if title:
        writer.add_metadata({"/Title": title})
    return writer


PAGE_TEXT = ("Models assign an energy to each configuration and lower energy means compatibility in this synthetic "
             "test page. Learning and inference are distinct operations. These sentences are test material only, "
             "long enough to count as a readable page of text for the importer.")


class SourceIsolationTests(unittest.TestCase):
    def test_pdf_parsing_that_exceeds_its_wall_clock_is_one_unreadable_source(self):
        with patch("podcast_automate.sources.subprocess.run", side_effect=subprocess.TimeoutExpired("pdf", 1)):
            with self.assertRaises(AppError) as caught:
                extract(b"%PDF-1.4 crafted", "application/pdf", "paper.pdf")
        self.assertEqual(caught.exception.code, "source_unreadable")
        self.assertIn("Sekunden", str(caught.exception))
        with patch("podcast_automate.sources.subprocess.run", return_value=Mock(returncode=2, stdout=b'{"error":"X"}')):
            with self.assertRaises(AppError):
                extract(b"%PDF-1.4 crafted", "application/pdf", "paper.pdf")

    def test_pdf_failures_name_their_cause(self):
        """A dropped PDF says which reader limit or parser error dropped it; a raw class name is the floor."""
        cases = [(b'{"error":"UnreadablePdf","reason":"encrypted"}', "source_unreadable", "verschl", {"pdf_reason": "encrypted"}),
                 (b'{"error":"UnreadablePdf","reason":"too_many_pages"}', "source_too_large", "300 Seiten", {"pdf_reason": "too_many_pages"}),
                 (b'{"error":"UnreadablePdf","reason":"text_too_large"}', "source_too_large", "1 MiB", {"pdf_reason": "text_too_large"}),
                 (b'{"error":"PdfReadError"}', "source_unreadable", "(PdfReadError)", {"parser_error": "PdfReadError"}),
                 (b'{"error":"not a class name; <script>"}', "source_unreadable", "eingelesen werden.", {}),
                 (b"garbage", "source_unreadable", "eingelesen werden.", {})]
        for stdout, code, phrase, details in cases:
            with self.subTest(stdout=stdout),                     patch("podcast_automate.sources.subprocess.run", return_value=Mock(returncode=2, stdout=stdout)):
                with self.assertRaises(AppError) as caught:
                    extract(b"%PDF-1.4 crafted", "application/pdf", "paper.pdf")
            self.assertEqual(caught.exception.code, code)
            self.assertIn(phrase, str(caught.exception))
            self.assertEqual(caught.exception.details, details)
            self.assertNotIn("<script>", str(caught.exception))

    def test_image_only_pdf_names_its_missing_text_layer(self):
        """A scan reports its page count and how many pages carry text, instead of guessing at a login wall."""
        verdict = {"metadata": {"extraction_coverage": {"pages_total": 3, "pages_with_text": 0, "empty_pages": [1, 2, 3]}},
                   "blocks": [["", 1], ["", 2], ["", 3]]}
        with patch("podcast_automate.sources.subprocess.run",
                   return_value=Mock(returncode=0, stdout=json.dumps(verdict).encode("utf-8"))):
            with self.assertRaises(AppError) as caught:
                extract(b"%PDF-1.4 scanned", "application/pdf", "scan.pdf")
        self.assertEqual(caught.exception.code, "source_unreadable")
        self.assertIn("0 von 3 Seiten", str(caught.exception))
        self.assertIn("OCR", str(caught.exception))
        self.assertEqual(caught.exception.details, {"pages_total": 3, "pages_with_text": 0})
        # Too little text without page coverage keeps the generic wording.
        with patch("podcast_automate.sources.subprocess.run",
                   return_value=Mock(returncode=0, stdout=json.dumps({"metadata": {}, "blocks": [["", 1]]}).encode("utf-8"))):
            with self.assertRaises(AppError) as caught:
                extract(b"%PDF-1.4 empty", "application/pdf", "empty.pdf")
        self.assertEqual(caught.exception.details, {})

    def test_pdf_reader_reports_its_limits_by_token(self):
        """The real child process names an encrypted file, so the parent's wording is not a guess."""
        from pypdf import PdfWriter
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt("owner", "user")
        buffer = io.BytesIO()
        writer.write(buffer)
        with self.assertRaises(AppError) as caught:
            extract(buffer.getvalue(), "application/pdf", "locked.pdf")
        self.assertEqual(caught.exception.code, "source_unreadable")
        self.assertEqual(caught.exception.details, {"pdf_reason": "encrypted"})

    def test_http_failures_report_their_status_code(self):
        with patch("podcast_automate.sources.public_url", side_effect=lambda url: url),                 patch("podcast_automate.sources.urllib.request.build_opener") as build:
            build.return_value.open.side_effect = urllib.error.HTTPError("https://example.org/p.pdf", 403, "Forbidden", {}, io.BytesIO(b"secret body"))
            with self.assertRaises(AppError) as caught:
                download("https://example.org/p.pdf")
            self.assertEqual((caught.exception.code, caught.exception.details), ("source_download_failed", {"http_status": 403}))
            self.assertIn("HTTP 403", str(caught.exception))
            self.assertNotIn("secret body", str(caught.exception))
            build.return_value.open.side_effect = urllib.error.URLError("unreachable")
            with self.assertRaises(AppError) as caught:
                download("https://example.org/p.pdf")
            self.assertIn("(URLError)", str(caught.exception))

    def test_failure_rows_carry_a_selectable_code(self):
        row = import_failure("https://example.org/a.pdf", AppError("PDF ist verschlüsselt.", code="source_unreadable"))
        self.assertEqual(row, {"source": "https://example.org/a.pdf", "reason": "PDF ist verschlüsselt.", "code": "source_unreadable"})
        self.assertEqual(import_failure("x", OSError("disk"))["code"], "import_failed")

    def test_child_output_survives_a_narrow_console_encoding(self):
        """Ligatures or symbols in a parsed PDF used to crash the child on a cp1252 pipe (Windows) after a
        successful parse, and the parent saw only a generic failure."""
        writer = text_pdf(PAGE_TEXT, title="Résumé ∑ ﬁ")
        buffer = io.BytesIO()
        writer.write(buffer)
        with patch.dict(os.environ, {"PYTHONIOENCODING": "ascii"}):
            kind, _, metadata, sections = extract(buffer.getvalue(), "application/pdf", "ligatures.pdf")
        self.assertEqual((kind, metadata["title"]), ("pdf", "Résumé ∑ ﬁ"))
        self.assertIn("Models assign an energy", sections[0].text)
        self.assertEqual(metadata["extraction_coverage"]["pages_with_text"], 1)

    def test_owner_locked_pdf_opens_with_the_empty_user_password(self):
        """Copy and print restrictions alone do not make a public document unreadable."""
        writer = text_pdf(PAGE_TEXT)
        writer.encrypt(user_password="", owner_password="owner")
        buffer = io.BytesIO()
        writer.write(buffer)
        kind, _, _, sections = extract(buffer.getvalue(), "application/pdf", "restricted.pdf")
        self.assertEqual(kind, "pdf")
        self.assertIn("Models assign an energy", sections[0].text)

    def test_name_resolution_cannot_hang_the_worker(self):
        def slow(*args, **kwargs):
            time.sleep(0.5)
            return []
        with patch("podcast_automate.sources.DNS_TIMEOUT_SECONDS", 0.05), \
                patch("podcast_automate.sources.socket.getaddrinfo", side_effect=slow):
            with self.assertRaises(AppError) as caught:
                public_url("https://example.org/paper")
        self.assertEqual(caught.exception.code, "source_download_failed")
        self.assertIn("DNS-Zeitlimit", str(caught.exception))


class StallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        script = self.root / "server.py"
        script.write_text(SERVER, encoding="utf-8")
        self.adapter = CodexAdapter(RuntimeSettings(codex_model="gpt-6-astra", text_timeout_seconds=8), reasoning_effort="xhigh")
        mock = patch.object(self.adapter, "command", return_value=[sys.executable, str(script)])
        mock.start()
        self.addCleanup(mock.stop)

    def test_silent_stream_is_stopped_as_a_stall_well_before_the_deadline(self):
        with patch("podcast_automate.codex.STALL_TIMEOUT_SECONDS", 1), patch.dict(os.environ, {"PLA_STREAM_TEST": "hang"}):
            started = time.monotonic()
            with self.assertRaises(AppError) as caught:
                self.adapter.structured("Synthetic test only", Result, self.root / "call", prompt_version="test")
        self.assertEqual(caught.exception.code, "stall")
        self.assertLess(time.monotonic() - started, 6)
        failure = json.loads((self.root / "call/failure.json").read_text(encoding="utf-8"))
        self.assertEqual((failure["code"], failure["stall_timeout_seconds"]), ("stall", 1))
        self.assertFalse((self.root / "call/response.json").exists())

    def test_pool_repeats_a_stalled_call_once_then_reports_it(self):
        pool = AdapterPool(RuntimeSettings(codex_model="gpt-6-astra"),
                           {"provider": "codex_cli", "model": "gpt-6-astra", "reasoning_effort": None})
        outcomes = [AppError("hung", code="stall"),
                    (TextProbeOutput(topic="t", focus_questions=["q"], note="n"), {"provider": "codex_cli"})]

        def fake(prompt, output_type, directory, **kwargs):
            value = outcomes.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        directory = self.root / "pooled"
        with patch("podcast_automate.codex.CodexAdapter.structured", side_effect=fake):
            output, _ = pool.structured("Synthetic", TextProbeOutput, directory, prompt_version="test")
        self.assertEqual(output.topic, "t")
        self.assertEqual(json.loads((directory / "stall_retry.json").read_text(encoding="utf-8"))["provider"], "codex_cli")
        with patch("podcast_automate.codex.CodexAdapter.structured", side_effect=AppError("hung", code="stall")):
            with self.assertRaises(AppError) as caught:
                pool.structured("Synthetic", TextProbeOutput, self.root / "twice", prompt_version="test")
        self.assertEqual(caught.exception.code, "stall")


class PromptSizeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_oversized_prompt_never_starts_claude_and_auto_mode_routes_around_it(self):
        adapter = ClaudeCodeAdapter(RuntimeSettings(), model="claude-opus-5", reasoning_effort="high")
        with patch("podcast_automate.claude_code.PROMPT_LIMIT_CHARS", 10), \
                patch.object(ClaudeCodeAdapter, "check_login", side_effect=AssertionError("must not start")):
            with self.assertRaises(AppError) as caught:
                adapter.structured("x" * 11, TextProbeOutput, self.root / "call", prompt_version="test")
        self.assertEqual((caught.exception.code, caught.exception.status), ("prompt_too_large", "blocked"))
        fakes = QuotaFakes(self, codex=False)
        pool = AdapterPool(RuntimeSettings(), {"provider": "auto", "prefer": "codex_cli", "candidates": auto_candidates()})
        with patch("podcast_automate.provider_pool.PROMPT_LIMIT_CHARS", 10), \
                patch("podcast_automate.claude_code.ClaudeCodeAdapter.structured", side_effect=AssertionError("excluded")):
            with self.assertRaises(AppError) as paused:
                pool.structured("x" * 11, TextProbeOutput, self.root / "auto", prompt_version="test")
            self.assertEqual(paused.exception.status, "waiting_for_quota")
            self.assertEqual(json.loads((self.root / "auto/prompt_size.json").read_text(encoding="utf-8"))["excluded"], ["claude_code"])
            fakes.codex = True
            with patch("podcast_automate.codex.CodexAdapter.structured",
                       return_value=(TextProbeOutput(topic="t", focus_questions=["q"], note="n"), {"provider": "codex_cli"})):
                output, _ = pool.structured("x" * 11, TextProbeOutput, self.root / "auto", prompt_version="test")
        self.assertEqual(output.topic, "t")
        self.assertEqual(json.loads((self.root / "auto/provider_choice.json").read_text(encoding="utf-8"))["provider"], "codex_cli")


class StudioApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.root = self.workspace / "projects" / "example"
        init_project(self.root, TopicBrief(topic="A test project", voice_profile={"host_a": "Aiden", "host_b": "Vivian"}))
        self.server = make_server(self.workspace, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.app = self.server.studio
        self.work = self.root / "runs/run_x"
        write_yaml(self.work / "run_manifest.yaml", RunManifest(run_id="run_x", kind="research", project_hash="0" * 64,
                                                                input_hash="b" * 64, stages={}).model_dump(mode="json"))
        save_value(self.work / "question_research/state.json",
                   {"tasks": {"task_a": {"status": "blocked"}, "task_b": {"status": "pending"}}})

    def request(self, path, data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        base = {"X-Studio-Token": self.app.token, "Content-Type": "application/json"}
        base.update(headers or {})
        connection.request("GET" if data is None else "POST", path,
                           None if data is None else json.dumps(data), headers=base)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_approvals_are_explicit_actions_bound_to_the_run(self):
        status, _ = self.request("/api/projects/example/approve",
                                 {"kind": "gap", "run_id": "run_x", "task_id": "task_a", "reason": "ok"})
        self.assertEqual(status, 200)
        self.assertEqual(list(accepted_gaps(self.work, "b" * 64)), ["task_a"])
        self.assertEqual(self.request("/api/projects/example/approve",
                                      {"kind": "gap", "run_id": "run_x", "task_id": "task_b"})[0], 400)
        status, _ = self.request("/api/projects/example/approve",
                                 {"kind": "model_calls", "run_id": "run_x", "model_calls": 200, "search_rounds": 20})
        self.assertEqual(status, 200)
        limits = effective_limits(self.work, TopicBrief(topic="x").research_limits, "b" * 64)
        self.assertEqual((limits.model_calls, limits.search_rounds), (200, 20))
        self.assertEqual(self.request("/api/projects/example/approve",
                                      {"kind": "model_calls", "run_id": "run_x", "model_calls": 100})[0], 400)
        self.assertEqual(self.request("/api/projects/example/approve",
                                      {"kind": "gap", "run_id": "run_x", "task_id": "task_a"},
                                      {"X-Studio-Token": "wrong"})[0], 403)

    def test_paused_job_is_resumed_automatically_after_its_named_reset(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        job = {"id": "j1", "action": "research", "status": "waiting_for_quota", "started_at": now(), "retry_at": past,
               "run": {"run_id": "run_x", "kind": "research", "status": "waiting_for_quota", "stages": {}}}
        write_json(self.root / "studio/job.json", job)
        self.assertEqual(self.app.due_resumes(), [("example", "run_x", 0)])
        self.assertEqual(self.app.job(self.root)["auto_resume_at"], past)
        write_json(self.root / "studio/job.json", {**job, "retry_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
        self.assertEqual(self.app.due_resumes(), [])
        write_json(self.root / "studio/job.json", {**job, "auto_resume_count": 3})
        self.assertEqual(self.app.due_resumes(), [])
        self.assertNotIn("auto_resume_at", self.app.job(self.root))
        write_json(self.root / "studio/job.json", job)
        captured = []

        class Pipe(io.StringIO):
            def close(self):
                captured.append(self.getvalue())
                super().close()
        process = Mock(stdin=Pipe())
        process.poll.return_value = None
        with patch("podcast_automate.studio.subprocess.Popen", return_value=process):
            self.assertEqual(self.app.resume_due(), ["example"])
        payload = json.loads(captured[0])
        self.assertEqual((payload["action"], payload["run_id"]), ("resume", "run_x"))
        # The count lives in the job record the worker carries through, so a paused chain stays bounded.
        resumed = read_json(self.root / "studio/job.json")
        self.assertEqual((resumed["status"], resumed["auto_resume_count"]), ("running", 1))


if __name__ == "__main__":
    unittest.main()
