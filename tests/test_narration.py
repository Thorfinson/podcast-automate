"""The narration stage (D-180): story material, the narration review and its rework, and a run with and without it."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from podcast_automate import prompts
from podcast_automate.errors import AppError
from podcast_automate.models import EpisodeScript, Segment
from podcast_automate.research_models import Evidence
from podcast_automate.teaching_research import FoundationExplanation, FoundationSupplement
from podcast_automate.narration import (REWORK_REFUSED, MissingStory, NarrationPoint, NarrationReview, StoryItem,
                                        StoryMaterial,
                                        allowed_sections, length_errors, narrate_episode, narration_metrics, sanitized,
                                        scope_errors, story_material)
from podcast_automate.script_checkpoints import baseline_path, finished
from podcast_automate.script_pipeline import WRITE_EPISODE_VERSION
from podcast_automate.scripting import run_script
from podcast_automate.studio_scripts import review_notes
from podcast_automate.storage import digest
from podcast_automate.teaching import TeachingPlan
from tests import script_fixtures as fixtures
from tests.narration_fixtures import narration_review
from tests.teaching_fixtures import teaching_response

SECTION = "Am 10. Februar 1389 hängt Graf Günther von Schwarzburg in Jena sein Siegel an den Brief und verkauft Saalfeld."


def point(category="compressed_story", fix="rewrite", segments=("seg_002",), items=()):
    return NarrationPoint(category=category, segment_ids=list(segments), problem="Told in one sentence.",
                          correction="Tell who sold what, where and why.", fix=fix, item_ids=list(items))


def reworked(text="Graf Günther verkauft in Jena den Hof; ein niedriger Wert passt."):
    script = fixtures.example_script()
    segments = [script.segments[0], script.segments[1].model_copy(update={"text": text})]
    return script.model_copy(update={"segments": segments})


class StoryMaterialTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.entry = fixtures.example_plan().episodes[0]
        finding = SimpleNamespace(id="f_energy", statement="Saalfeld was sold in 1389.", illustration="",
                                  evidence=[SimpleNamespace(reference="src_a#sec_1")])
        other = SimpleNamespace(id="f_other", statement="Another episode's finding.", illustration="",
                                evidence=[SimpleNamespace(reference="src_a#sec_2")])
        self.dossier = SimpleNamespace(findings=[finding, other])
        self.sources = [{"source_id": "src_a", "sections": [
            {"reference": "src_a#sec_1", "text": SECTION, "page": 1},
            {"reference": "src_a#sec_2", "text": "Ein Abschnitt, den kein Befund dieser Folge zitiert.", "page": 2}]}]
        self.config = SimpleNamespace(language="de-DE", topic="Test topic", central_question="")

    def item(self, item_id, **changes):
        values = {"item_id": item_id, "kind": "scene", "text": "Günther siegelt in Jena den Verkauf.",
                  "source_ref": "src_a#sec_1", "anchor": "hängt Graf Günther von Schwarzburg in Jena sein Siegel",
                  "finding_ids": ["f_energy"], "in_finding": False}
        return StoryItem(**{**values, **changes})

    def test_only_sections_the_episode_cites_are_offered_and_a_bad_item_is_dropped(self):
        prompts_seen = []
        answers = [StoryMaterial(items=[self.item("story_01"),
                                        self.item("story_02", source_ref="src_a#sec_2"),
                                        self.item("story_03", anchor="Günther verkaufte die Stadt an die Wettiner"),
                                        self.item("story_04", finding_ids=["f_other"])]),
                   StoryMaterial(items=[self.item("story_01"), self.item("story_02", kind="event")])]

        def invoke(prompt, schema, version):
            prompts_seen.append((prompt, version))
            return answers[len(prompts_seen) - 1]
        items = story_material(self.config, self.entry, self.dossier, self.sources, invoke, self.folder)
        offered = json.loads(prompts_seen[0][0].splitlines()[-1])["allowed_sections"]
        self.assertEqual([(s["reference"], s["finding_ids"]) for s in offered], [("src_a#sec_1", ["f_energy"])])
        # Three of four refused: asked once more with the defects named.
        self.assertEqual([version for _, version in prompts_seen], ["story_material.v2-missing", "story_material.v2-missing+retry"])
        self.assertIn("rejected_items", prompts_seen[1][0].splitlines()[-1])
        self.assertEqual([item["item_id"] for item in items], ["story_01", "story_02"])
        saved = json.loads((self.folder / "story_material.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["items"], items)
        self.assertEqual(story_material(self.config, self.entry, self.dossier, self.sources, invoke, self.folder), items)
        self.assertEqual(len(prompts_seen), 2, "the saved material is reused for the same prompt")

    def test_sections_without_a_citing_finding_cost_no_call(self):
        self.dossier.findings[0].evidence = [SimpleNamespace(reference="src_b#sec_9")]
        items = story_material(self.config, self.entry, self.dossier, self.sources, self.fail, self.folder)
        self.assertEqual(items, [])
        self.assertEqual(allowed_sections(self.entry, self.dossier, self.sources), [])


class NarrateEpisodeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.draft = fixtures.example_script()
        self.calls = []

    def run_narration(self, first, *, repair=None, follow=None, signature="sig"):
        def review(script):
            self.calls.append("review")
            return first

        def rework(script, points):
            self.calls.append(("repair", [p.category for p in points]))
            if isinstance(repair, Exception):
                raise repair
            return repair

        def followup(script, previous, changed):
            self.calls.append(("followup", changed))
            return follow
        return narrate_episode(self.folder, signature, self.draft, review=review, repair=rework, followup=followup)

    def test_a_draft_without_points_stands_unchanged_after_one_call(self):
        final, result = self.run_narration(narration_review())
        self.assertEqual((final, result["status"], self.calls), (self.draft, "unchanged", ["review"]))
        self.assertEqual(result["draft_digest"], result["narrated_digest"])

    def test_rewrite_points_are_reworked_and_reviewed_within_the_changed_segments(self):
        follow = narration_review().model_copy(update={"points": [point("flat_ending", "note")]})
        follow.scores.storytelling.score = 7
        first = narration_review([point(), point("missing_story", "research", ())])
        final, result = self.run_narration(first, repair=reworked(), follow=follow)
        self.assertEqual(self.calls, ["review", ("repair", ["compressed_story"]), ("followup", ["seg_002"])])
        self.assertEqual((final, result["status"]), (reworked(), "repaired"))
        self.assertEqual((result["first_scores"]["storytelling"]["score"], result["scores"]["storytelling"]["score"]),
                         (5, 7))
        self.assertEqual([n["category"] for n in result["notes"]], ["flat_ending"])
        self.assertEqual([n["category"] for n in result["research_points"]], ["missing_story"])
        self.assertEqual(json.loads((self.folder / "script.json").read_text(encoding="utf-8")), reworked().model_dump())
        # A resume with the same inputs makes no call.
        self.calls.clear()
        self.assertEqual(self.run_narration(first, repair=reworked(), follow=follow)[0], reworked())
        self.assertEqual(self.calls, [])

    def test_a_refused_rework_keeps_the_draft_and_reports_its_points(self):
        refused = AppError("still too short", code=REWORK_REFUSED, status="blocked")
        final, result = self.run_narration(narration_review([point()]), repair=refused)
        self.assertEqual((final, result["status"]), (self.draft, "kept_draft"))
        self.assertEqual([n["category"] for n in result["notes"]], ["compressed_story"])
        self.assertIn("still too short", result["repair_failed"])

    def test_a_stop_for_quota_or_budget_ends_the_call_and_is_asked_again_on_resume(self):
        quota = AppError("no quota", code="quota_exhausted", status="waiting_for_quota")
        with self.assertRaises(AppError) as caught:
            self.run_narration(narration_review([point()]), repair=quota)
        self.assertEqual(caught.exception.code, "quota_exhausted")
        self.calls.clear()
        final, result = self.run_narration(narration_review([point()]), repair=reworked(), follow=narration_review())
        self.assertEqual(self.calls[0][0], "repair", "the saved review is not asked again")
        self.assertEqual(result["status"], "repaired")


class NarrationCheckTests(unittest.TestCase):
    def test_a_rework_changes_only_the_named_segments_and_keeps_the_others_in_order(self):
        draft = fixtures.example_script()
        added = reworked().model_copy(update={"segments": [*reworked().segments, Segment(
            segment_id="seg_003", scene_id="scene_example", chapter_id="scene_example", speaker_id="host_a",
            text="So kam der Hof an die Wettiner.", knowledge_refs=["f_energy"])]})
        self.assertEqual(scope_errors(draft, added, {"seg_002"}), [])
        self.assertEqual(len(scope_errors(draft, added, set())), 1, "seg_002 changed without a point naming it")
        swapped = draft.model_copy(update={"segments": list(reversed(draft.segments))})
        self.assertEqual(scope_errors(draft, swapped, set()), ["Keep the segments no point names in their order."])

    def test_a_rework_may_not_grow_the_episode_beyond_its_room(self):
        draft = fixtures.example_script()
        long = reworked(" ".join(["Wort"] * 60))
        self.assertEqual(length_errors(draft, reworked(), 10), [])
        self.assertIn("stay within about", length_errors(draft, long, 10)[0])
        self.assertEqual(length_errors(draft, long, 80), [])

    def test_points_that_name_what_is_not_there_are_trimmed_not_asked_again(self):
        draft = fixtures.example_script()
        review = narration_review([point(segments=("seg_404",), items=("story_09",)),
                                   point(segments=("seg_001", "seg_404"), items=("story_01",))])
        trimmed = sanitized(review, draft, ["story_01"])
        self.assertEqual([(p.fix, p.segment_ids, p.item_ids) for p in trimmed.points],
                         [("note", [], []), ("rewrite", ["seg_001"], ["story_01"])])

    def test_code_counts_workshop_talk_open_ends_and_repeated_questions(self):
        script = EpisodeScript(episode_id="ep_001", title="T", purpose="deep_dive",
                               chapters=[{"chapter_id": "scene_example", "title": "C"}], segments=[
            Segment(segment_id="seg_001", scene_id="scene_example", chapter_id="scene_example", speaker_id="host_a",
                    text="Das Buch haben wir nicht im Volltext gelesen, laut Wikipedia bleibt das offen."),
            Segment(segment_id="seg_002", scene_id="scene_example", chapter_id="scene_example", speaker_id="host_b",
                    text="Wer war das?"),
            Segment(segment_id="seg_003", scene_id="scene_example", chapter_id="scene_example", speaker_id="host_b",
                    text="Und warum?")])
        metrics = narration_metrics(script, "de-DE")
        self.assertEqual([hit["phrase"] for hit in metrics["workshop_phrases"]],
                         ["nicht im Volltext gelesen", "Wikipedia"])
        self.assertEqual((metrics["stays_open"], metrics["repeated_questions"]), (1, [["seg_002", "seg_003"]]))
        self.assertEqual(metrics["chapter_words"], {"scene_example": 18})
        english = narration_metrics(script.model_copy(update={"segments": [script.segments[0].model_copy(
            update={"text": "We've only examined the abstract; the full text remains open."})]}), "en-US")
        self.assertEqual(len(english["workshop_phrases"]), 2)


class NarratedRunTests(unittest.TestCase):
    """A whole script run with the narration stage, and one started before it."""

    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        self.prompts = []

    def model(self, *, story=True, first=None, follow=None, rework=None):
        def answer(prompt, output_type, directory, **kwargs):
            self.prompts.append((output_type, kwargs.get("prompt_version"), prompt))
            payload = json.loads(prompt.splitlines()[-1])
            if output_type is StoryMaterial and story:
                section = payload["allowed_sections"][0]
                anchor = " ".join(section["text"].split()[:8])
                return StoryMaterial(items=[StoryItem(item_id="story_01", kind="scene", text="Eine Szene der Quelle.",
                                                      source_ref=section["reference"], anchor=anchor,
                                                      finding_ids=section["finding_ids"][:1], in_finding=False)]), {}
            if output_type is TeachingPlan and payload.get("story_material") and "design" not in payload:
                design = teaching_response(prompt, TeachingPlan)
                return design.model_copy(update={"core_story": "Who did what, and the moment it turned.",
                                                 "story_item_ids": ["story_01"],
                                                 "story_scene_id": design.scenes[0].scene_id}), {}
            if output_type is NarrationReview:
                followup = "previous_review" in payload
                chosen = (follow if followup else first) or narration_review()
                return chosen, {}
            if output_type is EpisodeScript and str(kwargs.get("prompt_version", "")).startswith("narration_repair"):
                return rework, {}
            return self.fixture.model(prompt, output_type, directory, **kwargs)
        return answer

    def test_the_draft_is_reviewed_reworked_and_polished_from_its_narrated_form(self):
        follow = narration_review([point("missing_story", "research", ())])
        follow.scores.storytelling.score = 7
        model = self.model(first=narration_review([point(items=("story_01",))]), follow=follow, rework=reworked())
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages)
        self.assertEqual(list(run.stages), ["planning", "teaching", "writing", "narration", "polishing", "review",
                                            "publish"])
        work = self.root / "runs" / run.run_id
        result = json.loads((work / "narration/ep_001/result.json").read_text(encoding="utf-8"))
        self.assertEqual((result["status"], result["scores"]["storytelling"]["score"]), ("repaired", 7))
        self.assertTrue(finished(work, "ep_001", "narration"))
        self.assertEqual(baseline_path(work, "ep_001"), work / "narration/ep_001/script.json")
        # Polishing and the reviews start from the narrated draft.
        self.assertIn("Graf Günther verkauft", (work / "polishing/ep_001/before.md").read_text(encoding="utf-8"))
        design = TeachingPlan.model_validate_json((work / "teaching/ep_001/plan.json").read_text(encoding="utf-8"))
        self.assertEqual((design.story_item_ids, design.story_scene_id), (["story_01"], "scene_example"))
        writing = [p for schema, version, p in self.prompts
                   if schema is EpisodeScript and version == WRITE_EPISODE_VERSION]
        self.assertTrue(writing and all(prompts.text("write_episode_story") in p for p in writing))
        self.assertEqual(json.loads(writing[0].splitlines()[-1])["story_material"][0]["item_id"], "story_01")
        designs = [p for schema, version, p in self.prompts if schema is TeachingPlan]
        self.assertTrue(all(prompts.text("teaching_design_story") in p for p in designs))
        plans = [p for schema, version, p in self.prompts if version and version.startswith("series_plan.")]
        self.assertTrue(plans and all(prompts.text("series_plan_density") in p for p in plans))
        # The scores and what stays open reach the report and the Studio's review notes.
        report = yaml.safe_load((self.root / "reports/script_quality.yaml").read_text(encoding="utf-8"))
        narration = report["episodes"]["ep_001"]["narration"]
        self.assertEqual([p["category"] for p in narration["research_points"]], ["missing_story"])
        notes = review_notes(report["episodes"]["ep_001"])
        self.assertEqual(notes["narration_scores"], {"tension": 5, "interest": 5, "storytelling": 7})
        self.assertEqual([row["category"] for row in notes["narration"]], ["missing_story"])

    def test_a_review_without_points_costs_one_call_and_the_draft_goes_on_as_written(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model(story=False)):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages)
        schemas = [schema for schema, _, _ in self.prompts]
        self.assertEqual((schemas.count(StoryMaterial), schemas.count(NarrationReview)), (1, 1))
        work = self.root / "runs" / run.run_id
        self.assertEqual(json.loads((work / "narration/ep_001/script.json").read_text(encoding="utf-8")),
                         json.loads((work / "drafts/ep_001.json").read_text(encoding="utf-8")))
        design = TeachingPlan.model_validate_json((work / "teaching/ep_001/plan.json").read_text(encoding="utf-8"))
        self.assertEqual(design.core_story, "", "no story material, no core story")

    def story_research_model(self):
        """Story material that names one story its sections only point to, on the first call only."""
        asked = []

        def answer(prompt, output_type, directory, **kwargs):
            if output_type is StoryMaterial:
                asked.append(prompt)
                missing = [MissingStory(question="Was erzählt Thietmar über die Gefolgschaft des Kuchavez?",
                                        why_needed="Die Episode trägt den Streit um den Adel.",
                                        finding_ids=["f_energy", "f_unknown"])] if len(asked) == 1 else []
                self.prompts.append((output_type, kwargs.get("prompt_version"), prompt))
                return StoryMaterial(items=[], missing=missing), {}
            return self.model(story=False)(prompt, output_type, directory, **kwargs)
        return answer, asked

    def test_a_story_the_sections_only_point_to_is_researched_once_before_the_design(self):
        """D-180: the story research reads it through the supplementary research, at most once per episode, and the
        material is collected again from the sources it added."""
        model, asked = self.story_research_model()
        found = FoundationSupplement(explanations=[], remaining_gaps=[])
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.script_pipeline.research_foundations", return_value=found) as research:
            found.explanations = [FoundationExplanation(questions=["q"], finding_ids=["f_energy"], explanation="Thietmar.",
                                                        evidence=[Evidence(reference="src_x#sec_y", excerpt="Thietmar")])]
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages)
        self.assertEqual(research.call_count, 1)
        self.assertTrue(research.call_args.kwargs["optional"])
        folder = self.root / "runs" / run.run_id / "teaching/ep_001"
        self.assertEqual(json.loads((folder / "story_research.json").read_text(encoding="utf-8"))["status"], "added")
        request = json.loads((folder / "research_needed.json").read_text(encoding="utf-8"))
        self.assertEqual((request["story"], request["resolved"], request["questions"][0]["kind"]), (True, True, "evidence"))
        saved = json.loads((folder / "story_material.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["missing"][0]["finding_ids"], ["f_energy"], "only the episode's core findings")
        # The supplement left the sources as they were here, so the saved material stands and is not asked again.
        self.assertEqual(len(asked), 1)

    def test_a_story_research_that_finds_nothing_or_is_refused_never_stops_the_run(self):
        model, _ = self.story_research_model()
        refused = AppError("Die zusätzlichen Belege beantworten noch nicht alle offenen Erklärfragen.",
                           code="teaching_research_required", status="blocked")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.script_pipeline.research_foundations", side_effect=refused):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages)
        marker = json.loads((self.root / "runs" / run.run_id / "teaching/ep_001/story_research.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(marker["status"], "refused")
        self.assertIn("beantworten noch nicht", marker["reason"])

    def test_a_quota_stop_during_the_story_research_resumes_it(self):
        model, _ = self.story_research_model()
        quota = AppError("Kontingent erreicht", code="quota_exhausted", status="waiting_for_quota")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.script_pipeline.research_foundations", side_effect=quota):
            stopped = run_script(self.root)
        self.assertEqual(stopped.status, "waiting_for_quota")
        folder = self.root / "runs" / stopped.run_id / "teaching/ep_001"
        self.assertFalse((folder / "story_research.json").exists())
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.script_pipeline.research_foundations",
                   return_value=FoundationSupplement(explanations=[], remaining_gaps=[])) as research:
            resumed = run_script(self.root, resume=True, run_id=stopped.run_id)
        self.assertEqual(resumed.status, "completed", resumed.stages)
        self.assertEqual(research.call_count, 1)
        self.assertEqual(json.loads((folder / "story_research.json").read_text(encoding="utf-8"))["status"], "not_found")

    def test_a_run_started_before_the_stage_keeps_its_prompts_and_runs_without_it(self):
        with patch("podcast_automate.scripting.STAGES", ("planning", "teaching", "writing", "polishing", "review",
                                                         "publish")), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model()):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages)
        self.assertNotIn("narration", run.stages)
        schemas = [schema for schema, _, _ in self.prompts]
        self.assertNotIn(StoryMaterial, schemas)
        self.assertNotIn(NarrationReview, schemas)
        texts = [prompts.text(name) for name in ("write_episode_story", "teaching_design_story", "series_plan_density")]
        self.assertFalse(any(text in prompt for text in texts for _, _, prompt in self.prompts))
        work = self.root / "runs" / run.run_id
        self.assertFalse((work / "narration").exists())
        self.assertEqual(baseline_path(work, "ep_001"), work / "drafts/ep_001.json")


if __name__ == "__main__":
    unittest.main()
