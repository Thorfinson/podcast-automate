"""The per-question loop of question_answering: reading progress, offered actions, prerequisite digests, review memory
and the settling of a review's final attempt (2026-10-02)."""
import threading
import unittest

from podcast_automate.question_answering import (TaskResearchMixin, VIEW_CHARS, carry_earlier, read_context, review_memory,
                                                review_passes, review_scope)
from podcast_automate.question_dependencies import revalidate
from podcast_automate.research_evidence import support_errors
from podcast_automate.research_ledger import read_value
from podcast_automate.research_models import SourceDocument, SourceIndex, SourceSection
from podcast_automate.research_reader import SourceReader
from podcast_automate.research_tasks import AnswerReview, QuestionAnswer, QuestionPlan, ReaderWindow, ResearchDecision
from podcast_automate.storage import digest, write_json
from tests import research_fixtures
from tests import test_question_research as harness
from tests.question_fixtures import complete_fixture_response, decision, task_value

CALL = "question_research.v3-clauses"


def document(source_id, sections):
    return SourceDocument(id=source_id, type="text", title="Long source", authors=[], published_date="", imported_at="now",
                          url="https://example.org/long", final_url="https://example.org/long", reliability_note="Test",
                          uncertainties=[], raw_path="raw.txt", raw_hash="0", text_hash=source_id,
                          sections=[SourceSection(id=name, text=text) for name, text in sections.items()])


class Host(TaskResearchMixin):
    def __init__(self, reader):
        self.reader = reader


