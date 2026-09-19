import json
import unittest
from unittest.mock import patch

from podcast_automate.script_pipeline import WRITE_EPISODE_VERSION
from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript
from podcast_automate.research_models import (Evidence, Finding, ResearchDiscovery, SourceDocument,
                                              SourceSection)
from podcast_automate.script_models import ScriptReview, SeriesPlan
from podcast_automate.scripting import episode_sources, load_research, outline_hash, run_script
from podcast_automate.storage import file_hash, read_yaml, write_json
from podcast_automate.teaching import ResearchGap, TeachingPlanReview
from podcast_automate.teaching_research import (FoundationSupplement, FoundationReview,
    apply_foundations, research_foundations, gaps_in, validate_supplement)
from tests.research_fixtures import HTML, discovery
from tests import script_fixtures as fixtures
from tests.question_fixtures import claim_contract, support_receipts

# A stored section that shares whole words with the bias-rule gap below, in a source the
# fixture's research never cited. Which episode owns it is decided per test.
EXTRA_TEXT = ("The bias term of an overloaded expert decreases at the end of each step, and the bias "
              "term of an underloaded expert increases by the same update speed.")


def extra_source():
    return SourceDocument(id="src_extra", type="html", title="Extra report", authors=[], published_date="",
        imported_at="2026-09-19T00:00:00+00:00", url="https://example.org/extra", final_url="https://example.org/extra",
        reliability_note="", uncertainties=[], raw_path="sources/extra.html", raw_hash="0" * 64, text_hash="1" * 64,
        sections=[SourceSection(id="sec_001", text=EXTRA_TEXT)])


def two_episode_plan(second_findings):
    """The fixture plan plus ep_002 on the given findings, sharing the scene layout of ep_001."""
    plan = fixtures.example_plan()
    second = plan.episodes[0].model_copy(deep=True, update={
        "episode_id": "ep_002", "title": "A second look", "finding_ids": list(second_findings)})
    second.scenes[0].finding_ids = list(second_findings)
    plan.episodes.append(second)
    return plan


def script_for(episode):
    """The fixture script for whichever episode the writing prompt names."""
    script = fixtures.example_script().model_copy(deep=True, update={
        "episode_id": episode["episode_id"], "title": episode["title"]})
    for segment in script.segments:
        segment.knowledge_refs = list(episode["finding_ids"])
    return script


