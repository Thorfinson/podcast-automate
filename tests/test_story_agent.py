"""The story agent (story_agent.py, D-181): its checks, its folder and a whole run written by a fake session."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from podcast_automate.editorial import EPISODE_FRAMING
from podcast_automate.episode_audio import reviewed_episode
from podcast_automate.models import Chapter, EpisodeScript, RunManifest, Segment, StageRecord
from podcast_automate.script_budget import calls_per_episode
from podcast_automate.script_checks import SCRIPT_REVIEW_VERSION, word_budget
from podcast_automate.script_models import EpisodePlan, ScenePlan, ScriptIssue, ScriptReview, SeriesPlan
from podcast_automate.script_pipeline import ScriptRun
from podcast_automate.script_checks import outline_hash
from podcast_automate.scripting import STORY_STAGES, approve_outline, next_episode, outline_episodes, run_script
from podcast_automate.storage import read_yaml, write_json, write_yaml
from podcast_automate.errors import AppError
from podcast_automate.models import ResearchLimits
from podcast_automate.scripting import STARTING_RATES, measured_rate, speaking_rate
from podcast_automate.story_agent import (ALLOWED_TOOLS, TARGET_SHARE, CritiquePoint, CritiqueScores, EarlierPoint,
                                          OrientationCheck, OrientationItem, OrientationReview, OrientationSheet,
                                          Scale, StoryCritique, draft_errors, earlier_episodes, echo_errors,
                                          planned_entry, prepare, request_critique, run_agent, serve_critiques,
                                          session_command, session_files, session_timeout, task_text, used_documents)
from tests.script_fixtures import ScriptProjectCase, fake_agent

# A stand-in for a session on an Anthropic key: it records the key, the dropped login token and the money cap it was
# given, and reports the session's cost.
KEY_SESSION = r'''
import json, os, sys
args = sys.argv[1:]
json.dump({"key": os.environ.get("ANTHROPIC_API_KEY"), "oauth": os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"),
           "budget": args[args.index("--max-budget-usd") + 1]}, open("seen.json", "w", encoding="utf-8"))
print(json.dumps({"type": "result", "result": "Finished.", "is_error": False, "total_cost_usd": 3.25}))
'''


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
                task="TASK")
        script = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                               chapters=[Chapter(chapter_id="s1", title="Eins"), Chapter(chapter_id="s2", title="Zwei")],
                               segments=[segment("a", "s1", "host_a", "Eins zwei drei.", ["f_a"]),
                                         segment("b", "s2", "host_b", "Vier fünf sechs sieben.", ["f_b"])])
        errors = draft_errors(self.folder, script)
        self.assertTrue(any("7 words" in error for error in errors), errors)
        # The agent reaches its target, not only the run's floor (D-184: ep_007 stopped at 89 % and ran 35:47 of 45).
        context = json.loads((self.folder / "context.json").read_text(encoding="utf-8"))
        write_json(self.folder / "context.json", {**context, "length": {"minimum_words": 10, "target_words": 10}})
        errors = draft_errors(self.folder, script)
        self.assertTrue(any("write at least length.minimum_words (10)" in error for error in errors), errors)

    def test_a_segment_cites_only_corpus_sections_the_agent_had(self):
        """D-182: source_refs name a section of material.json or one recorded in sources_used.json."""
        prepare(self.folder, material={"episode": entry().model_dump(),
                                       "sections": [{"reference": "src_a#sec_1", "text": "Gegeben."}]},
                context={"entry": entry().model_dump(), "roles": ["host_a", "host_b"], "assembled": False,
                         "sources": [], "length": {"minimum_words": 1, "target_words": 40}, "root": str(self.folder)},
                task="TASK")
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
        story_run = SimpleNamespace(config=None, dossier=SimpleNamespace(assembled=False),
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
        self.assertTrue(saved_verdict_stands(SCRIPT_REVIEW_VERSION + "+corpus"))
        self.assertFalse(saved_verdict_stands(SCRIPT_REVIEW_VERSION))

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
        self.assertNotIn("--max-budget-usd", command, "a subscription session has no money cap")
        self.assertIn("--max-budget-usd", session_command(["claude"], budget_usd=40))
        # Episode 1 introduces the series after its opening story; every episode reads the ones before it (D-184).
        self.assertIn("## The episodes before this one", text)
        self.assertIn("one to two minutes", text)
        self.assertNotIn("## Three hosts", text)
        self.assertNotIn("## This is a revision", text)

    def test_a_third_host_and_a_revision_extend_the_task(self):
        text = task_text("de-DE", 20, 2, cast="THREE HOSTS. The cast.", revision=True)
        self.assertIn("## Three hosts\n\nTHREE HOSTS. The cast.", text)
        self.assertIn("## This is a revision", text)
        self.assertIn("at most 2 times", text)
        self.assertNotIn("{critiques}", text)

    def test_an_episode_reads_the_published_episodes_before_it(self):
        """D-184: a series written in order gives each episode what the listeners heard before it; an episode not yet
        written is left out, and none after it is read."""
        plan = SeriesPlan(topic="Probe", central_question="Was?", explanation_path="Erst.", scope_note="Klein.",
                          dependencies=[], omitted_findings=[], episodes=[
                              entry().model_copy(update={"episode_id": f"ep_00{n}"}) for n in (1, 2, 3, 4)])
        root = self.folder
        published = EpisodeScript(episode_id="ep_001", title="Die Nacht", purpose="deep_dive",
                                  chapters=[Chapter(chapter_id="s1", title="Eins")],
                                  segments=[segment("a", "s1", "host_a", "Er kam bei Nacht.", ["f_a"])])
        for episode in ("ep_001", "ep_004"):
            folder = root / "episodes" / episode
            folder.mkdir(parents=True)
            (folder / "script.yaml").write_text(json.dumps(published.model_copy(update={"episode_id": episode})
                                                           .model_dump()), encoding="utf-8")
        config = SimpleNamespace(host_names={"host_a": "Thomas"}, third_host=None)
        rows = earlier_episodes(root, plan, plan.episodes[2], config)
        self.assertEqual([row["episode_id"] for row, _ in rows], ["ep_001"])
        self.assertEqual(rows[0][0], {"episode_id": "ep_001", "number": 1, "title": "Die Nacht",
                                      "file": "earlier/ep_001.md"})
        self.assertTrue(rows[0][1].startswith("# Episode 1: Die Nacht"))
        self.assertIn("**host_a (Thomas):** Er kam bei Nacht.", rows[0][1])
        self.assertEqual(earlier_episodes(root, plan, plan.episodes[0], config), [])
        # With the run's outline, only an episode published from that outline counts: an earlier plan's ep_001 is
        # another series.
        outline = root / "runs/run_outline/series_plan.json"
        write_json(outline, plan.model_dump())
        write_json(root / "runs/run_old/series_plan.json", {"episodes": []})
        write_json(root / "episodes/ep_001/latest.json", {"run_id": "run_old", "episode_ids": ["ep_001"]})
        self.assertEqual(earlier_episodes(root, plan, plan.episodes[2], config, outline), [])
        (root / "runs/run_new").mkdir(parents=True)
        (root / "runs/run_new/series_plan.json").write_bytes(outline.read_bytes())
        write_json(root / "episodes/ep_001/latest.json", {"run_id": "run_new", "episode_ids": ["ep_001"]})
        self.assertEqual([row["episode_id"] for row, _ in earlier_episodes(root, plan, plan.episodes[2], config, outline)],
                         ["ep_001"])
        pipeline = SimpleNamespace(root=root, config=config, revision=None, work=root / "runs/run_outline")
        self.assertEqual(list(session_files(pipeline, plan, plan.episodes[2], {})), ["earlier/ep_001.md"])

    def test_a_session_on_an_anthropic_key_is_billed_under_the_money_limit(self):
        """D-184: with Claude over the API key as the run's text provider the session runs on that key, capped,
        counted against the money limit with the cost the CLI reports; never without a money limit."""
        script = self.folder / "fake_key_claude.py"
        script.write_text(KEY_SESSION, encoding="utf-8")
        folder = self.folder / "story" / "ep_001"
        folder.mkdir(parents=True)
        limits = {"model_calls": 10, "search_rounds": 1, "sources": 1}
        pipeline = SimpleNamespace(work=self.folder, text_generation={"provider": "claude_api"},
                                   adapter=SimpleNamespace(anthropic_key="sk-ant-test"),
                                   limits=lambda: ResearchLimits(**limits))
        with patch("podcast_automate.claude_code.claude_command", return_value=[sys.executable, str(script)]):
            with self.assertRaises(AppError) as caught:
                run_agent(pipeline, folder)
            self.assertEqual(caught.exception.code, "cost_limit_required")
            limits["cost_usd"] = 50.0
            answer = run_agent(pipeline, folder)
        self.assertEqual(answer["total_cost_usd"], 3.25)
        seen = json.loads((folder / "seen.json").read_text(encoding="utf-8"))
        self.assertEqual(seen, {"key": "sk-ant-test", "oauth": None, "budget": "40"})
        budget = json.loads((self.folder / "budget.json").read_text(encoding="utf-8"))
        self.assertEqual(budget["billed_usd"], 3.25)
        call = next(path.parent for path in self.folder.glob("calls/call_*/billing.json"))
        rows = json.loads((call / "billing.json").read_text(encoding="utf-8"))
        self.assertEqual([(row["state"], row["usd"], row["provider"]) for row in rows], [("priced", 3.25, "claude_api")])
        self.assertEqual(json.loads((call / "metadata.json").read_text(encoding="utf-8"))["provider"], "claude_api")

    def test_a_series_correction_in_a_story_run_repairs_like_its_review(self):
        """Until 2026-11-01 a story run's series correction called the writing prompt, which reads a teaching design
        such a run never has, and stopped with FileNotFoundError; it is repaired as the review repairs, against the
        agent's own chapters."""
        work = self.folder
        own = entry().model_copy(update={"scenes": [ScenePlan(scene_id="c1", title="Die Nacht", question="Wer?",
                                                              purpose="worked_example", finding_ids=["f_a", "f_b"],
                                                              explanation_steps=["Erzählen."])]})
        write_json(work / "story" / "ep_001" / "entry.json", own.model_dump())
        draft = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                              chapters=[Chapter(chapter_id="c1", title="Die Nacht")],
                              segments=[segment("a", "c1", "host_a", "Er kam bei Nacht.", ["f_a", "f_b"]),
                                        segment("b", "c1", "host_b", "Und dann?")])
        repaired = draft.model_copy(update={"segments": [draft.segments[0],
                                                         segment("b", "c1", "host_b", "Und was geschah dann?")]})
        passing = ScriptReview(issues=[], limitations=[])
        write_json(work / "reviewed" / "ep_001.json", draft.model_dump())
        write_json(work / "reviews" / "ep_001.json", passing.model_dump())
        write_json(work / "reviews" / "ep_001_checkpoint.json", {"draft": draft.model_dump()})
        seen = []
        run = SimpleNamespace(work=work, root=work, story_written=True, episode=None,
                              execution=SimpleNamespace(text_workers=1), probe_path=lambda e: work / "none.json",
                              story_repair=lambda plan, e, current, asked: seen.append(e) or repaired,
                              review_script=lambda *args, **kwargs: passing)
        plan = SeriesPlan(topic="Probe", central_question="Was?", explanation_path="Erst.", scope_note="Klein.",
                          dependencies=[], omitted_findings=[], episodes=[entry()])
        issue = ScriptIssue(category="structure", segment_ids=["b"], reason="Wiederholt Folge 1.")
        with patch("podcast_automate.script_pipeline.reviewed_scripts", return_value=["checked"]):
            self.assertEqual(ScriptRun.repair_series(run, plan, {"ep_001": [issue]}), ["checked"])
        self.assertEqual([scene.scene_id for scene in seen[0].scenes], ["c1"])
        self.assertEqual(json.loads((work / "reviewed" / "ep_001.json").read_text(encoding="utf-8")),
                         repaired.model_dump())


