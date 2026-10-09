import json
import unittest
from unittest.mock import patch

from podcast_automate.script_pipeline import WRITE_EPISODE_VERSION
from podcast_automate.editorial import MACHINE_LEARNING_TERMS, TERMINOLOGY, TOPIC_TERMINOLOGY
from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript
from podcast_automate.research_ledger import read_value, save_value
from podcast_automate.research_models import (Evidence, Finding, ResearchDiscovery, SourceDocument,
                                              SourceSection)
from podcast_automate.runner import manifest_path
from podcast_automate.script_models import ScriptReview, SeriesPlan
from podcast_automate.scripting import episode_sources, load_research, outline_hash, run_script
from podcast_automate.storage import digest, file_hash, read_yaml, write_json, write_yaml
from podcast_automate.teaching import ResearchGap, TeachingPlanReview
from podcast_automate.text_settings import stage_effort
from podcast_automate.teaching_research import (ASSEMBLED_ALLOWANCE, FoundationSupplement, FoundationReview,
    apply_foundations, named_question, research_foundations, gaps_in, source_budget, validate_supplement)
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
        # Resolved, as run_script() resolves the root it hands research_foundations (an 8.3 TEMP differs).
        self.root, self.config = fixture.root.resolve(), fixture.config
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

    def test_supplement_prompts_carry_the_topics_terminology_and_a_saved_supplement_survives_a_new_rule(self):
        """2026-10-02: every supplement call named Query, Key and Value, also for the Asimov series. A topic that is
        not machine learning gets the topic-neutral rule; a supplement saved under the earlier rule is replayed."""
        prompts = []

        def invoke(prompt, schema, version, **kwargs):
            prompts.append(prompt)
            return self.invoke(prompt, schema, version, **kwargs)
        self.research(invoke)
        self.assertEqual(len(prompts), 3)
        self.assertTrue(all(prompt.startswith(TOPIC_TERMINOLOGY) for prompt in prompts))
        self.assertTrue(all(MACHINE_LEARNING_TERMS not in prompt and TERMINOLOGY not in prompt for prompt in prompts))
        # Without its receipt the supplement passes through every stored answer again, now under another rule.
        (self.work / "teaching/ep_001/supplement/receipt.json").unlink()
        with patch("podcast_automate.teaching_research.terminology", return_value=TERMINOLOGY):
            self.research(invoke)
        self.assertEqual(len(prompts), 3)
        self.assertIn("better match", self.apply()[0].findings[0].statement)

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

    def test_invalid_evidence_is_corrected_twice_then_blocks_and_is_not_regenerated_on_resume(self):
        """A rejected answer is asked again with its defects, twice; then the run stops, and a resume
        replays the stored stop instead of buying more attempts. Until 2026-09-27 the first rejection
        stopped the run, and "Fortsetzen" replayed that one answer (Ontologies)."""
        prompts = []

        def invoke(prompt, schema, version, **kwargs):
            value = self.invoke(prompt, schema, version, **kwargs)
            if isinstance(value, FoundationSupplement):
                prompts.append(prompt)
                value.explanations[0].evidence[0].excerpt = "An invented quote"
            return value
        for _ in range(2):
            with self.assertRaises(AppError) as caught:
                self.research(invoke)
            self.assertEqual(caught.exception.code, "teaching_research_required")
        self.assertEqual(self.calls, [ResearchDiscovery, FoundationSupplement, FoundationSupplement, FoundationSupplement])
        first, *retries = [json.loads(prompt.splitlines()[-1]) for prompt in prompts]
        self.assertNotIn("rejected_attempt", first)
        for retry in retries:
            self.assertEqual(retry["questions"], first["questions"])
            self.assertIn("An invented quote", json.dumps(retry["rejected_attempt"]["answer"]))
            self.assertIn("Eine ergänzende Aussage hat keinen gültigen Textbeleg.", retry["rejected_attempt"]["defects"])
        folder = self.work / "teaching/ep_001/supplement"
        self.assertEqual(sorted(p.name for p in folder.glob("evidence*.json")),
                         ["evidence.json", "evidence_rejected_00.json", "evidence_rejected_01.json"])
        self.assertEqual(self.apply()[0], self.dossier)

    def test_a_corrected_answer_passes_on_its_second_attempt(self):
        attempts = []

        def invoke(prompt, schema, version, **kwargs):
            value = self.invoke(prompt, schema, version, **kwargs)
            if isinstance(value, FoundationSupplement):
                attempts.append(prompt)
                if len(attempts) == 1:
                    value.explanations[0].evidence[0].excerpt = "An invented quote"
            return value
        self.research(invoke)
        self.assertEqual(self.calls, [ResearchDiscovery, FoundationSupplement, FoundationSupplement, FoundationReview])
        self.assertIn("better match", self.apply()[0].findings[0].statement)
        self.research(invoke)
        self.assertEqual(len(self.calls), 4, "a verified supplement is reused on resume")

    def test_a_review_that_also_assesses_an_uncited_source_needs_no_correction(self):
        """The Ontologies stop of 2026-09-28: the review assessed every supplied source, not only the cited
        ones, and the exactly-once check stopped the run. An out-of-scope assessment is dropped, as in a
        dossier review part."""
        def invoke(prompt, schema, version, **kwargs):
            value = self.invoke(prompt, schema, version, **kwargs)
            if isinstance(value, FoundationReview):
                value.source_assessments.append(value.source_assessments[0].model_copy(
                    update={"source_id": "src_uncited", "evidence_refs": []}))
            return value
        self.research(invoke)
        self.assertEqual(self.calls, [ResearchDiscovery, FoundationSupplement, FoundationReview])
        saved = json.loads((self.work / "teaching/ep_001/supplement/review.json").read_text(encoding="utf-8"))
        self.assertNotIn("src_uncited", [a["source_id"] for a in saved["value"]["source_assessments"]])
        self.assertIn("better match", self.apply()[0].findings[0].statement)

    def test_a_review_that_leaves_a_cited_source_unassessed_is_asked_again(self):
        prompts = []
        def invoke(prompt, schema, version, **kwargs):
            value = self.invoke(prompt, schema, version, **kwargs)
            if isinstance(value, FoundationReview):
                prompts.append(prompt)
                if len(prompts) == 1:
                    value.source_assessments = []
            return value
        self.research(invoke)
        self.assertEqual(self.calls, [ResearchDiscovery, FoundationSupplement, FoundationReview, FoundationReview])
        self.assertIn("Assess the suitability and identity of every cited source exactly once.", prompts[1])
        self.assertIn("better match", self.apply()[0].findings[0].statement)

    def test_only_a_critical_review_point_blocks_the_supplement_and_it_is_corrected_first(self):
        """Asimov, 2026-09-28: the supplement review listed wording, an unexplained term and an attribution, and
        each stopped the run at once. Now an uncritical point is an advisory, and a critical one goes back to the
        supplement as a correction within its attempts; the next review sees what the earlier one raised."""
        critical = "The explanation drops the source's limit to accelerating production functions."
        wording = "'Produktionsfunktion' is used without a short explanation."
        reviews, supplements = [], []

        def invoke(prompt, schema, version, **kwargs):
            value = self.invoke(prompt, schema, version, **kwargs)
            payload = json.loads(prompt.splitlines()[-1])
            if schema is FoundationSupplement:
                supplements.append(payload.get("rejected_attempt"))
            if schema is FoundationReview:
                reviews.append(payload.get("previous_issues"))
                if len(reviews) == 1:
                    value.issues = [critical, wording]  # no basis: corrected before it counts
                elif len(reviews) == 2:
                    value.issues, value.issue_basis, value.advisories = [critical], ["unsupported_claim"], [wording]
                else:
                    value.issues, value.advisories = [], [wording]
            return value
        self.research(invoke)
        self.assertEqual(self.calls, [ResearchDiscovery, FoundationSupplement, FoundationReview, FoundationReview,
                                      FoundationSupplement, FoundationReview])
        self.assertEqual(reviews, [None, None, [critical]])
        self.assertIsNone(supplements[0])
        self.assertEqual(supplements[1]["defects"], ["Die Prüfung der Ergänzung beanstandet: " + critical])
        folder = self.work / "teaching/ep_001/supplement"
        self.assertTrue((folder / "receipt.json").exists())
        self.assertEqual(json.loads((folder / "review.json").read_text(encoding="utf-8"))["value"]["advisories"], [wording])
        self.assertEqual(json.loads((folder / "review_rejected_00.json").read_text(encoding="utf-8"))["value"]["issues"], [critical])
        self.assertIn("better match", self.apply()[0].findings[0].statement)

    def test_review_corrections_and_check_corrections_have_their_own_attempts(self):
        """Ontologies ep_004, 2026-09-28: two corrections the review asked for used both attempts, the third answer
        then broke the per-source word limit, and nothing was left to fix it. Each kind now has its own two."""
        critical = "The explanation says the builders' experience is unknown; the passage states it."
        answers, reviews = [], []

        def invoke(prompt, schema, version, **kwargs):
            value = self.invoke(prompt, schema, version, **kwargs)
            if schema is FoundationSupplement:
                answers.append(json.loads(prompt.splitlines()[-1]).get("rejected_attempt"))
                if len(answers) == 3:
                    value.explanations[0].evidence[0].excerpt = "An invented quote"
            if schema is FoundationReview:
                reviews.append(schema)
                if len(reviews) <= 2:
                    value.issues, value.issue_basis = [critical], ["source_contradiction"]
            return value
        self.research(invoke)
        self.assertEqual(self.calls, [ResearchDiscovery, FoundationSupplement, FoundationReview, FoundationSupplement,
                                      FoundationReview, FoundationSupplement, FoundationSupplement, FoundationReview])
        self.assertIn("Eine ergänzende Aussage hat keinen gültigen Textbeleg.", answers[3]["defects"])
        folder = self.work / "teaching/ep_001/supplement"
        self.assertEqual(len(list(folder.glob("evidence_rejected_*.json"))), 3)
        self.assertTrue((folder / "receipt.json").exists())

    def test_a_stuck_supplement_gets_fresh_attempts_only_when_asked_and_keeps_its_earlier_issues(self):
        """Ontologies ep_004, 2026-09-28: the review's point stayed unresolved after both corrections. On the user's
        request the spent corrections move aside, the next review still knows every earlier point, and the
        supplement passes once the point is fixed."""
        from podcast_automate.models import RunManifest, StageRecord
        from podcast_automate.run_budget import approve_fresh_attempts
        from podcast_automate.storage import write_yaml
        from podcast_automate.teaching_research import stuck_supplements
        point = "The gap is listed although the passage answers its first half."
        reviews = []

        def invoke(prompt, schema, version, **kwargs):
            value = self.invoke(prompt, schema, version, **kwargs)
            if schema is FoundationReview:
                reviews.append(json.loads(prompt.splitlines()[-1]).get("previous_issues"))
                if len(reviews) <= 3:
                    value.issues, value.issue_basis = [point], ["source_contradiction"]
            return value
        with self.assertRaises(AppError):
            self.research(invoke)
        folder = self.work / "teaching/ep_001/supplement"
        self.assertEqual(stuck_supplements(self.work), [folder])
        write_yaml(self.work / "run_manifest.yaml", RunManifest(run_id="run_foundations", kind="script", project_hash="0" * 64,
            input_hash="d" * 64, status="blocked", stages={"teaching": StageRecord(status="blocked")}).model_dump(mode="json"))
        record = approve_fresh_attempts(self.root, "run_foundations")
        self.assertEqual(record["supplements"], ["teaching/ep_001/supplement"])
        self.assertEqual(sorted(p.name for p in folder.glob("*_rejected_*.json")), [])
        self.assertEqual(len(list(folder.glob("evidence_superseded_01_*.json"))), 2)
        self.assertEqual(stuck_supplements(self.work), [])
        self.research(invoke)
        self.assertEqual(reviews[3], [point], "the fresh round's review knows the earlier point")
        self.assertTrue((folder / "receipt.json").exists())
        with self.assertRaises(AppError):
            approve_fresh_attempts(self.root, "run_foundations")

    def test_a_partly_supported_explanation_is_corrected_before_it_stops_the_run(self):
        """Asimov ep_012, 2026-09-29: the review judged the explanation only partly supported, and the run stopped
        right after the review without a correction. Such a receipt now goes back like a critical issue."""
        reviews = []

        def invoke(prompt, schema, version, **kwargs):
            value = self.invoke(prompt, schema, version, **kwargs)
            if schema is FoundationReview:
                reviews.append(schema)
                if len(reviews) == 1:
                    # As at Asimov ep_012: partly supported, and the explanation shortens the source's own claim.
                    value.finding_support[0].verdict = "partially_supported"
                    value.finding_support[0].contract_preserved = False
            return value
        self.research(invoke)
        self.assertEqual(self.calls, [ResearchDiscovery, FoundationSupplement, FoundationReview,
                                      FoundationSupplement, FoundationReview])
        folder = self.work / "teaching/ep_001/supplement"
        defects = json.loads((folder / "evidence_rejected_00.json").read_text(encoding="utf-8"))["errors"]
        self.assertTrue(defects and all(d.startswith("Die Prüfung der Ergänzung beanstandet: ") for d in defects))
        self.assertTrue((folder / "receipt.json").exists())

    def test_a_supplement_review_stored_before_the_basis_rule_is_asked_again(self):
        self.research()
        folder = self.work / "teaching/ep_001/supplement"
        (folder / "receipt.json").unlink()
        stored = {"issues": ["Teixeira is not named."], "scope_change_required": False,
                  "finding_support": [], "source_assessments": []}
        write_json(folder / "review.json", {"value": stored, "sha256": digest(stored)})
        self.research()
        self.assertEqual(self.calls.count(FoundationReview), 2, "the stored review without a basis is asked again")
        self.assertEqual(json.loads((folder / "review_before_check.json").read_text(encoding="utf-8"))["value"], stored)
        self.assertTrue((folder / "receipt.json").exists())

    def test_a_page_the_research_stored_is_reused_not_fetched_again(self):
        """Ontologies, 2026-09-28: the supplement fetched the dbt MetricFlow page again, the page had changed since
        the research, and the two versions stopped the run when the supplement joined the dossier."""
        with patch("podcast_automate.sources.download", side_effect=AssertionError("a stored page is not fetched")):
            research_foundations(self.root, self.work, self.config, self.entry, self.dossier, self.invoke,
                                 known_sources=self.sources)
        index = json.loads((self.work / "teaching/ep_001/supplement/source_index.json").read_text(encoding="utf-8"))
        stored = {s.id: s.raw_hash for s in self.sources.sources}
        self.assertTrue(index["sources"])
        self.assertTrue(all(stored.get(s["id"]) == s["raw_hash"] for s in index["sources"]))
        self.assertIn("better match", self.apply()[0].findings[0].statement)

    def test_a_changed_copy_of_a_stored_page_is_left_out_unless_an_explanation_cites_it(self):
        self.research()
        folder = self.work / "teaching/ep_001/supplement"
        cited = {e["reference"].split("#")[0] for a in json.loads((folder / "evidence.json").read_text(encoding="utf-8"))["value"]["explanations"]
                 for e in a["evidence"]}
        index_path = folder / "source_index.json"
        index = json.loads(index_path.read_text(encoding="utf-8"))
        source = next(s for s in index["sources"] if s["id"] in cited)
        index["sources"].append({**source, "id": "src_uncited"})
        write_json(index_path, index)
        receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
        receipt["outputs"][index_path.relative_to(self.root).as_posix()] = file_hash(index_path)
        write_json(folder / "receipt.json", receipt)
        # The research holds the uncited page in another version: the supplement's copy is left out.
        stored = self.sources.model_copy(deep=True)
        stored.sources.append(SourceDocument.model_validate({**source, "id": "src_uncited", "raw_hash": "f" * 64}))
        dossier, _, merged, _ = apply_foundations(self.root, self.work, self.config, [self.entry], self.dossier,
                                                  self.context, stored)
        self.assertIn("better match", dossier.findings[0].statement)
        self.assertEqual([s.raw_hash for s in merged.sources if s.id == "src_uncited"], ["f" * 64])
        # A changed page an explanation cites still stops: its passages may not be the stored ones.
        changed = self.sources.model_copy(deep=True)
        changed.sources = [s for s in changed.sources if s.id != source["id"]]
        changed.sources.append(SourceDocument.model_validate({**source, "raw_hash": "f" * 64}))
        with self.assertRaises(AppError) as caught:
            apply_foundations(self.root, self.work, self.config, [self.entry], self.dossier, self.context, changed)
        self.assertEqual(caught.exception.code, "invalid_source_snapshot")

    def test_scope_change_blocks_instead_of_silently_replanning(self):
        def invoke(*args, **kwargs):
            value = self.invoke(*args, **kwargs)
            if isinstance(value, FoundationReview):
                value.scope_change_required = True
            return value
        with self.assertRaises(AppError):
            self.research(invoke)
        self.assertFalse((self.work / "teaching/ep_001/supplement/receipt.json").exists())

    def test_a_raised_limit_keeps_the_supplement_and_one_saved_before_the_change_still_counts(self):
        """2026-10-02 review: the supplement binding digested the whole brief, runtime and limits included, which a
        resumed run's hash leaves out (storage.bound_brief). A limit raised while the run waited stopped every resume
        with invalid_supplement, and an unfinished supplement was bought again as a new one."""
        self.research()
        limits = self.config.research_limits
        raised = self.config.model_copy(update={"research_limits": limits.model_copy(update={"model_calls": limits.model_calls + 50})})
        self.assertIn("better match", apply_foundations(self.root, self.work, raised, [self.entry], self.dossier,
                                                        self.context, self.sources)[0].findings[0].statement)
        # A supplement saved under the earlier binding over the whole brief keeps its files as they are.
        folder = self.work / "teaching/ep_001/supplement"
        earlier = digest({"version": "teaching_research.v1", "config": self.config.model_dump(),
                          "episode": self.entry.model_dump(), "dossier": self.dossier.model_dump()})
        request = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        write_json(folder / "request.json", {**request, "binding": earlier})
        receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
        receipt["outputs"][(folder / "request.json").relative_to(self.root).as_posix()] = file_hash(folder / "request.json")
        write_json(folder / "receipt.json", {**receipt, "binding": earlier})
        self.apply()
        calls = len(self.calls)
        self.research()
        self.assertEqual((len(self.calls), len(list(self.work.glob("teaching/ep_001/supplement*")))), (calls, 1),
                         "the saved supplement is found, not bought again")
        # A content edit still binds: the supplement no longer belongs to the brief.
        edited = self.config.model_copy(update={"depth_request": self.config.depth_request + " More."})
        with self.assertRaises(AppError) as changed:
            apply_foundations(self.root, self.work, edited, [self.entry], self.dossier, self.context, self.sources)
        self.assertEqual(changed.exception.code, "invalid_supplement")

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

    def test_a_quoted_gap_with_a_reason_after_it_still_names_its_question(self):
        """Ontologies, 2026-09-27: the supplement wrote every confirmed gap as "'question' reason", and the
        exact-text check counted none of them. The question must still stand in the entry verbatim."""
        gap = self.SEEDED["gap_seed"]
        self.assertEqual(named_question(f"'{gap}' The pinned section does not state it.", [gap]),
                         (gap, "The pinned section does not state it."))
        self.assertEqual(named_question(f"„{gap}“", [gap]), (gap, ""))
        self.assertEqual(named_question(gap, [gap]), (gap, ""))
        self.assertEqual(named_question(gap[:-1] + " anywhere.", [gap]), (None, None))
        self.assertEqual(named_question(gap[:-1] + "s.", [gap[:-1]]), (None, None), "a question must end where the entry says")
        probes = [{"question": gap, "references": ["src_a#sec_1"]}]
        context = [{"source_id": "src_a", "sections": [{"reference": "src_a#sec_1", "text": "Read."}]}]
        quoted = FoundationSupplement(explanations=[], remaining_gaps=[f"'{gap}' No passage states the rule."])
        self.assertEqual(validate_supplement(quoted, [gap], self.entry, context, self.dossier, probes), [])
        paraphrased = FoundationSupplement(explanations=[], remaining_gaps=["The energy rule is not stated."])
        errors = validate_supplement(paraphrased, [gap], self.entry, context, self.dossier, probes)
        self.assertEqual(len(errors), 1)
        self.assertIn("Weder beantwortet noch als Lücke genannt", errors[0])
        self.assertIn("The energy rule is not stated.", errors[0])

    def test_a_supplement_that_only_confirms_probe_gaps_needs_no_review(self):
        """Ontologies, 2026-09-28: the supplement read the pinned sections and confirmed all three gaps; the
        review then assessed eight uncited sources and blocked the run for explaining nothing. With nothing to
        assess, no review is asked, and a review stored under the old rule is kept beside the empty one."""
        section = next(s for source in self.context for s in source["sections"])
        gap = "The rule that assigns an energy to each configuration is missing."
        write_json(self.work / "teaching/ep_001/research_needed.json", {"episode_id": "ep_001", "questions": [
            {"question": gap, "why_needed": "Unread corpus hits.", "references": [section["reference"]]}]})
        pinned = [{"source_id": section["reference"].split("#")[0], "sections": [section]}]

        def invoke(prompt, schema, version, **kwargs):
            if schema is FoundationSupplement:
                self.calls.append(schema)
                return FoundationSupplement(explanations=[], remaining_gaps=[gap])
            if schema is FoundationReview:
                self.fail("a supplement without explanations is not reviewed")
            return self.invoke(prompt, schema, version, **kwargs)

        def research():
            with patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
                return research_foundations(self.root, self.work, self.config, self.entry, self.dossier, invoke, pinned=pinned)
        supplement = research()
        self.assertEqual((supplement.explanations, supplement.remaining_gaps), ([], [gap]))
        self.assertEqual(self.calls, [ResearchDiscovery, FoundationSupplement])
        folder = self.work / "teaching/ep_001/supplement"
        self.assertTrue((folder / "receipt.json").exists())
        self.assertEqual(self.apply()[0], self.dossier, "a confirmed gap adds nothing to the dossier")
        # A run stopped under the old rule: its model review blocked and no receipt was written.
        (folder / "receipt.json").unlink()
        stored = {"issues": ["The supplement contains no explanations."], "scope_change_required": False,
                  "finding_support": [], "source_assessments": []}
        write_json(folder / "review.json", {"value": stored, "sha256": "x"})
        research()
        self.assertEqual(len(self.calls), 2, "the resume asks no model")
        self.assertEqual(json.loads((folder / "review_before_skip.json").read_text(encoding="utf-8"))["value"], stored)
        self.assertEqual(json.loads((folder / "review.json").read_text(encoding="utf-8"))["value"]["issues"], [])
        self.assertTrue((folder / "receipt.json").exists())

    def test_the_supplement_sees_what_each_source_has_left_and_an_overlong_answer_names_the_source(self):
        """The source-wide limits count the findings that already cite a source; the answer is told what is
        left, and a rejection names the source and its counts (Ontologies: 149 of 150 words already spent). A
        supplement may add 10 quoted and 40 paraphrased words on top of the dossier's 25 and 150 (2026-09-29)."""
        cited = self.dossier.findings[0].evidence[0].reference
        source_id = cited.split("#")[0]
        context = [{"source_id": source_id, "sections": [{"reference": cited, "text": "Irrelevant here."}]},
                   {"source_id": "src_new", "sections": [{"reference": "src_new#s1", "text": "Fresh."}]}]
        budget = source_budget(context, self.dossier)
        used = sum(len(f.statement.split()) for f in self.dossier.findings
                   if any(e.reference.startswith(source_id + "#") for e in f.evidence))
        self.assertEqual(budget[source_id]["paraphrased_words_left"], 190 - used)
        self.assertEqual(budget["src_new"], {"quoted_words_left": 35, "paraphrased_words_left": 190})
        seen = []

        def invoke(prompt, schema, version, **kwargs):
            if schema is FoundationSupplement:
                seen.append(json.loads(prompt.splitlines()[-1])["source_budget"])
            return self.invoke(prompt, schema, version, **kwargs)
        self.research(invoke)
        self.assertTrue(seen and all(set(row) == {"quoted_words_left", "paraphrased_words_left"}
                                     for row in seen[0].values()))
        long = FoundationSupplement(explanations=[{
            "questions": ["How are scores compared?"], "finding_ids": ["f_energy"],
            "explanation": " ".join(["word"] * (191 - used)), "claim_contract": claim_contract(),
            "evidence": [{"reference": cited, "excerpt": self.dossier.findings[0].evidence[0].excerpt}]}], remaining_gaps=[])
        context = [{"source_id": source_id, "sections": [{"reference": cited,
                    "text": next(s["text"] for src in self.context for s in src["sections"] if s["reference"] == cited)}]}]
        errors = validate_supplement(long, ["How are scores compared?"], self.entry, context, self.dossier)
        self.assertEqual(len(errors), 1)
        self.assertIn(f"{source_id}: ", errors[0])
        self.assertIn("191 von 190 umschriebenen Wörtern", errors[0])
        # Within the allowance the same answer passes: one word fewer.
        fits = long.model_copy(deep=True)
        fits.explanations[0].explanation = " ".join(["word"] * (190 - used))
        self.assertEqual(validate_supplement(fits, ["How are scores compared?"], self.entry, context, self.dossier), [])

    def test_a_supplement_to_an_assembled_dossier_counts_only_its_own_words(self):
        """An assembled dossier holds every verified answer without word limits per source (2026-10-01): the limits
        of a supplement then count only what it adds, and its writer is told so."""
        assembled = self.dossier.model_copy(update={"assembled": True})
        cited = self.dossier.findings[0].evidence[0].reference
        source_id = cited.split("#")[0]
        text = next(s["text"] for src in self.context for s in src["sections"] if s["reference"] == cited)
        context = [{"source_id": source_id, "sections": [{"reference": cited, "text": text}]}]
        self.assertEqual(source_budget(context, assembled)[source_id], {"quoted_words_left": 35, "paraphrased_words_left": 190})

        def supplement(words):
            return FoundationSupplement(explanations=[{
                "questions": ["How are scores compared?"], "finding_ids": ["f_energy"],
                "explanation": " ".join(["word"] * words), "claim_contract": claim_contract(),
                "evidence": [{"reference": cited, "excerpt": self.dossier.findings[0].evidence[0].excerpt}]}], remaining_gaps=[])
        self.assertEqual(validate_supplement(supplement(190), ["How are scores compared?"], self.entry, context, assembled), [])
        # Against a composed dossier the findings already citing the source count, as before.
        self.assertTrue(validate_supplement(supplement(190), ["How are scores compared?"], self.entry, context, self.dossier))
        (error,) = validate_supplement(supplement(191), ["How are scores compared?"], self.entry, context, assembled)
        self.assertIn("191 von 190 umschriebenen Wörtern", error)
        prompts = []

        def invoke(prompt, schema, version, **kwargs):
            if schema is FoundationSupplement:
                prompts.append(prompt)
            return self.invoke(prompt, schema, version, **kwargs)
        self.dossier = assembled
        self.research(invoke)
        self.assertTrue(prompts and all(ASSEMBLED_ALLOWANCE in prompt for prompt in prompts))

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

    def research_ledger(self):
        return manifest_path(self.root, self.fixture.research.run_id).parent / "question_research/state.json"

    def forget_research_reads(self):
        """The fixture's research read the section the seeded gap hits, so its probe row would stand as
        read (settle_by_research). These tests need hits nobody read: the research ledger forgets its reads."""
        state = read_value(self.research_ledger())
        for row in state["tasks"].values():
            row["read_refs"] = []
        save_value(self.research_ledger(), state)

    def test_a_gap_with_unread_corpus_hits_costs_exactly_one_supplement_round(self):
        """The probe routes the gap, the supplement answers it, and the gap is then closed."""
        self.forget_research_reads()
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

    GERMAN = {"gap_de": "Die Regel, nach der jede Konfiguration ihren Wert erhält, fehlt."}

    def test_a_german_gap_the_words_miss_is_found_by_jev_and_read_like_a_term_hit(self):
        """Asimov, 2026-09-29: 15 of 23 German gaps stood as no_hits against mostly English sources. With the Jev
        probe the section holding the answer becomes a hit to read, and the supplement round settles it as a term
        hit would be settled. The OpenRouter key reaches no file of the run."""
        self.forget_research_reads()
        rounds, asked = [], []

        def decide(client, state, questions):
            asked.append(state)
            return {name: {"type": "noul", "noul": 0.9 if "energy to each configuration" in state else 0.02}
                    for name in questions}, 0.0001
        with patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value=self.GERMAN), \
             patch("podcast_automate.jev.JevClient.decide", autospec=True, side_effect=decide), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.script_model(rounds)), \
             patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            run = run_script(self.root, jev_probe=True, probe_key="test-key")
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual(len(rounds), 1)
        work = self.root / "runs" / run.run_id
        probes = self.probes_of(run)
        self.assertEqual([(row["status"], row["owner_episodes"]) for row in probes], [("resolved", ["ep_001"])])
        self.assertEqual({hit["via"] for hit in probes[0]["hits"]}, {"jev"}, "the words alone found nothing")
        report = json.loads((work / "jev_probe.json").read_text(encoding="utf-8"))
        self.assertEqual((report["status"], report["requests"], report["gaps"]), ("completed", len(asked), 1))
        request = json.loads((work / "script_request.json").read_text(encoding="utf-8"))
        self.assertTrue(request["execution"]["jev_probe"])
        self.assertFalse([path for path in work.rglob("*") if path.is_file() and b"test-key" in path.read_bytes()])
        # Without the probe the same gap stands as no_hits and costs no supplement round.
        rounds.clear()
        with patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value=self.GERMAN), \
             patch("podcast_automate.jev.JevClient.decide", side_effect=AssertionError("not asked")), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.script_model(rounds)):
            plain = run_script(self.root)
        self.assertEqual([row["status"] for row in self.probes_of(plain)], ["no_hits"])
        self.assertEqual(rounds, [])

    def test_jev_by_a_german_projects_default_runs_without_a_key_on_words_alone_but_a_chosen_one_stops(self):
        self.forget_research_reads()
        write_json(self.root / "studio/jev_probe.json", {"enabled": True, "by_default": True})
        rounds = []
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": ""}), \
             patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value=self.GERMAN), \
             patch("podcast_automate.jev.build_opener", side_effect=AssertionError("no request without a key")), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.script_model(rounds)):
            run = run_script(self.root)
            self.assertEqual(run.status, "completed", run.stages["review"].error)
            work = self.root / "runs" / run.run_id
            self.assertEqual(json.loads((work / "jev_probe.json").read_text(encoding="utf-8")),
                             {"status": "skipped", "reason": "no_key"})
            self.assertEqual([row["status"] for row in self.probes_of(run)], ["no_hits"])
            # Switched on by the user, a missing key stops the run before any request.
            write_json(self.root / "studio/jev_probe.json", {"enabled": True})
            chosen = run_script(self.root)
        self.assertEqual([stage.error.code for stage in chosen.stages.values() if stage.error], ["openrouter_key_required"])

    def test_a_gap_whose_hits_stay_unread_blocks_the_review(self):
        self.forget_research_reads()
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
        self.forget_research_reads()
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

    def note_script_limit(self, text):
        """A limit the research noted for the script, as research_quality_gate.json carries it. The gate is a hashed
        output of the research run, so its recorded hash follows (as in test_scripting)."""
        work = manifest_path(self.root, self.fixture.research.run_id).parent
        gate = work / "research_quality_gate.json"
        write_json(gate, {**json.loads(gate.read_text(encoding="utf-8")), "script_notes": [text]})
        manifest = read_yaml(work / "run_manifest.yaml")
        relative = gate.relative_to(self.root).as_posix()
        for record in manifest["stages"].values():
            if relative in (record.get("outputs") or {}):
                record["outputs"][relative] = file_hash(gate)
        write_yaml(work / "run_manifest.yaml", manifest)

    def test_a_research_limit_is_read_by_the_episode_stating_it_wherever_its_hits_lie(self):
        """Orlagau, 2026-10-09: ep_001 cited the large editions, so 16 of the 19 gaps routed to it were limits of
        nine later episodes, and a limit it resolved would have left its own episode with neither the limit nor the
        answer. A limit goes to the episode that states it (D-173): the plan places this one in ep_001, its hit sits
        in a source only ep_002 cites, and ep_001 reads the pinned section while ep_002 neither routes nor waits."""
        rounds = []
        gap = "The update of the bias term for an overloaded expert is missing."
        self.note_script_limit(gap)
        plan = two_episode_plan(("f_extra",))
        plan.episodes[0].research_limit_ids = ["limit_001"]

        def confirming(prompt, schema, version, **kwargs):
            if schema is FoundationSupplement:
                data = json.loads(prompt.splitlines()[-1])
                self.assertEqual(data["episode"]["episode_id"], "ep_001")
                self.assertIn("src_extra#sec_001", {s["reference"] for source in data["sources"] for s in source["sections"]})
                return FoundationSupplement(explanations=[], remaining_gaps=data["questions"])
            return self.invoke(prompt, schema, version, **kwargs)

        with patch("podcast_automate.scripting.load_research", side_effect=self.research_with_extra_source), \
             patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.script_model(rounds, plan=plan, invoke=confirming)), \
             patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["teaching"].error or run.stages["review"].error)
        work = self.root / "runs" / run.run_id
        self.assertEqual([p.relative_to(work / "teaching").as_posix() for p in sorted((work / "teaching").glob("ep_*/supplement*"))],
                         ["ep_001/supplement"])
        row = next(row for row in self.probes_of(run) if row["text"] == gap)
        self.assertEqual((row["status"], row["settled_by"], row["owner_episodes"]),
                         ("hits_read_confirmed", "ep_001", ["ep_001"]))
        self.assertEqual([h["reference"] for h in row["hits"]], ["src_extra#sec_001"])
        self.assertFalse((work / "teaching/ep_002/research_needed.json").exists())

    def test_hits_the_research_already_read_are_not_routed_again(self):
        """Ontologies, 2026-09-27: 102 of 140 hits had been read by the research's question readers, yet the
        script lane counted all of them as unread and sent 18 gaps to one supplement. A hit the research read
        settles its row, as the research lane's own closing probe does, also in a probe file saved before this
        rule; the run needs no supplement round and a resume leaves the file unchanged."""
        rounds = []
        with patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value=self.SEEDED), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.script_model(rounds)), \
             patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            plan = run_script(self.root, plan_only=True)
            work = self.root / "runs" / plan.run_id
            probes = self.probes_of(plan)
            self.assertEqual([(row["status"], row["settled_by"]) for row in probes], [("hits_read_confirmed", "research")])
            read = {ref for row in read_value(self.research_ledger())["tasks"].values() for ref in row["read_refs"]}
            self.assertEqual(probes[0]["research_read"], [h["reference"] for h in probes[0]["hits"] if h["reference"] in read])
            # A file saved before the rule: the row as the probe wrote it, unread.
            write_json(work / "gap_probes.json", [{key: value for key, value in row.items()
                                                   if key not in {"research_read", "settled_by", "unread_references"}}
                                                  | {"status": "hits_unread"} for row in probes])
            run = run_script(self.root, resume=True, run_id=plan.run_id, approved_plan_hash=outline_hash(work))
            self.assertEqual(run.status, "completed")
            self.assertEqual(rounds, [])
            self.assertFalse((work / "teaching/ep_001/research_needed.json").exists())
            settled = self.probes_of(run)
            self.assertEqual([(row["status"], row["settled_by"]) for row in settled], [("hits_read_confirmed", "research")])
            before = (work / "gap_probes.json").read_bytes()
            run_script(self.root, resume=True, run_id=plan.run_id)
        self.assertEqual((work / "gap_probes.json").read_bytes(), before)

    def test_a_confirmed_gap_keeps_the_reason_written_after_it(self):
        rounds = []
        gap = "The update of the bias term for an overloaded expert is missing."

        def confirming(prompt, schema, version, **kwargs):
            if schema is FoundationSupplement:
                self.calls.append(schema)
                data = json.loads(prompt.splitlines()[-1])
                return FoundationSupplement(explanations=[], remaining_gaps=[
                    f"'{question}' The pinned section names the update, not its size." for question in data["questions"]])
            return self.invoke(prompt, schema, version, **kwargs)

        with patch("podcast_automate.scripting.load_research", side_effect=self.research_with_extra_source), \
             patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value={"gap_extra": gap}), \
             patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.script_model(rounds, plan=two_episode_plan(("f_extra",)), invoke=confirming)), \
             patch("podcast_automate.sources.download", return_value=(HTML, "text/html", "https://example.org/paper0")):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["teaching"].error)
        self.assertEqual([(row["status"], row["settled_by"], row["confirmation_note"]) for row in self.probes_of(run)],
                         [("hits_read_confirmed", "ep_002", "The pinned section names the update, not its size.")])

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

    def test_a_quote_across_a_line_break_hyphen_is_verbatim_and_a_paraphrase_is_not(self):
        """Asimov ep_012, 2026-09-29: the PDF split "pol- icies" at a line end, and the supplement's exact quote
        "institutions and policies" was rejected three times as invented. The dossier's rule applies here too."""
        context = [{"source_id": "src_pdf", "sections": [{"reference": "src_pdf#s1",
                    "text": "Which one dominates depends on the institutions and pol- icies that societies choose."}]}]

        def supplement(excerpt):
            return FoundationSupplement(explanations=[{
                "questions": ["How are scores compared?"], "finding_ids": ["f_energy"], "claim_contract": claim_contract(),
                "explanation": "Institutions decide which force dominates.",
                "evidence": [{"reference": "src_pdf#s1", "excerpt": excerpt}]}], remaining_gaps=[])
        self.assertEqual(validate_supplement(supplement("institutions and policies"), ["How are scores compared?"],
                                             self.entry, context, self.dossier), [])
        errors = validate_supplement(supplement("institutions and laws"), ["How are scores compared?"],
                                     self.entry, context, self.dossier)
        self.assertIn("Eine ergänzende Aussage hat keinen gültigen Textbeleg.", errors)

    def test_a_question_answered_in_part_may_also_stand_as_a_remaining_gap(self):
        """Asimov ep_014, 2026-09-29: the question stated what is known and what stays open; the supplement
        answered the known part and named the question a gap, and the exact-once rule rejected that three
        times without naming a defect. Now each question counts once it appears at least once."""
        gap = self.SEEDED["gap_seed"]
        probes = [{"question": gap, "references": ["src_a#sec_1"]}]
        context = [{"source_id": "src_a", "sections": [{"reference": "src_a#sec_1", "text": "Lower energy represents compatibility."}]}]
        both = FoundationSupplement(explanations=[{
            "questions": [gap], "finding_ids": ["f_energy"], "claim_contract": claim_contract(),
            "explanation": "A lower energy means a better fit.",
            "evidence": [{"reference": "src_a#sec_1", "excerpt": "Lower energy represents compatibility"}]}],
            remaining_gaps=[gap])
        self.assertEqual(validate_supplement(both, [gap], self.entry, context, self.dossier, probes), [])
        other = both.model_copy(update={"remaining_gaps": [gap, "A question nobody asked."]})
        self.assertTrue(validate_supplement(other, [gap], self.entry, context, self.dossier, probes))

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
            # The run's level, lowered only where a stage asks at most at medium (STAGE_EFFORT_CAPS).
            self.assertEqual(adapter.reasoning_effort, stage_effort(kwargs["prompt_version"], "high"))
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