class FoundationResearchTests(unittest.TestCase):
    def setUp(self):
        fixture = self.fixture = fixtures.script_project(self)
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
            data = json.loads(prompt.splitlines()[-1])
            return FoundationReview(issues=[], scope_change_required=False, **support_receipts(data["findings"], data["sources"]))
        data = json.loads(prompt.splitlines()[-1])
        section = next(s for source in data["sources"] for s in source["sections"] if "Lower energy" in s["text"])
        return FoundationSupplement(explanations=[{
            "questions": data["questions"], "finding_ids": ["f_energy"],
            "explanation": "A lower score represents a better match in this example.",
            "claim_contract": claim_contract(),
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

    def test_pinned_sections_join_a_supplement_context_without_duplicating_it(self):
        from podcast_automate.teaching_research import merge_pinned
        context = [{"source_id": "src_a", "sections": [{"reference": "src_a#s1", "text": "One."}]}]
        pinned = [{"source_id": "src_a", "sections": [{"reference": "src_a#s1", "text": "One."},
                                                      {"reference": "src_a#s2", "text": "Two."}]},
                  {"source_id": "src_b", "sections": [{"reference": "src_b#s1", "text": "Three."}]}]
        merged = merge_pinned(context, pinned)
        self.assertEqual([s["source_id"] for s in merged], ["src_a", "src_b"])
        self.assertEqual([s["reference"] for s in merged[0]["sections"]], ["src_a#s1", "src_a#s2"])
        self.assertEqual(context[0]["sections"], [{"reference": "src_a#s1", "text": "One."}])
        self.assertEqual(merge_pinned(context, []), context)

    # --- the corpus probe in the script lane ---------------------------------------------------

    SEEDED = {"gap_seed": "The rule that assigns an energy to each configuration is missing."}

    def script_model(self, rounds, *, plan=None, invoke=None):
        """The fixture model, with research calls routed to ``invoke`` and a two-episode plan when given."""
        invoke = invoke or self.invoke

        def model(prompt, schema, directory, **kwargs):
            if schema in (ResearchDiscovery, FoundationSupplement, FoundationReview):
                if schema is ResearchDiscovery:
                    rounds.append(directory)
                return invoke(prompt, schema, kwargs["prompt_version"], research=True, search=kwargs["search"]), {}
            if plan is not None and schema is SeriesPlan:
                self.fixture.calls.append(schema)
                return plan, {}
            if plan is not None and schema is EpisodeScript:
                self.fixture.calls.append(schema)
                return script_for(json.loads(prompt.splitlines()[-1])["episode"]), {}
            return self.fixture.model(prompt, schema, directory, **kwargs)
        return model

    def probes_of(self, run):
        return json.loads((self.root / "runs" / run.run_id / "gap_probes.json").read_text(encoding="utf-8"))

    def test_a_gap_with_unread_corpus_hits_costs_exactly_one_supplement_round(self):
        """The probe routes the gap, the supplement answers it, and the gap is then closed."""
        rounds = []
        with patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value=self.SEEDED), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.script_model(rounds)), \
             patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(rounds), 1)
        work = self.root / "runs" / run.run_id
        probes = self.probes_of(run)
        self.assertEqual([(row["status"], row["settled_by"], row["owner_episodes"]) for row in probes],
                         [("resolved", "ep_001", ["ep_001"])])
        self.assertTrue(probes[0]["hits"])
        self.assertFalse((work / "teaching/ep_001/gap_probes.json").exists(), "one probe file per run, not per episode")
        request = json.loads((work / "teaching/ep_001/research_needed.json").read_text(encoding="utf-8"))
        self.assertTrue(request["resolved"])
        self.assertIn(probes[0]["hits"][0]["reference"], request["questions"][0]["why_needed"])
        self.assertEqual(request["questions"][0]["references"], [h["reference"] for h in probes[0]["hits"]])
        context = json.loads((work / "teaching/ep_001/supplement/source_context.json").read_text(encoding="utf-8"))
        references = {s["reference"] for source in context for s in source["sections"]}
        self.assertIn(probes[0]["hits"][0]["reference"], references)
        # Resuming a completed run leaves the run-level probe file byte-identical.
        before = (work / "gap_probes.json").read_bytes()
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.script_model(rounds)):
            resumed = run_script(self.root, resume=True)
        self.assertEqual((resumed.status, resumed.run_id, len(rounds)), ("completed", run.run_id, 1))
        self.assertTrue(all(stage.attempts == 1 for stage in resumed.stages.values()))
        self.assertEqual((work / "gap_probes.json").read_bytes(), before)

    def test_a_gap_whose_hits_stay_unread_blocks_the_review(self):
        with patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value=self.SEEDED), \
             patch("podcast_automate.script_pipeline.ScriptRun.route_probe_gaps", autospec=True,
                   side_effect=lambda self, entry: None), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            run = run_script(self.root)
        self.assertEqual(run.stages["review"].error.code, "research_gap_unread")
        self.assertEqual(run.stages["publish"].status, "pending")
        self.assertEqual([row["status"] for row in self.probes_of(run)], ["hits_unread"])

    def test_a_gap_owned_by_two_episodes_is_routed_once_and_settled_by_the_first(self):
        """Both episodes cite the source holding the hit. The first episode's supplement settles the
        run-level row, so the second episode spends no supplement round on it."""
        rounds = []
        plan = two_episode_plan(("f_energy",))
        with patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value=self.SEEDED), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.script_model(rounds, plan=plan)), \
             patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(rounds), 1)
        work = self.root / "runs" / run.run_id
        self.assertEqual([p.relative_to(work / "teaching").as_posix() for p in sorted((work / "teaching").glob("ep_*/supplement*"))],
                         ["ep_001/supplement"])
        probes = self.probes_of(run)
        self.assertEqual([(row["status"], row["settled_by"], row["owner_episodes"]) for row in probes],
                         [("resolved", "ep_001", ["ep_001", "ep_002"])])
        self.assertFalse((work / "teaching/ep_002/research_needed.json").exists())
        # Both episodes were written and polished: one EpisodeScript call per stage and episode.
        self.assertEqual(self.fixture.calls.count(EpisodeScript), 4)

    def test_a_gap_owned_only_by_a_later_episode_never_blocks_the_earlier_one(self):
        """The deadlock case: the hit sits in a source only ep_002 cites. ep_001 must neither route
        nor wait for it; ep_002 routes it, reads the pinned section and confirms the gap in
        ``remaining_gaps``, which settles the row as ``hits_read_confirmed`` and passes its review."""
        rounds = []
        gap = "The update of the bias term for an overloaded expert is missing."
        plan = two_episode_plan(("f_extra",))

        def confirming(prompt, schema, version, **kwargs):
            if schema is FoundationSupplement:
                self.calls.append(schema)
                data = json.loads(prompt.splitlines()[-1])
                self.assertEqual(data["episode"]["episode_id"], "ep_002")
                self.assertIn("src_extra#sec_001", {s["reference"] for source in data["sources"] for s in source["sections"]})
                return FoundationSupplement(explanations=[], remaining_gaps=data["questions"])
            return self.invoke(prompt, schema, version, **kwargs)

        with patch("podcast_automate.scripting.load_research", side_effect=self.research_with_extra_source), \
             patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value={"gap_extra": gap}), \
             patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.script_model(rounds, plan=plan, invoke=confirming)), \
             patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["teaching"].error or run.stages["review"].error)
        self.assertEqual(len(rounds), 1)
        work = self.root / "runs" / run.run_id
        self.assertEqual([p.relative_to(work / "teaching").as_posix() for p in sorted((work / "teaching").glob("ep_*/supplement*"))],
                         ["ep_002/supplement"])
        probes = self.probes_of(run)
        self.assertEqual([(row["status"], row["settled_by"], row["owner_episodes"], row["unread_references"])
                          for row in probes], [("hits_read_confirmed", "ep_002", ["ep_002"], [])])
        self.assertEqual([h["reference"] for h in probes[0]["hits"]], ["src_extra#sec_001"])
        self.assertFalse((work / "teaching/ep_001/research_needed.json").exists())
        request = json.loads((work / "teaching/ep_002/research_needed.json").read_text(encoding="utf-8"))
        self.assertTrue(request["resolved"])
        saved = json.loads((work / "teaching/ep_002/supplement/request.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["probes"], [{"question": gap, "references": ["src_extra#sec_001"]}])
        # A second resume changes nothing in the run folder's probe file.
        before = (work / "gap_probes.json").read_bytes()
        with patch("podcast_automate.scripting.load_research", side_effect=self.research_with_extra_source), \
             patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.script_model(rounds, plan=plan, invoke=confirming)):
            resumed = run_script(self.root, resume=True)
        self.assertEqual((resumed.status, len(rounds)), ("completed", 1))
        self.assertEqual((work / "gap_probes.json").read_bytes(), before)

    def test_a_gap_with_hits_in_no_episode_source_is_reported_not_blocking(self):
        """A hit in a source no episode cites cannot be read in this lane; the row is recorded as
        ``hits_unowned`` for the report and the run completes without a supplement."""
        gap = "The update of the bias term for an overloaded expert is missing."
        seen = []

        def model(prompt, schema, directory, **kwargs):
            if schema is ScriptReview:
                seen.append(json.loads(prompt.splitlines()[-1])["gap_probes"])
            return self.fixture.model(prompt, schema, directory, **kwargs)

        with patch("podcast_automate.scripting.load_research",
                   side_effect=lambda root, config: self.research_with_extra_source(root, config, finding=False)), \
             patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value={"gap_extra": gap}), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(self.calls, [])
        probes = self.probes_of(run)
        self.assertEqual([(row["status"], row["owner_episodes"]) for row in probes], [("hits_unowned", [])])
        self.assertEqual(seen[0], [{"gap_id": "gap_extra", "text": gap, "status": "hits_unowned",
                                    "references": ["src_extra#sec_001"]}])

    def test_confirming_a_probe_gap_requires_its_pinned_sections_in_the_context(self):
        gap = self.SEEDED["gap_seed"]
        probes = [{"question": gap, "references": ["src_a#sec_1", "src_a#sec_2"]}]
        confirmation = FoundationSupplement(explanations=[], remaining_gaps=[gap])
        partial = [{"source_id": "src_a", "sections": [{"reference": "src_a#sec_1", "text": "One."}]}]
        errors = validate_supplement(confirmation, [gap], self.entry, partial, self.dossier, probes)
        self.assertEqual(len(errors), 1)
        self.assertIn("Korpusprobe", errors[0])
        complete = [{"source_id": "src_a", "sections": [{"reference": r, "text": "Read."} for r in probes[0]["references"]]}]
        self.assertEqual(validate_supplement(confirmation, [gap], self.entry, complete, self.dossier, probes), [])
        # A remaining gap that is not a routed probe question still fails, as before.
        other = FoundationSupplement(explanations=[], remaining_gaps=["Something the probe never found."])
        self.assertTrue(validate_supplement(other, ["Something the probe never found."], self.entry, complete,
                                            self.dossier, probes))
        self.assertTrue(validate_supplement(other, ["Something the probe never found."], self.entry, complete, self.dossier))

    def research_with_extra_source(self, root, config, *, finding=True):
        """``load_research`` plus one stored source no episode cites, and optionally a finding on it."""
        research_id, dossier, discovery, sources, context = load_research(root, config)
        sources = sources.model_copy(deep=True)
        sources.sources.append(extra_source())
        context = [*context, {"source_id": "src_extra", "title": "Extra report", "url": "https://example.org/extra",
                              "total_sections": 1, "sections": [{"reference": "src_extra#sec_001", "text": EXTRA_TEXT, "page": None}]}]
        if finding:
            dossier = dossier.model_copy(deep=True)
            dossier.findings.append(Finding(id="f_extra", kind="mechanism", claim_contract=claim_contract(),
                statement="The bias term of an overloaded expert decreases at the end of each step.",
                evidence=[Evidence(reference="src_extra#sec_001", excerpt="bias term of an overloaded expert decreases")]))
        return research_id, dossier, discovery, sources, context

    def test_approved_script_automatically_recovers_then_resumes_with_same_evidence(self):
        first_review, paused = True, False
        captured = []
        def model(adapter, prompt, schema, directory, **kwargs):
            nonlocal first_review, paused
            self.assertEqual(adapter.settings.codex_model, "gpt-5.6-sol")
            self.assertEqual(adapter.reasoning_effort, "high")
            if schema in (ResearchDiscovery, FoundationSupplement, FoundationReview):
                return self.invoke(prompt, schema, kwargs["prompt_version"], research=True, search=kwargs["search"]), {}
            if schema is EpisodeScript and kwargs["prompt_version"] == WRITE_EPISODE_VERSION:
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
            # Call counters and the budget projection are live status, not run inputs.
            original = {p.name: file_hash(p) for p in work.glob("*.json")
                        if p.name not in {"budget.json", "budget_projection.json"}}
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