class StoryCritiqueTests(unittest.TestCase):
    """D-187: all five critiques of 2026-11-01 named exactly the eight points the prompt allowed, whatever their scores,
    and each began afresh, so a passage the agent had fixed made room for the next weakest one. The editor now names
    as many points as there are, each with its severity, and from the second critique on gets the points still open
    and the segments changed since; only an open blocking point, a worse one and a new one in changed segments block."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        write_json(self.folder / "context.json", {"critiques": 4})
        self.answers, self.asked = [], []

        def invoke(prompt, schema, version):
            self.asked.append(json.loads(prompt.splitlines()[-1]))
            return self.answers.pop(0)
        self.pipeline = SimpleNamespace(invoke=invoke)

    @staticmethod
    def draft(**texts):
        lines = {key: texts.get(key, f"Satz {key}.") for key in "abcd"}
        return EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                             chapters=[Chapter(chapter_id="s1", title="Eins")],
                             segments=[segment(f"ep_001_s1_{key}", "s1", "host_a" if key in "ac" else "host_b", text)
                                       for key, text in lines.items()])

    @staticmethod
    def critique(scores, earlier=(), points=()):
        scale = lambda score: Scale(score=score, reason="Weil.")  # noqa: E731
        return StoryCritique(scores=CritiqueScores(tension=scale(scores[0]), interest=scale(scores[1]),
                                                   storytelling=scale(scores[2])),
                             earlier=[EarlierPoint(point=number, status=status, note="So.")
                                      for number, status in earlier],
                             keeps_from_10=[CritiquePoint(severity=severity, scale="interest", segment_ids=ids,
                                                          problem="Zäh.", fix="Kürzen.") for severity, ids in points])

    def ask(self, draft, answer, replies=None):
        """critique.py as the session runs it, with the run answering while it waits."""
        (self.folder / "draft.json").write_text(draft.model_dump_json(), encoding="utf-8")
        if replies:
            write_json(self.folder / "replies.json", replies)
        self.answers.append(answer)
        with patch("podcast_automate.story_agent.time.sleep", side_effect=lambda _: serve_critiques(self.pipeline,
                                                                                                 self.folder)):
            return request_critique(self.folder, "draft.json")

    def test_points_block_by_severity_until_the_editor_finds_them_resolved(self):
        first = self.ask(self.draft(), self.critique((8, 7, 8), points=[("severe", ["s1_b"]),
                                                                        ("minor", ["ep_001_s1_d"])]))
        self.assertEqual(set(self.asked[0]), {"script"})
        self.assertEqual([(p["point"], p["blocking"]) for p in first["open_points"]], [(1, True), (2, True)])
        self.assertEqual((first["severe_open"], first["ready"]), (1, False))
        # The agent rewrote c only and answers point 2; the editor keeps point 1 open, skips point 2 and finds two
        # severe ones: in c, which changed (named in short, as the critics of 2026-11-01 did), and in a, which it read
        # before.
        second = self.ask(self.draft(c="Neu c."), self.critique((8, 8, 8), earlier=[(1, "open")],
                                                                points=[("severe", ["s1_c"]),
                                                                        ("severe", ["ep_001_s1_a"])]),
                          replies={"2": "Bleibt, die Zahl trägt die Wende."})
        sent = self.asked[1]
        self.assertEqual([(p["point"], p["segment_ids"]) for p in sent["earlier_points"]],
                         [(1, ["s1_b"]), (2, ["ep_001_s1_d"])])
        self.assertNotIn("blocking", sent["earlier_points"][0])
        self.assertEqual(sent["changed_segments"], ["ep_001_s1_c"])
        self.assertEqual(sent["replies"], {"2": "Bleibt, die Zahl trägt die Wende."})
        self.assertFalse((self.folder / "replies.json").exists(), "the replies answer one critique only")
        self.assertEqual([(p["status"], p["blocking"]) for p in second["earlier"]], [("open", True), ("open", True)])
        self.assertEqual([p["blocking"] for p in second["keeps_from_10"]], [True, False])
        self.assertEqual([(p["point"], p["segment_ids"]) for p in second["open_points"]],
                         [(1, ["s1_b"]), (2, ["ep_001_s1_d"]), (3, ["s1_c"]), (4, ["ep_001_s1_a"])])
        self.assertEqual((second["severe_open"], second["ready"]), (2, False), "8 throughout is not enough")
        # Both blocking severe points resolved: a 7 still keeps it from ready, the severe advice in a does not.
        third = self.ask(self.draft(b="Neu b.", c="Neu c, kürzer."),
                         self.critique((8, 7, 9), earlier=[(1, "resolved"), (2, "open"), (3, "resolved"), (4, "open")]))
        self.assertEqual(self.asked[2]["changed_segments"], ["ep_001_s1_b", "ep_001_s1_c"])
        self.assertEqual([(p["segment_ids"], p["blocking"]) for p in third["open_points"]],
                         [(["ep_001_s1_d"], True), (["ep_001_s1_a"], False)])
        self.assertEqual((third["severe_open"], third["ready"]), (0, False))
        fourth = self.ask(self.draft(b="Neu b.", c="Neu c, kürzer."),
                          self.critique((8, 8, 9), earlier=[(1, "open"), (2, "worse")]))
        self.assertEqual(self.asked[3]["changed_segments"], [])
        # Advice that got worse blocks, though nothing changed where it stands; this one is severe.
        self.assertEqual([p["blocking"] for p in fourth["open_points"]], [True, True])
        self.assertEqual((fourth["severe_open"], fourth["ready"]), (1, False))
        self.assertEqual(self.ask(self.draft(), self.critique((9, 9, 9)))["refused"], "critique.py runs at most 4 times.")

    def test_a_revision_blocks_only_where_it_changed_the_published_episode(self):
        (self.folder / "published.json").write_text(self.draft().model_dump_json(), encoding="utf-8")
        answer = self.ask(self.draft(b="Neu b."), self.critique((8, 8, 8), points=[("severe", ["ep_001_s1_b"]),
                                                                                   ("severe", ["ep_001_s1_a"])]))
        self.assertEqual(self.asked[0]["changed_segments"], ["ep_001_s1_b"])
        self.assertEqual([p["blocking"] for p in answer["open_points"]], [True, False])
        self.assertEqual((answer["severe_open"], answer["ready"]), (1, False))

    def test_unparsable_replies_cost_no_critique(self):
        (self.folder / "draft.json").write_text(self.draft().model_dump_json(), encoding="utf-8")
        (self.folder / "replies.json").write_text("{1: nein", encoding="utf-8")
        self.assertIn("replies.json does not parse", request_critique(self.folder, "draft.json")["error"])
        self.assertFalse((self.folder / "requests" / "critique_1.json").exists())


class SpeakingRateTests(unittest.TestCase):
    """D-184: a new script run writes and checks its lengths at the words a minute its voices speak."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def recording(self, run_id, provider, words, seconds, pause_ms=0):
        work = self.root / "runs" / run_id
        work.mkdir(parents=True)
        (work / "run_manifest.yaml").write_text("kind: episode_audio\nstatus: completed\n", encoding="utf-8")
        write_json(work / "inputs.json", {"episode_id": "ep_001", "audio_generation": {"provider": provider}})
        script = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                               chapters=[Chapter(chapter_id="s1", title="Eins")],
                               segments=[segment("a", "s1", "host_a", " ".join(["Wort"] * words), ["f_a"])])
        script.segments[0].pause_after_ms = pause_ms
        write_json(work / "approved_script.json", script.model_dump())
        write_json(self.root / "exports" / "ep_001" / run_id / "audio_report.json", {"duration_seconds": seconds})

    def test_the_newest_recording_by_the_voice_engine_sets_the_rate(self):
        self.recording("run_20261001_000000_000000_aaaaaaaa", "elevenlabs_tts", 1400, 600)
        self.recording("run_20261002_000000_000000_bbbbbbbb", "elevenlabs_tts", 1500, 606, pause_ms=6_000)
        self.recording("run_20261003_000000_000000_cccccccc", "google_gemini_tts", 1480, 600)
        # 1,500 words over 10.1 minutes less the 6 s of planned pauses, as script_metrics estimates a length.
        self.assertEqual(measured_rate(self.root, "elevenlabs_tts"), 150)
        self.assertEqual(measured_rate(self.root, "google_gemini_tts"), 148)
        self.assertIsNone(measured_rate(self.root, "qwen3_local"))
        config = SimpleNamespace()
        for provider, rate in (("elevenlabs_tts", 150), ("qwen3_local", 130)):
            with patch("podcast_automate.speech.selected_audio", return_value=SimpleNamespace(provider=provider)):
                self.assertEqual(speaking_rate(self.root, config), rate)

    def test_without_a_recording_the_engine_starts_at_its_measured_rate(self):
        self.assertEqual(STARTING_RATES, {"elevenlabs_tts": 150, "google_gemini_tts": 148})
        with patch("podcast_automate.speech.selected_audio", return_value=SimpleNamespace(provider="elevenlabs_tts")):
            self.assertEqual(speaking_rate(self.root, SimpleNamespace()), 150)

    def test_lengths_and_checks_follow_the_rate(self):
        planned = entry().model_copy(update={"target_minutes": 10})
        self.assertEqual(word_budget(planned)["target_words"], 1300)
        self.assertEqual(word_budget(planned, 1.0, 150)["target_words"], 1500)
        from podcast_automate.script_checks import validate_script
        script = EpisodeScript(episode_id="ep_001", title="Probe", purpose="deep_dive",
                               chapters=[Chapter(chapter_id="s1", title="Eins"), Chapter(chapter_id="s2", title="Zwei")],
                               segments=[segment("a", "s1", "host_a", " ".join(["Wort"] * 600), ["f_a"]),
                                         segment("b", "s2", "host_b", " ".join(["Wort"] * 600), ["f_b"])])
        # 1,200 words fill 9.2 minutes at 130 and 8 at 150: below 85 % of ten only at the faster rate.
        self.assertFalse(validate_script(script, planned))
        short = validate_script(script, planned, rate=150)
        self.assertTrue(any("at 150 words per minute" in error for error in short), short)


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
            run = run_script(self.root)
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
        budget = word_budget(planned)
        # It reaches its target, not only the run's floor (D-184).
        self.assertEqual(material["length"], {**budget, "minimum_words": max(
            budget["minimum_words"], math.ceil(budget["target_words"] * TARGET_SHARE))})
        # The fixture's voices are Qwen's, which speak at the old rate: the run's inputs stay as they were.
        self.assertNotIn("speaking_rate", json.loads((work / "inputs.json").read_text(encoding="utf-8")))
        # The first episode has nothing before it to read.
        self.assertNotIn("earlier_episodes", material)
        self.assertFalse((folder / "earlier").exists())
        # The review's own rule for opening and ending, which both first real reviews raised (2026-11-01).
        self.assertEqual(material["framing"], EPISODE_FRAMING.strip())
        # The agent's commands ran: the check passed and the critique came back from a call of the run.
        log = json.loads((folder / "agent_log.json").read_text(encoding="utf-8"))
        self.assertTrue(json.loads(log["check"])["passed"], log["check"])
        critique = json.loads(log["critique"])
        self.assertEqual(critique["scores"]["tension"]["score"], 8)
        self.assertEqual((critique["open_points"], critique["ready"]), ([], True))
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
        self.assertEqual(report["story_agent"]["critiques"],
                         [{"tension": 8, "interest": 8, "storytelling": 8, "severe_open": 0}])
        self.assertEqual(report["story_agent"]["reported_cost_usd"], 1.5)
        self.assertNotIn("dialogue_polish", report)
        # The recording takes the agent's episode as reviewed: it has a story stage instead of writing and polishing
        # (2026-11-01: Asimov v2 ep_007 stopped there with invalid_script).
        _, script, _ = reviewed_episode(self.root, self.config, "ep_001")
        self.assertEqual(script.model_dump(), json.loads((work / "reviewed" / "ep_001.json").read_text(encoding="utf-8")))

    def test_a_run_begun_with_the_writing_prompt_moves_onto_the_agent_while_it_has_written_nothing(self):
        """D-184: Orlagau v2's series run stopped in the old teaching design after its outline was approved; on resume
        it takes the agent's stages and keeps its outline and approval. A run that wrote an episode cannot resume."""
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model):
            planned = run_script(self.root, plan_only=True)
        work = self.root / "runs" / planned.run_id
        before = outline_hash(work)
        old = ("planning", "teaching", "writing", "narration", "polishing", "review", "publish")
        manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
        manifest.stages = {name: manifest.stages["planning"] if name == "planning" else
                           StageRecord(status="blocked" if name == "teaching" else "pending",
                                       attempts=4 if name == "teaching" else 0) for name in old}
        write_yaml(work / "run_manifest.yaml", manifest.model_dump(mode="json"))
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model):
            done = run_script(self.root, resume=True, run_id=planned.run_id, approved_plan_hash=before)
        self.assertEqual(done.status, "completed", {k: v.error for k, v in done.stages.items() if v.error})
        self.assertEqual(tuple(done.stages), STORY_STAGES)
        self.assertEqual(outline_hash(work), before)
        self.assertEqual(json.loads((work / "writer_switch.json").read_text(encoding="utf-8"))["reason"], "D-184")
        self.assertTrue((work / "story" / "ep_001" / "draft.json").is_file())
        manifest = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
        manifest.stages = {name: StageRecord(status="completed") if name in ("planning", "teaching", "writing")
                           else StageRecord() for name in old}
        write_yaml(work / "run_manifest.yaml", manifest.model_dump(mode="json"))
        with self.assertRaises(Exception) as caught:
            run_script(self.root, resume=True, run_id=planned.run_id)
        self.assertEqual(caught.exception.code, "writer_retired")

    def test_the_outline_is_approved_and_its_episodes_written_in_order(self):
        """D-184: the approval writes nothing by itself; outline_episodes names the run that published each episode
        from this outline, and next_episode the first one still to write."""
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model):
            planned = run_script(self.root, plan_only=True)
        work = self.root / "runs" / planned.run_id
        self.assertEqual(next_episode(self.root, planned.run_id), "ep_001")
        with self.assertRaises(Exception) as caught:
            approve_outline(self.root, planned.run_id, "0" * 64)
        self.assertEqual(caught.exception.code, "plan_changed")
        approve_outline(self.root, planned.run_id, outline_hash(work))
        self.assertEqual(json.loads((work / "plan_approval.json").read_text(encoding="utf-8"))["plan_hash"],
                         outline_hash(work))
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model):
            written = run_script(self.root, episode="ep_001", outline_run=planned.run_id)
        self.assertEqual(outline_episodes(self.root, planned.run_id),
                         [{"episode_id": "ep_001", "title": "A model compares possibilities", "run_id": written.run_id}])
        self.assertIsNone(next_episode(self.root, planned.run_id))

    def test_one_episode_of_an_approved_outline_is_written_without_planning_again(self):
        """2026-11-01: approving Asimov v2's outline would have started 20 agent sessions at once; the operator wanted
        one episode first. A new run takes the approved outline of the series run and writes that episode only."""
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model):
            planned = run_script(self.root, plan_only=True)
        work = self.root / "runs" / planned.run_id
        with self.assertRaises(Exception) as caught:
            run_script(self.root, episode="ep_001", outline_run=planned.run_id)
        self.assertEqual(caught.exception.code, "plan_approval_required")
        write_json(work / "plan_approval.json", {"plan_hash": outline_hash(work), "approved_at": "2026-11-01T00:00:00+00:00"})
        with self.assertRaises(Exception) as caught:
            run_script(self.root, outline_run=planned.run_id)
        self.assertEqual(caught.exception.code, "invalid_request", "an outline's episode is named")
        self.calls.clear()
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model),              patch("podcast_automate.claude_code.claude_command", return_value=fake_agent(self.root.parent)):
            probe = run_script(self.root, episode="ep_001", outline_run=planned.run_id)
        self.assertEqual(probe.status, "completed", {k: v.error for k, v in probe.stages.items() if v.error})
        self.assertNotEqual(probe.run_id, planned.run_id)
        self.assertEqual(tuple(probe.stages), STORY_STAGES)
        self.assertNotIn(SeriesPlan, self.calls, "the outline is not planned again")
        probe_work = self.root / "runs" / probe.run_id
        self.assertEqual((probe_work / "series_plan.json").read_bytes(), (work / "series_plan.json").read_bytes())
        self.assertTrue((self.root / "episodes" / "ep_001" / "script.yaml").is_file())
        series = RunManifest.model_validate(read_yaml(work / "run_manifest.yaml"))
        self.assertEqual(series.stages["teaching"].status, "pending", "the series run waits as it was")

    def test_the_agent_revises_a_published_episode_from_the_feedback(self):
        """D-184: a revision is an agent session given the published episode, the corpus sections it used and the
        operator's feedback; it starts from the chapters the agent published, and its text is reviewed as every draft."""
        cli = fake_agent(self.root.parent)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.story_model), \
             patch("podcast_automate.claude_code.claude_command", return_value=cli):
            first = run_script(self.root)
            write_json(self.root / "runs" / first.run_id / "story" / "ep_001" / "sources_used.json", [])
            revised = run_script(self.root, revise="ep_001", feedback="Der Schluss soll ruhiger werden.")
        self.assertEqual(revised.status, "completed", {k: v.error for k, v in revised.stages.items() if v.error})
        self.assertNotEqual(revised.run_id, first.run_id)
        self.assertEqual(tuple(revised.stages), STORY_STAGES)
        folder = self.root / "runs" / revised.run_id / "story" / "ep_001"
        material = json.loads((folder / "material.json").read_text(encoding="utf-8"))
        self.assertEqual(material["revision"], {"feedback": "Der Schluss soll ruhiger werden.",
                                                "published": "published.json"})
        original = json.loads((self.root / "runs" / first.run_id / "reviewed" / "ep_001.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((folder / "published.json").read_text(encoding="utf-8")), original)
        self.assertEqual(json.loads((folder / "sources_used.json").read_text(encoding="utf-8")), [])
        task = (folder / "TASK.md").read_text(encoding="utf-8")
        self.assertIn("## This is a revision", task)
        self.assertIn("at most 2 times", task)
        self.assertEqual(json.loads((self.root / "episodes" / "ep_001" / "latest.json").read_text(encoding="utf-8"))
                         ["run_id"], revised.run_id)

    def test_the_budget_counts_the_story_stage_instead_of_writing_narration_and_polishing(self):
        # Teaching: story material, orientation sheet and its check; story: the session and a critique; review: the
        # script review alone, with no listener readback, editorial or teaching review.
        self.assertEqual(calls_per_episode(STORY_STAGES), 3 + 2 + 1)


if __name__ == "__main__":
    unittest.main()
