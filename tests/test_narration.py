"""The story material of a script run (D-180), its story research, and the scope of a repair that changes only
named segments (narration.with_neighbours, scope_errors)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from podcast_automate.errors import AppError
from podcast_automate.models import Segment
from podcast_automate.research_models import Evidence
from podcast_automate.teaching_research import FoundationExplanation, FoundationSupplement
from podcast_automate.narration import (MissingStory, StoryItem, StoryMaterial, allowed_sections, scope_errors,
                                        story_material, unnamed_refs_kept, with_neighbours)
from podcast_automate.scripting import run_script
from tests import script_fixtures as fixtures

SECTION = "Am 10. Februar 1389 hängt Graf Günther von Schwarzburg in Jena sein Siegel an den Brief und verkauft Saalfeld."


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


class ScopedRepairTests(unittest.TestCase):
    def test_a_rework_changes_only_the_named_segments_and_keeps_the_others_in_order(self):
        draft = fixtures.example_script()
        added = reworked().model_copy(update={"segments": [*reworked().segments, Segment(
            segment_id="seg_003", scene_id="scene_example", chapter_id="scene_example", speaker_id="host_a",
            text="So kam der Hof an die Wettiner.", knowledge_refs=["f_energy"])]})
        self.assertEqual(scope_errors(draft, added, {"seg_002"}), [])
        self.assertEqual(len(scope_errors(draft, added, set())), 1, "seg_002 changed without a point naming it")
        swapped = draft.model_copy(update={"segments": list(reversed(draft.segments))})
        self.assertEqual(scope_errors(draft, swapped, set()), ["Keep the segments no point names in their order."])

    def test_a_rework_may_also_adjust_the_handover_just_before_and_after_a_named_segment(self):
        """Orlagau ep_014 of the narration evaluation, 2026-10-24: three reworks each changed the segment after the named
        one, and all were refused. The neighbours may change; a segment two away may not."""
        def segment(key, text):
            return Segment(segment_id=key, scene_id="scene_example", chapter_id="scene_example",
                           speaker_id="host_a" if key in {"seg_001", "seg_003"} else "host_b", text=text,
                           knowledge_refs=["f_energy"])
        draft = fixtures.example_script().model_copy(update={"segments": [
            segment(f"seg_{n:03d}", f"Satz {n}.") for n in range(1, 6)]})
        self.assertEqual(with_neighbours(draft, {"seg_003"}), {"seg_002", "seg_003", "seg_004"})
        self.assertEqual(with_neighbours(draft, {"seg_001"}), {"seg_001", "seg_002"})
        handover = draft.model_copy(update={"segments": [
            s.model_copy(update={"text": s.text + " Und dann?"}) if s.segment_id in {"seg_002", "seg_004"} else s
            for s in draft.segments]})
        self.assertEqual(scope_errors(draft, handover, with_neighbours(draft, {"seg_003"})), [])
        far = draft.model_copy(update={"segments": [
            s.model_copy(update={"text": "Anders."}) if s.segment_id == "seg_005" else s for s in draft.segments]})
        self.assertEqual(len(scope_errors(draft, far, with_neighbours(draft, {"seg_003"}))), 1)

    def test_references_a_rework_changes_outside_its_scope_are_put_back_and_its_words_still_count(self):
        """Asimov v2 ep_008, 2026-11-04: every repair copied the review's sections into the empty source_refs of
        eight unnamed segments, words unchanged, and twelve answers were refused. Such a segment is put back as it
        was; a named one keeps its new references, and a changed word outside the scope is still refused."""
        draft = fixtures.example_script()
        first, second = draft.segments
        cited = reworked(second.text).model_copy(update={"segments": [
            first.model_copy(update={"source_refs": ["src_a#sec_1"], "knowledge_refs": []}),
            second.model_copy(update={"source_refs": ["src_a#sec_2"]})]})
        kept = unnamed_refs_kept(draft, cited, {"seg_002"})
        self.assertEqual(kept.segments, [first, cited.segments[1]])
        self.assertEqual(scope_errors(draft, kept, {"seg_002"}), [])
        self.assertEqual(scope_errors(draft, cited, {"seg_002"}),
                         ["seg_001: no review point names this segment; keep it exactly as it was."])
        worded = cited.model_copy(update={"segments": [cited.segments[0].model_copy(update={"text": "Anders."}),
                                                       cited.segments[1]]})
        self.assertEqual(len(scope_errors(draft, unnamed_refs_kept(draft, worded, {"seg_002"}), {"seg_002"})), 1)

class StoryResearchTests(unittest.TestCase):
    """A whole script run whose story material names a story its sections only point to."""

    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root
        self.prompts = []

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
            return self.fixture.model(prompt, output_type, directory, **kwargs)
        return answer, asked

    def test_a_story_the_sections_only_point_to_is_researched_once_before_the_agent_writes(self):
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

if __name__ == "__main__":
    unittest.main()
