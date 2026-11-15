"""The per-question loop of question_answering: reading progress, offered actions, prerequisite digests, review memory
and the settling of a review's final attempt (2026-10-02)."""
import json
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
from tests.question_fixtures import answer_for, complete_fixture_response, decision, task_value

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
        self.assertIn(f"{CALL}.view.absence.digest.story.reader", versions)
        self.assertIn(f"{CALL}.view.absence.story.reader", versions)
        self.assertTrue(any(v.startswith(f"{CALL}.digest.story.review_") for v in versions))
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
        self.assertTrue(any(version.startswith(f"{CALL}.memory.story.review_") for _, version in self.fixture.calls))
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
        self.assertEqual((row["outcome"], row["block_cause"]), ("budget_block", "search_budget"))
        self.assertIn("Web-Suchbudget", row["reason"])
        # With the model calls spent as well, the run stops for an approval as before.
        with patch.object(engine, "call", side_effect=taken), patch.object(engine, "calls_left", return_value=False), \
                self.assertRaises(AppError):
            engine.web_search(spec, dict(row, outcome=None), ["energy measured"])

    def test_a_run_limit_that_ends_the_web_search_is_named_beside_its_reason(self):
        """D-152: the Studio offered to raise the search rounds by matching /Suchbudget|Suchrunden/ in the German
        reason. ``block_cause`` names the run limit instead; the reason stays as the advisor reads it."""
        from podcast_automate.question_answering import SEARCH_BUDGET_REASON
        from podcast_automate.research_ledger import public_ledger
        engine, spec, row = self.completed()
        limits = engine.limits()

        def public():
            return next(r for r in public_ledger(engine.state, engine.index)["questions"] if r["id"] == spec.id)
        self.assertNotIn("block_cause", public(), "a row no run limit stopped keeps its earlier form")
        row.update(status="blocked", web_attempts=0, reason="", outcome=None)
        write_json(self.fixture.work / "budget.json", {"model_calls": 1, "search_rounds": limits.search_rounds})
        self.assertFalse(engine.web_search(spec, row, ["energy"]))
        self.assertEqual((row["outcome"], row["reason"], row["block_cause"]),
                         ("budget_block", SEARCH_BUDGET_REASON, "search_budget"))
        engine.save()
        written = json.loads((self.fixture.work / "research_questions.json").read_text(encoding="utf-8"))
        self.assertEqual(next(r for r in written["questions"] if r["id"] == spec.id)["block_cause"], "search_budget")
        self.assertEqual(read_value(engine.folder / "state.json")["tasks"][spec.id]["block_cause"], "search_budget")

        write_json(self.fixture.work / "budget.json", {"model_calls": 1, "search_rounds": 0})
        engine.attempts = {f"https://example.org/{n}" for n in range(limits.sources)}
        self.assertFalse(engine.web_search(spec, row, ["energy"]))
        self.assertIn(f"Das Quellenlimit des Laufs ist erreicht ({limits.sources} Quellen)", row["reason"])
        self.assertEqual((row["block_cause"], public()["block_cause"]), ("source_limit", "source_limit"))

        # Only beside a budget block: the reader's own block keeps the sentence in its reason, the Studio no button.
        row["outcome"] = "evidence_block"
        self.assertNotIn("block_cause", public())
        row["outcome"] = "budget_block"
        # A reason built on the earlier one keeps the cause; one started afresh (after a reopening) drops it.
        engine.lock_block(spec, row)
        self.assertEqual((row["outcome"], row["block_cause"]), ("budget_block", "source_limit"))
        fresh = dict(row, reason="")
        engine.lock_block(spec, fresh)
        self.assertNotIn("block_cause", fresh)
        # A limit of this sub-question alone is no run limit.
        engine.attempts = set()
        row.update(web_attempts=engine.web_attempt_limit(row))
        self.assertFalse(engine.web_search(spec, row, ["energy"]))
        self.assertEqual(row["outcome"], "budget_block")
        self.assertNotIn("block_cause", row)
        self.assertNotIn("block_cause", public())

        # A row written before block_cause existed loads and dumps as before; the Studio reads its reason.
        row.update(reason=SEARCH_BUDGET_REASON, outcome="budget_block")
        engine.save()
        self.assertNotIn("block_cause", read_value(engine.folder / "state.json")["tasks"][spec.id])
        written = json.loads((self.fixture.work / "research_questions.json").read_text(encoding="utf-8"))
        self.assertNotIn("block_cause", next(r for r in written["questions"] if r["id"] == spec.id))

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

    def test_after_a_reopening_only_a_web_search_of_this_pass_ends_a_locked_answer_at_once(self):
        """Review 2026-11-14: the immediate block of a locked answer asks whether the web was searched in this working
        pass, but counted every search the question ever made. A question retried or reworked after a first pass that had
        searched was blocked after one step without anything new, and its reason named a web search that never ran."""
        engine, spec, row = self.completed()
        row.update(status="blocked", outcome="evidence_block", web_attempts=1)
        engine.reopen_task(row, activity="Neuer Versuch", feedback=[])
        decisions = [decision("answer", answer=answer_for(self.ref)),
                     decision("read", windows=[ReaderWindow(reference=self.ref, before=0, after=0)])]
        readers = []

        def hook(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                readers.append(payload["allowed_actions"])
                return decisions[len(readers) - 1] if len(readers) <= len(decisions) else decision(
                    "search_web", web_queries=["energy measured"])
            if schema is AnswerReview:
                return self.review(payload, passed=False)
        self.fixture.hook = hook
        engine.research_task(spec)
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(row["web_attempts"], 2, "the block came after a web search of this pass")
        self.assertIn("auch die Websuche brachte keine neuen Belege", row["reason"])

    def two_criteria(self, passed=(True, True)):
        """A review of a two-criterion answer; ``passed`` per criterion."""
        def review(payload):
            return complete_fixture_response(AnswerReview(criteria=[
                dict(index=i, passed=ok, reason="Fixture verdict.") for i, ok in enumerate(passed)],
                supported=True, source_adequacy=True, issues=[]), payload)
        return review

    def two_findings(self):
        answer = answer_for(self.ref)
        second = {**answer.findings[0].model_dump(), "id": "f_second", "statement": "Energies order the configurations."}
        return QuestionAnswer.model_validate({**answer.model_dump(), "findings": [*answer.model_dump()["findings"], second],
                                              "criteria": [*answer.model_dump()["criteria"], {
                                                  "index": 1, "explanation": "The source orders them.",
                                                  "finding_ids": ["f_second"]}]})

    def reopened_by_audit(self):
        """A completed run of one two-criterion question whose audit objects to the wording of its finding f_energy;
        returns engine, spec and row after the routing."""
        from unittest.mock import patch
        from podcast_automate.evidence_models import ResearchObjection
        from podcast_automate.research_models import ResearchDossier
        from podcast_automate.research_review import SourceReview
        from podcast_automate.research_tasks import ReopenPlan
        passing = self.two_criteria()

        def hook(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[{**task_value(), "acceptance": ["Define energy.", "Order the configurations."]}])
            if schema is ResearchDecision:
                return decision("answer", answer=self.two_findings())
            if schema is AnswerReview:
                return passing(payload)
        self.fixture.hook = hook
        engine, spec, row = self.completed()
        dossier = ResearchDossier.model_validate_json(
            (self.fixture.work / "complete_research/dossier.json").read_text(encoding="utf-8"))
        plan = ReopenPlan(routes=[dict(index=0, task_ids=[spec.id], reason="Wording only.", anchors=[ResearchObjection(
            id="obj_wording", rule="claim_preservation", task_id=spec.id, criterion_index=None,
            finding_ids=[f"{spec.id}__f_energy"],
            evidence_refs=[self.ref], missing_evidence="", reason="The summary widens the source.",
            correction="Restore the source's limit.", closure_condition="The summary keeps the source's limit.",
            resolution="revise")])])

        def routed(folder, name, schema, prompt, *, validate=None, **kwargs):
            validate(plan, True)
            return plan
        with patch.object(engine, "call", side_effect=routed):
            engine.reopen(dossier, SourceReview(issues=[], limitations=[]),
                          {"blocking_gaps": ["The summary widens the source."], "requirements": []})
        return engine, spec, row

    def test_the_review_of_an_answer_reworked_after_an_audit_keeps_what_it_passed_before(self):
        """Review 2026-11-14 (the design rule for repeated reviews): the review of an answer an audit reopened started
        without memory or scope, so a point it had passed in unchanged material could fail the rework, which then ended
        blocked and gave back the answer from before, its correction lost."""
        engine, spec, row = self.reopened_by_audit()
        memory = row["review_memory"]
        self.assertEqual((memory["passed"], memory["scoped_finding_ids"]), (True, ["f_energy"]),
                         "the finding the objection names may still fail")
        reworked = self.two_findings()
        reworked.summary += " Restricted to the configurations the source names."
        prompts = []
        failing_second = self.two_criteria((True, False))

        def hook(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                return decision("answer", answer=reworked)
            if schema is AnswerReview:
                prompts.append((prompt, payload))
                return failing_second(payload)
        self.fixture.hook = hook
        engine.research_task(spec)
        prompt, payload = prompts[0]
        self.assertIn("previous_review is your earlier verdict on this question's answer, which passed", prompt)
        self.assertEqual(payload["previous_review"]["may_fail"],
                         {"finding_ids": ["f_energy"], "criteria": [0], "source_adequacy": True})
        self.assertEqual(row["status"], "verified", "a new objection to criterion 1, unchanged and passed, is an advisory")
        self.assertIn("advisory", [item["kind"] for item in row["verification"]["limitations"]])
        self.assertTrue(any(".memory.kept.story.review_" in version for _, version in self.fixture.calls))
        self.assertNotIn("review_memory", row)

    def test_a_revalidation_may_fail_only_the_findings_built_on_the_changed_prerequisite(self):
        """Review 2026-11-14: a revalidated answer was reviewed afresh, so an unchanged finding unrelated to the changed
        prerequisite could fail and block the run. The scope is the findings that cite the prerequisite's passages."""
        from podcast_automate.question_dependencies import invalidate_dependents
        _, _, row = self.completed()
        answer = answer_for(self.ref).model_dump()
        other = {**answer["findings"][0], "id": "f_other", "evidence": [{"reference": "src_other#s1", "excerpt": "Other"}]}
        answer["findings"].append(other)
        answer["criteria"].append({"index": 1, "explanation": "Another source.", "finding_ids": ["f_other"]})
        dependent = {**copy_row(row), "answer": answer}
        revalidate(dependent, scope_refs={self.ref})
        memory = dependent["review_memory"]
        self.assertEqual((memory["passed"], memory["scoped_finding_ids"]), (True, ["f_energy"]))
        scope = review_scope(memory, QuestionAnswer.model_validate(answer))
        self.assertEqual(scope["may_fail"], {"finding_ids": ["f_energy"], "criteria": [0], "source_adequacy": True})
        # Where no finding cites the prerequisite's passages, code cannot tell what rests on it: the whole answer is judged.
        unrelated = {**copy_row(row), "answer": answer}
        revalidate(unrelated, scope_refs={"src_none#s0"})
        self.assertNotIn("review_memory", unrelated)
        # A recheck under tightened rules judges the whole answer, as before.
        recheck = {**copy_row(row), "answer": answer, "review_memory": {"stale": True}}
        revalidate(recheck)
        self.assertNotIn("review_memory", recheck)
        # The dependents a reworked prerequisite sends back are scoped by that prerequisite's passages.
        state = {"plan": {"tasks": [task_value(), {**task_value("task_synthesis", "synthesis"), "depends_on": ["task_definition"]}]},
                 "tasks": {"task_definition": {**copy_row(row), "status": "researching", "answer": None,
                                               "draft_answer": answer_for(self.ref).model_dump()},
                           "task_synthesis": {**copy_row(row), "answer": answer}}}
        invalidate_dependents(state, ["task_definition"])
        self.assertEqual(state["tasks"]["task_synthesis"]["review_memory"]["scoped_finding_ids"], ["f_energy"])

    def test_a_rejected_review_s_objections_stay_in_view_after_new_evidence_unlocks_the_answer(self):
        """Review 2026-11-14: after a failed review a web search or a recovery read replaced the feedback, and the unlock
        hid the lock, so the reader answered again without knowing why its answer had failed."""
        from podcast_automate.research_tasks import QuestionSearch
        f = self.fixture
        f.download.side_effect = lambda url: (research_fixtures.HTML.replace(
            b"These sentences", b"Additional independent evidence. These sentences"), "text/html", url)
        engine, spec, row = self.completed()
        payloads = []

        class Stop(Exception):
            pass

        def hook(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                return self.review(payload, passed=False)
            if schema is QuestionSearch:
                return QuestionSearch(candidates=research_fixtures.discovery(count=2).candidates[1:], limitations=[])
            if schema is ResearchDecision:
                payloads.append(payload)
                if len(payloads) == 1:
                    return decision("search_web", web_queries=["energy measured"])
                raise Stop
        f.hook = hook
        row.update(status="reviewing", step=row["step"] + 1)
        with self.assertRaises(Stop):
            engine.research_task(spec)
        self.assertTrue(payloads[0]["answer_lock"])
        self.assertIsNone(payloads[1]["answer_lock"], "the new source unlocked the answer")
        self.assertIn("Criterion 0 not met", " ".join(payloads[1]["last_review"]["reasons"]))
        self.assertEqual([c["index"] for c in payloads[1]["last_review"]["criteria"]], [0])

    def test_a_call_limit_reached_during_a_question_leaves_its_outcome_as_it_is(self):
        """Review 2026-11-14: the budget check stamped budget_block on a question still being researched; once the limit
        was raised and the run resumed, a later evidence block of that question was reported as a spent budget."""
        from podcast_automate.errors import AppError
        engine, spec, row = self.completed()
        row.update(status="researching", outcome=None, step=row["step"] + 1, pending=None)
        write_json(self.fixture.work / "budget.json", {"model_calls": engine.limits().model_calls, "search_rounds": 0})
        with self.assertRaises(AppError) as stopped:
            engine.ensure_budget(engine.task_folder(spec, row) / f"step_{row['step']:03d}" / "reader.json")
        self.assertEqual(stopped.exception.code, "research_budget_insufficient")
        self.assertIsNone(row["outcome"])

    def test_a_reader_receipt_never_applied_is_asked_again_when_its_prompt_changed(self):
        """Review 2026-11-14: a reader receipt saved just before a stop, while the ledger still lacked its decision, was
        rebuilt on resume from a ledger other questions or an update had changed, and every resume stopped with
        invalid_research_checkpoint. Nothing is bound to such a receipt; it is set aside and asked again."""
        f = self.fixture
        folder = f.work / "question_research/tasks/task_definition/attempt_0/step_000"
        stale = decision("blocked").model_dump()
        write_json(folder / "reader.json", {"input_hash": "0" * 64, "sha256": digest(stale), "value": stale})
        engine = f.engine()
        engine.run(f.discovery, f.index)
        self.assertEqual(engine.state["tasks"]["task_definition"]["status"], "verified")
        self.assertTrue((folder / "reader_superseded_receipt_00.json").exists())

    def test_a_provided_work_whose_import_fails_on_the_file_is_recorded_not_raised(self):
        """Review 2026-11-14: the import of a provided work caught only AppError, while every other import loop also
        records OSError and ValueError."""
        from unittest.mock import patch
        from podcast_automate import provided_works
        engine, spec, _ = self.completed()
        work = provided_works.add(self.fixture.root, research_fixtures.TEXT.encode(), citation="Kuran (1995)",
                                  tasks=[spec.id])
        with patch("podcast_automate.question_research.import_source", side_effect=OSError("disk gone")):
            self.assertEqual(engine.adopt_provided_works(), [])
        self.assertIn("disk gone", engine.state["provided_works"][work["id"]]["error"])


def copy_row(row):
    return json.loads(json.dumps(row))


class ClosingCallTests(unittest.TestCase):
    def test_a_follow_up_round_counts_its_follow_up_assessment_not_a_whole_one(self):
        """Review 2026-11-14: every assembled round counted a whole assessment (assessment.json) among the closing calls,
        which a follow-up round never makes, so the last approved call could not be spent on its follow-up or routing."""
        import tempfile
        from pathlib import Path
        from podcast_automate.question_budget import remaining_calls
        from podcast_automate.research_ledger import save_value
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            state = {"phase": "audit", "tasks": {}, "audit_round": 1, "prompt_generation": 4, "seed_dossier": None,
                     "dirty_tasks": []}
            self.assertEqual(remaining_calls(dict(state, audit_round=0), folder)[1],
                             {folder / "synthesis/audit_00/assessment.json"})
            save_value(folder / "synthesis/audit_00/assessment_merged.json", {"requirements": [], "issues": []})
            self.assertEqual(remaining_calls(state, folder)[1], {folder / "synthesis/audit_01/assessment_followup.json"})
            save_value(folder / "synthesis/audit_01/assessment_scope.json", {"requirements": [], "changed_tasks": []})
            self.assertEqual(remaining_calls(state, folder)[1], set(), "a follow-up with nothing in scope makes no call")
            save_value(folder / "synthesis/audit_01/assessment_merged.json", {"requirements": [], "issues": []})
            self.assertEqual(remaining_calls(state, folder)[1], set(), "an assessed round makes no further call")


if __name__ == "__main__":
    unittest.main()
