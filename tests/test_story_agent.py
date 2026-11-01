"""The story agent (story_agent.py, D-181): its checks, its folder and a whole run written by a fake session."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from podcast_automate.editorial import EPISODE_FRAMING
from podcast_automate.episode_audio import reviewed_episode
from podcast_automate.models import Chapter, EpisodeScript, RunManifest, Segment
from podcast_automate.script_budget import calls_per_episode
from podcast_automate.script_checks import SCRIPT_REVIEW_VERSION, word_budget
from podcast_automate.script_models import EpisodePlan, ScenePlan, ScriptIssue, ScriptReview, SeriesPlan
from podcast_automate.script_pipeline import ScriptRun
from podcast_automate.script_checks import outline_hash
from podcast_automate.scripting import STORY_STAGES, adopt_writer, run_script
from podcast_automate.storage import read_yaml, write_json
from podcast_automate.story_agent import (ALLOWED_TOOLS, CritiqueScores, OrientationCheck, OrientationItem,
                                          OrientationReview, OrientationSheet, Scale, StoryCritique, draft_errors,
                                          echo_errors, planned_entry, prepare, session_command, session_timeout,
                                          task_text, used_documents)
from tests.script_fixtures import ScriptProjectCase

# A stand-in for the Claude Code CLI: it answers the login check, then acts as the agent would, reading material.json,
# writing plan.md and draft.json, running check.py and critique.py, and printing the session's JSON result.
FAKE_AGENT = r'''
import json, subprocess, sys
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    sys.exit(0)
assert "-p" in args, args
assert args[args.index("--tools") + 1] == "Read,Write,Edit,Glob,Grep,Bash", args
allowed = args[args.index("--allowedTools") + 1]
assert "Bash(python check.py:*)" in allowed and "WebSearch" not in allowed and "Bash(*)" not in allowed, allowed
material = json.load(open("material.json", encoding="utf-8"))
length = material["length"]
words = (length["minimum_words"] + length["target_words"]) // 2
filler = ["Kerze", "Fenster", "Brücke", "Wolke", "Garten", "Lampe", "Hafen", "Stufe", "Mantel", "Faden"]
segments = []
for number, scene in enumerate(material["episode"]["scenes"], 1):
    text = " ".join(filler[i % len(filler)] for i in range(max(3, words // (2 * len(material["episode"]["scenes"])))))
    segments.append({"segment_id": f"{scene['scene_id']}_a", "scene_id": scene["scene_id"],
                     "chapter_id": scene["scene_id"], "speaker_id": "host_a", "text": text + ".",
                     "knowledge_refs": scene["finding_ids"]})
    segments.append({"segment_id": f"{scene['scene_id']}_b", "scene_id": scene["scene_id"],
                     "chapter_id": scene["scene_id"], "speaker_id": "host_b",
                     "text": "Darauf wäre ich nie gekommen, erzähl weiter.", "knowledge_refs": []})
draft = {"schema_version": "1.0", "episode_id": material["episode"]["episode_id"], "title": "Eine Probe",
         "purpose": "deep_dive", "chapters": [{"chapter_id": s["scene_id"], "title": s["title"]}
                                              for s in material["episode"]["scenes"]],
         "segments": segments}
open("plan.md", "w", encoding="utf-8").write("# Plan\n\nThe turn: a probe.\n")
json.dump(draft, open("draft.json", "w", encoding="utf-8"), ensure_ascii=False)
check = subprocess.run([sys.executable, "check.py"], capture_output=True, text=True, encoding="utf-8")
critique = subprocess.run([sys.executable, "critique.py"], capture_output=True, text=True, encoding="utf-8")
json.dump({"check": check.stdout, "critique": critique.stdout}, open("agent_log.json", "w", encoding="utf-8"))
print(json.dumps({"type": "result", "result": "Finished the episode.", "is_error": False, "num_turns": 7,
                  "duration_ms": 1234, "total_cost_usd": 1.5, "usage": {"input_tokens": 10, "output_tokens": 20}}))
'''


def fake_agent(folder):
    script = Path(folder) / "fake_claude.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    return [sys.executable, str(script)]


def entry():
    return EpisodePlan(episode_id="ep_001", title="Probe", central_question="Was geschah?", target_minutes=1,
                       prerequisite_episodes=[], finding_ids=["f_a", "f_b"], deferred_questions=[],
                       series_role="Eine Probe.", scenes=[
                           ScenePlan(scene_id="s1", title="Eins", question="Was?", purpose="worked_example",
                                     finding_ids=["f_a"], explanation_steps=["Erzählen."]),
                           ScenePlan(scene_id="s2", title="Zwei", question="Warum?", purpose="synthesis",
                                     finding_ids=["f_b"], explanation_steps=["Deuten."])])


def segment(key, scene, speaker, text, refs=()):
    return Segment(segment_id=key, scene_id=scene, chapter_id=scene, speaker_id=speaker, text=text,
                   knowledge_refs=list(refs))


class StoryAgentChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_own_chapters_must_hold_every_core_finding_once(self):
        (self.folder / "outline.json").write_text(json.dumps({"chapters": [
            {"scene_id": "c1", "title": "Die Nacht", "question": "Wer kam?", "finding_ids": ["f_a", "f_b"]},
            {"scene_id": "c2", "title": "Das Grab", "question": "Wo liegt er?", "finding_ids": []}]}), encoding="utf-8")
        planned, defects = planned_entry(entry(), self.folder)
        self.assertEqual(defects, [])
        self.assertEqual([s.scene_id for s in planned.scenes], ["c1", "c2"])
        self.assertEqual(planned.finding_ids, ["f_a", "f_b"])
        (self.folder / "outline.json").write_text(json.dumps({"chapters": [
            {"scene_id": "c1", "title": "Die Nacht", "question": "Wer kam?", "finding_ids": ["f_a"]}]}),
            encoding="utf-8")
        planned, defects = planned_entry(entry(), self.folder)
        self.assertEqual(planned, entry())
        self.assertEqual(len(defects), 2)

    def test_an_opening_that_only_repeats_the_other_host_is_an_echo(self):
        script = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                               chapters=[Chapter(chapter_id="s1", title="Eins")],
                               segments=[segment("a", "s1", "host_a", "Er ließ sie alle aufhängen."),
                                         segment("b", "s1", "host_b", "Alle. Und dann?"),
                                         segment("c", "s1", "host_a", "Dann holte er die Beute zurück."),
                                         segment("d", "s1", "host_b", "Das hätte ich nicht erwartet.")])
        errors = echo_errors(script)
        self.assertEqual(len(errors), 1)
        self.assertIn("b:", errors[0])

    def test_the_draft_is_held_to_the_run_checks_and_the_length_cap(self):
        prepare(self.folder, material={"episode": entry().model_dump()},
                context={"entry": entry().model_dump(), "roles": ["host_a", "host_b"], "assembled": False,
                         "sources": [], "length": {"minimum_words": 1, "target_words": 4}, "root": str(self.folder)},
                language="de-DE", minutes=1)
        script = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                               chapters=[Chapter(chapter_id="s1", title="Eins"), Chapter(chapter_id="s2", title="Zwei")],
                               segments=[segment("a", "s1", "host_a", "Eins zwei drei.", ["f_a"]),
                                         segment("b", "s2", "host_b", "Vier fünf sechs sieben.", ["f_b"])])
        errors = draft_errors(self.folder, script)
        self.assertTrue(any("7 words" in error for error in errors), errors)

    def test_a_segment_cites_only_corpus_sections_the_agent_had(self):
        """D-182: source_refs name a section of material.json or one recorded in sources_used.json."""
        prepare(self.folder, material={"episode": entry().model_dump(),
                                       "sections": [{"reference": "src_a#sec_1", "text": "Gegeben."}]},
                context={"entry": entry().model_dump(), "roles": ["host_a", "host_b"], "assembled": False,
                         "sources": [], "length": {"minimum_words": 1, "target_words": 40}, "root": str(self.folder)},
                language="de-DE", minutes=1)
        (self.folder / "sources_used.json").write_text(json.dumps([{"reference": "src_b#sec_9", "why": "Die Bulle."}]),
                                                       encoding="utf-8")
        corpus = [("src_a#sec_1", "A", "Gegeben."), ("src_b#sec_9", "B", "Die Bulle Gregors IX.")]

        def cited(*refs):
            script = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                                   chapters=[Chapter(chapter_id="s1", title="Eins"), Chapter(chapter_id="s2", title="Zwei")],
                                   segments=[segment("a", "s1", "host_a", "Eins zwei drei.", ["f_a"]),
                                             segment("b", "s2", "host_b", "Vier fünf sechs.", ["f_b"])])
            script.segments[1].source_refs = list(refs)
            return [error for error in draft_errors(self.folder, script, corpus) if "source_refs" in error]
        self.assertEqual(cited("src_a#sec_1", "src_b#sec_9"), [])
        self.assertEqual(len(cited("src_c#sec_4")), 1)
        self.assertEqual(len(cited("Thietmar")), 2, "neither in the corpus's form nor a section it had")

    def test_a_segment_citing_only_sections_is_judged_against_them_and_only_in_a_story_run(self):
        """D-182: the review's receipt for such a segment is preserved or drift against its sections, never
        no_research_claim; a writing-prompt run refuses source_refs."""
        from podcast_automate.script_evidence import receipt_defects, settle_receipts
        from podcast_automate.evidence_models import SegmentClaimCheck
        told = segment("b", "s1", "host_b", "Der Bischof sprach die Scheidung aus.")
        told.source_refs = ["src_a#sec_2"]
        script = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                               chapters=[Chapter(chapter_id="s1", title="Eins")], segments=[told])

        def check(verdict, sources=()):
            return SegmentClaimCheck(segment_id="b", quote="Der Bischof", finding_ids=[], verdict=verdict,
                                     changed_fields=[], source_refs=list(sources), reason="Geprüft.")
        sections = {"src_a#sec_2"}
        self.assertEqual(receipt_defects(check("preserved", ["src_a#sec_2"]), told, {"f_a"}, sections), [])
        self.assertTrue(receipt_defects(check("no_research_claim"), told, {"f_a"}, sections))
        self.assertTrue(receipt_defects(check("preserved", ["src_z#sec_1"]), told, {"f_a"}, sections))
        review = ScriptReview(issues=[], limitations=[], claim_checks=[check("no_research_claim")])
        settled = settle_receipts(review, script).claim_checks[0]
        self.assertEqual((settled.verdict, settled.source_refs), ("preserved", ["src_a#sec_2"]))
        # A writing-prompt run never cites sections; a story run only those its review is given.
        prompt_run = SimpleNamespace(config=None, dossier=SimpleNamespace(assembled=False), story_written=False)
        self.assertTrue(any("Leave source_refs empty" in error for error in ScriptRun.script_errors(prompt_run, script,
                                                                                                    entry())))
        story_run = SimpleNamespace(config=None, dossier=SimpleNamespace(assembled=False), story_written=True,
                                    source_sections=lambda episode: ({"src_a#sec_9"}, {}))
        self.assertTrue(any("src_a#sec_2" in error for error in ScriptRun.script_errors(story_run, script, entry())))

    def test_the_show_notes_name_the_sources_of_cited_corpus_sections(self):
        from podcast_automate.script_artifacts import render_show_notes
        told = segment("a", "s1", "host_a", "Der Bischof sprach die Scheidung aus.", ["f_a"])
        told.source_refs = ["src_e#sec_2"]
        script = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                               chapters=[Chapter(chapter_id="s1", title="Eins")], segments=[told])
        dossier = SimpleNamespace(findings=[], source_assessments=[])
        source_map = {"src_e": SimpleNamespace(title="Regesta diplomatica", final_url="https://e.org/regesta",
                                               raw_path="sources/raw/e.html")}
        notes = render_show_notes(script, entry(), dossier, source_map, research_id="run_r", run_id="run_s",
                                  language="de-DE")
        self.assertIn("[Regesta diplomatica](https://e.org/regesta)", notes)

    def test_a_story_run_keeps_only_review_verdicts_given_with_the_corpus_rule(self):
        from podcast_automate.script_pipeline import saved_verdict_stands
        passed = ScriptReview(issues=[], limitations=[])
        self.assertTrue(saved_verdict_stands(SCRIPT_REVIEW_VERSION + "+corpus", passed, story=True))
        self.assertFalse(saved_verdict_stands(SCRIPT_REVIEW_VERSION, passed, story=True))
        self.assertTrue(saved_verdict_stands(SCRIPT_REVIEW_VERSION, passed))

    def test_the_review_also_gets_the_corpus_sections_the_agent_used(self):
        folder = self.folder / "story" / "ep_001"
        folder.mkdir(parents=True)
        (folder / "sources_used.json").write_text(json.dumps([
            {"reference": "src_a#sec_2", "why": "Die Scheidung."}, {"reference": "src_b#sec_9", "why": "Die Bulle."}]),
            encoding="utf-8")
        corpus = [("src_a#sec_1", "A", "Gegeben."), ("src_a#sec_2", "A", "Die Scheidung vor dem Bischof."),
                  ("src_b#sec_9", "B", "Die Bulle Gregors IX.")]
        given = [{"source_id": "src_a", "title": "A", "sections": [{"reference": "src_a#sec_1", "text": "Gegeben."}]}]
        run = SimpleNamespace(work=self.folder, root=self.folder, dossier=None, context=[], sources=None,
                              story_written=True)
        with patch("podcast_automate.script_pipeline.episode_sources", return_value=given), \
             patch("podcast_automate.story_agent.corpus_sections", return_value=corpus):
            sources = ScriptRun.review_sources(run, entry())
            self.assertEqual({d["source_id"]: [s["reference"] for s in d["sections"]] for d in sources},
                             {"src_a": ["src_a#sec_1", "src_a#sec_2"], "src_b": ["src_b#sec_9"]})
            run.story_written = False
            self.assertEqual(ScriptRun.review_sources(run, entry()), given)
        (folder / "sources_used.json").write_text(json.dumps({"reference": "src_a#sec_2"}), encoding="utf-8")
        documents, defects = used_documents(folder, self.folder, corpus)
        self.assertEqual((documents, len(defects)), ([], 1))

    def test_a_supplement_is_matched_by_the_planned_episode_not_the_agents_chapters(self):
        """A supplement's receipt binds the episode as planned; on resume selected() gives the agent's own chapters,
        which stopped the lean research test of 2026-11-01 with invalid_supplement."""
        work = self.folder
        own = entry().model_copy(update={"scenes": [ScenePlan(scene_id="c1", title="Die Nacht", question="Wer?",
                                                              purpose="worked_example", finding_ids=["f_a", "f_b"],
                                                              explanation_steps=["Erzählen."])]})
        write_json(work / "series_plan.json", {"topic": "Probe", "central_question": "Was?", "explanation_path": "Erst.",
                                               "scope_note": "Klein.", "dependencies": [], "omitted_findings": [],
                                               "episodes": [entry().model_dump()]})
        run = SimpleNamespace(work=work, root=work, config=None, story_written=True, base_dossier=None,
                              base_context=None, base_sources=None)
        with patch("podcast_automate.script_pipeline.apply_foundations", return_value=(None, None, None, [])) as apply:
            ScriptRun.refresh_foundations(run, [own])
        self.assertEqual(apply.call_args.args[3], [entry()])

    def test_a_blocking_issue_without_segments_opens_the_whole_draft_to_the_repair(self):
        """A length issue names no segment; limited to named ones it could never be repaired, and the lean research
        test of 2026-11-01 spent all three repairs on one. A named grounding issue keeps its neighbours' scope."""
        draft = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                              chapters=[Chapter(chapter_id="s1", title="Eins")],
                              segments=[segment(key, "s1", "host_a" if index % 2 == 0 else "host_b", f"Satz {key}.")
                                        for index, key in enumerate("abcdef")])
        run = SimpleNamespace(work=self.folder, root=self.folder, invoke=None)
        asked = []

        def repaired(review):
            with patch("podcast_automate.story_agent.material", return_value=({"sections": []}, {})), \
                 patch("podcast_automate.script_pipeline.corrected_call",
                       side_effect=lambda invoke, prompt, *rest: asked.append(json.loads(prompt.splitlines()[-1]))):
                ScriptRun.story_repair(run, None, entry(), draft, review)
            return asked[-1]["editable_segments"]

        length = ScriptIssue(category="scope", segment_ids=[], reason="Gut ein Viertel zu lang.")
        grounding = ScriptIssue(category="grounding", segment_ids=["c"], reason="Kein Beleg.")
        clarity = ScriptIssue(category="clarity", segment_ids=[], reason="Unklar.")
        self.assertEqual(repaired(ScriptReview(issues=[length], limitations=[])), list("abcdef"))
        self.assertEqual(repaired(ScriptReview(issues=[grounding, clarity], limitations=[])), list("bcd"))

    def test_the_task_and_the_session_offer_only_the_folder_and_its_commands(self):
        text = task_text("de-DE", 12.5, 3)
        self.assertIn("in de-DE", text)
        self.assertIn("about 12.5 minutes", text)
        self.assertIn("at most 3 times", text)
        for placeholder in ("{language}", "{minutes}", "{critiques}", "{{"):
            self.assertNotIn(placeholder, text)
        self.assertIn('[{"reference"', text)
        self.assertIn("## Opening and ending", text)
        # Ten minutes keep the two hours the lab's sessions fit in; a 45-minute episode gets six.
        self.assertEqual((session_timeout(10), session_timeout(45)), (2 * 3600, 6 * 3600))
        command = session_command(["claude"])
        self.assertEqual(command[command.index("--tools") + 1], "Read,Write,Edit,Glob,Grep,Bash")
        self.assertEqual(command[command.index("--allowedTools") + 1], ALLOWED_TOOLS)
        self.assertIn("--no-session-persistence", command)
        self.assertNotIn("WebSearch", ALLOWED_TOOLS)
        for name in ("check", "critique", "search", "read"):
            self.assertIn(f"Bash(python {name}.py:*)", ALLOWED_TOOLS)