class ReadingTests(unittest.TestCase):
    def test_reading_a_passage_pushed_out_of_view_again_is_progress(self):
        # 2026-10-02: the prompt told the reader that passages beyond VIEW_CHARS "can be read again", but the re-read
        # was measured against every passage ever read, counted as no progress and blocked a locked task.
        size = VIEW_CHARS // 3
        reader = SourceReader(SourceIndex(sources=[document("src_long", {n: n * size for n in "abcd"})], failures=[]))
        row = {"current_refs": ["src_long#d"], "read_refs": [f"src_long#{n}" for n in "abcd"]}
        host = Host(reader)
        self.assertEqual(host.read(dict(row), [ReaderWindow(reference="src_long#c", before=0, after=0)]), 0,
                         "a passage still in view is not new")
        self.assertEqual(host.read(dict(row), [ReaderWindow(reference="src_long#a", before=0, after=0)]), 1,
                         "a passage pushed out of view is new to the reader")

    def test_every_requested_passage_is_read_however_long(self):
        # 2026-10-02: read_context dropped what lay beyond 2,000,000 characters without a word.
        sections = {"a": "word " * 220_000, "b": "more " * 220_000}
        reader = SourceReader(SourceIndex(sources=[document("src_long", sections)], failures=[]))
        context = read_context(reader, ["src_long#a", "src_long#b"])
        self.assertEqual([s["reference"] for s in context[0]["sections"]], ["src_long#a", "src_long#b"])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = harness.QuestionResearchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ref = self.fixture.ref

    def completed(self):
        engine = self.fixture.engine()
        engine.run(self.fixture.discovery, self.fixture.index)
        spec = QuestionPlan.model_validate(engine.state["plan"]).tasks[0]
        return engine, spec, engine.state["tasks"][spec.id]

    def review(self, payload, *, passed=True, verdict="supported"):
        review = complete_fixture_response(AnswerReview(criteria=[dict(index=0, passed=passed, reason="Fixture verdict.")],
                                                        supported=True, source_adequacy=True, issues=[]), payload)
        if verdict != "supported":
            review.finding_support[0].verdict = verdict
            review.finding_support[0].unsupported_clauses = ["Configurations are assigned energies."]
        return review

    def test_web_search_is_offered_only_while_it_could_still_load_a_source(self):
        # 2026-10-02: the reader was offered search_web after its attempts, the run's rounds or its sources ran out.
        engine, _, row = self.completed()
        row = dict(row, answer_locked=False, revise_only=False, web_attempts=0)
        self.assertIn("search_web", engine.allowed_actions(row))
        self.assertNotIn("search_web", engine.allowed_actions(dict(row, web_attempts=engine.web_attempt_limit(row))))
        limits = engine.limits()
        write_json(self.fixture.work / "budget.json", {"search_rounds": limits.search_rounds})
        self.assertNotIn("search_web", engine.allowed_actions(row))
        (self.fixture.work / "budget.json").unlink()
        engine.attempts = {f"https://example.org/{n}" for n in range(limits.sources)}
        self.assertEqual(engine.allowed_actions(row), ["search_local", "read", "answer", "blocked"])

    def test_a_revalidated_answer_may_come_back_unchanged_and_is_reviewed_again(self):
        engine, spec, row = self.completed()
        answer = QuestionAnswer.model_validate(row["answer"])
        revalidate(row)
        self.assertEqual(engine.answer_defects(spec, row, answer), [])
        self.assertTrue(engine.answer_defects(spec, dict(row, resubmit=None), answer),
                        "an ordinary failed draft still may not come back unchanged")
        reviews = []
        self.fixture.hook = lambda prompt, schema, payload, kwargs: reviews.append(1) if schema is AnswerReview else None
        engine.research_task(spec)
        self.assertEqual((row["status"], len(reviews)), ("verified", 1))
        self.assertEqual(row["answer"], answer.model_dump())
        self.assertNotIn("resubmit", row, "the pass ends the permission, so a later reopening cannot reuse it")

    def test_prerequisites_reach_reader_and_review_as_digests_and_stay_bound_whole(self):
        # Ontologies, 2026-10-02: 238,614 characters of whole prerequisite answers in every step of one synthesis.
        captured = {}

        def hook(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[{**task_value("task_synthesis", "synthesis"), "depends_on": ["task_definition"]},
                                           task_value()])
            if schema in (ResearchDecision, AnswerReview):
                captured.setdefault((schema, payload["task"]["id"]), (prompt, payload))
        self.fixture.hook = hook
        engine = self.fixture.engine()
        engine.run(self.fixture.discovery, self.fixture.index)
        definition = engine.state["tasks"]["task_definition"]["answer"]
        for schema in (ResearchDecision, AnswerReview):
            with self.subTest(schema=schema.__name__):
                prompt, payload = captured[(schema, "task_synthesis")]
                self.assertEqual(payload["prerequisite_answers"], [{
                    "task_id": "task_definition", "question": "What is energy?", "summary": definition["summary"],
                    "outcome": "supported_answer", "limits": [],
                    "findings": [{"id": "f_energy", "statement": "Configurations are assigned energies.",
                                  "relation": "definition", "references": [self.ref]}]}])
                self.assertIn("prerequisite_answers gives each verified prerequisite answer", prompt)
                unrelated, _ = captured[(schema, "task_definition")]
                self.assertNotIn("prerequisite_answers gives", unrelated, "a task without prerequisites keeps its prompt")
        versions = {version for _, version in self.fixture.calls}
        self.assertIn(f"{CALL}.view.absence.digest.reader", versions)
        self.assertIn(f"{CALL}.view.absence.reader", versions)
        self.assertTrue(any(v.startswith(f"{CALL}.digest.review_") for v in versions))
        self.assertEqual(engine.state["tasks"]["task_synthesis"]["verification"]["prerequisite_hashes"],
                         {"task_definition": digest(definition)})

    def failed_then_reworked(self, second):
        """A failed review of criterion 0, then the review of a reworked answer whose finding stayed as it was."""
        engine, spec, row = self.completed()
        prompts, saved_scope = [], []
        self.fixture.hook = lambda prompt, schema, payload, kwargs: (
            prompts.append((prompt, payload)) or self.review(payload, passed=False)) if schema is AnswerReview else None
        row.update(status="reviewing", step=row["step"] + 1)
        engine.verify(spec, row)
        self.assertEqual((row["status"], row["review_memory"]["failed_criteria"][0]["index"]), ("researching", 0))
        reworked = QuestionAnswer.model_validate(row["draft_answer"])
        reworked.summary += " Restricted to the configurations the source names."
        row.update(status="reviewing", answer=reworked.model_dump(), answer_locked=False, step=row["step"] + 1)

        def again(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                saved_scope.append(read_value(engine.folder / "state.json")["tasks"][spec.id].get("review_scope"))
                prompts.append((prompt, payload))
                return second(payload)
        self.fixture.hook = again
        engine.verify(spec, row)
        return engine, spec, row, prompts, saved_scope

    def test_the_review_of_a_reworked_answer_remembers_its_verdict_and_keeps_to_its_scope(self):
        # The design rule for repeated reviews: memory, a scope set by code and saved before the call, and only
        # unresolved earlier points or new defects in changed material may block.
        engine, spec, row, prompts, saved_scope = self.failed_then_reworked(
            lambda payload: self.review(payload, verdict="contradicted"))
        prompt, payload = prompts[-1]
        self.assertIn("previous_review is your earlier verdict", prompt)
        self.assertNotIn("previous_review", prompts[0][1], "a first review has no memory")
        self.assertEqual(payload["previous_review"]["failed_criteria"], [{"index": 0, "reason": "Fixture verdict."}])
        self.assertEqual((payload["previous_review"]["changed_finding_ids"], payload["previous_review"]["may_fail"]),
                         ([], {"finding_ids": [], "criteria": [0], "source_adequacy": False}))
        self.assertEqual(saved_scope[0]["may_fail"], payload["previous_review"]["may_fail"], "saved before the call")
        self.assertTrue(any(version.startswith(f"{CALL}.memory.review_") for _, version in self.fixture.calls))
        # The unchanged finding passed before: the new objection is an advisory, its earlier receipt stands.
        self.assertEqual(row["status"], "verified")
        verification = row["verification"]
        self.assertEqual(verification["review"]["finding_support"][0]["verdict"], "supported")
        self.assertIn("advisory", [item["kind"] for item in verification["limitations"]])
        self.assertNotIn("review_memory", row)
        # What a resume checks of a stored verification still holds.
        answer = QuestionAnswer.model_validate(row["answer"])
        verdict = AnswerReview.model_validate(verification["review"])
        self.assertTrue(review_passes(verdict, spec))
        self.assertEqual(support_errors(answer.findings, verdict, read_context(engine.reader, [self.ref])), [])

    def test_a_kept_receipt_names_only_the_passages_given_to_this_review(self):
        """2026-10-02 review: an earlier passing receipt may have assessed another finding's passage, which the rework
        replaced. Put back unchanged, support_errors refused it after the review was stored, on every resume."""
        engine, spec, row = self.completed()
        answer = QuestionAnswer.model_validate(row["answer"])
        earlier = AnswerReview.model_validate(row["verification"]["review"])
        earlier.finding_support[0].references = [self.ref, "src_replaced#s1"]
        verdict = AnswerReview.model_validate(row["verification"]["review"])
        verdict.finding_support[0].verdict = "contradicted"
        verdict.finding_support[0].unsupported_clauses = ["Configurations are assigned energies."]
        passages = read_context(engine.reader, [self.ref])
        scope = {"may_fail": {"finding_ids": [], "criteria": [], "source_adequacy": False}}
        advisories = carry_earlier(verdict, {"review": earlier.model_dump()}, scope, passages)
        self.assertEqual((verdict.finding_support[0].verdict, verdict.finding_support[0].references), ("supported", [self.ref]))
        self.assertEqual(len(advisories), 1)
        self.assertEqual(support_errors(answer.findings, verdict, passages), [])

    def test_a_criterion_that_lost_or_moved_a_finding_is_judged_afresh(self):
        """2026-10-02 review: review_scope saw only the current findings. A criterion whose second finding the rework
        removed kept its earlier pass, and the answer was stored as verified on the remaining finding alone."""
        _, _, row = self.completed()
        answer = QuestionAnswer.model_validate(row["answer"])
        hashes = {f.id: digest(f.model_dump()) for f in answer.findings}
        resting = list(answer.criteria[0].finding_ids)
        memory = {"step": 1, "finding_hashes": {**hashes, "f_extra": "0" * 64}, "failed_criteria": [],
                  "blocking_findings": [], "source_adequacy": True, "criterion_findings": {"0": [*resting, "f_extra"]}}
        scope = review_scope(memory, answer)
        self.assertEqual((scope["changed_finding_ids"], scope["may_fail"]["criteria"], scope["may_fail"]["source_adequacy"]),
                         ([], [0], True))
        # A memory from before the criterion sets were kept cannot tell which criterion lost the finding: all may fail.
        legacy = {key: value for key, value in memory.items() if key != "criterion_findings"}
        self.assertEqual(review_scope(legacy, answer)["may_fail"]["criteria"], [0])
        # Nothing removed or regrouped: the earlier pass stands as before.
        same = {**memory, "finding_hashes": hashes, "criterion_findings": {"0": resting}}
        self.assertEqual(review_scope(same, answer)["may_fail"], {"finding_ids": [], "criteria": [], "source_adequacy": False})
        verdict = AnswerReview.model_validate(row["verification"]["review"])
        self.assertEqual(review_memory(1, answer, verdict, [])["criterion_findings"], {"0": resting})

    def test_a_search_round_taken_meanwhile_is_this_questions_budget_block(self):
        """2026-10-02 review: web_search checked for a free round under the ledger lock and reserved it in the call,
        after the lock. A question beside it that took the last round stopped the whole run for an approval."""
        from unittest.mock import patch
        from podcast_automate.errors import AppError
        engine, spec, row = self.completed()
        write_json(self.fixture.work / "budget.json", {"model_calls": 1, "search_rounds": 0})
        row = dict(row, web_attempts=0, reason="", outcome=None)
        taken = AppError("Limit von 4 Rechercherunden erreicht.", code="research_budget_exhausted", status="blocked")
        with patch.object(engine, "call", side_effect=taken) as called:
            self.assertFalse(engine.web_search(spec, row, ["energy"]))
        self.assertEqual(called.call_count, 1, "the round was free when checked")
        self.assertEqual(row["outcome"], "budget_block")
        self.assertIn("Web-Suchbudget", row["reason"])
        # With the model calls spent as well, the run stops for an approval as before.
        with patch.object(engine, "call", side_effect=taken), patch.object(engine, "calls_left", return_value=False), \
                self.assertRaises(AppError):
            engine.web_search(spec, dict(row, outcome=None), ["energy measured"])

    def test_an_earlier_objection_that_is_still_unresolved_still_blocks(self):
        _, _, row, _, _ = self.failed_then_reworked(lambda payload: self.review(payload, passed=False))
        self.assertEqual((row["status"], row["answer_locked"]), ("researching", True))
        self.assertIn("Criterion 0 not met", row["lock"]["reasons"][0])

    def test_a_review_that_never_judges_a_finding_is_repeated_once_instead_of_locking_the_answer(self):
        # A finding the review left unjudged on its final attempt gets a conservative receipt; that says nothing
        # about the answer, so the same answer is reviewed again at the next step.
        reviews = []

        def hook(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                reviews.append(prompt)
                review = self.review(payload)
                if len(reviews) <= 3:
                    review.finding_support[0].finding_id = "f_other"
                return review
        self.fixture.hook = hook
        engine, _, row = self.completed()
        self.assertEqual(len(reviews), 4)
        self.assertIn("Evidence review must assess every finding exactly once. Missing: f_energy. Unknown findings: f_other.",
                      reviews[1])
        self.assertEqual(row["status"], "verified")
        self.assertNotIn("review_repeat", row)
        names = sorted(p.name for p in (engine.folder / "tasks/task_definition/attempt_0").glob("review_*.json"))
        self.assertEqual(len([n for n in names if "rejected" not in n]), 2, "the repeat is a review of its own step")

    def test_a_download_leaves_the_ledger_to_the_other_tasks_and_keeps_their_sources(self):
        # 2026-10-02: a web search downloaded and parsed under the ledger lock, and every other worker waited.
        # Here task_a's download waits inside the fetch until task_b's search has set a new index; under the lock
        # task_b could never get there.
        from unittest.mock import patch
        from podcast_automate import question_answering
        from podcast_automate.question_research import QuestionResearch
        from podcast_automate.research_models import SourceCandidate
        from podcast_automate.research_tasks import QuestionSearch
        f = self.fixture
        address = lambda task: f"https://example.org/{task}"
        searched = set()

        def hook(prompt, schema, payload, kwargs):
            task = payload.get("task", {}).get("id")
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value("task_a"), task_value("task_b")])
            if schema is ResearchDecision and task not in searched:
                searched.add(task)
                return decision("search_web", web_queries=[f"energy {task}"])
            if schema is QuestionSearch:
                return QuestionSearch(candidates=[SourceCandidate(url=address(task), title=f"Paper {task}", authors=[],
                    published_date="", rationale="Fixture", primary_source=True)], limitations=[])
        f.hook = hook
        f.download.side_effect = lambda url: (
            research_fixtures.HTML.replace(b"These sentences", f"Text of {url}. These sentences".encode()), "text/html", url)
        inside, changed, waited = threading.Event(), threading.Event(), []
        real = question_answering.import_source

        def fetch(candidate, *args, **kwargs):
            if candidate.url == address("task_a"):
                inside.set()
                waited.append(changed.wait(timeout=10))
            else:
                inside.wait(timeout=10)
            return real(candidate, *args, **kwargs)
        engine = QuestionResearch(f.root, f.work, f.config, f.model, lambda activity: None, workers=2)
        set_index = engine.set_index

        def tracked(index):
            set_index(index)
            if any(s.url == address("task_b") for s in index.sources):
                changed.set()
        engine.set_index = tracked
        with patch("podcast_automate.question_answering.import_source", side_effect=fetch):
            engine.run(f.discovery, f.index)
        self.assertEqual(waited, [True], "task_b's search finished while task_a's download ran")
        urls = {s.url for s in engine.index.sources}
        self.assertTrue({address("task_a"), address("task_b")} <= urls, "neither search lost the other's source")
        # Each receipt still restores on the ledger's final index, whatever base it was written against.
        for task in ("task_a", "task_b"):
            receipt = read_value(next((engine.folder / "tasks" / task).rglob("downloads.json")))
            self.assertIn(address(task), {s.url for s in engine.receipt_index(receipt).sources})
            self.assertEqual(len(engine.receipt_index(receipt).sources), len(engine.index.sources))


if __name__ == "__main__":
    unittest.main()
