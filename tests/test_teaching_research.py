import json
import unittest
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript
from podcast_automate.research_models import ResearchDiscovery, SourceSection
from podcast_automate.scripting import episode_sources, load_research, outline_hash, run_script
from podcast_automate.storage import file_hash, read_yaml, write_json
from podcast_automate.teaching import ResearchGap, TeachingPlanReview
from podcast_automate.teaching_research import (FoundationSupplement, FoundationReview,
    apply_foundations, research_foundations, gaps_in)
from tests.test_research import HTML, discovery
from tests import test_scripting as fixtures


class FoundationResearchTests(unittest.TestCase):
    def setUp(self):
        fixture = self.fixture = fixtures.ScriptingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.root, self.config = fixture.root, fixture.config
        _, self.dossier, _, self.sources, self.context = load_research(self.root, self.config)
        self.entry = fixtures.example_plan().episodes[0]
        self.work = self.root / "runs/run_foundations"
        write_json(self.work / "teaching/ep_001/research_needed.json", {
            "episode_id": "ep_001", "questions": [{"question": "How are scores compared?", "why_needed": "The comparison needs evidence."}]})
        self.calls = []

    def test_internal_editorial_questions_never_trigger_external_research(self):
        write_json(self.work / "teaching/ep_001/research_needed.json", {
            "episode_id": "ep_001", "questions": [{"question": "What text did our previous episode use?",
                "why_needed": "Carry forward the internal illustration.", "kind": "editorial_context"}]})
        self.assertEqual(gaps_in(self.work), [])
        with self.assertRaises(AppError):
            self.research()
        self.assertEqual(self.calls, [])

    def invoke(self, prompt, schema, version, **kwargs):
        self.calls.append(schema)
        self.assertTrue(kwargs["research"])
        if schema is ResearchDiscovery:
            self.assertTrue(kwargs["search"])
            return discovery()
        if schema is FoundationReview:
            return FoundationReview(issues=[], scope_change_required=False)
        data = json.loads(prompt.splitlines()[-1])
        section = next(s for source in data["sources"] for s in source["sections"] if "Lower energy" in s["text"])
        return FoundationSupplement(explanations=[{
            "questions": data["questions"], "finding_ids": ["f_energy"],
            "explanation": "A lower score represents a better match in this example.",
            "evidence": [{"reference": section["reference"], "excerpt": "Lower energy represents compatibility"}]}], remaining_gaps=[])

    def research(self, invoke=None):
        with patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            research_foundations(self.root, self.work, self.config, self.entry, self.dossier, invoke or self.invoke)

    def apply(self):
        return apply_foundations(self.root, self.work, self.config, [self.entry], self.dossier, self.context, self.sources)

    def test_verified_supplement_survives_resume_without_changing_base(self):
        original = self.dossier.model_dump()
        self.research()
        self.research()
        self.assertEqual(len(self.calls), 3)
        dossier, _, _, outputs = self.apply()
        self.assertIn("better match", dossier.findings[0].statement)
        self.assertEqual(self.dossier.model_dump(), original)
        self.assertTrue(any(p.name == "receipt.json" for p in outputs))

    def test_episode_context_recovers_mechanism_omitted_by_both_previous_filters(self):
        index = self.sources.model_copy(deep=True)
        source = index.sources[0]
        anchor = self.dossier.findings[0].evidence[0].reference.split("#")[1]
        source.sections = [SourceSection(id=anchor, text="A summary mentions Attention.", page=3),
            SourceSection(id="sec_mechanism", text="The Query and Keys form scores; scaling and Softmax produce Attention weights.", page=4)]
        unrelated = source.model_copy(deep=True, update={"id": "src_unrelated"})
        index.sources.append(unrelated)
        # Neither the original dossier excerpt nor its sampled context includes page four.
        context = [{"source_id": source.id, "title": source.title, "url": source.url,
                    "sections": [{"reference": source.id + "#" + anchor, "text": source.sections[0].text, "page": 3}]}]
        supplied = episode_sources(self.entry, self.dossier, context, index)
        self.assertEqual([s["source_id"] for s in supplied], [source.id])
        self.assertTrue(any("Softmax" in s["text"] for doc in supplied for s in doc["sections"]))
        self.assertEqual(len(context[0]["sections"]), 1)

    def test_distinct_followup_gaps_use_bounded_saved_rounds(self):
        self.research()
        for number in (2, 3):
            write_json(self.work / "teaching/ep_001/research_needed.json", {
                "episode_id": "ep_001", "questions": [{"question": f"Follow-up mechanism {number}?", "why_needed": "An additional prerequisite."}]})
            self.research()
        self.assertEqual(len(self.calls), 9)
        self.assertEqual(len(list(self.work.glob("teaching/ep_001/supplement*/receipt.json"))), 3)
        self.assertIn("better match", self.apply()[0].findings[0].statement)
        write_json(self.work / "teaching/ep_001/research_needed.json", {
            "episode_id": "ep_001", "questions": [{"question": "A fourth question?", "why_needed": "Check the persistent bound."}]})
        for _ in range(2):
            with self.assertRaises(AppError):
                self.research()
        self.assertEqual(len(self.calls), 9)

    def test_second_gap_is_researched_in_same_script_job_without_another_approval(self):
        reviews = 0
        def model(prompt, schema, directory, **kwargs):
            nonlocal reviews
            if schema in (ResearchDiscovery, FoundationSupplement, FoundationReview):
                value = self.invoke(prompt, schema, kwargs["prompt_version"], research=True, search=kwargs["search"])
                if schema is FoundationSupplement and len(self.calls) > 3:
                    value.explanations[0].explanation = "This comparison does not establish that every model defines a normalized probability."
                return value, {}
            value, meta = self.fixture.model(prompt, schema, directory, **kwargs)
            if schema is TeachingPlanReview:
                reviews += 1
                if reviews <= 2:
                    value.research_gaps = [ResearchGap(question=f"Missing mechanism {reviews}?", why_needed="A necessary prerequisite.")]
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            plan = run_script(self.root, plan_only=True)
            work = self.root / "runs" / plan.run_id
            approved = outline_hash(work)
            run = run_script(self.root, resume=True, run_id=plan.run_id, approved_plan_hash=approved)
            self.assertEqual(run.status, "completed")
            again = run_script(self.root, resume=True, run_id=plan.run_id)
        self.assertEqual(again.status, "completed")
        self.assertEqual(outline_hash(work), approved)
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(reviews, 3)
        self.assertEqual(gaps_in(work), [])
        self.assertFalse(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])

    def test_invalid_evidence_blocks_and_is_not_regenerated_on_resume(self):
        def invoke(*args, **kwargs):
            value = self.invoke(*args, **kwargs)
            if isinstance(value, FoundationSupplement):
                value.explanations[0].evidence[0].excerpt = "An invented quote"
            return value
        for _ in range(2):
            with self.assertRaises(AppError) as caught:
                self.research(invoke)
            self.assertEqual(caught.exception.code, "teaching_research_required")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.apply()[0], self.dossier)

    def test_scope_change_blocks_instead_of_silently_replanning(self):
        def invoke(*args, **kwargs):
            value = self.invoke(*args, **kwargs)
            if isinstance(value, FoundationReview):
                value.scope_change_required = True
            return value
        with self.assertRaises(AppError):
            self.research(invoke)
        self.assertFalse((self.work / "teaching/ep_001/supplement/receipt.json").exists())

    def test_changed_evidence_or_incomplete_receipt_cannot_be_used(self):
        self.research()
        receipt = self.work / "teaching/ep_001/supplement/receipt.json"
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        write_json(receipt, {**saved, "outputs": {}})
        with self.assertRaises(AppError):
            self.apply()
        write_json(receipt, saved)
        write_json(self.work / "teaching/ep_001/supplement/evidence.json", {})
        with self.assertRaises(AppError):
            self.apply()

    def test_approved_script_automatically_recovers_then_resumes_with_same_evidence(self):
        first_review, paused = True, False
        captured = []
        def model(adapter, prompt, schema, directory, **kwargs):
            nonlocal first_review, paused
            self.assertEqual(adapter.settings.codex_model, "gpt-5.6-sol")
            self.assertEqual(adapter.reasoning_effort, "high")
            if schema in (ResearchDiscovery, FoundationSupplement, FoundationReview):
                return self.invoke(prompt, schema, kwargs["prompt_version"], research=True, search=kwargs["search"]), {}
            if schema is EpisodeScript and kwargs["prompt_version"] == "write_episode.v5":
                captured.append(prompt)
                if not paused:
                    paused = True
                    raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            value, meta = self.fixture.model(prompt, schema, directory, **kwargs)
            if schema is TeachingPlanReview and first_review:
                first_review = False
                value.research_gaps = [ResearchGap(question="How are scores compared?", why_needed="The comparison needs evidence.")]
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", autospec=True, side_effect=model), \
             patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            plan = run_script(self.root, plan_only=True, model="gpt-5.6-sol", reasoning_effort="high")
            work = self.root / "runs" / plan.run_id
            approved = outline_hash(work)
            original = {p.name: file_hash(p) for p in work.glob("*.json") if p.name != "budget.json"}
            first = run_script(self.root, resume=True, run_id=plan.run_id, approved_plan_hash=approved)
            self.assertEqual(first.status, "waiting_for_quota")
            self.assertEqual(first.stages["teaching"].status, "completed")
            finished = run_script(self.root, resume=True, run_id=plan.run_id)
        self.assertEqual(finished.status, "completed")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(outline_hash(work), approved)
        for name, sha in original.items():
            self.assertEqual(file_hash(work / name), sha, name)
        self.assertTrue(all("better match" in p for p in captured))
        self.assertIn("better match", read_yaml(self.root / "models/knowledge_model.yaml")["claims"][0]["statement"])
        self.assertFalse(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])


if __name__ == "__main__":
    unittest.main()