class StoryRunTests(ScriptProjectCase):
    def story_model(self, prompt, output_type, directory, **kwargs):
        if output_type is OrientationSheet:
            self.calls.append(output_type)
            return OrientationSheet(items=[OrientationItem(name="Probe", kind="term", orientation="Ein Begriff.",
                                                           certain=True),
                                           OrientationItem(name="Zweifel", kind="term", orientation="Unsicher.",
                                                           certain=False)]), {}
        if output_type is OrientationReview:
            self.calls.append(output_type)
            return OrientationReview(checks=[OrientationCheck(name="Probe", verdict="correct"),
                                             OrientationCheck(name="Zweifel", verdict="uncertain")]), {}
        if output_type is StoryCritique:
            self.calls.append(output_type)
            scale = Scale(score=8, reason="Trägt.")
            return StoryCritique(scores=CritiqueScores(tension=scale, interest=scale, storytelling=scale),
                                 keeps_from_10=[]), {}
        if output_type is ScriptReview:
            self.reviews.append((kwargs.get("prompt_version"), prompt))
        return self.model(prompt, output_type, directory, **kwargs)

    def setUp(self):
        super().setUp()
        self.reviews = []

    def test_a_story_run_writes_each_episode_with_the_agent_and_publishes_its_plan(self):
        cli = fake_agent(self.root.parent)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model), \
             patch("podcast_automate.claude_code.claude_command", return_value=cli):
            run = run_script(self.root, writer="agent")
        self.assertEqual(run.status, "completed", {k: v.error for k, v in run.stages.items() if v.error})
        self.assertEqual(tuple(run.stages), STORY_STAGES)
        work = self.root / "runs" / run.run_id
        folder = work / "story" / "ep_001"
        self.assertEqual(json.loads((work / "script_request.json").read_text(encoding="utf-8"))["writer"], "agent")
        # The teaching stage made no design, but the story material and the checked orientation, of which only the
        # entry the check found correct reaches the agent.
        self.assertFalse((work / "teaching" / "ep_001" / "plan.json").exists())
        material = json.loads((folder / "material.json").read_text(encoding="utf-8"))
        self.assertEqual([item["name"] for item in material["orientation"]], ["Probe"])
        # The plan's own length: the agent counts its words, so the writing prompt's 1.3 made it a quarter too long.
        planned = SeriesPlan.model_validate_json((work / "series_plan.json").read_text(encoding="utf-8")).episodes[0]
        self.assertEqual(material["length"], word_budget(planned))
        # The review's own rule for opening and ending, which both first real reviews raised (2026-11-01).
        self.assertEqual(material["framing"], EPISODE_FRAMING.strip())
        # The agent's commands ran: the check passed and the critique came back from a call of the run.
        log = json.loads((folder / "agent_log.json").read_text(encoding="utf-8"))
        self.assertTrue(json.loads(log["check"])["passed"], log["check"])
        self.assertEqual(json.loads(log["critique"])["scores"]["tension"]["score"], 8)
        self.assertIn(StoryCritique, self.calls)
        # The review knows that a story agent's segments may cite corpus sections (D-182).
        self.assertTrue(self.reviews)
        for version, prompt in self.reviews:
            self.assertTrue(version.startswith(SCRIPT_REVIEW_VERSION + "+corpus"), version)
            self.assertIn("A story agent wrote this episode", prompt)
        # The session is one call of the run, with what the CLI reported.
        sessions = [path.parent for path in (work / "calls").glob("call_*/output_schema.json")
                    if json.loads(path.read_text(encoding="utf-8"))["title"] == "StorySession"]
        self.assertEqual(len(sessions), 1)
        metadata = json.loads((sessions[0] / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["reported_cost_usd"], 1.5)
        self.assertEqual(metadata["model"], "claude-opus-5-5")
        # No narration review and no polishing; the draft was reviewed as the agent wrote it.
        self.assertFalse((work / "narration").exists())
        self.assertFalse((work / "polishing").exists())
        self.assertEqual(json.loads((work / "reviewed" / "ep_001.json").read_text(encoding="utf-8")),
                         json.loads((work / "drafts" / "ep_001.json").read_text(encoding="utf-8")))
        # Publishing shows the agent's plan in place of a teaching plan, and the report what the agent did.
        episode = self.root / "episodes" / "ep_001"
        self.assertTrue((episode / "story_plan.md").is_file())
        self.assertFalse((episode / "teaching_plan.yaml").exists())
        report = read_yaml(self.root / "reports" / "script_quality.yaml")["episodes"]["ep_001"]
        self.assertEqual(report["story_agent"]["critiques"], [{"tension": 8, "interest": 8, "storytelling": 8}])
        self.assertEqual(report["story_agent"]["reported_cost_usd"], 1.5)
        self.assertNotIn("dialogue_polish", report)
        # The recording takes the agent's episode as reviewed: it has a story stage instead of writing and polishing
        # (2026-11-01: Asimov v2 ep_007 stopped there with invalid_script).
        _, script, _ = reviewed_episode(self.root, self.config, "ep_001")
        self.assertEqual(script.model_dump(), json.loads((work / "reviewed" / "ep_001.json").read_text(encoding="utf-8")))

    def test_a_run_waiting_for_its_outline_takes_the_writer_switch(self):
        """The Studio's outline card offers the writer until the outline is approved (2026-11-01: the operator saw only
        approve or revise for a run planned with the writing prompt). Only stages change, so the approval's binding
        holds, and after the approval the agent writes."""
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model):
            planned = run_script(self.root, plan_only=True)
        work = self.root / "runs" / planned.run_id
        before = outline_hash(work)
        self.assertEqual(adopt_writer(self.root, "agent", planned.run_id), planned.run_id)
        manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
        self.assertEqual(tuple(manifest.stages), STORY_STAGES)
        self.assertEqual(manifest.stages["planning"].status, "completed")
        self.assertEqual(outline_hash(work), before)
        self.assertEqual(json.loads((work / "writer_switch.json").read_text(encoding="utf-8"))["writer"], "agent")
        self.assertIsNone(adopt_writer(self.root, "agent", planned.run_id), "it already has the writer")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model),              patch("podcast_automate.claude_code.claude_command", return_value=fake_agent(self.root.parent)):
            done = run_script(self.root, resume=True, run_id=planned.run_id, approved_plan_hash=before)
        self.assertEqual(done.status, "completed", {k: v.error for k, v in done.stages.items() if v.error})
        self.assertTrue((work / "story" / "ep_001" / "draft.json").is_file())
        self.assertIsNone(adopt_writer(self.root, "prompt", planned.run_id), "a run past its outline keeps its writer")

    def test_one_episode_of_an_approved_outline_is_written_without_planning_again(self):
        """2026-11-01: approving Asimov v2's outline would have started 20 agent sessions at once; the operator wanted
        one episode first. A new run takes the approved outline of the series run and writes that episode only."""
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model):
            planned = run_script(self.root, plan_only=True)
        work = self.root / "runs" / planned.run_id
        with self.assertRaises(Exception) as caught:
            run_script(self.root, episode="ep_001", outline_run=planned.run_id, writer="agent")
        self.assertEqual(caught.exception.code, "plan_approval_required")
        write_json(work / "plan_approval.json", {"plan_hash": outline_hash(work), "approved_at": "2026-11-01T00:00:00+00:00"})
        with self.assertRaises(Exception) as caught:
            run_script(self.root, outline_run=planned.run_id, writer="agent")
        self.assertEqual(caught.exception.code, "invalid_request", "an outline's episode is named")
        self.calls.clear()
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model),              patch("podcast_automate.claude_code.claude_command", return_value=fake_agent(self.root.parent)):
            probe = run_script(self.root, episode="ep_001", outline_run=planned.run_id, writer="agent")
        self.assertEqual(probe.status, "completed", {k: v.error for k, v in probe.stages.items() if v.error})
        self.assertNotEqual(probe.run_id, planned.run_id)
        self.assertEqual(tuple(probe.stages), STORY_STAGES)
        self.assertNotIn(SeriesPlan, self.calls, "the outline is not planned again")
        probe_work = self.root / "runs" / probe.run_id
        self.assertEqual((probe_work / "series_plan.json").read_bytes(), (work / "series_plan.json").read_bytes())
        self.assertTrue((self.root / "episodes" / "ep_001" / "script.yaml").is_file())
        series = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
        self.assertEqual(series.stages["teaching"].status, "pending", "the series run waits as it was")

    def test_a_resumed_run_keeps_its_writer(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model):
            planned = run_script(self.root, plan_only=True)
        manifest = RunManifest.model_validate(read_yaml(self.root / "runs" / planned.run_id / "run_manifest.yaml"))
        self.assertNotIn("story", manifest.stages)
        with self.assertRaises(Exception) as caught:
            run_script(self.root, resume=True, run_id=planned.run_id, writer="agent")
        self.assertEqual(caught.exception.code, "invalid_request")
        # A revision's feedback is read only by the writing prompt.
        with self.assertRaises(Exception) as caught:
            run_script(self.root, revise="ep_001", feedback="Kürzer.", writer="agent")
        self.assertEqual(caught.exception.code, "invalid_revision")

    def test_the_budget_counts_the_story_stage_instead_of_writing_narration_and_polishing(self):
        # Teaching: story material, orientation sheet and its check; story: the session and a critique; review: the
        # script review alone, with no listener readback, editorial or teaching review.
        self.assertEqual(calls_per_episode(STORY_STAGES), 3 + 2 + 1)


if __name__ == "__main__":
    unittest.main()
