"""Question workflow regressions: retrieval, independent gates, bounded work and replay."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.models import TopicBrief
from podcast_automate.question_answering import (READER_ACTIONS, normalise_answer, normalise_review, read_context,
                                                 review_outcome, review_passes)
from podcast_automate.question_research import QuestionResearch, answer_errors, validate_plan
from podcast_automate.question_scope import QuestionScopeReview
from podcast_automate.evidence_models import ResearchObjection
from podcast_automate.question_synthesis import (SUPPORT_VERDICT_FIELDS, SynthesisMixin, compact_assessment_material,
                                                 compact_review_instructions, compact_routing_material)
from podcast_automate.research import run_research
from podcast_automate.research_advisor import BlockAdvice
from podcast_automate.research_evidence import support_errors
from podcast_automate.research_ledger import bootstrap_legacy, public_ledger, read_value, save_value
from podcast_automate.research_models import Evidence, Finding, ResearchDossier, SourceIndex, SourceSection
from podcast_automate.research_patches import DossierPatch
from podcast_automate.research_quality import FollowUpAssessment, ResearchAssessment
from podcast_automate.research_reader import SourceReader
from podcast_automate.research_review import SourceReview
from podcast_automate.research_review import SourceReviewIssue
from podcast_automate.research_tasks import AnswerReview, QuestionPlan, QuestionSearch, ReaderWindow, ResearchDecision, ReopenPlan
from podcast_automate.runner import status
from podcast_automate.sources import import_source
from podcast_automate.storage import digest, file_hash, init_project, write_json, write_yaml
from podcast_automate.run_budget import approve_model_call_limit
from podcast_automate.studio_progress import research_progress
from tests import research_fixtures as fixtures
from tests.research_fixtures import composed_generation


from tests.question_fixtures import (task_value, decision, answer_for, question_response, complete_fixture_response, claim_contract,
                                     support_receipts)


def failing_follow_up(payload, reason="A required mechanism is still absent.", remedy="research"):
    """A follow-up that still finds the first requirement in scope unmet (question_synthesis.follow_up_assessment)."""
    ids = [f["id"] for f in payload["findings"]]
    cited = next((fid for fid in ids if fid.endswith("__f_energy")), ids[0])
    return FollowUpAssessment(requirements=[dict(requirement_id=rid, finding_ids=[cited], direct_answer=True,
        explanation=index != 0, evidence=True, cross_check=True, boundaries=True, reason=reason if index == 0 else "Met.",
        missing=[], search_queries=[], remedy=remedy if index == 0 else "none")
        for index, rid in enumerate(payload["follow_up"]["requirements_in_scope"])], issues=[])


class QuestionResearchTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # Resolved, as run_research() resolves the root it hands the engine (an 8.3 TEMP differs from its long form).
        self.root = Path(temp.name).resolve() / "Projekt mit Leerzeichen"
        self.config = TopicBrief(topic="Test topic")
        init_project(self.root, self.config)
        self.work = self.root / "runs/run_test"
        self.discovery = fixtures.discovery()
        fetch = patch("podcast_automate.sources.download", return_value=(fixtures.HTML, "text/html", "https://example.org/paper0"))
        self.download = fetch.start()
        self.addCleanup(fetch.stop)
        doc, _ = import_source(self.discovery.candidates[0], self.root, self.work.name)
        self.index = SourceIndex(sources=[doc], failures=[])
        self.ref = f"{doc.id}#{next(s.id for s in doc.sections if 'Models assign an energy' in s.text)}"
        self.calls = []
        self.hook = lambda *args: None

    def model(self, prompt, schema, version="test", **kwargs):
        payload = json.loads(prompt.splitlines()[-1])
        self.calls.append((schema, version))
        override = self.hook(prompt, schema, payload, kwargs)
        if override is not None:
            return complete_fixture_response(override, payload), {}
        if schema is fixtures.ResearchDiscovery:
            return self.discovery, {"research_performed": True, "web_search_events": 1}
        result = question_response(prompt, schema)
        if result is not None:
            return result, {}
        if schema is ResearchDossier:
            if "retrieved_sources" in payload:
                return fixtures.dossier_from_prompt(prompt), {}
            return fixtures.dossier_from_prompt(json.dumps({"topic": "Test topic", "retrieved_sources": payload["sources"]})), {}
        if schema is DossierPatch:
            return DossierPatch(updates=[], additions=[], coverage_updates=[], resolved_open_questions=[], new_open_questions=[]), {}
        if schema is SourceReview:
            return SourceReview(issues=[], limitations=[]), {}
        if schema is QuestionSearch:
            return complete_fixture_response(QuestionSearch(candidates=[], limitations=["No additional suitable source in this fixture."]), payload), {}
        if schema is ResearchAssessment:
            return fixtures.assessment_from_prompt(prompt), {}
        self.fail(f"Unexpected schema {schema}")

    def engine(self):
        return QuestionResearch(self.root, self.work, self.config, self.model,
            lambda activity: write_json(self.work / "research_activity.json", {"activity": activity}))

    def test_scope_split_is_checked_before_reading_and_preserves_coverage(self):
        def hook(prompt, schema, payload, kwargs):
            if schema is QuestionScopeReview and len(payload["tasks"]) == 1:
                task = payload["tasks"][0]
                return QuestionScopeReview(decisions=[{"task_id": task["id"], "reason": "Two independently answerable obligations", "parts": [
                    {"question": title, "criterion_indices": [0], "acceptance": [title], "queries": ["energy"], "key_terms": ["energy"]}
                    for title in ("Define energy", "Explain configuration")]}])
        self.hook = hook
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual([call[0] for call in self.calls[:3]], [QuestionPlan, QuestionScopeReview, QuestionScopeReview])
        self.assertEqual(len(engine.state["plan"]["tasks"]), 2)
        self.assertTrue(all(row["status"] == "verified" for row in engine.state["tasks"].values()))
        self.assertEqual(engine.state["task_groups"]["task_definition"], ["task_definition_a", "task_definition_b"])

    def keep_splitting(self, prompt, schema, payload, kwargs):
        """A scope review that splits every task it sees, in every pass."""
        if schema is QuestionScopeReview:
            return QuestionScopeReview(decisions=[{"task_id": task["id"], "reason": "Still bundled", "parts": [
                {"question": title, "criterion_indices": list(range(len(task["acceptance"]))),
                 "acceptance": [title], "queries": ["energy"], "key_terms": ["energy"]}
                for title in ("Focus A", "Focus B")]} for task in payload["tasks"]])

    def test_a_scope_review_that_keeps_splitting_converges_on_its_last_pass_with_a_note(self):
        """2026-10-02: a second pass that still split a larger share stopped with question_scope_unresolved, which no
        fresh attempt moved and every resume replayed (the Ontologies run run_20260930_195553 was abandoned on it). Its
        splits are adopted within the task cap, and the note goes with the plan to the plan gate. Still two passes."""
        from podcast_automate.question_budget import plan_review_message
        self.hook = self.keep_splitting
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual([call[0] for call in self.calls[:3]], [QuestionPlan, QuestionScopeReview, QuestionScopeReview])
        self.assertEqual(sum(call[0] is QuestionScopeReview for call in self.calls), 2, "no third pass")
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(len(engine.state["plan"]["tasks"]), 4, "the second pass's splits are adopted")
        note = engine.state["scope_unresolved"]
        self.assertEqual((note["adopted"], note["reviewed_tasks"], note["split_tasks"]),
                         (True, 2, ["task_definition_a", "task_definition_b"]))
        projection = json.loads((self.work / "question_research/plan_projection.json").read_text(encoding="utf-8"))
        self.assertEqual(projection["scope_note"], note["note"])
        self.assertIn("im zweiten Durchgang noch 2 von 2 Teilfragen", plan_review_message(projection))
        # A resume replays both passes to the same plan instead of stopping again.
        count = len(self.calls)
        self.engine().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), count)

    def test_a_last_scope_pass_beyond_the_task_cap_leaves_the_plan_as_the_first_pass_left_it(self):
        # A cap of three tasks: (750 - 690 - 4) // 16. The second pass would make four of two.
        write_json(self.work / "budget.json", {"model_calls": 690, "search_rounds": 0})
        self.hook = self.keep_splitting
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual([t["id"] for t in engine.state["plan"]["tasks"]], ["task_definition_a", "task_definition_b"])
        note = engine.state["scope_unresolved"]
        self.assertEqual((note["adopted"], note["tasks"]), (False, 2))
        self.assertIn("Obergrenze von 3 Teilfragen", note["note"])

    def test_a_second_scope_pass_that_splits_only_a_few_tasks_is_adopted(self):
        """Ontologies, 2026-09-30: the second pass split 3 of 56 tasks and stopped the run; a nearly converged review
        is adopted within the task cap, while one that keeps splitting everything still stops (test above)."""
        passes = []

        def hook(prompt, schema, payload, kwargs):
            if schema is QuestionScopeReview:
                passes.append(len(payload["tasks"]))
                # Every task is judged; only the first is split, in each pass.
                return QuestionScopeReview(decisions=[{"task_id": task["id"], "reason": "Two obligations" if i == 0 else "Focused",
                    "parts": [{"question": title, "criterion_indices": list(range(len(task["acceptance"]))),
                               "acceptance": [title], "queries": ["energy"], "key_terms": ["energy"]}
                              for title in ("Define energy", "Explain configuration")] if i == 0 else []}
                    for i, task in enumerate(payload["tasks"])])
        self.hook = hook
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(passes, [1, 2])
        self.assertEqual(len(engine.state["plan"]["tasks"]), 3, "the second pass's split is kept")

    @composed_generation()
    def test_complete_workflow_freezes_verified_answers_and_replay_has_no_calls(self):
        engine = self.engine()
        outputs = engine.run(self.discovery, self.index)
        self.assertTrue(all(p.exists() for p in outputs))
        self.assertEqual([c[0] for c in self.calls], [QuestionPlan, QuestionScopeReview, ResearchDecision, AnswerReview,
                                                   ResearchDossier, SourceReview, ResearchAssessment])
        answer_hash = engine.state["tasks"]["task_definition"]["verification"]["answer_hash"]
        count = len(self.calls)
        self.engine().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), count)
        ledger = read_value(self.work / "question_research/state.json")
        self.assertEqual(ledger["tasks"]["task_definition"]["verification"]["answer_hash"], answer_hash)
        public = json.loads((self.work / "research_questions.json").read_text())
        self.assertEqual((public["closed"], public["total"], public["phase"]), (1, 1, "completed"))

    @composed_generation()
    def test_material_beyond_one_window_is_composed_and_audited_in_bounded_parts(self):
        assessed = []

        def two_tasks(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchDecision and payload["task"]["kind"] == "empirical":
                return decision("answer", answer=answer_for(self.ref))
            if schema is ResearchAssessment:
                assessed.append(payload)
        self.hook = two_tasks
        with patch("podcast_automate.question_synthesis.PROMPT_BUDGET_CHARS", 1):
            engine = self.engine()
            outputs = engine.run(self.discovery, self.index)
            self.assertTrue(all(p.exists() for p in outputs))
            self.assertEqual(engine.state["phase"], "completed")
            # The assessment of a dossier beyond one window reads verdicts and identities, not prose.
            [payload] = assessed
            self.assertEqual(set(payload["finding_support"][0]), {"finding_id", "verdict", "unsupported_clauses", "suitability",
                                                                  "empirical_status", "independent_evidence_refs"})
            self.assertTrue(all(set(e) == {"reference"} for f in payload["dossier"]["findings"] for e in f["evidence"]))
            self.assertTrue(all(set(s) == {"source_id", "title", "url"} for s in payload["sources"]))
            self.assertTrue(all("illustration" in f and "claim_contract" in f for f in payload["dossier"]["findings"]))
            schemas = [c[0] for c in self.calls]
            # The dossier opens with the first answer, the second is integrated as a patch, the audit runs in parts.
            self.assertEqual(schemas.count(ResearchDossier), 1)
            self.assertEqual(schemas.count(DossierPatch), 1)
            self.assertEqual(schemas.count(SourceReview), 1)
            self.assertEqual(schemas.count(ResearchAssessment), 1)
            audit = self.work / "question_research/synthesis/audit_00"
            for name in ("dossier_batch_000.json", "dossier_batch_001.json", "dossier_batches.json",
                         "grounding_0_part_000.json", "grounding_0_merged.json", "assessment.json"):
                self.assertTrue((audit / name).exists(), name)
            self.assertFalse((audit / "dossier.json").exists())
            batches = json.loads((audit / "dossier_batches.json").read_text(encoding="utf-8"))
            self.assertEqual((batches["opening"], batches["batches"]), (["task_definition"], 1))
            merged = json.loads((audit / "grounding_0_merged.json").read_text(encoding="utf-8"))
            self.assertEqual((merged["parts"], merged["findings_per_part"]), (1, [1]))
            report = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
            self.assertTrue(report["passed"])
            count = len(self.calls)
            self.engine().run(self.discovery, self.index)
            self.assertEqual(len(self.calls), count, "a replay makes no calls")

    def test_audit_parts_keep_their_own_material_when_the_shared_context_outgrows_the_budget(self):
        # The Asimov round-two audit: outline, other findings, tasks and baseline alone passed the
        # budget, so every part held one finding, still overran it, and the run stopped.
        findings = [Finding(id=f"f_{n}", kind="claim", statement=f"Claim {n}.", claim_contract=claim_contract(),
                            evidence=[Evidence(reference=f"src#s{n}", excerpt=f"Passage {n}")]) for n in range(6)]
        dossier = ResearchDossier(topic="Test topic", scope_note="Bounded.", findings=findings, coverage=[], open_questions=[])
        context = [{"source_id": "src", "title": "Paper", "url": "https://example.org/p",
                    "sections": [{"reference": f"src#s{n}", "text": f"Passage {n} " + "z" * 2_000} for n in range(6)]}]
        self.work.mkdir(parents=True, exist_ok=True)

        def split(shared_chars, budget, answer_budget):
            sizes = []

            class Engine:
                config = self.config
                state = {"plan": {"tasks": [{"id": "t", "question": "q" * shared_chars}]}, "verified_baseline": []}
                settled_findings = SynthesisMixin.settled_findings  # a first round: the whole dossier

                def save(self, activity):
                    pass

                def call(self, folder, name, schema, prompt, validate):
                    sizes.append(len(prompt))
                    return SourceReview(issues=[], limitations=[])

            with patch("podcast_automate.question_synthesis.PROMPT_BUDGET_CHARS", budget), \
                    patch("podcast_automate.question_synthesis.ANSWER_BUDGET_CHARS", answer_budget):
                SynthesisMixin.grounding_review(Engine(), self.work, 0, dossier, context, {}, lambda *a, **k: None)
            merged = json.loads((self.work / "grounding_0_merged.json").read_text(encoding="utf-8"))
            return merged["findings_per_part"], sizes

        # A shared context within the budget: the budget alone bounds each part, as before.
        parts, sizes = split(1_000, 12_000, 1)
        self.assertEqual(sum(parts), 6)
        self.assertTrue(len(parts) > 1 and all(size <= 12_000 for size in sizes), (parts, sizes))
        # A shared context beyond the budget: without room of its own a part holds one finding and still
        # overruns; with ANSWER_BUDGET_CHARS of room it holds what fits. Each finding brings its 2 000-character
        # passage and about 600 characters of JSON, so two fit into 7 000 characters and three do not.
        parts, sizes = split(60_000, 12_000, 1)
        self.assertEqual(parts, [1] * 6)
        parts, sizes = split(60_000, 12_000, 7_000)
        self.assertEqual(parts, [2, 2, 2])
        self.assertTrue(all(size > 60_000 for size in sizes), sizes)

    def test_a_part_assesses_only_the_sources_its_findings_cite_while_objections_bring_their_own(self):
        # The Asimov round-two audit: an objection cited a source no finding of its part cites; the part
        # listed it under sources, the model assessed it as told, and the source check refused all three tries.
        findings = [Finding(id=f"f_{n}", kind="claim", statement=f"Claim {n}.", claim_contract=claim_contract(),
                            evidence=[Evidence(reference=f"a#s{n}", excerpt=f"Passage {n}")]) for n in range(2)]
        dossier = ResearchDossier(topic="Test topic", scope_note="Bounded.", findings=findings, coverage=[], open_questions=[])
        context = [{"source_id": sid, "title": sid, "url": f"https://example.org/{sid}",
                    "sections": [{"reference": f"{sid}#s{n}", "text": f"Passage {n}"} for n in range(2)]} for sid in ("a", "b")]
        objection = {"finding_ids": ["f_0"], "evidence_refs": ["b#s1", "a#s1"], "reason": "Source b qualifies the claim."}
        self.work.mkdir(parents=True, exist_ok=True)
        prompts = []

        class Engine:
            config = self.config
            state = {"plan": {"tasks": [{"id": "t", "question": "q"}]}, "verified_baseline": []}
            settled_findings = SynthesisMixin.settled_findings  # a first round: the whole dossier

            def save(self, activity):
                pass

            def call(self, folder, name, schema, prompt, validate):
                prompts.append(prompt)
                payload = json.loads(prompt.splitlines()[-1])
                return SourceReview(issues=[], limitations=[], **support_receipts(payload["dossier"]["findings"], payload["sources"]))

        with patch("podcast_automate.question_synthesis.PROMPT_BUDGET_CHARS", 1),                 patch("podcast_automate.question_synthesis.ANSWER_BUDGET_CHARS", 1):
            review = SynthesisMixin.grounding_review(Engine(), self.work, 0, dossier, context, {"o1": objection},
                                                     lambda *a, **k: None)
        first, second = (json.loads(p.splitlines()[-1]) for p in prompts)
        self.assertEqual([s["source_id"] for s in first["sources"]], ["a"])
        # a#s1 belongs to the finding of the other part: here it is objection material, like all of source b.
        self.assertEqual([(s["source_id"], [p["reference"] for p in s["sections"]]) for s in first["objection_sources"]],
                         [("a", ["a#s1"]), ("b", ["b#s1"])])
        self.assertIn("objection_sources holds passages", prompts[0])
        # A part without objection-only passages keeps the prompt it had before.
        self.assertNotIn("objection_sources", second)
        self.assertNotIn("objection_sources holds passages", prompts[1])
        # Assessing exactly the listed sources is what the source check expects, part by part and merged.
        support_errors(findings, review, context)

    @composed_generation()
    def test_an_audit_that_also_assesses_a_source_no_finding_cites_passes_without_it(self):
        # Ontologies, 2026-09-27: a part copied two sources from the outline into its assessments; the
        # extra rows cost a whole retry. Every cited source still needs its one assessment.
        added = []

        def model(prompt, schema, version="test", **kwargs):
            value, meta = self.model(prompt, schema, version, **kwargs)
            if schema is SourceReview and not added:
                extra = value.source_assessments[0].model_copy(update={"source_id": "src_elsewhere", "evidence_refs": []})
                value.source_assessments = [*value.source_assessments, extra]
                added.append(extra)
            return value, meta
        engine = QuestionResearch(self.root, self.work, self.config, model,
                                  lambda activity: write_json(self.work / "research_activity.json", {"activity": activity}))
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        audit = self.work / "question_research/synthesis/audit_00"
        self.assertEqual(sorted(p.name for p in audit.glob("grounding_*rejected*")), [])
        saved = json.loads((audit / "grounding_0.json").read_text(encoding="utf-8"))["value"]
        self.assertNotIn("src_elsewhere", [row["source_id"] for row in saved["source_assessments"]])
        self.assertEqual(len(added), 1)

    def test_an_audit_limitation_needs_no_corrective_issue_but_a_broken_contract_does(self):
        # Asimov, round two: beside a contradicted finding with its issue, partially supported findings whose
        # contract and source held were refused for lacking an issue of their own, although a question's
        # review records them as limitations. The audit now blocks on the same receipts (research_evidence.blocks).
        engine = self.engine()
        engine.run(self.discovery, self.index)
        first = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text()).findings[0]
        other = first.model_copy(update={"id": "f_other"})
        dossier = ResearchDossier(topic="Test topic", scope_note="Bounded.", findings=[first, other], coverage=[], open_questions=[])
        context = read_context(engine.reader, [self.ref])
        receipts = support_receipts([f.model_dump() for f in dossier.findings], context)
        receipts["finding_support"][0].update(verdict="contradicted", unsupported_clauses=["the whole claim"])
        issue = SourceReviewIssue(finding_id=first.id, reason="The passage contradicts the claim.", resolution="revise",
            search_queries=[], objection=ResearchObjection(id="obj_x", rule="support", task_id="", finding_ids=[first.id],
                evidence_refs=[self.ref], missing_evidence="", reason="The passage contradicts the claim.",
                correction="Restate the claim.", closure_condition="The claim matches the passage.", resolution="revise"))
        outcomes = []

        class Stop(Exception):
            pass

        def judge(folder, revision, dossier_, context_, expected, well_formed):
            for contract_preserved in (True, False):
                support = [dict(receipts["finding_support"][0]),
                           {**receipts["finding_support"][1], "verdict": "partially_supported",
                            "unsupported_clauses": ["the scope beyond the fixture"], "contract_preserved": contract_preserved}]
                review = SourceReview.model_validate({**receipts, "finding_support": support, "issues": [issue.model_dump()],
                                                      "limitations": []})
                try:
                    well_formed(review, False)
                    outcomes.append((contract_preserved, "accepted"))
                except AppError as exc:
                    outcomes.append((contract_preserved, str(exc)))
            raise Stop
        with patch.object(engine, "grounding_review", side_effect=judge), patch.object(engine, "save"):
            with self.assertRaises(Stop):
                engine.audit(dossier, self.discovery, context)
        self.assertEqual(outcomes[0], (True, "accepted"))
        self.assertEqual(outcomes[1][0], False)
        self.assertIn("require explicit corrective issues", outcomes[1][1])

    def test_a_later_round_reviews_only_what_changed_or_is_still_contested(self):
        # Asimov, 2026-09-27: every round re-reviewed all findings and raised new objections on unchanged ones
        # (9, then 16), so the loop never settled.
        def finding(n, statement=None):
            return Finding(id=f"f_{n}", kind="claim", statement=statement or f"Claim {n}.", claim_contract=claim_contract(),
                           evidence=[Evidence(reference=f"a#s{n}", excerpt=f"Passage {n}")])
        before = ResearchDossier(topic="Test topic", scope_note="Bounded.", findings=[finding(n) for n in range(6)],
                                 coverage=[], open_questions=[])
        now = before.model_copy(update={"findings": [finding(n, "Claim 2, reworded." if n == 2 else None) for n in range(6)]})
        context = [{"source_id": "a", "title": "a", "url": "https://example.org/a",
                    "sections": [{"reference": f"a#s{n}", "text": f"Passage {n}"} for n in range(6)]}]
        receipts = support_receipts([f.model_dump() for f in before.findings], context)
        issue = SourceReviewIssue(finding_id="f_1", reason="Too broad.", resolution="revise", search_queries=[],
            objection=ResearchObjection(id="obj_new", rule="support", task_id="", finding_ids=["f_1"], evidence_refs=["a#s1"],
                missing_evidence="", reason="Too broad.", correction="Narrow it.", closure_condition="Narrowed.", resolution="revise"))
        previous = SourceReview(issues=[issue], limitations=[], **receipts, objection_checks=[
            {"objection_id": "obj_closed", "verdict": "closed", "references": ["a#s3"], "reason": "Met."},
            {"objection_id": "obj_open", "verdict": "open", "references": ["a#s4"], "reason": "Still broad."}])
        synthesis = self.work / "question_research/synthesis"
        write_json(synthesis / "audit_00/grounding_0_merged.json", {"parts": 1, "review": previous.model_dump(mode="json")})
        expected = {"obj_closed": {"finding_ids": ["f_3"], "evidence_refs": ["a#s3"]},
                    "obj_open": {"finding_ids": ["f_4"], "evidence_refs": ["a#s4"]}}
        reviewed = []

        class Engine:
            config, folder = self.config, self.work / "question_research"
            state = {"plan": {"tasks": [{"id": "t", "question": "q"}]}, "verified_baseline": [], "audit_round": 1,
                     "seed_dossier": before.model_dump()}

            def save(self, activity=None):
                pass

            def call(self, folder, name, schema, prompt, validate):
                payload = json.loads(prompt.splitlines()[-1])
                reviewed.extend(f["id"] for f in payload["dossier"]["findings"])
                return SourceReview(issues=[], limitations=[], **support_receipts(payload["dossier"]["findings"], payload["sources"]),
                                    objection_checks=[{"objection_id": oid, "verdict": "closed", "references": ["a#s4"],
                                                       "reason": "Now met."} for oid in payload["open_objections"]])
        engine = Engine()
        engine.settled_findings = SynthesisMixin.settled_findings.__get__(engine)
        review = SynthesisMixin.grounding_review(engine, synthesis / "audit_01", 0, now, context, expected, lambda *a, **k: None)
        # The changed finding, the one with last round's issue and the one an open objection names are reviewed.
        self.assertEqual(sorted(reviewed), ["f_1", "f_2", "f_4"])
        self.assertEqual(sorted(r.finding_id for r in review.finding_support), [f"f_{n}" for n in range(6)])
        self.assertEqual(sorted(c.objection_id for c in review.objection_checks), ["obj_closed", "obj_open"])
        merged = json.loads((synthesis / "audit_01/grounding_0_merged.json").read_text(encoding="utf-8"))
        self.assertEqual(merged["targeted"], {"carried_findings": 3, "carried_checks": 1})
        # The choice stands for the round: a resume carries the same findings even if the seed moved on.
        engine.state["seed_dossier"] = now.model_dump()
        self.assertEqual(engine.settled_findings(synthesis / "audit_01", 0, now, context, expected)["findings"], ["f_0", "f_3", "f_5"])
        # A first round, or a correction revision, reviews the whole dossier.
        self.assertIsNone(engine.settled_findings(synthesis / "audit_01", 1, now, context, expected))

    @composed_generation()
    def test_answers_beyond_one_output_are_composed_in_parts_even_when_the_prompt_fits(self):
        # The prompt budget is untouched: the answers alone bound the opening batch, because the
        # dossier a call writes grows with them and the CLI cuts an answer at its output cap.
        openings = []

        def two_tasks(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchDecision and payload["task"]["kind"] == "empirical":
                return decision("answer", answer=answer_for(self.ref))
            if schema is ResearchDossier:
                openings.append([item["task"]["id"] for item in payload["verified_answers"]])
        self.hook = two_tasks
        with patch("podcast_automate.question_synthesis.ANSWER_BUDGET_CHARS", 1):
            engine = self.engine()
            outputs = engine.run(self.discovery, self.index)
            self.assertTrue(all(p.exists() for p in outputs))
            self.assertEqual(engine.state["phase"], "completed")
            schemas = [c[0] for c in self.calls]
            self.assertEqual((schemas.count(ResearchDossier), schemas.count(DossierPatch)), (1, 1))
            self.assertEqual(openings, [["task_definition"]])
            audit = self.work / "question_research/synthesis/audit_00"
            self.assertFalse((audit / "dossier.json").exists())
            batches = json.loads((audit / "dossier_batches.json").read_text(encoding="utf-8"))
            self.assertEqual((batches["opening"], batches["batches"], batches["answer_budget_chars"]), (["task_definition"], 1, 1))
            count = len(self.calls)
            self.engine().run(self.discovery, self.index)
            self.assertEqual(len(self.calls), count, "a replay makes no calls")

    @composed_generation()
    def test_new_runs_state_the_synthesis_evidence_rule_and_old_runs_keep_their_prompts(self):
        prompts = []

        def capture(prompt, schema, payload, kwargs):
            if schema is ResearchDossier:
                prompts.append(prompt)
        self.hook = capture
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["prompt_generation"], 2)
        self.assertEqual(engine.prompt_tag, ".g2")
        self.assertTrue(all("at least one for each compared finding" in p for p in prompts))
        self.assertEqual(len(prompts), 1)
        self.assertTrue(any(v.endswith(".g2.dossier") for _, v in self.calls))
        engine.state["prompt_generation"] = 1
        self.assertEqual((engine.synthesis_rule(), engine.prompt_tag), ("", ""))

    def test_compact_assessment_material_keeps_verdicts_and_one_row_per_source(self):
        dossier = self.seed_dossier()
        sources = [{"source_id": s.id, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text} for x in s.sections]}
                   for s in self.index.sources]
        receipts = support_receipts([f.model_dump() for f in dossier.findings], sources)
        first = receipts["source_assessments"][0]
        second = {**first, "roles": ["empirical_test"], "rationale": "Second part of a split review."}
        review = SourceReview(issues=[], limitations=[], finding_support=receipts["finding_support"],
                              source_assessments=[first, second])
        material = compact_assessment_material(dossier, review)
        self.assertTrue(all(set(e) == {"reference"} for f in material["dossier"]["findings"] for e in f["evidence"]))
        self.assertEqual([f["id"] for f in material["dossier"]["findings"]], [f.id for f in dossier.findings])
        self.assertEqual(set(material["finding_support"][0]), set(SUPPORT_VERDICT_FIELDS))
        self.assertEqual(len(material["source_assessments"]), 1)
        self.assertEqual(material["source_assessments"][0]["roles"], ["original_definition", "empirical_test"])
        self.assertNotIn("rationale", material["source_assessments"][0])

    def test_compact_routing_and_correction_material_keep_anchors_and_statements(self):
        dossier = self.seed_dossier()
        sources = [{"source_id": s.id, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text} for x in s.sections]}
                   for s in self.index.sources]
        receipts = support_receipts([f.model_dump() for f in dossier.findings], sources)
        objection = ResearchObjection(id="obj_energy", rule="support", task_id="task_definition", criterion_index=0,
                                      finding_ids=["f_energy"], evidence_refs=[self.ref], missing_evidence="",
                                      reason="The clause about configurations is not covered.",
                                      correction="Restrict the statement to the read passage.",
                                      closure_condition="A passage covers the configuration clause.", resolution="revise")
        review = SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason="Not covered.", resolution="revise",
                                                        search_queries=[], objection=objection)],
                              limitations=["fixture"], finding_support=receipts["finding_support"],
                              source_assessments=receipts["source_assessments"])
        plan = QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
        answers = {"task_definition": answer_for(self.ref).model_dump(), "task_empirical": None}
        material = compact_routing_material(plan.tasks, answers, review, dossier)
        self.assertTrue(set(material["tasks"][0]) <= set(plan.tasks[0].model_dump()))
        self.assertIn("acceptance", material["tasks"][0])
        self.assertNotIn("queries", material["tasks"][0])
        self.assertEqual(material["anchored_issues"][0]["closure_condition"], objection.closure_condition)
        self.assertEqual(material["anchored_issues"][0]["evidence_refs"], [self.ref])
        self.assertNotIn("correction", material["anchored_issues"][0])
        # An anchor must cite read references, so every finding keeps its references, never its excerpts.
        self.assertEqual([e for f in material["dossier"]["findings"] for e in f["evidence"]],
                         [{"reference": e.reference} for f in dossier.findings for e in f.evidence])
        self.assertEqual(set(material["answers"]["task_definition"]), {"summary", "limits", "finding_ids"})
        self.assertIsNone(material["answers"]["task_empirical"])
        self.assertEqual([set(f) for f in material["dossier"]["findings"]],
                         [{"id", "kind", "statement", "evidence"}] * len(dossier.findings))
        self.assertEqual(material["dossier"]["coverage"], dossier.model_dump()["coverage"])
        guidance = compact_review_instructions(review, {"f_energy"})
        self.assertEqual(set(guidance), {"issues", "objection_checks", "finding_support"})
        self.assertEqual([s["finding_id"] for s in guidance["finding_support"]], ["f_energy"])
        self.assertEqual(guidance["issues"][0]["objection"]["id"], "obj_energy")

    def seed_dossier(self, *open_questions):
        dossier = fixtures.dossier_from_prompt(json.dumps({"topic": "Test topic", "retrieved_sources": [
            {"source_id": s.id, "url": s.final_url, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text}
                                                                 for x in s.sections]} for s in self.index.sources]}))
        dossier.open_questions = list(open_questions)
        return dossier

    def probed(self, gap):
        """Initialise with one declared gap and research its task; no run-level audit involved."""
        context = [{"source_id": s.id, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text}
                                                    for x in s.sections]} for s in self.index.sources]
        engine = self.engine()
        engine.initialise(self.discovery, self.index, self.seed_dossier(gap), context)
        task = next(t for t in QuestionPlan.model_validate(engine.state["plan"]).tasks)
        self.assertEqual(task.gap_ids, list(engine.state["gaps"]))
        engine.research_task(task)
        return engine, json.loads((self.work / "question_research/gap_probes.json").read_text(encoding="utf-8"))

    def test_a_declared_gap_is_probed_and_its_hits_are_read_before_any_reader_call(self):
        gap = "The rule that assigns an energy to each configuration is missing."
        engine, probes = self.probed(gap)
        self.assertEqual([row["text"] for row in probes], [gap])
        self.assertTrue(probes[0]["hits"])
        self.assertIn(probes[0]["status"], ("resolved", "hits_read_confirmed"))
        row = engine.state["tasks"]["task_definition"]
        self.assertIn(probes[0]["hits"][0]["reference"], row["read_refs"])
        # The probe itself costs nothing; only planning and the task spend calls.
        self.assertNotIn("gap_probe", [version for _, version in self.calls])

    def test_a_gap_without_corpus_hits_never_blocks(self):
        from podcast_automate.research_gap_probe import unread
        engine, probes = self.probed("Which tokenizer curriculum shaped the vocabulary schedule?")
        self.assertEqual(probes[0]["hits"], [])
        self.assertEqual(unread(probes), [])
        self.assertEqual(engine.state["tasks"]["task_definition"]["status"], "verified")

    def test_a_blocked_task_leaves_its_gap_unread_when_the_hits_were_never_read(self):
        from podcast_automate.research_gap_probe import unread
        gap = "The rule that assigns an energy to each configuration is missing."
        context = [{"source_id": s.id, "sections": [{"reference": f"{s.id}#{x.id}", "text": x.text}
                                                    for x in s.sections]} for s in self.index.sources]
        engine = self.engine()
        engine.initialise(self.discovery, self.index, self.seed_dossier(gap), context)
        task = QuestionPlan.model_validate(engine.state["plan"]).tasks[0]
        engine.state["tasks"][task.id].update(status="blocked", read_refs=[], outcome="evidence_block")
        engine.settle_probes(task, engine.state["tasks"][task.id])
        self.assertEqual([row["status"] for row in engine.state["gap_probes"]], ["hits_unread"])
        self.assertEqual(len(unread(engine.state["gap_probes"])), 1)

    def test_production_pipeline_publishes_and_resumes_without_more_calls(self):
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            first = run_research(self.root)
            used = len(self.calls)
            resumed = run_research(self.root, resume=True)
        self.assertEqual(first.status, "completed", first.model_dump())
        self.assertEqual(first.run_id, resumed.run_id)
        self.assertEqual(len(self.calls), used)
        # Discovery, plan, scope review, reader, answer review and the assessment: an assembled dossier (prompt
        # generation 3) needs no composing call and no second source review of findings their answers' reviews checked.
        self.assertEqual(used, 6)
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])
        report = json.loads((self.root / "reports/research_quality.json").read_text())
        self.assertTrue(report["quality_gate"]["passed"])
        self.assertEqual(report["budget"]["model_calls"], used)
        activity = json.loads((self.root / "runs" / first.run_id / "research_activity.json").read_text())
        self.assertEqual(activity["research_questions"]["closed"], 1)
        self.assertEqual(activity["research_questions"]["phase"], "completed")
        self.assertTrue((self.root / "research/questions.md").exists())

    def test_research_allowance_resumes_without_changing_inputs_or_saved_work(self):
        self.config.research_limits.model_calls = 3
        write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            first = run_research(self.root)
            self.assertEqual(first.status, "blocked")
            work = self.root / "runs" / first.run_id
            protected = [self.root / "project.yaml", work / "project_snapshot.yaml", work / "budget.json",
                         work / "question_research/state.json"]
            before = {p: file_hash(p) for p in protected}
            approve_model_call_limit(self.root, first.run_id, 250)
            self.assertEqual(before, {p: file_hash(p) for p in protected})
            self.assertEqual(research_progress(self.root, first.model_dump(mode="json"))["model_call_limit"], 250)
            resumed = run_research(self.root, resume=True, run_id=first.run_id)
        self.assertEqual(resumed.status, "completed", resumed.model_dump())
        self.assertEqual(resumed.run_id, first.run_id)
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(json.loads((work / "budget.json").read_text())["model_calls"], 6)
        self.assertEqual(json.loads((work / "research_activity.json").read_text())["model_call_limit"], 250)

    def test_research_reads_increased_allowance_before_next_call(self):
        self.config.research_limits.model_calls = 3
        write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
        def increase(prompt, schema, payload, kwargs):
            if schema is QuestionScopeReview:
                latest = status(self.root)["run"]
                approve_model_call_limit(self.root, latest["run_id"], 250)
        self.hook = increase
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            run = run_research(self.root)
        self.assertEqual(run.status, "completed", run.model_dump())
        self.assertEqual(len(self.calls), 6)

    def test_pause_after_answer_resumes_at_independent_review(self):
        def pause(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                raise AppError("Stopped", code="interrupted", status="interrupted")
        self.hook = pause
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        saved = read_value(self.work / "question_research/state.json")
        self.assertEqual(saved["tasks"]["task_definition"]["status"], "reviewing")
        self.hook = lambda *args: None
        self.engine().run(self.discovery, self.index)
        self.assertEqual(sum(c[0] is QuestionPlan for c in self.calls), 1)
        self.assertEqual(sum(c[0] is ResearchDecision for c in self.calls), 1)

    def test_paused_legacy_production_run_adopts_new_workflow_and_preserves_budget(self):
        # Build the historical on-disk boundary directly. Migration must not need
        # a second, obsolete executable research engine to manufacture its input.
        from podcast_automate.models import StageRecord
        from podcast_automate.research import source_context
        for stopped_stage in ("review", "completeness"):
            with self.subTest(stopped_stage=stopped_stage):
                with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model), \
                     patch("podcast_automate.research.run_question_research",
                           side_effect=AppError("Old run paused", code="interrupted", status="pending")):
                    old = run_research(self.root)
                work = self.root / "runs" / old.run_id
                index = SourceIndex.model_validate_json((work / "source_index.json").read_text())
                context = source_context(index, self.discovery)
                dossier = fixtures.dossier_from_prompt(json.dumps({"topic": self.config.topic, "retrieved_sources": context}))
                artifacts = {"dossier.json": dossier.model_dump(), "source_context.json": context,
                    "reference_check.json": {"errors": []}, "reviewed_dossier.json": dossier.model_dump(),
                    "review.json": {"issues": [], "limitations": []},
                    "review_context.json": {"prompt_version": "research_review.v6"}}
                for name, value in artifacts.items():
                    write_json(work / name, value)
                stages = {"dossier": ["dossier.json", "source_context.json", "reference_check.json"]}
                if stopped_stage == "completeness":
                    stages["review"] = ["reviewed_dossier.json", "review.json", "review_context.json"]
                for stage, names in stages.items():
                    old.stages[stage] = StageRecord(status="completed", attempts=1, outputs={
                        (work / name).relative_to(self.root).as_posix(): file_hash(work / name) for name in names})
                write_yaml(work / "run_manifest.yaml", old.model_dump(mode="json"))
                spent = 4 if stopped_stage == "completeness" else 2
                write_json(work / "budget.json", {"model_calls": spent, "search_rounds": 1})
                before = len(self.calls)
                downloads = self.download.call_count
                with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
                    resumed = run_research(self.root, resume=True)
                self.assertEqual(resumed.status, "completed", resumed.model_dump())
                self.assertEqual(old.run_id, resumed.run_id)
                self.assertEqual(self.download.call_count, downloads)
                budget = json.loads((work / "budget.json").read_text())
                self.assertEqual(budget["model_calls"], spent + len(self.calls) - before)
                self.assertEqual(budget["search_rounds"], 1)
                self.assertGreater(budget["model_calls"], spent)
                self.assertEqual(sum(c[0] is fixtures.ResearchDiscovery for c in self.calls[before:]), 0)
                self.assertEqual(sum(c[0] is QuestionPlan for c in self.calls[before:]), 1)
                for stage in stages:
                    self.assertEqual(resumed.stages[stage].outputs, old.stages[stage].outputs)

    def test_web_download_receipts_resume_without_repeating_search_or_first_download(self):
        engine = self.engine()
        engine.initialise(self.discovery, self.index, None, [])
        task = QuestionPlan.model_validate(engine.state["plan"]).tasks[0]
        row = engine.state["tasks"][task.id]
        engine.seed(task, row)
        row["pending"] = decision("search_web", web_queries=["energy independent study"]).model_dump()
        engine.save()
        extra = fixtures.discovery(count=3).candidates[1:]
        self.hook = lambda prompt, schema, payload, kwargs: QuestionSearch(candidates=extra, limitations=[]) \
            if schema is QuestionSearch else None
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"These sentences", (url + ". These sentences").encode()), "text/html", url)
        downloaded = []
        def interrupted(candidate, *args):
            if downloaded:
                raise KeyboardInterrupt("Power interruption between downloads")
            downloaded.append(candidate.url)
            return import_source(candidate, *args)
        with patch("podcast_automate.question_answering.import_source", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                engine.research_task(task)
        used = self.download.call_count
        restored = self.engine()
        restored.run(self.discovery, self.index)
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        self.assertEqual(self.download.call_count, used + 1)
        self.assertEqual(len(restored.index.sources), 3)
        self.assertEqual(restored.state["tasks"][task.id]["web_attempts"], 1)

    def test_local_reading_remains_available_when_web_budget_is_exhausted(self):
        self.config.research_limits.sources = 1
        write_json(self.work / "budget.json", {"model_calls": 5, "search_rounds": self.config.research_limits.search_rounds})
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertFalse(any(c[0] is QuestionSearch for c in self.calls))

    def web_recovery(self):
        """The reader blocks once, so the recovery ladder reaches its web search."""
        decisions = []
        def recover(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                decisions.append(1)
                if len(decisions) == 1:
                    return decision("blocked")
            if schema is QuestionSearch:
                return QuestionSearch(candidates=fixtures.discovery(count=2).candidates[1:], limitations=[])
        self.hook = recover
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"These sentences", b"Additional independent evidence. These sentences"), "text/html", url)

    def test_a_full_source_limit_ends_the_web_search_and_says_so(self):
        self.config.research_limits.sources = 1
        self.web_recovery()
        with self.assertRaises(AppError) as raised:
            self.engine().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        row = next(iter(read_value(self.work / "question_research/state.json")["tasks"].values()))
        self.assertEqual((row["status"], row["web_attempts"]), ("blocked", 0))
        # The reader's own reason stays, and the limit that stopped the search stands next to it.
        self.assertIn("A concrete next step.", row["reason"])
        self.assertIn("Das Quellenlimit des Laufs ist erreicht (1 Quellen)", row["reason"])
        self.assertFalse(any(c[0] is QuestionSearch for c in self.calls))

    def test_an_approved_source_limit_lets_the_web_search_load_new_sources(self):
        self.config.research_limits.sources = 1
        self.web_recovery()
        approved = self.config.research_limits.model_copy(update={"sources": 3})
        engine = QuestionResearch(self.root, self.work, self.config, self.model, lambda activity: None,
                                  limits=lambda: approved)
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        self.assertEqual(len(engine.index.sources), 2)

    def test_the_reader_sees_unread_search_hits_newest_first_within_a_budget(self):
        from podcast_automate.question_answering import open_candidates
        hit = lambda ref: {"reference": ref, "excerpt": "x" * 400}
        catalog = [{"query": "old", "candidates": [hit("s#1"), hit("s#2")]},
                   {"query": "read", "candidates": [hit("s#3")]},
                   {"query": "new", "candidates": [hit("s#4"), hit("s#5")]}]
        # A search whose hits are all read disappears; a partly read one keeps its unread hits; order stays oldest first.
        shown = open_candidates(catalog, ["s#3", "s#5"])
        self.assertEqual([(r["query"], [c["reference"] for c in r["candidates"]]) for r in shown],
                         [("old", ["s#1", "s#2"]), ("new", ["s#4"])])
        # Over the budget the older searches go first; the newest with unread hits always stays.
        self.assertEqual([r["query"] for r in open_candidates(catalog, [], budget=1_000)], ["new"])
        self.assertEqual([r["query"] for r in open_candidates(catalog, [], budget=10)], ["new"])
        self.assertEqual(open_candidates(catalog, ["s#1", "s#2", "s#3", "s#4", "s#5"]), [])
        # The catalog in the ledger itself stays whole: recovery still finds every unread hit there.
        self.assertEqual(len(catalog), 3)

    def advised(self):
        return QuestionResearch(self.root, self.work, self.config, self.model, lambda activity: None, advisor=True)

    def rejecting(self, advice, recover=False):
        """Every answer is rejected and the advisor answers ``advice``. With ``recover`` the attempt after the
        advice searches the web, finds a new source and answers from it, and that answer passes."""
        seen = []
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"These sentences", b"Additional independent evidence. These sentences"), "text/html", url)
        def hook(prompt, schema, payload, kwargs):
            advised = any(call[0] is BlockAdvice for call in self.calls)
            if schema is BlockAdvice:
                self.assertTrue(kwargs.get("advisor"))
                return advice
            if schema is ResearchDecision and advised:
                seen.append(json.dumps(payload.get("feedback", []), ensure_ascii=False))
                if recover:
                    new = [section["reference"] for source in payload["sources"] for section in source["sections"]
                           if "Additional independent evidence" in section["text"] and "Models assign an energy" in section["text"]]
                    return decision("answer", answer=answer_for(new[0])) if new else decision("search_web", web_queries=["energy"])
            if schema is QuestionSearch and advised and recover:
                return QuestionSearch(candidates=fixtures.discovery(count=2).candidates[1:], limitations=[])
            if schema is AnswerReview and not (recover and advised):
                return AnswerReview(criteria=[dict(index=0, passed=False, reason="Mechanism absent")],
                                    supported=False, source_adequacy=False, issues=["Read the missing mechanism"])
        self.hook = hook
        return seen

    def test_the_advisor_starts_one_automatic_attempt_with_its_hint(self):
        advice = BlockAdvice(diagnosis="Der Verlag sperrt den Download.", recommendation="retry", limit="none",
                             hint="Freie Fassung in PubMed Central lesen.",
                             sources=[dict(title="Paper", url="https://pmc.example/paper", note="frei lesbar")])
        seen = self.rejecting(advice, recover=True)
        engine = self.advised()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        row = next(iter(engine.state["tasks"].values()))
        self.assertEqual((row["status"], row["auto_retries"], row["advice"]["recommendation"]), ("verified", 1, "retry"))
        self.assertEqual(sum(call[0] is BlockAdvice for call in self.calls), 1)
        # The new attempt reads the hint and the suggested source as feedback.
        self.assertTrue(seen and "PubMed Central" in seen[0] and "https://pmc.example/paper" in seen[0])

    def test_blocked_questions_are_advised_side_by_side_with_several_workers(self):
        # 2026-09-30: 18 blocked Asimov questions would have waited about an hour for one advisor call after another.
        import threading
        together = threading.Barrier(3)

        def hook(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(f"task_{name}", "empirical") for name in "abc"])
            if schema is ResearchDecision:
                return decision("blocked")
            if schema is BlockAdvice:
                together.wait(timeout=5)  # all three are inside the advisor at once, or this times out
                return BlockAdvice(diagnosis="Kein Zugang.", recommendation="accept_gap", limit="none", hint="", sources=[])
        self.hook = hook
        engine = QuestionResearch(self.root, self.work, self.config, self.model, lambda activity: None, advisor=True, workers=3)
        with self.assertRaises(AppError) as raised:
            engine.run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        self.assertEqual(sum(call[0] is BlockAdvice for call in self.calls), 3)
        rows = read_value(self.work / "question_research/state.json")["tasks"]
        self.assertEqual({row["advice"]["recommendation"] for row in rows.values()}, {"accept_gap"})

    def test_advice_against_a_new_attempt_stops_the_run_with_the_advice_stored(self):
        self.rejecting(BlockAdvice(diagnosis="Die geforderte Studie gibt es nicht frei.", recommendation="accept_gap",
                                   limit="none", hint="", sources=[]))
        with self.assertRaises(AppError) as raised:
            self.advised().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        row = public_ledger(read_value(self.work / "question_research/state.json"))["questions"][0]
        self.assertEqual((row["status"], row["auto_retries"]), ("blocked", 0))
        self.assertEqual(row["advice"]["diagnosis"], "Die geforderte Studie gibt es nicht frei.")

    def test_an_automatic_attempt_that_read_nothing_new_stops_the_automatic_ones(self):
        self.rejecting(BlockAdvice(diagnosis="Neue Quelle nötig.", recommendation="retry", limit="none",
                                   hint="Andere Suchbegriffe.", sources=[]))
        with self.assertRaises(AppError) as raised:
            self.advised().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        ledger = public_ledger(read_value(self.work / "question_research/state.json"))
        row = ledger["questions"][0]
        # One advice per block: the first started an attempt; that attempt read no new passage, so the second
        # advice waits for the editor instead of starting the same attempt again.
        self.assertEqual((row["status"], row["auto_retries"], row["auto_stop"]), ("blocked", 1, "no_progress"))
        self.assertEqual(sum(call[0] is BlockAdvice for call in self.calls), 2)
        self.assertEqual(ledger["auto_retry_limit"], 5)

    def test_the_ledger_names_each_question_s_still_open_audit_objections(self):
        from podcast_automate.question_scope import pending_task
        state = {"plan": {"tasks": [task_value("t1"), task_value("t2")]}, "phase": "questions", "audit_round": 2,
                 "tasks": {"t1": {**pending_task(), "status": "verified"}, "t2": {**pending_task(), "status": "verified"}},
                 "objections": {"o1": {"task_id": "t1", "rule": "support", "reason": "Beleg fehlt.", "status": "open"},
                                "o2": {"task_id": "t1", "rule": "claim_preservation", "reason": "Wortlaut.", "status": "open"},
                                "o3": {"task_id": "t2", "rule": "scope", "reason": "Umfang.", "status": "closed"}},
                 # The latest audit closed o2; o3 was closed when an earlier run completed.
                 "closed_objections": ["o2"]}
        rows = {row["id"]: row for row in public_ledger(state)["questions"]}
        self.assertEqual(rows["t1"]["objections"], [{"rule": "support", "reason": "Beleg fehlt."}])
        self.assertEqual(rows["t2"]["objections"], [])

    def test_automatic_attempts_stop_at_five_or_after_one_without_new_passages(self):
        from podcast_automate.research_advisor import automatic_retry
        self.assertEqual(automatic_retry({"auto_retries": 0, "read_refs": []}), (True, None))
        # Each automatic attempt that read something new earns the next one, up to five.
        self.assertEqual(automatic_retry({"auto_retries": 4, "auto_retry_read": 10, "read_refs": ["r"] * 12}), (True, None))
        self.assertEqual(automatic_retry({"auto_retries": 5, "auto_retry_read": 10, "read_refs": ["r"] * 12}), (False, "limit"))
        self.assertEqual(automatic_retry({"auto_retries": 2, "auto_retry_read": 12, "read_refs": ["r"] * 12}), (False, "no_progress"))

    def test_without_room_in_the_call_budget_the_advisor_is_not_asked(self):
        write_json(self.work / "budget.json", {"model_calls": self.config.research_limits.model_calls - 8, "search_rounds": 0})
        self.rejecting(BlockAdvice(diagnosis="x", recommendation="retry", limit="none", hint="", sources=[]))
        with self.assertRaises(AppError) as raised:
            self.advised().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        self.assertFalse(any(call[0] is BlockAdvice for call in self.calls))

    def test_advice_that_loses_the_last_search_round_to_a_parallel_one_goes_on_without_search(self):
        """Asimov, 2026-10-01: three advice calls decided to search with one round left; the two that lost the
        reservation stopped the whole run for an approval at 91 of 94 rounds. They go on without a search now."""
        import threading
        from podcast_automate.research import reserve_call
        limits = self.config.research_limits
        write_json(self.work / "budget.json", {"model_calls": 0, "search_rounds": limits.search_rounds - 1})
        together, reserved = threading.Barrier(3), []

        def hook(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(f"task_{name}", "empirical") for name in "abc"])
            if schema is ResearchDecision:
                return decision("blocked")
            if schema is BlockAdvice:
                if kwargs.get("search"):
                    together.wait(timeout=5)  # all three decided to search before any reserved its round
                # The production reservation (research.invoke): it refuses a search round the others took.
                reserve_call(self.work, limits, search=kwargs.get("search", False))
                reserved.append((payload["web_search"], kwargs.get("search", False)))
                return BlockAdvice(diagnosis="Kein Zugang.", recommendation="accept_gap", limit="none", hint="", sources=[])
        self.hook = hook
        engine = QuestionResearch(self.root, self.work, self.config, self.model, lambda activity: None, advisor=True, workers=3)
        with self.assertRaises(AppError) as raised:
            engine.run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        self.assertEqual(sorted(reserved), [(False, False), (False, False), (True, True)])
        rows = read_value(self.work / "question_research/state.json")["tasks"]
        self.assertEqual(sorted((row["advice"]["web_search"], row["advice"].get("search_fallback", False))
                                for row in rows.values()), [(False, True), (False, True), (True, False)])
        budget = json.loads((self.work / "budget.json").read_text(encoding="utf-8"))
        self.assertEqual((budget["search_rounds"], budget["model_calls"]), (limits.search_rounds, 3))
        # The request each advice was asked with is saved before its call, the fallback's in place of the first.
        requests = sorted(read_value(path)["search"] for path in
                          (self.work / "question_research/tasks").rglob("advice_*_request.json"))
        self.assertEqual(requests, [False, False, True])
        # Stopped after a fallback's receipt but before its row was saved: the resume replays that receipt.
        path = self.work / "question_research/state.json"
        state = read_value(path)
        fallback = next(tid for tid, row in state["tasks"].items() if row["advice"].get("search_fallback"))
        del state["tasks"][fallback]["advice"]
        save_value(path, state)
        count = sum(schema is BlockAdvice for schema, _ in self.calls)
        engine = QuestionResearch(self.root, self.work, self.config, self.model, lambda activity: None, advisor=True, workers=3)
        with self.assertRaises(AppError):
            engine.run(self.discovery, self.index)
        self.assertEqual(sum(schema is BlockAdvice for schema, _ in self.calls), count)
        self.assertTrue(engine.state["tasks"][fallback]["advice"]["search_fallback"])

    def test_the_advisor_reads_every_earlier_advice_with_what_became_of_it(self):
        """The design rule for repeated reviews: a question can be advised up to six times, and the advisor saw only
        the latest advice. It reads each earlier one now, with the attempt that followed and how that ended."""
        self.rejecting(BlockAdvice(diagnosis="Neue Quelle nötig.", recommendation="retry", limit="none",
                                   hint="Andere Suchbegriffe.", sources=[]))
        requests = []
        rejecting = self.hook

        def hook(prompt, schema, payload, kwargs):
            if schema is BlockAdvice:
                requests.append(payload)
            return rejecting(prompt, schema, payload, kwargs)
        self.hook = hook
        with self.assertRaises(AppError):
            self.advised().run(self.discovery, self.index)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["earlier_advice"], [])
        (earlier,) = requests[1]["earlier_advice"]
        self.assertEqual((earlier["recommendation"], earlier["hint"], earlier["key"]), ("retry", "Andere Suchbegriffe.", "0.0"))
        self.assertEqual(earlier["result"]["new_attempt"], "automatic")
        self.assertEqual(earlier["result"]["new_sections_read"], 0)
        self.assertTrue(earlier["result"]["ended_as"].endswith("_block"))
        self.assertTrue(all(version.endswith(".block_advice.v2") for schema, version in self.calls if schema is BlockAdvice))
        row = read_value(self.work / "question_research/state.json")["tasks"]["task_definition"]
        self.assertEqual([entry["key"] for entry in row["advice_history"]], ["0.0"])
        self.assertEqual(row["advice"]["key"], "0.1")

    def test_a_revalidated_answer_sends_its_whole_chain_back_and_may_return_unchanged(self):
        """2026-10-02: a resume revalidated a stale task B but left C, which builds on B, verified against B's old
        answer; and the revalidated B was told its unchanged correct answer had "already failed independent review"."""
        def chain(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value("task_a"), dict(task_value("task_b", "mechanism"), depends_on=["task_a"]),
                                           dict(task_value("task_c", "example"), depends_on=["task_b"])])
        self.hook = chain
        self.engine().run(self.discovery, self.index)
        path = self.work / "question_research/state.json"
        state = read_value(path)
        answer_b = state["tasks"]["task_b"]["answer"]
        # B was verified against an earlier answer of A; C against B's current one.
        state["tasks"]["task_b"]["verification"]["prerequisite_hashes"] = {"task_a": "0" * 64}
        state["tasks"]["task_b"].update(answer_locked=True, revise_only=True, lock={"step": 1})
        save_value(path, state)
        resumed = self.engine()
        resumed.initialise(self.discovery, self.index, None, ())
        rows = resumed.state["tasks"]
        self.assertEqual({tid: (row["status"], row.get("dependency_revision", 0)) for tid, row in rows.items()},
                         {"task_a": ("verified", 0), "task_b": ("researching", 1), "task_c": ("researching", 1)})
        self.assertEqual((rows["task_b"]["resubmit"], rows["task_b"]["answer_locked"], rows["task_b"]["revise_only"],
                          rows["task_b"]["lock"]), (digest(answer_b), False, False, None))
        # The completed round's receipts answered the old answers: a new audit round of its own.
        self.assertEqual((resumed.state["phase"], resumed.state["audit_round"]), ("questions", 1))
        count = len(self.calls)
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual([row["status"] for row in engine.state["tasks"].values()], ["verified"] * 3)
        # Each is answered once more, unchanged, and passes its review.
        new = [schema for schema, _ in self.calls[count:] if schema in (ResearchDecision, AnswerReview)]
        self.assertEqual(new, [ResearchDecision, AnswerReview] * 2)
        self.assertEqual(engine.state["tasks"]["task_b"]["answer"], answer_b)
        self.assertEqual(engine.state["tasks"]["task_c"]["verification"]["prerequisite_hashes"],
                         {"task_b": digest(engine.state["tasks"]["task_b"]["answer"])})

    def test_a_tightened_rule_sends_a_stored_verified_answer_back_instead_of_stopping_the_resume(self):
        # 2026-10-02: a stored verified answer that a later, stricter check failed stopped every resume for good.
        self.engine().run(self.discovery, self.index)
        with patch("podcast_automate.question_research.review_passes", return_value=False):
            resumed = self.engine()
            resumed.initialise(self.discovery, self.index, None, ())
        row = resumed.state["tasks"]["task_definition"]
        self.assertEqual((row["status"], row["dependency_revision"]), ("researching", 1))
        self.assertIn("no longer passes the review rules", row["feedback"][0])
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual((engine.state["phase"], engine.state["tasks"]["task_definition"]["status"]), ("completed", "verified"))

    def test_a_run_never_completes_with_a_question_still_open(self):
        """Transformer, 2026-10-02: a routing that reopened nothing had sent two dependents back, and the run was
        published as completed with both still researching. They are answered first now; and a question still open
        before the dossier stops the run instead of completing it."""
        from podcast_automate.question_dependencies import revalidate
        engine = self.engine()
        audits, real_audit = [], engine.audit

        def audit(dossier, discovery, context):
            dossier, review, report = real_audit(dossier, discovery, context)
            audits.append(engine.state["audit_round"])
            return dossier, review, {**report, "passed": len(audits) > 1}

        def reopen(dossier, review, report):
            # As invalidate_dependents did beside a question noted after its reworks: a revalidation, nothing reopened.
            revalidate(engine.state["tasks"]["task_definition"])
            engine.state.update(audit_round=engine.state["audit_round"] + 1, phase="questions")
            return [], []
        with patch.object(engine, "audit", side_effect=audit), patch.object(engine, "reopen", side_effect=reopen):
            engine.run(self.discovery, self.index)
        self.assertEqual((engine.state["phase"], audits), ("completed", [0, 1]))
        self.assertEqual(engine.state["tasks"]["task_definition"]["status"], "verified")
        self.assertEqual(sum(schema is ResearchDecision for schema, _ in self.calls), 2)
        # A question left open by whatever means: no dossier, no completion.
        self.work = self.root / "runs/run_open"
        engine = self.engine()
        real_tasks = engine.research_tasks

        def leave_open():
            real_tasks()
            engine.state["tasks"]["task_definition"]["status"] = "researching"
        with patch.object(engine, "research_tasks", side_effect=leave_open):
            with self.assertRaises(AppError) as raised:
                engine.run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_open")
        self.assertNotEqual(read_value(self.work / "question_research/state.json")["phase"], "completed")
        self.assertFalse((self.work / "complete_research").exists())

    def test_a_residual_finish_keeps_objections_an_earlier_assembled_round_closed_closed(self):
        # 2026-10-02: an assembled audit records no objection checks, so every objection still marked open became
        # residual, those an earlier round had closed included.
        self.engine().run(self.discovery, self.index)
        path = self.work / "question_research/state.json"
        state = read_value(path)
        objection = {"task_id": "task_definition", "rule": "support", "reason": "Beleg fehlt.", "status": "open"}
        state.update(objections={"obj_closed": {**objection, "id": "obj_closed"}, "obj_open": {**objection, "id": "obj_open"}},
                     closed_objections=["obj_closed"])
        save_value(path, state)
        finish = {"note": "Mit dieser Grenze.", "approved_at": "2026-10-02T10:00:00Z"}
        engine = QuestionResearch(self.root, self.work, self.config, self.model, lambda activity: None, residual=lambda: finish)
        real_audit = engine.audit

        def audit(dossier, discovery, context):
            dossier, review, report = real_audit(dossier, discovery, context)
            self.assertTrue(dossier.assembled)
            return dossier, review, {**report, "passed": False}
        with patch.object(engine, "audit", side_effect=audit):
            engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual({oid: row["status"] for oid, row in engine.state["objections"].items()},
                         {"obj_closed": "closed", "obj_open": "residual"})

    def test_run_folder_paths_of_attempts_and_searches_come_from_one_helper(self):
        """2026-10-02: the attempt restore missed downloads below dependency_* and search_<hash>, and the call
        projection looked for a second search of one step in the step folder instead of its own."""
        from podcast_automate.question_budget import remaining_calls
        from podcast_automate.question_scope import pending_task
        from podcast_automate.question_sources import attempt_folder, restore_attempts, source_identity
        folder = self.work / "question_research"
        urls = {"tasks/t/attempt_0/step_001/downloads.json": "https://a.example/one",
                "tasks/t/attempt_1/dependency_2/step_000/downloads.json": "https://b.example/two",
                "tasks/t/attempt_0/step_003/search_abcdef12/downloads.json": "https://c.example/three"}
        for relative, url in urls.items():
            save_value(folder / relative, {"processed": [url], "attempted": [url]})
        self.assertTrue({source_identity(url) for url in urls.values()} <= restore_attempts(folder, self.index))
        row = {**pending_task(), "status": "researching", "step": 3, "dependency_revision": 2,
               "pending": decision("search_web", web_queries=["second query"]).model_dump()}
        self.assertEqual(attempt_folder(folder, "t", row), folder / "tasks/t/attempt_0/dependency_2")
        step = folder / "tasks/t/attempt_0/dependency_2/step_003"
        save_value(step / "search_request.json", {"prompt": "x\n" + json.dumps({"queries": ["first query"]}), "maximum": 4})
        state = {"phase": "questions", "tasks": {"t": row}, "audit_round": 0, "prompt_generation": 3,
                 "dirty_tasks": [], "seed_dossier": None}
        questions, _ = remaining_calls(state, folder)
        self.assertIn(step / f"search_{digest(['second query'])[:8]}" / "search.json", questions)
        self.assertNotIn(step / "search.json", questions)

    def test_exhausted_local_passages_trigger_one_focused_web_recovery(self):
        decisions = []
        def recover(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                decisions.append(1)
                if len(decisions) == 1:
                    return decision("blocked")
            if schema is QuestionSearch:
                self.assertTrue(kwargs["search"])
                return QuestionSearch(candidates=fixtures.discovery(count=2).candidates[1:], limitations=[])
        self.hook = recover
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"These sentences", b"Additional independent evidence. These sentences"), "text/html", url)
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        self.assertEqual(len(engine.index.sources), 2)

    def test_failed_verification_cannot_publish_or_be_shown_as_completed(self):
        def reject(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                return AnswerReview(criteria=[dict(index=0, passed=False, reason="Mechanism absent")],
                                    supported=False, source_adequacy=False, issues=["Read the missing mechanism"])
        self.hook = reject
        with self.assertRaises(AppError) as raised:
            self.engine().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        public = public_ledger(read_value(self.work / "question_research/state.json"))
        self.assertEqual(public["closed"], 0)
        self.assertEqual(public["questions"][0]["answer"], "")
        self.assertLessEqual(sum(c[0] is ResearchDecision for c in self.calls), 10)
        self.assertFalse((self.work / "complete_research/dossier.json").exists())

    def test_no_progress_reads_are_bounded_and_no_blind_retry_on_resume(self):
        self.hook = lambda prompt, schema, payload, kwargs: decision("read", windows=[
            dict(reference=self.ref, before=0, after=0)]) if schema is ResearchDecision else None
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        calls = len(self.calls)
        self.assertLess(calls, 10)
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), calls)

    def test_rephrasing_rejected_answers_does_not_reset_no_progress_detection(self):
        drafts = []
        def rephrase(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                drafts.append(1)
                answer = answer_for(self.ref)
                answer.summary += f" Wording attempt {len(drafts)}."
                return decision("answer", answer=answer)
            if schema is AnswerReview:
                return AnswerReview(criteria=[dict(index=0, passed=False, reason="Original evidence still absent.")],
                                    supported=False, source_adequacy=False, issues=["Missing evidence"])
        self.hook = rephrase
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        self.assertEqual(len(drafts), 2)
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        # The reworded second draft was locked out of review: only the first one cost a review call.
        self.assertEqual(sum(c[0] is AnswerReview for c in self.calls), 1)

    def test_definition_stays_closed_when_empirical_question_remains_blocked(self):
        def separate(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchDecision and payload["task"]["kind"] == "empirical":
                return decision("blocked")
        self.hook = separate
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        state = read_value(self.work / "question_research/state.json")
        self.assertEqual(state["tasks"]["task_definition"]["status"], "verified")
        self.assertEqual(state["tasks"]["task_empirical"]["status"], "blocked")
        self.assertEqual(public_ledger(state)["closed"], 1)

    @composed_generation()
    def test_only_concretely_routed_answer_reopens(self):
        self.hook = lambda prompt, schema, payload, kwargs: QuestionPlan(tasks=[task_value(),
            task_value("task_empirical", "empirical")]) if schema is QuestionPlan else None
        engine = self.engine()
        engine.run(self.discovery, self.index)
        old = dict(engine.state["tasks"]["task_definition"])
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text())
        def route(prompt, schema, payload, kwargs):
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_empirical"], reason="Only validation is challenged.")
                    for i in range(len(payload["objections"]))])
        self.hook = route
        # Two objections, one per routing part: the parts merge back into the audit's objection list.
        with patch("podcast_automate.question_synthesis.ROUTING_PART_SIZE", 1):
            engine.reopen(dossier, SourceReview(issues=[], limitations=[]),
                          {"blocking_gaps": ["An empirical check is missing.", "A second empirical check is missing."],
                           "requirements": []})
        self.assertEqual(engine.state["tasks"]["task_definition"], old)
        empirical = engine.state["tasks"]["task_empirical"]
        self.assertEqual(empirical["status"], "researching")
        self.assertIsNone(empirical["answer"])
        self.assertEqual(len(empirical["reopenings"][0]["reason"]), 2)
        self.assertIn("empirical check", empirical["reopenings"][0]["reason"][0])
        self.assertIn("second empirical check", empirical["reopenings"][0]["reason"][1])
        audit = self.work / "question_research/synthesis/audit_00"
        merged = json.loads((audit / "routes_merged.json").read_text(encoding="utf-8"))
        self.assertEqual((merged["parts"], merged["objections_per_part"], [r["index"] for r in merged["routes"]["routes"]]),
                         (2, [1, 1], [0, 1]))
        self.assertTrue((audit / "routes_part_001.json").exists())
        self.assertFalse((audit / "routes.json").exists())
        ledger = public_ledger(engine.state)
        self.assertEqual((ledger["audit_round"], ledger["reopened"]), (1, 1))
        self.assertEqual(engine.last_activity, "Konkrete Einwände werden ihren ursprünglichen Recherchefragen zugeordnet")

    @composed_generation()
    def test_a_routed_review_disagreement_starts_no_research_and_the_other_objections_still_route(self):
        # Regression, Psychohistorie run of 2026-09-20: the router called a status-note complaint an
        # unsupported demand. The run stopped after registering the objections routed so far, so the
        # resumed audit rebuilt its prompts with them and refused every saved part as a changed input.
        self.hook = lambda prompt, schema, payload, kwargs: QuestionPlan(tasks=[task_value(),
            task_value("task_empirical", "empirical")]) if schema is QuestionPlan else None
        engine = self.engine()
        engine.run(self.discovery, self.index)
        old = dict(engine.state["tasks"]["task_definition"])
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text())

        def anchor(task_id, resolution):
            # A support defect still reopens; a completeness objection to a covered criterion is only noted.
            return ResearchObjection(id=f"obj_{resolution}", rule="support", task_id=task_id, criterion_index=None,
                finding_ids=["f_energy"], evidence_refs=[], missing_evidence=f"{resolution}: a check is missing.",
                reason="Named by the audit.", correction="Supply the check.",
                closure_condition="The check is met by read evidence.", resolution=resolution)
        plan = ReopenPlan(routes=[
            dict(index=0, task_ids=["task_definition"], reason="Only a status note disagrees with the answer.",
                 anchors=[anchor("task_definition", "review_disagreement")]),
            dict(index=1, task_ids=["task_empirical"], reason="Validation is challenged.",
                 anchors=[anchor("task_empirical", "research")])])

        def routed(folder, name, schema, prompt, *, validate=None, **kwargs):
            validate(plan, True)
            return plan

        with patch.object(engine, "call", side_effect=routed):
            reopened, blocked = engine.reopen(dossier, SourceReview(issues=[], limitations=[]),
                {"blocking_gaps": ["The scope note calls answered blocks unresearched.", "An empirical check is missing."],
                 "requirements": []})
        self.assertEqual((reopened, blocked), (["task_empirical"], []))
        self.assertEqual(engine.state["tasks"]["task_definition"], old)
        saved = read_value(self.work / "question_research/state.json")
        self.assertEqual([row["task_id"] for row in saved["objections"].values()], ["task_empirical"])
        self.assertEqual([row["task_id"] for row in saved["review_disagreements"]], ["task_definition"])
        # The registered objections and the next audit round are saved together, never one without the other.
        self.assertEqual((saved["audit_round"], saved["phase"]), (1, "questions"))

    def reopened_for_wording(self):
        """A completed two-task run whose audit objects to task_definition's wording only and to task_empirical
        with a wording and a research objection; returns the engine after routing."""
        self.hook = lambda prompt, schema, payload, kwargs: QuestionPlan(tasks=[task_value(),
            task_value("task_empirical", "empirical")]) if schema is QuestionPlan else None
        engine = self.engine()
        engine.run(self.discovery, self.index)
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text())

        def anchor(task_id, resolution):
            return ResearchObjection(id=f"obj_{task_id}_{resolution}", rule="claim_preservation", task_id=task_id,
                criterion_index=None, finding_ids=["f_energy"], evidence_refs=[self.ref], missing_evidence="",
                reason="The wording widens the source.",
                correction="Restore the source's limit.", closure_condition="The answer keeps the source's limit.",
                resolution=resolution)
        plan = ReopenPlan(routes=[
            dict(index=0, task_ids=["task_definition"], reason="Wording only.", anchors=[anchor("task_definition", "revise")]),
            dict(index=1, task_ids=["task_empirical"], reason="Wording and a missing test.",
                 anchors=[anchor("task_empirical", "revise"), anchor("task_empirical", "research")])])

        def routed(folder, name, schema, prompt, *, validate=None, **kwargs):
            validate(plan, True)
            return plan
        with patch.object(engine, "call", side_effect=routed):
            engine.reopen(dossier, SourceReview(issues=[], limitations=[]),
                          {"blocking_gaps": ["The wording widens the source.", "A test is missing."], "requirements": []})
        return engine

    def disputed_run(self):
        """The reworded run's next audit disputes task_definition's objection once; returns the stopped
        state's objection id, the decisions the resumed engines read and a factory for them."""
        engine = self.reopened_for_wording()
        disputed = next(oid for oid, row in engine.state["objections"].items() if row["task_id"] == "task_definition")
        decisions, raised, drafts = {}, [], {}

        def rework(prompt, schema, payload, kwargs):
            # Every reopened question hands in a new wording, so no draft repeats one that was already reviewed.
            if schema is ResearchDecision and payload["reopening"]:
                task = payload["task"]["id"]
                drafts[task] = drafts.get(task, 0) + 1
                answer = answer_for(self.ref)
                answer.summary += f" Reworded ({task}, {drafts[task]})."
                return decision("answer", answer=answer)
        self.hook = rework

        def model(prompt, schema, version="test", **kwargs):
            value, meta = self.model(prompt, schema, version, **kwargs)
            if schema is SourceReview and not raised and disputed in json.loads(prompt.splitlines()[-1]).get("open_objections", {}):
                raised.append(1)
                for check in value.objection_checks:
                    if check.objection_id == disputed:
                        check.verdict, check.reason = "review_disagreement", "The read passage now carries the wording."
            return value, meta

        def resumed():
            return QuestionResearch(self.root, self.work, self.config, model,
                                    lambda activity: write_json(self.work / "research_activity.json", {"activity": activity}),
                                    disputes=lambda: decisions)
        with self.assertRaises(AppError) as stopped:
            resumed().run(self.discovery, self.index)
        self.assertEqual(stopped.exception.code, "review_disagreement")
        saved = read_value(self.work / "question_research/synthesis/audit_01/review_disagreement.json")
        self.assertEqual((saved["objection_id"], saved["objection"]["task_id"]), (disputed, "task_definition"))
        # Without a decision a resume stops at the same place, from the saved review and without a call.
        calls = len(self.calls)
        with self.assertRaises(AppError):
            resumed().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), calls)
        return disputed, decisions, resumed

    @composed_generation()
    def test_finishing_with_residual_objections_ends_the_loop_with_them_on_record(self):
        # The editor ends the audit loop: the next audit's open objection is not reworked again but recorded.
        engine = self.reopened_for_wording()
        remaining = next(oid for oid, row in engine.state["objections"].items() if row["task_id"] == "task_definition")
        drafts = {}

        def rework(prompt, schema, payload, kwargs):
            if schema is ResearchDecision and payload["reopening"]:
                task = payload["task"]["id"]
                drafts[task] = drafts.get(task, 0) + 1
                answer = answer_for(self.ref)
                answer.summary += f" Reworded ({task}, {drafts[task]})."
                return decision("answer", answer=answer)
        self.hook = rework

        def model(prompt, schema, version="test", **kwargs):
            value, meta = self.model(prompt, schema, version, **kwargs)
            if schema is SourceReview and remaining in json.loads(prompt.splitlines()[-1]).get("open_objections", {}):
                for check in value.objection_checks:
                    if check.objection_id == remaining:
                        check.verdict, check.reason = "open", "The limit is still widened."
                value.issues = [SourceReviewIssue(finding_id="f_energy", reason="The limit is still widened.", resolution="revise",
                    search_queries=[], objection=ResearchObjection(id="obj_left", rule="support", task_id="", finding_ids=["f_energy"],
                        evidence_refs=[self.ref], missing_evidence="", reason="The limit is still widened.",
                        correction="Restore the limit.", closure_condition="The source's limit is kept.", resolution="revise"))]
            return value, meta
        finish = {"note": "Episode geht mit dieser Grenze in Produktion.", "approved_at": "2026-09-27T14:30:00Z"}
        before = len(self.calls)
        engine = QuestionResearch(self.root, self.work, self.config, model,
                                  lambda activity: write_json(self.work / "research_activity.json", {"activity": activity}),
                                  residual=lambda: finish)
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertFalse(any(schema is ReopenPlan for schema, _ in self.calls[before:]), "no further rework round")
        self.assertEqual(engine.state["objections"][remaining]["status"], "residual")
        self.assertTrue(all(row["status"] == "closed" for oid, row in engine.state["objections"].items() if oid != remaining))
        gate = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
        self.assertTrue(gate["passed"] and gate["passed_with_residual_objections"])
        self.assertFalse(gate["passed_with_accepted_gaps"], "no gap was accepted")
        self.assertIn("The limit is still widened.", gate["residual_objections"])
        report = (self.work / "research_quality.md").read_text(encoding="utf-8")
        self.assertIn("mit dokumentierten Resteinwänden abgeschlossen (Episode geht mit dieser Grenze in Produktion.)", report)
        self.assertIn("## Verbliebene Prüfeinwände\n", report)

    def test_a_question_whose_reworks_are_spent_gets_no_advice_and_no_automatic_attempt(self):
        # Asimov, 2026-09-27: the advisor reopened t02 and t04 a third time and stepped round the rework limit.
        engine = QuestionResearch(self.root, self.work, self.config, self.model,
                                  lambda activity: write_json(self.work / "research_activity.json", {"activity": activity}),
                                  advisor=True)
        engine.run(self.discovery, self.index)
        row = engine.state["tasks"]["task_definition"]
        row.update(status="blocked", outcome="audit_block", reason="Wiederholte Gesamtprüfung widerspricht dem Abschluss: x")
        advice = sum(schema is BlockAdvice for schema, _ in self.calls)
        self.assertFalse(engine.advise())
        self.assertEqual(sum(schema is BlockAdvice for schema, _ in self.calls), advice)
        self.assertEqual((row["status"], row.get("advice")), ("blocked", None))
        # An ordinary research block is still advised.
        row.update(outcome="evidence_block")
        engine.advise()
        self.assertEqual(sum(schema is BlockAdvice for schema, _ in self.calls), advice + 1)

    def test_a_question_waiting_on_a_prerequisite_is_taken_up_once_it_passes(self):
        # Asimov, 2026-09-27: t15 stayed blocked after its prerequisite t14 had passed a later attempt.
        self.hook = lambda prompt, schema, payload, kwargs: QuestionPlan(tasks=[task_value(),
            dict(task_value("task_follow", "empirical"), depends_on=["task_definition"])]) if schema is QuestionPlan else None
        engine = self.engine()
        engine.run(self.discovery, self.index)
        follow = engine.state["tasks"]["task_follow"]
        follow.update(status="blocked", outcome="prerequisite_block", reason="A required prerequisite has not passed evidence review.")
        engine.state["tasks"]["task_definition"]["accepted_gap"] = {"reason": "x"}
        engine.state["tasks"]["task_definition"]["status"] = "blocked"
        self.assertEqual(engine.release_ready(), [], "a prerequisite accepted as a gap keeps it blocked")
        engine.state["tasks"]["task_definition"].update(status="verified", accepted_gap=None)
        self.assertEqual(engine.release_ready(), ["task_follow"])
        self.assertEqual((follow["status"], follow["outcome"]), ("pending", None))

    def test_a_plan_whose_opening_question_holds_up_most_of_it_is_asked_again(self):
        # Ontologies, 2026-09-30: one framing question held up all 58 others and blocked, so nothing else could run.
        chain = [task_value()] + [dict(task_value(f"task_part_{n}", "mechanism"), depends_on=["task_definition"]) for n in range(6)]
        flat = [task_value()] + [task_value(f"task_part_{n}", "mechanism") for n in range(6)]
        prompts = []

        def plan(answer):
            def hook(prompt, schema, payload, kwargs):
                if schema is QuestionPlan:
                    prompts.append(prompt)
                    return QuestionPlan(tasks=answer(len(prompts)))
            return hook
        self.hook = plan(lambda number: chain if number == 1 else flat)
        engine = self.engine()
        engine.initialise(self.discovery, self.index, None, [])
        self.assertEqual(len(prompts), 2)
        self.assertIn("task_definition (6 von 7)", prompts[1])
        self.assertEqual([t["depends_on"] for t in engine.state["plan"]["tasks"]], [[]] * 7)
        # A planner that keeps the gate still reaches the plan gate with its last answer instead of stopping the run.
        prompts.clear()
        self.work = self.root / "runs/run_stubborn"
        self.hook = plan(lambda number: chain)
        engine = self.engine()
        engine.initialise(self.discovery, self.index, None, [])
        self.assertEqual(len(prompts), 3)
        self.assertEqual(engine.state["plan"]["tasks"][1]["depends_on"], ["task_definition"])

    def test_a_residual_finish_returns_a_question_waiting_on_spent_prerequisites_to_its_last_answer(self):
        # Asimov, 2026-09-27: t15 was reopened in the last rework but waited for t14, whose reworks were spent.
        self.hook = lambda prompt, schema, payload, kwargs: QuestionPlan(tasks=[task_value(),
            dict(task_value("task_follow", "empirical"), depends_on=["task_definition"])]) if schema is QuestionPlan else None
        engine = self.engine()
        engine.run(self.discovery, self.index)
        first, follow = engine.state["tasks"]["task_definition"], engine.state["tasks"]["task_follow"]
        answer, verification = follow["answer"], follow["verification"]
        follow.update(status="blocked", outcome="prerequisite_block", answer=None, draft_answer=answer,
                      reopenings=[{"reason": ["x"], "previous_answer": answer, "previous_verification": verification}])
        # A prerequisite still being reworked: nothing to settle yet.
        first.update(status="researching", outcome=None)
        self.assertEqual(engine.settle_residual(), [])
        self.assertEqual(follow["status"], "blocked")
        # Its reworks spent: the dependent returns to the answer and verification it had.
        first.update(status="blocked", outcome="audit_block")
        self.assertEqual(engine.settle_residual(), ["task_follow"])
        self.assertEqual((follow["status"], follow["answer"], follow["verification"]), ("verified", answer, verification))

    @composed_generation()
    def test_following_the_reviewer_closes_the_disputed_objection_on_record(self):
        disputed, decisions, resumed = self.disputed_run()
        decisions[disputed] = {"objection_id": disputed, "decision": "reviewer", "note": "Die Stelle trägt die Formulierung.",
                               "decided_at": "2026-09-27T12:40:00Z"}
        before = [c for c in self.calls]
        engine = resumed()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        after = self.calls[len(before):]
        self.assertFalse(any(schema in (ReopenPlan, ResearchDecision) for schema, _ in after), "nothing is researched again")
        self.assertEqual(engine.state["objections"][disputed]["status"], "closed")
        self.assertEqual(engine.state["disputed_objections"][disputed]["decision"], "reviewer")
        report = (self.work / "research_quality.md").read_text(encoding="utf-8")
        self.assertIn("## Strittige Prüfeinwände", report)
        self.assertIn("dem Prüfer gefolgt, Einwand geschlossen (Die Stelle trägt die Formulierung.)", report)
        self.assertIn("The read passage now carries the wording.", report)

    @composed_generation()
    def test_an_upheld_objection_goes_back_to_its_question_as_it_stands(self):
        disputed, decisions, resumed = self.disputed_run()
        decisions[disputed] = {"objection_id": disputed, "decision": "objection", "note": "",
                               "decided_at": "2026-09-27T12:40:00Z"}
        before = len(self.calls)
        engine = resumed()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        after = self.calls[before:]
        # The upheld objection needs no router: it returns to its question word for word, once.
        self.assertFalse(any(schema is ReopenPlan for schema, _ in after))
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual(len(row["reopenings"]), 2)
        self.assertTrue(row["reopenings"][-1]["reason"][0].startswith("Einwand von der Redaktion aufrechterhalten: The wording widens"))
        self.assertTrue(any(schema is ResearchDecision for schema, _ in after), "the question was corrected")
        self.assertEqual(engine.state["disputed_objections"][disputed]["decision"], "objection")
        self.assertIn("Einwand aufrechterhalten", (self.work / "research_quality.md").read_text(encoding="utf-8"))

    @composed_generation()
    def test_a_completeness_objection_to_a_treated_criterion_is_noted_not_researched_again(self):
        # 2026-10-01, the user's choice: the audit rounds did not settle, and 178 of Transformer's 220 objections said
        # a criterion its answer already treats was not quite complete. Such an objection is a limit on record; a
        # criterion the answer leaves without any finding is still researched.
        from podcast_automate.research_quality import render_quality
        self.hook = lambda prompt, schema, payload, kwargs: QuestionPlan(tasks=[task_value(),
            task_value("task_empirical", "empirical")]) if schema is QuestionPlan else None
        engine = self.engine()
        engine.run(self.discovery, self.index)
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text())
        engine.state["tasks"]["task_empirical"]["answer"]["criteria"][0]["finding_ids"] = []

        def criterion(task_id, text):
            return ResearchObjection(id=f"obj_{task_id}", rule="criterion", task_id=task_id, criterion_index=0,
                finding_ids=[], evidence_refs=[], missing_evidence=text, reason=text, correction="Complete the criterion.",
                closure_condition="The criterion is met in full by read evidence.", resolution="research")
        plan = ReopenPlan(routes=[
            dict(index=0, task_ids=["task_definition"], reason="Not quite complete.", anchors=[criterion("task_definition", "A second case is missing.")]),
            dict(index=1, task_ids=["task_empirical"], reason="Not treated at all.", anchors=[criterion("task_empirical", "No finding treats it.")])])

        def routed(folder, name, schema, prompt, *, validate=None, **kwargs):
            validate(plan, True)
            return plan
        with patch.object(engine, "call", side_effect=routed):
            reopened, blocked = engine.reopen(dossier, SourceReview(issues=[], limitations=[]),
                {"blocking_gaps": ["A second case is missing.", "No finding treats it."], "requirements": []})
        self.assertEqual((reopened, blocked), (["task_empirical"], []))
        self.assertEqual(engine.state["tasks"]["task_definition"]["status"], "verified")
        self.assertEqual([row["task_id"] for row in engine.state["noted_objections"].values()], ["task_definition"])
        self.assertEqual([row["task_id"] for row in engine.state["objections"].values()], ["task_empirical"])
        self.assertEqual(engine.noted_limits(), ["A second case is missing."])
        report = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
        self.assertIn("## Als Grenzen vermerkte Vollständigkeitseinwände", render_quality({**report, "noted_limits": engine.noted_limits()}))

    @composed_generation()
    def test_a_second_defect_of_a_finding_with_an_open_objection_is_its_own_objection(self):
        # Asimov, round two: eleven new defects of findings that already had an open objection of the same
        # rule were refused as a "changed closure condition", three times, which would have stopped the run.
        engine = self.reopened_for_wording()
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text())
        first = next((oid, row) for oid, row in engine.state["objections"].items() if row["task_id"] == "task_definition")
        second = ResearchObjection(id="obj_new", rule="claim_preservation", task_id="task_definition", criterion_index=None,
            finding_ids=["f_energy"], evidence_refs=[self.ref], missing_evidence="", reason="The example omits the source's unit.",
            correction="Name the unit.", closure_condition="The example names the unit the source uses.", resolution="revise")
        plan = ReopenPlan(routes=[dict(index=0, task_ids=["task_definition"], reason="A second wording defect.", anchors=[second])])

        def routed(folder, name, schema, prompt, *, validate=None, **kwargs):
            validate(plan, True)
            return plan
        with patch.object(engine, "call", side_effect=routed):
            engine.reopen(dossier, SourceReview(issues=[], limitations=[]),
                          {"blocking_gaps": ["The example omits the source's unit."], "requirements": []})
        rows = {oid: row for oid, row in engine.state["objections"].items() if row["task_id"] == "task_definition"}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[first[0]]["closure_condition"], first[1]["closure_condition"], "the first stays as it was")
        self.assertIn("The example names the unit the source uses.", [row["closure_condition"] for row in rows.values()])
        # The same defect restated keeps its id.
        self.assertEqual(engine.existing_objection(engine.state["objections"], set(), second, "obj_other"),
                         next(oid for oid, row in rows.items() if oid != first[0]))

    @composed_generation()
    def test_a_wording_objection_reopens_its_question_only_to_correct_the_answer(self):
        engine = self.reopened_for_wording()
        wording, mixed = engine.state["tasks"]["task_definition"], engine.state["tasks"]["task_empirical"]
        # Only-revise objections: the reader may only answer, from the passages the answer and the objection cite.
        self.assertEqual((wording["status"], wording["revise_only"]), ("researching", True))
        self.assertEqual(engine.allowed_actions(wording), ["answer"])
        self.assertIn(self.ref, wording["current_refs"])
        self.assertTrue(wording["feedback"][-1].startswith("Correction only"))
        # A research objection among them keeps the ordinary reopening with search and reading.
        self.assertFalse(mixed["revise_only"])
        self.assertIn("search_web", engine.allowed_actions(mixed))
        offered = []
        def correct(prompt, schema, payload, kwargs):
            if schema is ResearchDecision and payload["task"]["id"] == "task_definition":
                offered.append(payload["allowed_actions"])
                answer = answer_for(self.ref)
                answer.summary += " Limited to the configurations the source names."
                return decision("answer", answer=answer)
        self.hook = correct
        task = next(t for t in QuestionPlan.model_validate(engine.state["plan"]).tasks if t.id == "task_definition")
        engine.research_task(task)
        # One reader call and the independent review: verified again without another research round.
        self.assertEqual(engine.state["tasks"]["task_definition"]["status"], "verified")
        self.assertEqual(offered, [["answer"]])

    @composed_generation()
    def test_a_rejected_correction_falls_back_to_an_ordinary_reopening(self):
        engine = self.reopened_for_wording()
        offered = []
        def reject(prompt, schema, payload, kwargs):
            if schema is ResearchDecision and payload["task"]["id"] == "task_definition":
                offered.append(payload["allowed_actions"])
                if len(offered) == 1:
                    answer = answer_for(self.ref)
                    answer.summary += " Still too broad."
                    return decision("answer", answer=answer)
                return decision("blocked")
            if schema is AnswerReview and payload["task"]["id"] == "task_definition":
                return AnswerReview(criteria=[dict(index=0, passed=False, reason="Still widens the source.")],
                                    supported=False, source_adequacy=False, issues=["Keep the source's limit."])
        self.hook = reject
        task = next(t for t in QuestionPlan.model_validate(engine.state["plan"]).tasks if t.id == "task_definition")
        engine.research_task(task)
        self.assertEqual(offered[0], ["answer"])
        # After the rejection the question may read and search again; the correction mode is over.
        self.assertIn("read", offered[1])
        self.assertFalse(engine.state["tasks"]["task_definition"]["revise_only"])

    @composed_generation()
    def test_reworked_answers_leave_source_wide_limits_to_one_closing_pass(self):
        # Regression, Asimov run of 2026-09-27: a reworked answer paraphrased more of a source that other findings
        # also cite. The batch's strict reference repair stopped the run instead of deferring that shared limit.
        from podcast_automate import question_synthesis
        real, seen, reviews = question_synthesis.repair_references, [], []

        def spy(folder, name, *args, **kwargs):
            seen.append((name, kwargs.get("allowed_ids") is not None, kwargs.get("defer_shared", False)))
            return real(folder, name, *args, **kwargs)

        def revise(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is SourceReview:
                reviews.append(1)
                if len(reviews) == 1:
                    return SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason="An empirical boundary is missing.",
                        resolution="research", search_queries=["independent test"])], limitations=[])
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_empirical"], reason="Empirical boundary only.")
                    for i in range(len(payload["objections"]))])
            if schema is ResearchDecision and payload["reopening"]:
                answer = answer_for(self.ref)
                answer.summary += " The empirical scope is restricted to the synthetic fixture."
                return decision("answer", answer=answer)
        self.hook = revise
        engine = self.engine()
        with patch("podcast_automate.question_synthesis.repair_references", side_effect=spy):
            engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        batches = [entry for entry in seen if entry[0].startswith("batch_")]
        self.assertTrue(batches, "the reworked answer was integrated in a batch")
        self.assertTrue(all(allowed and deferred for _, allowed, deferred in batches))
        self.assertIn(("reopened_references", False, False), seen)

    @composed_generation()
    def test_full_audit_reopens_only_empirical_task_then_rechecks_before_publish(self):
        reviews, reviewed_tasks = [], []
        def revise(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is SourceReview:
                reviews.append(1)
                if len(reviews) == 1:
                    return SourceReview(issues=[SourceReviewIssue(finding_id="f_energy", reason="An empirical boundary is missing.",
                        resolution="research", search_queries=["independent test"])], limitations=[])
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_empirical"], reason="Empirical boundary only.")
                    for i in range(len(payload["objections"]))])
            if schema is ResearchDecision and payload["reopening"]:
                answer = answer_for(self.ref)
                answer.summary += " The empirical scope is restricted to the synthetic fixture."
                return decision("answer", answer=answer)
            if schema is AnswerReview:
                reviewed_tasks.append(payload["task"]["id"])
        self.hook = revise
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(reviewed_tasks, ["task_definition", "task_empirical", "task_empirical"])
        self.assertEqual(len(reviews), 2)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(engine.state["audit_round"], 1)

    @composed_generation()
    def test_repeated_final_objections_remain_blocking_and_reopenings_are_bounded(self):
        drafts = []
        def reject(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                answer = answer_for(self.ref)
                drafts.append(1)
                answer.summary += f" Revision {len(drafts)}."
                return decision("answer", answer=answer)
            if schema is ResearchAssessment:
                report = fixtures.assessment_from_prompt(prompt)
                report.requirements[0].explanation = False
                report.requirements[0].reason = "A required mechanism is still absent."
                return report
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_definition"], reason="Required mechanism absent.")
                    for i in range(len(payload["objections"]))])
        self.hook = reject
        engine = self.engine()
        with self.assertRaises(AppError) as raised:
            engine.run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        self.assertEqual(len(engine.state["tasks"]["task_definition"]["reopenings"]), 2)
        self.assertEqual(len(drafts), 3)
        self.assertFalse((self.work / "complete_research/dossier.json").exists())

    def test_unread_or_uploaded_only_evidence_is_rejected(self):
        spec = QuestionPlan(tasks=[task_value()]).tasks[0]
        answer = answer_for(self.ref)
        reader = SourceReader(self.index)
        self.assertEqual(answer_errors(answer, spec, reader, set()),
                         [f"f_energy: evidence reference '{self.ref}' was not read for this question; "
                          "cite a section from read_refs or read it first."])
        source_id = self.ref.split("#")[0]
        self.assertEqual(answer_errors(answer_for(source_id), spec, reader, {self.ref}),
                         [f"f_energy: evidence reference '{source_id}' names a whole source; cite a read section "
                          "as <source_id>#<section_id> instead."])
        self.assertEqual([e.split(";")[0] for e in answer_errors(answer_for("src_none#sec_none"), spec, reader, {self.ref})],
                         ["f_energy: evidence reference 'src_none#sec_none' is not a known section"])
        local = self.index.model_copy(deep=True)
        local.sources[0].url = local.sources[0].final_url = ""
        self.assertTrue(any("notes alone" in e for e in answer_errors(answer, spec, SourceReader(local), {self.ref})))
        answer.criteria[0].index = 1
        self.assertTrue(any("criterion" in e for e in answer_errors(answer, spec, reader, {self.ref})))

    def test_a_shortened_quote_is_rejected_with_the_ellipsis_named(self):
        # Asimov, 2026-09-30: the reader kept cutting quotes with "..." while the rejection only said "not verbatim".
        spec = QuestionPlan(tasks=[task_value()]).tasks[0]
        reader = SourceReader(self.index)
        words = reader.lookup[self.ref][2].text.split()
        for excerpt, named in ((" ".join(words[:3]) + " ... " + " ".join(words[5:8]), True),
                               (" ".join(words[:3]) + " invented", False)):
            answer = answer_for(self.ref)
            answer.findings[0].evidence[0].excerpt = excerpt
            with self.subTest(excerpt=excerpt):
                errors = answer_errors(answer, spec, reader, {self.ref})
                self.assertTrue(any("not verbatim" in e for e in errors))
                self.assertEqual(any("ellipsis" in e for e in errors), named)

    def test_plan_must_assign_all_original_requirements_and_known_gaps(self):
        plan = QuestionPlan(tasks=[task_value()])
        validate_plan(plan, self.config, self.discovery, None, {})
        self.config.focus_questions = ["Independent validation?"]
        with self.assertRaises(AppError):
            validate_plan(plan, self.config, self.discovery, None, {})
        self.config.focus_questions = []
        with self.assertRaises(AppError):
            validate_plan(plan, self.config, self.discovery, None, {"gap_one": "Original missing evidence"})

    def test_checkpoint_or_original_tampering_is_not_accepted(self):
        engine = self.engine()
        engine.run(self.discovery, self.index)
        path = self.work / "question_research/state.json"
        saved = json.loads(path.read_text())
        saved["value"]["tasks"]["task_definition"]["answer"]["summary"] = "Tampered"
        write_json(path, saved)
        with self.assertRaises(AppError) as raised:
            self.engine().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "invalid_research_checkpoint")
        # Even a re-hashed ledger must retain the independent verification binding.
        save_value(path, saved["value"])
        with self.assertRaises(AppError) as raised:
            self.engine().run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "invalid_research_checkpoint")

    def test_legacy_migration_reuses_downloads_and_formally_valid_latest_draft(self):
        reader = SourceReader(self.index)
        context = reader.read([ReaderWindow(reference=self.ref, before=0, after=0)])["context"]
        dossier = fixtures.dossier_from_prompt(json.dumps({"topic": "Test topic", "retrieved_sources": context}))
        folder = self.work / "completeness/round_002"
        save_value(folder / "retrieval.json", {"index": self.index.model_dump()})
        write_json(folder / "source_context.json", context)
        write_json(folder / "dossier_patch_applied.json", {"dossier": dossier.model_dump(), "dossier_hash": digest(dossier.model_dump())})
        result = bootstrap_legacy(self.root, self.work, self.discovery, self.index, None, [])
        self.assertEqual(result[2], dossier)
        self.assertEqual(result[1], self.index)
        count = self.download.call_count
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(self.download.call_count, count)
        self.assertEqual(engine.state["migration"]["drafts"], [str(Path("completeness/round_002/dossier_patch_applied.json"))])

    def test_reader_finds_english_definition_despite_uploaded_link_lists(self):
        original = self.index.sources[0].model_copy(deep=True)
        original.title = "Human Scale Development Max-Neef"
        original.sections = [SourceSection(id=f"sec_{n}", text=f"Max-Neef Human Scale Development introduction {n}") for n in range(40)]
        original.sections[27] = SourceSection(id="sec_definition", page=24,
            text="Singular satisfiers address one need. Synergic satisfiers also stimulate other needs.")
        notes = original.model_copy(deep=True)
        notes.id = "src_notes"
        notes.url = notes.final_url = ""
        notes.sections = [SourceSection(id="sec_links", text=("Max-Neef Human Scale Development " * 100) +
                                        " https://one.test https://two.test https://three.test")]
        reader = SourceReader(SourceIndex(sources=[notes, original], failures=[]))
        query = "Max-Neef Human Scale Development singular satisfiers synergic satisfiers definitions"
        result = reader.search(query, key_terms=["singular satisfiers", "synergic satisfiers"])
        self.assertEqual(result["candidates"][0]["reference"], original.id + "#sec_definition")
        self.assertFalse(any(c["role"] == "user_material" for c in result["candidates"]))
        last = reader.search(query, source_id=original.id, offset=24)
        self.assertEqual(last["offset"], 24)
        self.assertTrue(last["candidates"])
        read = reader.read([ReaderWindow(reference=original.id + "#sec_definition", before=1, after=1)])
        self.assertEqual(len(read["context"][0]["sections"]), 3)
        self.assertEqual(read["context"][0]["sections"][0]["page"], 24)
        bounded = reader.read([ReaderWindow(reference=original.id + "#sec_definition", before=1, after=1)], max_chars=100)
        self.assertTrue(bounded["deferred"])
        with self.assertRaises(AppError):
            reader.read([ReaderWindow(reference="../../secret", before=0, after=0)])

    def test_review_and_reader_prompts_carry_the_brevity_rule_under_the_loop_call_tag(self):
        prompts = {}
        def capture(prompt, schema, payload, kwargs):
            prompts.setdefault(schema, prompt)
        self.hook = capture
        self.engine().run(self.discovery, self.index)
        self.assertIn("one sentence that names the passage and the defect, at most 300 characters", prompts[AnswerReview])
        self.assertIn("at most 300 characters", prompts[ResearchDecision])
        self.assertIn("allowed_actions", prompts[ResearchDecision])
        self.assertIn((ResearchDecision, "question_research.v3-clauses.view.absence.reader"), self.calls)
        self.assertNotIn("demands a kind of evidence", prompts[AnswerReview], "no web search, no absence rule")

    def test_a_second_web_search_in_one_step_keeps_its_own_receipts(self):
        # Asimov, 2026-10-01: the recovery searched in step 31, the reader then chose its own search in the same step,
        # took the recovery's download receipt for its own and stopped on "passt nicht zum aktuellen Quellenindex".
        from podcast_automate.question_answering import search_folder
        step = self.work / "question_research/tasks/task_a/attempt_0/step_031"
        self.assertEqual(search_folder(step, ["energy"]), step, "the first search keeps the step folder")
        save_value(step / "search_request.json", {"prompt": "rules\n" + json.dumps({"queries": ["energy"]}), "maximum": 4})
        self.assertEqual(search_folder(step, ["energy"]), step, "a resumed search finds its receipts")
        other = search_folder(step, ["Pomeranz review"])
        self.assertEqual((other.parent, other), (step, search_folder(step, ["Pomeranz review"])))
        self.assertNotEqual(other, search_folder(step, ["de Vries review"]))

    def test_an_assessment_of_a_source_no_finding_cites_is_dropped_not_refused(self):
        # 2026-10-01: Ontologies' t18 assessed an invented "..._placeholder_unused" source, Asimov's asimov_evaluate an
        # uncited one; each refusal was asked again twice and then spent an automatic fresh attempt.
        def hook(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                review = question_response(prompt, schema)
                extra = review.source_assessments[0].model_copy(update={"source_id": "src_0000000000000000_placeholder_unused"})
                return review.model_copy(update={"source_assessments": [*review.source_assessments, extra]})
        self.hook = hook
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["tasks"]["task_definition"]["status"], "verified")
        self.assertFalse(list(self.work.rglob("review_*_rejected_*.json")))

    def test_a_provided_work_is_read_as_evidence_and_its_blocked_question_tried_again(self):
        # 2026-10-01: the books no free source offered come from the editor's library visit.
        from podcast_automate import provided_works
        from podcast_automate.research_models import is_idea
        explain = dict(task_value(), aim="explain", primary_works=["Kuran: Private Truths, Public Lies (1995)"])
        self.hook = lambda prompt, schema, payload, kwargs: (QuestionPlan(tasks=[explain]) if schema is QuestionPlan
                                                             else decision("blocked") if schema is ResearchDecision else None)
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        row = provided_works.add(self.root, fixtures.TEXT.encode(), citation="Kuran: Private Truths, Public Lies (1995)",
                                 tasks=["task_definition"])
        engine = self.engine()
        engine.initialise(self.discovery, self.index, None, [])
        self.assertEqual(engine.adopt_provided_works(), ["task_definition"])
        task = engine.state["tasks"]["task_definition"]
        source = next(s for s in engine.index.sources if s.citation)
        self.assertEqual((task["status"], task["provided_work"]), ("researching", row["id"]))
        self.assertIn(source.id, task["feedback"][0])
        self.assertEqual((source.title, source.source_type, source.published_date), (row["citation"], "primary_work", "1995"))
        self.assertFalse(is_idea(source), "a provided copy of a published work is evidence, not a note")
        self.assertEqual(engine.state["provided_works"][row["id"]]["source_id"], source.id)
        self.assertEqual(engine.adopt_provided_works(), [], "each work is read once")

    def test_the_web_search_of_an_explain_task_gets_no_recency_window(self):
        # Ontologies, 2026-10-01: the search for the 2014 W3C standards reported "recency filter cannot be met".
        self.config.recency_months = 6
        searched = {}

        def hook(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[dict(task_value(), aim="explain", primary_works=["W3C: RDF 1.1 Primer"]),
                                           task_value("task_follow", "empirical")])
            if schema is ResearchDecision and payload["task"]["id"] not in searched:
                return decision("search_web", web_queries=["energy model"])
            if schema is QuestionSearch:
                searched[payload["task"]["id"]] = payload
                self.assertIn("PsyArXiv", prompt, "the search names the open archives")
        self.hook = hook
        self.engine().run(self.discovery, self.index)
        self.assertNotIn("recency_months", searched["task_definition"])
        self.assertEqual(searched["task_follow"]["recency_months"], 6)

    def test_after_a_web_search_the_review_accepts_a_stated_absence_of_a_demanded_kind_of_evidence(self):
        # Ontologies, 2026-09-30: "only vendor estimates exist" failed "independent effort figures" and blocked.
        engine = self.engine()
        engine.run(self.discovery, self.index)
        spec = QuestionPlan.model_validate(engine.state["plan"]).tasks[0]
        row = engine.state["tasks"][spec.id]
        reviews = []
        self.hook = lambda prompt, schema, payload, kwargs: reviews.append(prompt) if schema is AnswerReview else None
        row.update(status="reviewing", step=row["step"] + 1, web_attempts=1)
        engine.verify(spec, row)
        self.assertIn("demands a kind of evidence", reviews[-1])
        self.assertIn((AnswerReview, f"question_research.v3-clauses.absence.review_{row['step']:03d}"), self.calls)
        self.assertEqual(row["status"], "verified")

    def test_the_reader_sees_what_it_read_earlier_for_this_question(self):
        # Transformer, 2026-09-30: with only the latest read in view, attention_qkv read 34 sections in 10 steps
        # and never answered; the passage it needed had left its prompt two steps before.
        from types import SimpleNamespace
        from podcast_automate import question_answering
        sizes = {"src#a": 100, "src#b": 200, "src#c": 300, "src#d": 400}
        reader = SimpleNamespace(lookup={ref: (None, 0, SimpleNamespace(text="x" * size)) for ref, size in sizes.items()})
        row = {"current_refs": ["src#d"], "read_refs": ["src#a", "src#b", "src#c", "src#d"]}
        self.assertEqual(question_answering.visible_refs(reader, row), ["src#d", "src#c", "src#b", "src#a"])
        # Within the budget the newest earlier passages stay; the latest read always stays whole.
        self.assertEqual(question_answering.visible_refs(reader, row, budget=750), ["src#d", "src#c"])
        self.assertEqual(question_answering.visible_refs(reader, row, budget=0), ["src#d"])
        with patch("podcast_automate.question_answering.visible_refs", wraps=question_answering.visible_refs) as seen:
            self.engine().run(self.discovery, self.index)
        self.assertTrue(seen.called, "the reader prompt takes its passages from visible_refs")
        self.assertTrue(any(s is AnswerReview and v.startswith("question_research.v3-clauses.review_") for s, v in self.calls))

    def failing_review(self, reason):
        return AnswerReview(criteria=[dict(index=0, passed=False, reason=reason)],
                            supported=True, source_adequacy=True, issues=[])

    def test_a_failed_review_locks_answering_and_a_fruitless_web_search_blocks_the_task(self):
        payloads = []
        def flow(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                payloads.append(payload)
                if "answer" in payload["allowed_actions"]:
                    return decision("answer", answer=answer_for(self.ref))
                return decision("search_web", web_queries=["freely accessible introduction"])
            if schema is AnswerReview:
                return self.failing_review("No freely accessible introductory source among the passages.")
        self.hook = flow
        engine = self.engine()
        with self.assertRaises(AppError) as raised:
            engine.run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        self.assertEqual(payloads[0]["allowed_actions"], READER_ACTIONS)
        self.assertIsNone(payloads[0]["answer_lock"])
        self.assertEqual(payloads[1]["allowed_actions"], ["search_local", "read", "search_web", "blocked"])
        self.assertEqual(payloads[1]["answer_lock"]["criteria"], [{"index": 0, "text": "Explain energy in this bounded example."}])
        self.assertIn("Criterion 0 not met", payloads[1]["feedback"][0])
        # One web search without new evidence is the escalation: no second recovery, no MAX_STEPS wait.
        self.assertEqual(len(payloads), 2)
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual((row["status"], row["outcome"], row["web_attempts"]), ("blocked", "evidence_block", 1))
        self.assertIn("Kriterium 0: Explain energy in this bounded example.", row["reason"])
        self.assertEqual(public_ledger(engine.state)["blocked"], 1)

    def test_an_answer_while_locked_is_not_reviewed_and_costs_at_most_one_call(self):
        reviews, payloads = [], []
        def flow(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                payloads.append(payload)
                answer = answer_for(self.ref)
                answer.summary += f" Wording {len(payloads)}."
                return decision("answer", answer=answer)
            if schema is AnswerReview:
                reviews.append(1)
                return self.failing_review("The mechanism is absent from the passage.")
        self.hook = flow
        engine = self.engine()
        with self.assertRaises(AppError):
            engine.run(self.discovery, self.index)
        self.assertEqual((len(reviews), len(payloads)), (1, 2))
        self.assertEqual(sum(c[0] is QuestionSearch for c in self.calls), 1)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual(row["locked_answers"], 1)
        self.assertEqual([a["action"] for a in row["actions"]], ["answer", "answer", "recovery_search_web"])
        self.assertEqual((row["status"], row["outcome"]), ("blocked", "evidence_block"))
        self.assertIn("Kriterium 0", row["reason"])

    def test_recovery_reads_saved_passages_first_and_searches_the_web_before_blocking(self):
        searches = []

        def flow(prompt, schema, payload, kwargs):
            if schema is QuestionSearch:
                searches.append(prompt)
                return QuestionSearch(candidates=[], limitations=["Nothing new in this fixture."])
        self.hook = flow
        engine = self.engine()
        engine.initialise(self.discovery, self.index, None, ())
        spec = QuestionPlan.model_validate(engine.state["plan"]).tasks[0]
        row = engine.state["tasks"][spec.id]
        with engine.guarded():
            engine.seed(spec, row)
            # A reader that answered or chose blocked at once leaves the catalogued passages unread.
            row["read_refs"], row["no_progress"] = [], 2
            self.assertTrue(engine.recover(spec, row))
            self.assertTrue(row["read_refs"])
            self.assertEqual((row["fallbacks"], row["web_attempts"], searches), (1, 0, []))
            # The second recovery is the web search, never a block while a web attempt is left.
            self.assertFalse(engine.recover(spec, row))
            self.assertEqual((row["fallbacks"], row["web_attempts"], len(searches)), (2, 1, 1))
            self.assertEqual(row["actions"][-1]["action"], "recovery_search_web")
            # The third is the concrete block, without another call.
            self.assertFalse(engine.recover(spec, row))
            self.assertEqual((row["fallbacks"], len(searches)), (3, 1))
        # The block names only what happened.
        row.update(answer_locked=True, lock={"criteria": [{"index": 0, "text": spec.acceptance[0]}], "reasons": []})
        engine.lock_block(spec, row)
        self.assertIn("auch die Websuche brachte keine neuen Belege", row["reason"])
        unsearched = {**row, "web_attempts": 0, "reason": ""}
        engine.lock_block(spec, unsearched)
        self.assertIn("die gespeicherten Quellen brachten keine neuen Belege", unsearched["reason"])
        self.assertNotIn("Websuche", unsearched["reason"])

    def test_a_task_blocked_without_a_web_attempt_is_resumed_with_one(self):
        engine = self.engine()
        engine.run(self.discovery, self.index)
        row = engine.state["tasks"]["task_definition"]
        # The block the earlier recovery rule produced: both fallbacks spent on saved passages, the web never searched.
        row.update(status="blocked", outcome="evidence_block", web_attempts=0, fallbacks=2, no_progress=2, pending=None,
                   answer=None, draft_answer=row["answer"], answer_locked=True,
                   lock={"step": row["step"], "web_attempts": 0, "finding_ids": [], "reasons": [],
                         "criteria": [{"index": 0, "text": "Explain energy in this bounded example."}]},
                   reason="Wiederholte Schritte lieferten keine neuen Belege oder geprüfte Antwort.")
        engine.state["phase"] = "blocked"
        engine.save()
        # The Studio learns from the ledger that a resume has something to do here.
        ledger = public_ledger(engine.state)
        self.assertEqual((ledger["reopenable"], ledger["questions"][0]["reopenable"], ledger["questions"][0]["web_attempts"]), (1, True, 0))
        searches = []

        def flow(prompt, schema, payload, kwargs):
            if schema is QuestionSearch:
                searches.append(prompt)
                return QuestionSearch(candidates=[], limitations=["Nothing new in this fixture."])
        self.hook = flow
        resumed = self.engine()
        with self.assertRaises(AppError) as raised:
            resumed.run(self.discovery, self.index)
        self.assertEqual(raised.exception.code, "research_questions_blocked")
        self.assertEqual(len(searches), 1)
        row = resumed.state["tasks"]["task_definition"]
        self.assertEqual((row["status"], row["outcome"], row["web_attempts"], row["fallbacks"]), ("blocked", "evidence_block", 1, 2))
        self.assertIn("auch die Websuche brachte keine neuen Belege", row["reason"])
        self.assertEqual(public_ledger(resumed.state)["reopenable"], 0)
        # Blocked under the current rule, a further resume repeats nothing.
        calls = len(self.calls)
        with self.assertRaises(AppError):
            self.engine().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), calls)

    def test_new_evidence_after_a_failed_review_unlocks_the_answer(self):
        payloads, reviews = [], []
        def flow(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                payloads.append(payload)
                if "answer" not in payload["allowed_actions"]:
                    return decision("search_web", web_queries=["independent evidence"])
                answer = answer_for(self.ref)
                if payload["previous_answer"]:
                    answer.summary += " Revised with the additional source."
                return decision("answer", answer=answer)
            if schema is AnswerReview:
                reviews.append(1)
                if len(reviews) == 1:
                    return self.failing_review("Independent evidence is absent.")
            if schema is QuestionSearch:
                return QuestionSearch(candidates=fixtures.discovery(count=2).candidates[1:], limitations=[])
        self.hook = flow
        self.download.side_effect = lambda url: (fixtures.HTML.replace(b"These sentences", b"Additional independent evidence. These sentences"), "text/html", url)
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        locked = [a for a in READER_ACTIONS if a != "answer"]
        self.assertEqual([p["allowed_actions"] for p in payloads], [READER_ACTIONS, locked, READER_ACTIONS])
        self.assertEqual(len(reviews), 2)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual((row["status"], row["answer_locked"]), ("verified", False))
        self.assertEqual(row["verification"]["limitations"], [])

    @composed_generation()
    def test_a_partially_supported_finding_passes_with_a_recorded_limitation_that_reaches_every_output(self):
        composed = []
        def partial(prompt, schema, payload, kwargs):
            if schema is AnswerReview:
                review = complete_fixture_response(AnswerReview(
                    criteria=[dict(index=0, passed=True, reason="The passage defines the assignment.")],
                    supported=False, source_adequacy=True, issues=["The summary sharpens 'assign' to 'always assign'."]), payload)
                review.finding_support[0].verdict = "partially_supported"
                review.finding_support[0].unsupported_clauses = ["in this fixture"]
                return review
            if schema is ResearchDossier:
                composed.append(payload)
        self.hook = partial
        engine = self.engine()
        engine.run(self.discovery, self.index)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual(row["status"], "verified")
        limitations = row["verification"]["limitations"]
        self.assertEqual([item["kind"] for item in limitations], ["partial_support", "issue", "not_fully_supported"])
        self.assertEqual(limitations[0]["finding_id"], "f_energy")
        self.assertIn("f_energy: nicht vollständig gestützt: in this fixture", limitations[0]["text"])
        self.assertEqual(limitations[1]["text"], "The summary sharpens 'assign' to 'always assign'.")
        self.assertEqual(public_ledger(engine.state)["questions"][0]["review_limitations"], limitations)
        self.assertEqual(composed[0]["verified_answers"][0]["review_limitations"], limitations)
        quality = (self.work / "research_quality.md").read_text(encoding="utf-8")
        self.assertIn("## Einschränkungen der Prüfung", quality)
        self.assertIn("### What is energy?", quality)
        self.assertIn("- f_energy: nicht vollständig gestützt: in this fixture", quality)
        self.assertIn("always assign", quality)
        self.assertEqual(engine.state["phase"], "completed")
        # The stored verification passes today's check on resume: no call is repeated.
        count = len(self.calls)
        self.engine().run(self.discovery, self.index)
        self.assertEqual(len(self.calls), count)

    def test_blocking_and_limitation_tiers_of_the_review_rule(self):
        spec = QuestionPlan(tasks=[task_value()]).tasks[0]
        answer = answer_for(self.ref)
        passages = read_context(SourceReader(self.index), [self.ref])
        receipts = support_receipts([f.model_dump() for f in answer.findings], passages)
        def review(**changes):
            return AnswerReview(**{"criteria": [dict(index=0, passed=True, reason="Defined in the passage.")], "supported": True,
                                   "source_adequacy": True, "issues": [], **json.loads(json.dumps(receipts)), **changes})
        self.assertEqual(review_outcome(review(), spec, answer.findings, passages), ([], []))
        partial = review()
        partial.finding_support[0].verdict = "partially_supported"
        partial.finding_support[0].unsupported_clauses = ["always"]
        blocking, limitations = review_outcome(partial, spec, answer.findings, passages)
        self.assertEqual(blocking, [])
        self.assertEqual((limitations[0]["finding_id"], limitations[0]["kind"]), ("f_energy", "partial_support"))
        self.assertIn("always", limitations[0]["text"])
        for field, value in (("verdict", "contradicted"), ("verdict", "insufficient_context"),
                             ("suitability", "unsuitable"), ("contract_preserved", False)):
            bad = review()
            bad.finding_support[0].unsupported_clauses = ["always"]
            setattr(bad.finding_support[0], field, value)
            with self.subTest(field=field, value=value):
                self.assertTrue(review_outcome(bad, spec, answer.findings, passages)[0])
        failed = review()
        failed.criteria[0].passed = False
        self.assertEqual(review_outcome(failed, spec, answer.findings, passages)[0], ["Criterion 0 not met: Defined in the passage."])
        self.assertTrue(review_outcome(review(source_adequacy=False), spec, answer.findings, passages)[0])
        self.assertFalse(review_passes(review(source_adequacy=False), spec))
        self.assertTrue(review_passes(review(supported=False, issues=["A wording issue."]), spec))

    def test_repeated_criterion_entries_collapse_and_only_missing_indices_still_reject(self):
        spec = QuestionPlan(tasks=[{**task_value(), "acceptance": ["A", "B", "C", "D"]}]).tasks[0]
        def review(indices):
            return AnswerReview(criteria=[dict(index=i, passed=True, reason="Checked.") for i in indices],
                                supported=True, source_adequacy=True, issues=[])
        # The trace's rejected shape: index 1 listed twice with identical verdicts.
        self.assertTrue(review_passes(review([0, 1, 1, 2, 3]), spec))
        self.assertEqual([c.index for c in normalise_review(review([0, 1, 1, 2, 3]), spec).criteria], [0, 1, 2, 3])
        for position in (1, 2):
            mixed = review([0, 1, 1, 2, 3])
            mixed.criteria[position].passed = False
            with self.subTest(failing_copy=position):
                self.assertFalse(review_passes(mixed, spec), "a differing repeat keeps the failing verdict")
        for indices in ([0, 1, 2, 4], [0, 1, 2], [0, 1, 2, 3, 4]):
            with self.subTest(indices=indices), self.assertRaises(AppError) as raised:
                review_passes(review(indices), spec)
            self.assertEqual(raised.exception.code, "invalid_question_review")
        one = QuestionPlan(tasks=[task_value()]).tasks[0]
        answer = answer_for(self.ref)
        answer.criteria = [answer.criteria[0], answer.criteria[0].model_copy(update={"explanation": "Repeated wording."})]
        answer.findings = answer.findings * 2
        normalised = normalise_answer(answer)
        self.assertEqual((len(normalised.criteria), len(normalised.findings)), (1, 1))
        self.assertEqual(answer_errors(normalised, one, SourceReader(self.index), {self.ref}), [])
        answer.findings[1] = answer.findings[1].model_copy(update={"statement": "A different finding under the same id."})
        self.assertTrue(answer_errors(normalise_answer(answer), one, SourceReader(self.index), {self.ref}))

    def test_the_sequential_ledger_names_the_task_in_progress_and_none_afterwards(self):
        ledgers = []

        def capture(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                ledgers.append(json.loads((self.work / "research_questions.json").read_text(encoding="utf-8")))
        self.hook = capture
        engine = self.engine()
        self.assertEqual(engine.workers, 1)
        engine.run(self.discovery, self.index)
        self.assertEqual([(row["active_task"], row["active_tasks"]) for row in ledgers], [("task_definition", ["task_definition"])])
        self.assertEqual(engine.state["active_tasks"], [])
        self.assertNotIn("active_task", engine.state)
        public = json.loads((self.work / "research_questions.json").read_text(encoding="utf-8"))
        self.assertEqual((public["active_task"], public["active_tasks"]), (None, []))
        # A ledger saved by an earlier version names one task; a resume carries it over as the list.
        path = self.work / "question_research/state.json"
        saved = read_value(path)
        del saved["active_tasks"]
        saved["active_task"] = None
        save_value(path, saved)
        count = len(self.calls)
        resumed = self.engine()
        resumed.run(self.discovery, self.index)
        self.assertEqual((len(self.calls), resumed.state["active_tasks"]), (count, []))
        self.assertNotIn("active_task", read_value(path))


    # --- the assembled dossier (prompt generation 3, 2026-10-01) ------------------------------------------------

    def test_the_dossier_is_assembled_from_every_verified_answer_without_a_composing_call(self):
        """The user's choice, 2026-10-01: a composed dossier kept at most 120 findings and dropped about two thirds
        of the verified research (Ontologies: 120 of 335); the dossier is now the sum of the answers, and the audit
        takes the receipts of their reviews instead of reviewing the findings again."""
        assessed = []

        def capture(prompt, schema, payload, kwargs):
            if schema is ResearchAssessment:
                assessed.append(payload)
        self.hook = capture
        engine = self.engine()
        engine.run(self.discovery, self.index)
        schemas = [schema for schema, _ in self.calls]
        self.assertEqual([s for s in schemas if s in (ResearchDossier, DossierPatch, SourceReview)], [])
        self.assertEqual(schemas.count(ResearchAssessment), 1)
        self.assertEqual(engine.state["prompt_generation"], 3)
        answer = engine.state["tasks"]["task_definition"]["answer"]
        dossier = ResearchDossier.model_validate_json((self.work / "complete_research/dossier.json").read_text(encoding="utf-8"))
        self.assertTrue(dossier.assembled)
        self.assertEqual([f.id for f in dossier.findings], ["task_definition__f_energy"])
        self.assertEqual(dossier.findings[0].model_dump(exclude={"id"}),
                         Finding.model_validate(answer["findings"][0]).model_dump(exclude={"id"}))
        self.assertEqual([(a.task_id, a.summary, a.finding_ids) for a in dossier.answers],
                         [("task_definition", answer["summary"], ["task_definition__f_energy"])])
        self.assertEqual([(c.question_id, c.status, c.finding_ids) for c in dossier.coverage],
                         [("q_energy", "answered", ["task_definition__f_energy"])])
        # The receipt is the answer review's, under the finding's dossier id.
        review = json.loads((self.work / "complete_research/source_review.json").read_text(encoding="utf-8"))
        verified = engine.state["tasks"]["task_definition"]["verification"]["review"]["finding_support"]
        self.assertEqual(review["finding_support"], [{**verified[0], "finding_id": "task_definition__f_energy"}])
        # The assessment reads the answers and their findings, not the excerpts.
        self.assertEqual(set(assessed[0]) - {"brief"}, {"answers", "findings", "coverage", "sources"})
        self.assertNotIn("evidence", assessed[0]["findings"][0])
        gate = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["dossier_hash"], digest(dossier.model_dump()))
        self.assertEqual(json.loads((self.work / "complete_research/dossier.json").read_text(encoding="utf-8"))["assembled"], True)
        self.assertIs(json.loads((self.work / "research_questions.json").read_text(encoding="utf-8"))["keeps_spent_answers"], True)

    def test_an_assembled_dossier_keeps_every_finding_and_no_source_word_limit(self):
        from podcast_automate.question_synthesis import assemble_dossier
        from podcast_automate.research import validate_dossier
        text = next(s.text for s in self.index.sources[0].sections if "Models assign an energy" in s.text)
        sentences = [s.strip() for s in text.split(".") if len(s.split()) >= 4]
        tasks, rows = [], {}
        for number in range(30):
            task = task_value(id=f"task_part_{number:02d}")
            tasks.append(task)
            # Every answer names its findings f_0 to f_4: ids inside an answer are local.
            rows[task["id"]] = {"answer": {"summary": f"Answer {number}.", "limits": [f"Limit {number}."], "findings": [
                dict(id=f"f_{k}", kind="claim", statement=f"Statement {number}-{k} " + " ".join(["about energies"] * 20),
                     claim_contract=claim_contract(), evidence=[dict(reference=self.ref, excerpt=sentences[k % len(sentences)])])
                for k in range(5)]}}
        gap = task_value(id="task_access")
        tasks.append(gap)
        rows[gap["id"]] = {"answer": None}
        plan = QuestionPlan(tasks=tasks)
        accepted = {"task_access": {"question": gap["question"], "question_ids": ["q_energy"], "reason": "Paywalled."}}
        dossier = assemble_dossier(self.discovery, plan, rows, accepted, "de-DE")
        self.assertEqual(len(dossier.findings), 150)
        self.assertEqual(len({f.id for f in dossier.findings}), 150)
        self.assertEqual(dossier.findings[5].id, "task_part_01__f_0")
        self.assertEqual(dossier.coverage[0].status, "partial")
        self.assertIn("Paywalled.", dossier.coverage[0].gap)
        self.assertEqual(dossier.open_questions, [])
        context = read_context(SourceReader(self.index), [self.ref])
        self.assertEqual(validate_dossier(dossier, self.discovery, context), [])
        # Stored and read back unchanged; a dossier a model composed still holds at most 120 findings.
        self.assertEqual(ResearchDossier.model_validate(dossier.model_dump()), dossier)
        with self.assertRaises(ValueError):
            ResearchDossier.model_validate({**dossier.model_dump(), "assembled": False, "answers": []})
        composed = ResearchDossier.model_validate({**dossier.model_dump(), "assembled": False, "answers": [],
                                                   "findings": dossier.model_dump()["findings"][:100]})
        errors = validate_dossier(composed, self.discovery, context)
        self.assertTrue(any("25 words" in e for e in errors) and any("150 words" in e for e in errors), errors)

    def test_an_assessment_objection_reopens_its_question_and_the_next_assembly_settles_it(self):
        assessments, routes = [], []

        def hook(prompt, schema, payload, kwargs):
            if schema is FollowUpAssessment:
                assessments.append(payload)
            if schema is ResearchAssessment:
                assessments.append(payload)
                report = fixtures.assessment_from_prompt(prompt)
                if len(assessments) == 1:
                    report.requirements[0].explanation = False
                    report.requirements[0].reason = "The mechanism is absent."
                return report
            if schema is ReopenPlan:
                routes.append(payload)
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_definition"], reason="Mechanism absent.")
                                          for i in range(len(payload["objections"]))])
            if schema is ResearchDecision and payload["reopening"]:
                answer = answer_for(self.ref)
                answer.summary += " Revised with the mechanism."
                return decision("answer", answer=answer)
        self.hook = hook
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual((engine.state["phase"], engine.state["audit_round"], len(assessments)), ("completed", 1, 2))
        # The second round judges again only what the rework changed (follow_up_assessment).
        self.assertEqual((assessments[1]["follow_up"]["changed_tasks"], assessments[1]["follow_up"]["requirements_in_scope"]),
                         (["task_definition"], ["rq_001"]))
        self.assertEqual(len(engine.state["tasks"]["task_definition"]["reopenings"]), 1)
        # The routing reads the answers and the findings' ids and passages, not their statements.
        self.assertEqual(len(routes), 1)
        self.assertNotIn("statement", routes[0]["dossier"]["findings"][0])
        self.assertEqual(routes[0]["answers"]["task_definition"]["finding_ids"], ["task_definition__f_energy"])
        self.assertIsNone(engine.state["seed_dossier"])
        (objection,) = engine.state["objections"].values()
        self.assertEqual((objection["task_id"], objection["status"]), ("task_definition", "closed"))
        dossier = json.loads((self.work / "complete_research/dossier.json").read_text(encoding="utf-8"))
        self.assertTrue(dossier["answers"][0]["summary"].endswith("Revised with the mechanism."))
        self.assertEqual([s for s, _ in self.calls if s in (ResearchDossier, DossierPatch, SourceReview)], [])

    def test_an_objection_raised_again_stays_open_and_one_left_out_is_closed(self):
        """An assembled audit checks no closure: the next assessment judges the whole again."""
        engine = self.engine()
        engine.run(self.discovery, self.index)
        old = {"id": "obj_old", "rule": "support", "task_id": "task_definition", "status": "open",
               "finding_ids": ["task_definition__f_energy"], "closure_condition": "Earlier condition."}
        engine.state["objections"] = {"obj_old": old}
        engine.state["phase"] = "audit"
        dossier, discovery, context = engine.compose()
        from podcast_automate.question_synthesis import assembled_review
        review = assembled_review(dossier, engine.state["tasks"], context)
        report = {"requirements": [], "blocking_gaps": ["A new gap."], "objection_checks": []}
        self.hook = lambda prompt, schema, payload, kwargs: ReopenPlan(routes=[dict(
            index=0, task_ids=["task_definition"], reason="New gap.")]) if schema is ReopenPlan else None
        reopened, blocked = engine.reopen(dossier, review, report)
        self.assertEqual((reopened, blocked), (["task_definition"], []))
        opened = [oid for oid, row in engine.state["objections"].items() if oid != "obj_old"]
        self.assertEqual(len(opened), 1)
        self.assertEqual(engine.state["closed_objections"], ["obj_old"])

    def test_the_call_projection_of_an_assembled_run_counts_only_its_assessment(self):
        from podcast_automate.question_budget import remaining_calls
        engine = self.engine()
        engine.run(self.discovery, self.index)
        state = {**engine.state, "phase": "audit", "audit_round": 1}
        _, closing = remaining_calls(state, self.work / "question_research")
        self.assertEqual(sorted(path.name for path in closing), ["assessment.json"])
        _, composed = remaining_calls({**state, "prompt_generation": 2, "seed_dossier": None}, self.work / "question_research")
        self.assertEqual(sorted(path.name for path in composed), ["assessment.json", "dossier.json", "grounding_0.json"])

    def test_a_composed_run_is_rebuilt_from_its_verified_answers_on_request(self):
        from podcast_automate.run_budget import approve_dossier_rebuild

        def stop(prompt, schema, payload, kwargs):
            if schema is ResearchAssessment:
                raise AppError("Stopped by the editor.", code="model_timeout", status="blocked")
        self.hook = stop
        with patch("podcast_automate.research.CodexAdapter.structured", side_effect=self.model):
            with patch("podcast_automate.question_research.PROMPT_GENERATION", 2):
                first = run_research(self.root)
            self.assertNotEqual(first.status, "completed")
            work = self.root / "runs" / first.run_id
            self.assertIn(ResearchDossier, [s for s, _ in self.calls])
            composed = {p.relative_to(work).as_posix(): file_hash(p) for p in (work / "question_research/synthesis/audit_00").rglob("*")
                        if p.is_file()}
            approval = approve_dossier_rebuild(self.root, first.run_id)
            self.assertEqual(approval.run_id, first.run_id)
            self.assertEqual(approve_dossier_rebuild(self.root, first.run_id), approval)
            self.hook = lambda *args: None
            count = len(self.calls)
            resumed = run_research(self.root, resume=True, run_id=first.run_id)
        self.assertEqual(resumed.status, "completed", resumed.model_dump())
        # Only the assessment of the assembled whole was asked; no question was researched again.
        self.assertEqual([s for s, _ in self.calls[count:]], [ResearchAssessment])
        state = read_value(work / "question_research/state.json")
        self.assertEqual((state["prompt_generation"], state["audit_round"]), (3, 1))
        self.assertEqual(state["rebuilds"][0]["from_audit_round"], 0)
        superseded = read_value(work / "question_research/synthesis/superseded_audit_00.json")
        self.assertEqual(superseded["prompt_generation"], 2)
        self.assertEqual(superseded["composed_findings"]["owners"], {"f_energy": ["task_definition"]})
        # The composed round's receipts stay as they were, beside the new round.
        self.assertEqual(composed, {p.relative_to(work).as_posix(): file_hash(p)
                                    for p in (work / "question_research/synthesis/audit_00").rglob("*") if p.is_file()})
        self.assertTrue((work / "question_research/synthesis/audit_01/assessment.json").exists())
        dossier = json.loads((work / "complete_research/dossier.json").read_text(encoding="utf-8"))
        self.assertEqual(([f["id"] for f in dossier["findings"]], dossier["assembled"]), (["task_definition__f_energy"], True))
        with self.assertRaises(AppError) as done:
            approve_dossier_rebuild(self.root, first.run_id)
        self.assertEqual(done.exception.code, "invalid_dossier_rebuild")


    def test_after_two_reworks_an_objection_is_noted_and_the_run_completes_with_the_last_verified_answer(self):
        """The user's choice, 2026-10-02: a question whose reworks are spent no longer stops the run. Before, every
        audit round blocked another such question, so Transformer stopped again after each decision."""
        drafts = []

        def reject(prompt, schema, payload, kwargs):
            if schema is ResearchDecision:
                answer = answer_for(self.ref)
                drafts.append(1)
                answer.summary += f" Revision {len(drafts)}."
                return decision("answer", answer=answer)
            if schema is ResearchAssessment:
                report = fixtures.assessment_from_prompt(prompt)
                report.requirements[0].explanation = False
                report.requirements[0].reason = "A required mechanism is still absent."
                return report
            if schema is FollowUpAssessment:
                return failing_follow_up(payload)
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_definition"], reason="Required mechanism absent.")
                                          for i in range(len(payload["objections"]))])
        self.hook = reject
        engine = self.engine()
        engine.run(self.discovery, self.index)
        row = engine.state["tasks"]["task_definition"]
        self.assertEqual((row["status"], len(row["reopenings"]), len(drafts)), ("verified", 2, 3))
        self.assertEqual(engine.state["phase"], "completed")
        (noted,) = [n for n in engine.state["noted_objections"].values() if n.get("basis") == "reworks_spent"]
        self.assertEqual(noted["task_id"], "task_definition")
        self.assertIn("A required mechanism is still absent.", noted["objection"])
        dossier = json.loads((self.work / "complete_research/dossier.json").read_text(encoding="utf-8"))
        self.assertTrue(dossier["answers"][0]["summary"].endswith("Revision 3."))
        gate = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
        self.assertTrue(gate["passed"])
        self.assertEqual([r["task_id"] for r in gate["noted_after_reworks"]], ["task_definition"])
        self.assertIn("Einwände nach zwei Nachbesserungen", (self.work / "research_quality.md").read_text(encoding="utf-8"))

    def test_a_spent_or_accepted_question_with_a_verified_answer_keeps_it_on_resume(self):
        """Questions a run stopped on before 2026-10-02: one blocked after its reworks and one the editor accepted as
        a gap while it still had its verified answer both return with that answer, their objection as a limit."""
        engine = self.engine()
        engine.run(self.discovery, self.index)
        path = self.work / "question_research/state.json"
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                state = read_value(path)
                row = state["tasks"]["task_definition"]
                row.update(status="blocked", outcome="accepted_gap" if accepted else "audit_block",
                           reason="Wiederholte Gesamtprüfung widerspricht dem Abschluss: Tabellenzahl prüfen.")
                if accepted:
                    row["accepted_gap"] = {"task_id": "task_definition", "reason": "", "approved_at": "2026-10-01T20:22:01Z"}
                state.update(phase="blocked", audit_round=state["audit_round"] + 1, noted_objections={})
                save_value(path, state)
                count = len(self.calls)
                resumed = self.engine()
                resumed.run(self.discovery, self.index)
                row = resumed.state["tasks"]["task_definition"]
                self.assertEqual((row["status"], resumed.state["phase"]), ("verified", "completed"))
                self.assertNotIn("accepted_gap", row)
                # Nothing changed since the last assessment and it had passed: the follow-up asks no call.
                self.assertEqual([s for s, _ in self.calls[count:]], [])
                (noted,) = resumed.state["noted_objections"].values()
                self.assertEqual((noted["basis"], noted["task_id"]), ("reworks_spent", "task_definition"))
                dossier = json.loads((self.work / "complete_research/dossier.json").read_text(encoding="utf-8"))
                self.assertEqual([f["id"] for f in dossier["findings"]], ["task_definition__f_energy"])

    def test_a_requirement_whose_five_criteria_are_met_passes_and_its_wishes_are_noted(self):
        """The user's choice, 2026-10-02: what the assessment still lists for a requirement it judges met on all five
        criteria is a limit, not missing research (Ontologies: 6 of 9 requirements failed only on such lists)."""
        def wishes(prompt, schema, payload, kwargs):
            if schema is ResearchAssessment:
                report = fixtures.assessment_from_prompt(prompt)
                report.requirements[0].missing = ["Person-hours for stewardship, which no source reports."]
                return report
        self.hook = wishes
        engine = self.engine()
        engine.run(self.discovery, self.index)
        gate = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
        self.assertTrue(gate["passed"])
        self.assertEqual((gate["requirements"][0]["missing"], gate["requirements"][0]["noted"]),
                         ([], ["Person-hours for stewardship, which no source reports."]))
        self.assertIn("Als Grenze vermerkt: Person-hours", (self.work / "research_quality.md").read_text(encoding="utf-8"))
        self.assertEqual([s for s, _ in self.calls].count(ReopenPlan), 0)


    def test_two_questions_spent_in_the_same_round_are_both_noted(self):
        """Transformer, 2026-10-02: the second spent question of a round looked up an objection the first had
        already moved to the noted ones, and the run stopped with a KeyError."""
        drafts = []

        def reject(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), task_value("task_empirical", "empirical")])
            if schema is ResearchDecision:
                drafts.append(payload["task"]["id"])
                answer = answer_for(self.ref)
                answer.summary += f" Revision {len(drafts)}."
                return decision("answer", answer=answer)
            if schema is ResearchAssessment:
                report = fixtures.assessment_from_prompt(prompt)
                report.requirements[0].explanation = False
                report.requirements[0].reason = "A required mechanism is still absent."
                return report
            if schema is FollowUpAssessment:
                return failing_follow_up(payload)
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_definition", "task_empirical"], reason="Absent.")
                                          for i in range(len(payload["objections"]))])
        self.hook = reject
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual({tid: (row["status"], len(row["reopenings"])) for tid, row in engine.state["tasks"].items()},
                         {"task_definition": ("verified", 2), "task_empirical": ("verified", 2)})
        noted = sorted(row["task_id"] for row in engine.state["noted_objections"].values() if row.get("basis") == "reworks_spent")
        self.assertEqual(noted, ["task_definition", "task_empirical"])
        self.assertEqual(sorted(drafts), ["task_definition"] * 3 + ["task_empirical"] * 3)
        self.assertEqual([o for o in engine.state["objections"].values() if o.get("status") == "open"
                          and o["id"] not in engine.state.get("closed_objections", [])], [])


    def test_a_follow_up_judges_only_what_changed_and_records_limits_instead_of_researching_them(self):
        """2026-10-02: each round judged the whole again, flipped unchanged verdicts and raised the same limits of the
        sources as new objections, so the audit never settled. A follow-up judges again only the requirements a
        changed answer serves; an unmet requirement whose gap is a limit of the sources, and a point about an
        unchanged answer, are recorded instead of reopening a question."""
        self.config.focus_questions = ["How is energy measured?"]
        rounds = []

        def two(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), {**task_value("task_empirical", "empirical"), "requirement_ids": ["rq_002"]}])
            if schema is ResearchDecision:
                answer = answer_for(self.ref)
                answer.summary += f" Draft {len(rounds)}."
                return decision("answer", answer=answer)
            if schema is ResearchAssessment:
                rounds.append(payload)
                return ResearchAssessment(requirements=[
                    dict(requirement_id="rq_001", finding_ids=["task_definition__f_energy"], direct_answer=True, explanation=False,
                         evidence=True, cross_check=True, boundaries=True, reason="The mechanism is absent.", missing=[], search_queries=[]),
                    dict(requirement_id="rq_002", finding_ids=["task_empirical__f_energy"], direct_answer=True, explanation=True,
                         evidence=True, cross_check=True, boundaries=True, reason="Met.", missing=[], search_queries=[])], issues=[])
            if schema is FollowUpAssessment:
                rounds.append(payload)
                follow_up = failing_follow_up(payload, reason="Only the abstract of the original is available.", remedy="limit")
                return FollowUpAssessment.model_validate({**follow_up.model_dump(), "issues": [dict(
                    text="The empirical answer names no sample size.", remedy="research", task_ids=["task_empirical"])]})
            if schema is ReopenPlan:
                return ReopenPlan(routes=[dict(index=i, task_ids=["task_definition"], reason="Mechanism absent.")
                                          for i in range(len(payload["objections"]))])
        self.hook = two
        engine = self.engine()
        engine.run(self.discovery, self.index)
        self.assertEqual(engine.state["phase"], "completed")
        self.assertEqual(len(rounds), 2)
        # Only the reworked question changed; the requirement only it serves is judged again, the other keeps its verdict.
        self.assertEqual((rounds[1]["follow_up"]["changed_tasks"], rounds[1]["follow_up"]["requirements_in_scope"]),
                         (["task_definition"], ["rq_001"]))
        self.assertEqual({tid: len(row["reopenings"]) for tid, row in engine.state["tasks"].items()},
                         {"task_definition": 1, "task_empirical": 0})
        gate = json.loads((self.work / "research_quality_gate.json").read_text(encoding="utf-8"))
        rows = {row["requirement_id"]: row for row in gate["requirements"]}
        self.assertEqual((rows["rq_001"]["passed"], rows["rq_001"].get("source_limit")), (False, True))
        self.assertTrue(rows["rq_002"]["passed"])
        self.assertEqual(gate["script_notes"], ["The empirical answer names no sample size."])
        self.assertTrue(gate["passed"])
        report = (self.work / "research_quality.md").read_text(encoding="utf-8")
        self.assertIn("Grenze der verfügbaren Quellen", report)
        self.assertIn("## Hinweise fürs Skript", report)
        # The scope is saved before the call, so a resume asks the same question.
        self.assertEqual(read_value(self.work / "question_research/synthesis/audit_01/assessment_scope.json")["requirements"], ["rq_001"])


    def test_a_spent_dependent_beside_its_reopened_prerequisite_is_revalidated_and_a_stale_one_heals_on_resume(self):
        """Ontologies, 2026-10-02: t53, noted after its reworks, stayed verified while its prerequisite t52 was reopened,
        so its answer was checked against t52's old answer, and the resume stopped for good on that."""
        from podcast_automate.question_synthesis import assembled_review

        def plan(prompt, schema, payload, kwargs):
            if schema is QuestionPlan:
                return QuestionPlan(tasks=[task_value(), {**task_value("task_synthesis", "synthesis"), "depends_on": ["task_definition"]}])
            if schema is ResearchDecision:
                return decision("answer", answer=answer_for(self.ref))
        self.hook = plan
        engine = self.engine()
        engine.run(self.discovery, self.index)
        spent = [{"reason": ["Earlier."], "previous_answer": None, "previous_verification": None}] * 2
        engine.state["tasks"]["task_synthesis"]["reopenings"] = spent
        engine.state["phase"] = "audit"
        dossier, discovery, context = engine.compose()
        review = assembled_review(dossier, engine.state["tasks"], context)
        report = {"requirements": [], "blocking_gaps": ["The definition and the synthesis disagree."], "objection_checks": []}
        self.hook = lambda prompt, schema, payload, kwargs: ReopenPlan(routes=[dict(
            index=0, task_ids=["task_definition", "task_synthesis"], reason="Disagree.")]) if schema is ReopenPlan else None
        reopened, blocked = engine.reopen(dossier, review, report)
        self.assertEqual((reopened, blocked), (["task_definition"], []))
        synthesis = engine.state["tasks"]["task_synthesis"]
        # Noted, not reworked, and revalidated against the reopened prerequisite.
        self.assertEqual((synthesis["status"], synthesis["dependency_revision"], len(synthesis["reopenings"])), ("researching", 1, 2))
        self.assertEqual([n["task_id"] for n in engine.state["noted_objections"].values()], ["task_synthesis"])
        # A run saved with the defect: the dependent kept its verified answer beside a reopened prerequisite.
        path = self.work / "question_research/state.json"
        state = read_value(path)
        state["tasks"]["task_synthesis"].update(status="verified", answer=synthesis["draft_answer"])
        save_value(path, state)
        resumed = self.engine()
        resumed.initialise(self.discovery, self.index, None, ())
        self.assertEqual(resumed.state["tasks"]["task_synthesis"]["status"], "researching")


if __name__ == "__main__":
    unittest.main()
