import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from podcast_automate.call_activity import contract_rejection
from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript
from podcast_automate.narration import StoryMaterial
from podcast_automate.research_models import ResearchDossier
from podcast_automate.runner import status
from podcast_automate.script_models import Dependency, ScenePlan, ScriptIssue, ScriptReview, SeriesPlan
from podcast_automate.script_pipeline import STORY_REPAIR_VERSION, STORY_REVIEW_SUFFIX, changed_segments, follow_up_scope
from podcast_automate.script_checks import (SCRIPT_REVIEW_VERSION, SERIES_PLAN_VERSION, planning_dossier,
                                            quotation_errors, word_budget)
from podcast_automate.scripting import run_script, validate_plan, validate_script
from podcast_automate.series_review import SeriesReview
from podcast_automate.storage import file_hash, read_yaml, write_json, write_yaml
from podcast_automate.story_agent import OrientationReview, OrientationSheet, StoryCritique
from tests import script_fixtures as fixtures
from tests.research_fixtures import TEXT
from tests.script_fixtures import FAKE_AGENT, example_plan, example_script

# The script review of a story agent's episode (D-181, D-182), the only kind of script run since D-184.
STORY_REVIEW = SCRIPT_REVIEW_VERSION + STORY_REVIEW_SUFFIX
# The model calls of the fixture's one-episode run, in order: the outline; the teaching stage's story material,
# orientation sheet and its check; the critique the fake agent asks for during its session; the script review; the
# series review. The session itself is one more call of the run's budget (sessions).
RUN_CALLS = [SeriesPlan, StoryMaterial, OrientationSheet, OrientationReview, StoryCritique, ScriptReview, SeriesReview]
# The two turns of the draft the fake agent writes for the fixture's episode (script_fixtures.example_script).
OPENING, REPLY = (segment.segment_id for segment in example_script().segments)


def sessions(work):
    """The story agent's sessions of the run in ``work``: each is one call of the run (StorySession)."""
    return [path.parent.name for path in sorted((work / "calls").glob("call_*/output_schema.json"))
            if json.loads(path.read_text(encoding="utf-8")).get("title") == "StorySession"]


def agent_variant(folder, old, new, draft):
    """A fake Claude CLI that works as script_fixtures.FAKE_AGENT does, with its line ``old`` replaced by ``new``, and
    writes ``draft`` for an episode with its chapters (fake_agent)."""
    assert FAKE_AGENT.count(old) == 1, old
    fixtures.fake_agent(folder, draft)
    script = Path(folder) / "variant_claude.py"
    script.write_text(FAKE_AGENT.replace(old, new), encoding="utf-8")
    return [sys.executable, str(script)]


def second_episode(plan):
    """The fixture plan with a second episode, a copy of the first."""
    second = plan.episodes[0].model_copy(deep=True)
    second.episode_id, second.title = "ep_002", "Further consequences"
    plan.episodes.append(second)
    return plan


class ScriptingTests(fixtures.ScriptProjectCase):
    def model(self, prompt, output_type, directory, **kwargs):
        """The fixture model. The only script a model writes in a story run is a repair of the agent's draft
        (ScriptRun.story_repair), which may change only the segments the review names and their neighbours: its answer
        here is the draft it was given."""
        value, meta = super().model(prompt, output_type, directory, **kwargs)
        if output_type is EpisodeScript:
            value = EpisodeScript.model_validate(json.loads(prompt.splitlines()[-1])["draft"])
        return value, meta

    def test_model_and_effort_apply_to_every_text_stage_and_resume_keeps_them(self):
        selections = []
        def selected(adapter, *args, **kwargs):
            selections.append((adapter.settings.codex_model, adapter.reasoning_effort, kwargs["prompt_version"]))
            return self.model(*args, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", autospec=True, side_effect=selected):
            first = run_script(self.root, model="gpt-6-astra", reasoning_effort="xhigh")
            self.assertEqual(first.status, "completed")
            second = run_script(self.root, resume=True, run_id=first.run_id)
            with self.assertRaises(AppError) as changed:
                run_script(self.root, resume=True, run_id=first.run_id, reasoning_effort="low")
        self.assertEqual(second.status, "completed")
        self.assertEqual(changed.exception.code, "inputs_changed")
        # Every call of the run at the run's model and level, the critique the agent asks for included; the resume of
        # the completed run asks nothing.
        self.assertEqual(len(selections), len(RUN_CALLS))
        self.assertEqual({(model, effort) for model, effort, _ in selections}, {("gpt-6-astra", "xhigh")})
        request = json.loads((self.root / "runs" / first.run_id / "script_request.json").read_text(encoding="utf-8"))
        self.assertEqual(request["text_generation"]["reasoning_effort"], "xhigh")

    def test_legacy_run_without_reasoning_field_keeps_its_inputs_and_approval(self):
        from podcast_automate.scripting import text_generation_settings, outline_hash
        def legacy_settings(*args, **kwargs):
            selected = text_generation_settings(*args, **kwargs)
            selected.pop("reasoning_effort", None)
            return selected
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            with patch("podcast_automate.scripting.text_generation_settings", side_effect=legacy_settings):
                first = run_script(self.root, plan_only=True)
            work = self.root / "runs" / first.run_id
            before = (work / "script_request.json").read_bytes()
            approval = outline_hash(work)
            second = run_script(self.root, resume=True, approved_plan_hash=approval)
        self.assertEqual(second.status, "completed")
        self.assertEqual(before, (work / "script_request.json").read_bytes())
        self.assertEqual(approval, outline_hash(work))

    def test_research_to_script_preserves_evidence_and_never_generates_audio(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model), \
             patch("podcast_automate.runner.run_tts", side_effect=AssertionError("No audio before user review")):
            first = run_script(self.root, episode="ep_001")
            resumed = run_script(self.root, resume=True)
        self.assertEqual(first.status, "completed")
        self.assertEqual(resumed.run_id, first.run_id)
        self.assertEqual(self.calls, RUN_CALLS)
        self.assertEqual(status(self.root)["invalid_completed_stages"], [])
        self.assertTrue(all(s.attempts == 1 for s in resumed.stages.values()))
        text = (self.root / "episodes/ep_001/script.md").read_text(encoding="utf-8")
        # Behaviour change of 19 September 2026: the readable script names the speaking role,
        # or the configured host name, never the voice preset that will read it aloud.
        self.assertIn("**Host A:**", text)
        self.assertIn("**Host B:**", text)
        self.assertNotIn("**Aiden:**", text)
        self.assertNotIn("src_", text)
        knowledge = read_yaml(self.root / "models/knowledge_model.yaml")
        self.assertEqual(knowledge["research_run_id"], self.research.run_id)
        self.assertTrue(knowledge["claims"][0]["evidence"][0]["reference"].startswith("src_"))
        report = read_yaml(self.root / "reports/script_quality.yaml")
        self.assertEqual(report["episodes"]["ep_001"]["advisories"], [])
        self.assertFalse(report["audio_generated"])
        self.assertFalse(report["human_reviewed"])
        self.assertTrue(report["complete_series_review"])
        approval = read_yaml(self.root / "episodes/audio_review.yaml")
        self.assertFalse(approval["audio_approved"])
        self.assertEqual(approval["status"], "awaiting_user_script_review")
        self.assertEqual(approval["scripts"]["ep_001"], report["episodes"]["ep_001"]["script_sha256"])

    def test_the_planner_reads_the_editorial_brief_without_the_audio_part_length(self):
        # Behaviour change of 2 October 2026: max_episode_minutes (30) was the longest audio part of a recording, and
        # is unused since 4 October. In the planning brief the model took it for an episode cap and split one long
        # explanation into several 30-minute episodes, although an episode may take up to 60.
        aimed = self.config.model_copy(update={"series_goal": {"understand": 3, "evaluate": 1, "apply": 0},
                                               "target_total_minutes": 90})
        write_yaml(self.root / "project.yaml", aimed.model_dump(mode="json"))
        briefs = []
        def model(prompt, output_type, directory, **kwargs):
            if output_type is SeriesPlan:
                briefs.append(json.loads(prompt.splitlines()[-1])["brief"])
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            planned = run_script(self.root, plan_only=True)
        self.assertEqual(planned.stages["planning"].status, "completed")
        self.assertEqual(len(briefs), 1)
        brief = briefs[0]
        self.assertFalse({"max_episode_minutes", "runtime", "voice_profile", "research_limits"} & set(brief))
        self.assertEqual((brief["topic"], brief["target_total_minutes"], brief["series_goal"]["understand"]),
                         ("Test topic", 90, 3))
        self.assertEqual(brief["depth_request"], aimed.depth_request)

    def note_research_limits(self, **keys):
        """Add keys to the research run's quality gate as the research of 2026-10-02 writes them; the gate is a
        hashed output of the research run, so its recorded hash follows."""
        work = self.root / "runs" / self.research.run_id
        gate = work / "research_quality_gate.json"
        saved = json.loads(gate.read_text(encoding="utf-8"))
        for key, value in keys.items():
            saved[key] = value(saved[key]) if callable(value) else value
        write_json(gate, saved)
        manifest = read_yaml(work / "run_manifest.yaml")
        relative = gate.relative_to(self.root).as_posix()
        for record in manifest["stages"].values():
            if relative in (record.get("outputs") or {}):
                record["outputs"][relative] = file_hash(gate)
        write_yaml(work / "run_manifest.yaml", manifest)

    def test_noted_research_limits_reach_the_plan_the_agent_and_the_script_review(self):
        """Finding of 2026-10-02: research_quality_gate.json holds the limits the research noted for the script and
        the quality report says the script states them, but the script lane read only whether the gate passed. The
        story agent is given the episode's limits the review holds it to (2026-11-01: only the review knew them)."""
        def source_limit(rows):
            rows[0].update(source_limit=True, missing=["Keine Quelle nennt Personenstunden."], finding_ids=["f_energy"])
            return rows
        self.note_research_limits(requirements=source_limit, noted_limits=["Die Gegenposition stützt sich auf eine Studie."],
                                  script_notes=["Die Gegenposition stützt sich auf eine Studie.",
                                                "Das Werk liegt nur als Abstract vor."])
        seen = {}
        def model(prompt, output_type, directory, **kwargs):
            payload = json.loads(prompt.splitlines()[-1])
            seen.setdefault(kwargs["prompt_version"], payload.get("research_limits"))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.model_dump())
        gate = self.root / "runs" / self.research.run_id / "research_quality_gate.json"
        question = json.loads(gate.read_text(encoding="utf-8"))["requirements"][0]["question"]
        self.assertEqual(seen[SERIES_PLAN_VERSION], [
            {"limit_id": "limit_001", "text": "Keine Quelle nennt Personenstunden.", "finding_ids": ["f_energy"],
             "question": question},
            {"limit_id": "limit_002", "text": "Die Gegenposition stützt sich auf eine Studie.", "finding_ids": []},
            {"limit_id": "limit_003", "text": "Das Werk liegt nur als Abstract vor.", "finding_ids": []}])
        # The only episode is the final one: it states every limit, the first with the finding it concerns.
        expected = [{"text": "Keine Quelle nennt Personenstunden.", "question": question, "finding_ids": ["f_energy"]},
                    {"text": "Die Gegenposition stützt sich auf eine Studie.", "finding_ids": []},
                    {"text": "Das Werk liegt nur als Abstract vor.", "finding_ids": []}]
        self.assertEqual(seen[STORY_REVIEW], expected)
        material = json.loads((self.root / "runs" / run.run_id / "story/ep_001/material.json").read_text(encoding="utf-8"))
        self.assertEqual(material["research_limits"], expected)

    def test_every_noted_limit_is_probed_like_a_gap_and_one_the_sources_answer_is_not_stated(self):
        """2026-10-04: an assembled dossier has no open questions and, with every task answered, no coverage gap, so
        the script's gap probe searched nothing and Jev never ran (Asimov, Ontologies, Transformer). The limits the
        script states are probed now; one a supplementary research resolves is no longer stated (D-131)."""
        from podcast_automate import script_pipeline
        self.note_research_limits(noted_limits=["Die Gegenposition stützt sich auf eine Studie."],
                                  script_notes=["Das Werk liegt nur als Abstract vor."])
        original, seen = script_pipeline.probe, {}

        def probe(*args, **kwargs):
            # As if a supplement had found the full text of the work: its row is resolved.
            return [{**row, "status": "resolved"} if row["text"] == "Das Werk liegt nur als Abstract vor." else row
                    for row in original(*args, **kwargs)]

        def model(prompt, output_type, directory, **kwargs):
            seen.setdefault(kwargs["prompt_version"], json.loads(prompt.splitlines()[-1]).get("research_limits"))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
                patch("podcast_automate.script_pipeline.probe", side_effect=probe):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.model_dump())
        work = self.root / "runs" / run.run_id
        limits = ["Die Gegenposition stützt sich auf eine Studie.", "Das Werk liegt nur als Abstract vor."]
        knowledge = json.loads((work / "knowledge_model.json").read_text(encoding="utf-8"))
        self.assertTrue(set(limits) <= set(knowledge["uncertainties"]))
        rows = {row["text"]: row["status"] for row in json.loads((work / "gap_probes.json").read_text(encoding="utf-8"))}
        self.assertEqual((limits[0] in rows, rows[limits[1]]), (True, "resolved"))
        # The plan, made before the probe, places both; the script review is given only the one that stands.
        self.assertEqual([limit["text"] for limit in seen[SERIES_PLAN_VERSION]], limits)
        self.assertEqual(seen[STORY_REVIEW], [{"text": limits[0], "finding_ids": []}])

    def test_the_agent_reads_its_episode_and_the_series_context_instead_of_the_whole_plan(self):
        """2026-10-02: the writing payload carried the whole series plan; the Asimov finale's prompt reached about
        780 000 characters. What the writer uses is the episode entry and series_context, and the story agent's
        material.json holds those (D-184)."""
        from podcast_automate.editorial import episode_series_context
        payloads = []
        def model(prompt, output_type, directory, **kwargs):
            if kwargs["prompt_version"] == SERIES_PLAN_VERSION:
                payloads.append(json.loads(prompt.splitlines()[-1]))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        (planning,) = payloads
        # A research run without noted limits changes nothing in the planning payload.
        self.assertNotIn("research_limits", planning)
        material = json.loads((self.root / "runs" / run.run_id / "story/ep_001/material.json").read_text(encoding="utf-8"))
        plan = example_plan()
        self.assertFalse({"plan", "series", "episodes", "research_limits"} & set(material))
        self.assertEqual(material["episode"], plan.episodes[0].model_dump())
        self.assertEqual(material["series_context"], episode_series_context(plan, plan.episodes[0]))
        self.assertEqual(material["series_context"]["episode_path"][0]["episode_id"], "ep_001")
        self.assertEqual(material["length"]["target_words"], word_budget(plan.episodes[0])["target_words"])

    def test_the_listenability_rules_reach_the_script_review_with_the_measured_shape(self):
        """2026-10-06: listeners of three finished series found the episodes "Fakten, Fakten, Fakten"; the expert spoke
        70 to 87 % of the words. The script review composes one rule block and gets the shape the code measured on the
        agent's draft instead of counting words itself; the agent's task has its own rules for the ear (D-184)."""
        from podcast_automate.editorial import LISTENABILITY
        from podcast_automate.script_advisories import dialogue_shape
        prompts = {}

        def model(prompt, output_type, directory, **kwargs):
            prompts.setdefault(kwargs["prompt_version"], prompt)
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertIn(LISTENABILITY, prompts[STORY_REVIEW])
        self.assertEqual(dialogue_shape(example_script()), {"partner_share": 0.69, "turns": 2, "long_turns": []})
        draft = EpisodeScript.model_validate_json(
            (self.root / "runs" / run.run_id / "drafts/ep_001.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads(prompts[STORY_REVIEW].splitlines()[-1])["dialogue_shape"], dialogue_shape(draft))

    def test_the_agent_gets_its_word_budget_computed_per_episode_and_scene(self):
        """Transformer, 2026-10-04: told to derive the budget from target_minutes, the writer wrote every first draft at
        58 to 75 % of it, and ep_012 stayed below the floor after three corrections. The floor is the one validate_script
        holds a draft to; the target is spread over the scenes by their explanation steps."""
        episode = example_plan().episodes[0].model_copy(update={"target_minutes": 50.0})
        episode.scenes = [episode.scenes[0].model_copy(update={"scene_id": f"scene_{n}", "explanation_steps": ["Step."] * n})
                          for n in (1, 2, 3)]
        budget = word_budget(episode)
        self.assertEqual((budget["minimum_words"], budget["target_words"]), (5525, 6500))
        self.assertEqual(budget["scene_words"], {"scene_1": 1083, "scene_2": 2167, "scene_3": 3250})
        # A factor raises the target and its spread over the scenes, never the floor (story_agent.TARGET_FACTOR).
        raised = word_budget(episode, 1.3)
        self.assertEqual((raised["minimum_words"], raised["target_words"]), (5525, 8450))
        self.assertEqual(raised["scene_words"], {"scene_1": 1408, "scene_2": 2817, "scene_3": 4225})
        # The hour stays with the check: a one-hour target is not capped.
        self.assertEqual(word_budget(episode.model_copy(update={"target_minutes": 60.0}), 1.3)["target_words"], 10140)
        # The floor is the check's: a draft of minimum_words spoken words without pauses passes it.
        script = example_script()
        script.segments[1].text = " ".join(["Wort"] * (word_budget(example_plan().episodes[0])["minimum_words"] - 5))
        for segment in script.segments:
            segment.pause_after_ms = 0
        self.assertFalse([e for e in validate_script(script, example_plan().episodes[0]) if "planned duration" in e])

    def test_script_prompts_take_the_projects_terminology_and_the_reviews_its_goal(self):
        """2026-10-02: the planner, the writer and the script review named Query, Key and Value for every topic, also
        for the Asimov series (editorial.terminology), and the reviews of a script never saw the brief's series_goal
        they judge by."""
        from types import SimpleNamespace
        from podcast_automate.editorial import MACHINE_LEARNING_TERMS, TERMINOLOGY, TOPIC_TERMINOLOGY
        from podcast_automate.script_pipeline import ScriptRun
        # Weighted below 2 for understand and apply, so the series review keeps the fixture's criteria.
        goal = {"understand": 1, "evaluate": 3, "apply": 1}
        write_yaml(self.root / "project.yaml", self.config.model_copy(update={"series_goal": goal}).model_dump(mode="json"))
        prompts = []
        def model(prompt, output_type, directory, **kwargs):
            prompts.append((output_type, kwargs["prompt_version"], prompt))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            self.assertEqual(run_script(self.root).status, "completed")
        script_lane = [prompt for _, version, prompt in prompts if version in (SERIES_PLAN_VERSION, STORY_REVIEW)]
        self.assertEqual(len(script_lane), 2)
        self.assertTrue(all(TOPIC_TERMINOLOGY in p and TERMINOLOGY not in p and MACHINE_LEARNING_TERMS not in p
                            for p in script_lane))
        goals = {schema.__name__: json.loads(prompt.splitlines()[-1])["brief"].get("series_goal")
                 for schema, _, prompt in prompts if schema in (ScriptReview, SeriesReview)}
        self.assertEqual(goals, {"ScriptReview": goal, "SeriesReview": goal})
        transformer = SimpleNamespace(config=self.config.model_copy(update={"topic": "Wie ein Transformer lernt",
                                                                           "language": "de-DE"}),
                                      central_question="Wie ein Transformer lernt")
        self.assertEqual(ScriptRun.terms(transformer), TOPIC_TERMINOLOGY + MACHINE_LEARNING_TERMS)

    def test_a_resume_returns_the_reservation_of_a_call_a_hard_stop_left_open(self):
        """Finding of 2026-10-02: a Studio stop ends the worker with taskkill /F, so no handler refunds the calls it
        had out. The research lane reconciles them on resume (research.reconcile_budget); script runs did not."""
        stopped = []
        def killed(prompt, output_type, directory, **kwargs):
            if output_type is ScriptReview and not stopped:
                write_json(directory / "output_schema.json", {})  # the adapter had sent the call
                stopped.append(int(directory.name.split("_")[1]))
                raise KeyboardInterrupt
            return self.model(prompt, output_type, directory, **kwargs)
        # The hard stop: the worker's own refund never runs.
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=killed), \
             patch("podcast_automate.script_pipeline.refund_call"), self.assertRaises(KeyboardInterrupt):
            run_script(self.root)
        run_id = json.loads((self.root / "runs/latest.json").read_text(encoding="utf-8"))["run_id"]
        work = self.root / "runs" / run_id
        budget_path = work / "budget.json"
        # The answered calls, the agent's session, and the one the stop left open.
        self.assertEqual(json.loads(budget_path.read_text(encoding="utf-8"))["model_calls"],
                         len(self.calls) + len(sessions(work)) + 1)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            resumed = run_script(self.root, resume=True, run_id=run_id)
        self.assertEqual(resumed.status, "completed")
        budget = json.loads(budget_path.read_text(encoding="utf-8"))
        self.assertEqual((budget["model_calls"], budget["refunded"]), (len(self.calls) + len(sessions(work)), stopped))

    def test_a_local_source_outside_the_project_is_refused_before_any_call(self):
        from podcast_automate.scripting import local_source_paths
        outside = self.root.parent / "outside.txt"
        outside.write_text("Fremde Datei.", encoding="utf-8")
        for value in (str(outside), "../outside.txt", "inputs/../../outside.txt"):
            with self.subTest(path=value):
                config = self.config.model_copy(update={"local_sources": [value]})
                write_yaml(self.root / "project.yaml", config.model_dump(mode="json"))
                with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model), \
                     self.assertRaises(AppError) as refused:
                    run_script(self.root)
                self.assertEqual((refused.exception.code, refused.exception.status), ("invalid_request", "blocked"))
                self.assertIn("außerhalb des Projektordners", str(refused.exception))
        self.assertEqual(self.calls, [])
        inside = self.config.model_copy(update={"local_sources": ["inputs/uploads/a.txt"]})
        self.assertEqual(local_source_paths(self.root, inside), [(self.root / "inputs/uploads/a.txt").resolve()])

    def test_a_second_series_correction_keeps_the_series_issue_beside_the_checks_objection(self):
        """Finding of 2026-10-02: the second attempt of a series correction was asked only against the objections of
        its evidence check, so a correction that satisfied them could drop the series issue it was made for."""
        sentence = " Episode one established this order first."
        asked, series_calls = [], []

        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            version, payload = kwargs["prompt_version"], json.loads(prompt.splitlines()[-1])
            if output_type is SeriesPlan:
                value = second_episode(value)
            elif output_type is EpisodeScript and version == STORY_REPAIR_VERSION:
                asked.append([issue["reason"] for issue in payload["review"]["issues"]])
                value.segments[-1].text += sentence
            elif output_type is ScriptReview and version.endswith("+followup") and len(asked) == 1:
                check = value.claim_checks[-1]
                check.verdict, check.changed_fields = "drift", ["scope"]
                check.reason = "The repaired segment claims more than the finding supports."
            elif output_type is SeriesReview:
                series_calls.append(version)
                if len(series_calls) == 1:
                    value.checks[2].verdict = "fail"
                    value.checks[2].reason = "Episode 2 contradicts the order episode 1 established."
                    value.checks[2].evidence = [e for e in value.checks[2].evidence if e.episode_id == "ep_002"]
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual(len(asked), 2)
        self.assertEqual(len(asked[0]), 1)
        self.assertTrue(asked[0][0].startswith("progression:"), asked)
        self.assertIn("claims more than the finding supports", asked[1][0], "the check's objection comes first")
        self.assertEqual(asked[1][1:], asked[0], "and the series issue stays in the second attempt's input")

    def test_the_script_review_sees_gap_probe_statuses_without_previews(self):
        seen = []
        def model(prompt, output_type, directory, **kwargs):
            if output_type is ScriptReview:
                seen.append(json.loads(prompt.splitlines()[-1]))
            return self.model(prompt, output_type, directory, **kwargs)
        seeded = {"gap_seed": "Which tokenizer curriculum shaped the vocabulary schedule?"}
        with patch("podcast_automate.script_pipeline.ScriptRun.knowledge_gaps", return_value=seeded), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(seen[0]["gap_probes"], [{"gap_id": "gap_seed", "text": seeded["gap_seed"],
                                                  "status": "no_hits", "references": []}])
        self.assertNotIn("preview", json.dumps(seen[0]["gap_probes"]))

    def test_style_notes_change_the_input_hash_and_reach_the_agent_and_the_review(self):
        from podcast_automate.scripting import style_notes
        self.assertEqual(style_notes(self.root), "")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            first = run_script(self.root)
        self.assertEqual(first.status, "completed")
        before = json.loads((self.root / "runs" / first.run_id / "inputs.json").read_text(encoding="utf-8"))
        self.assertNotIn("style_notes", before)
        (self.root / "style_notes.md").write_text("Keine Wiederholungen am Kapitelende." + chr(10), encoding="utf-8")
        briefs = []
        def model(prompt, output_type, directory, **kwargs):
            payload = json.loads(prompt.splitlines()[-1])
            if "style_notes" in (payload.get("brief") or {}):
                briefs.append((kwargs["prompt_version"], payload["brief"]["style_notes"]))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            second = run_script(self.root)
        self.assertEqual(second.status, "completed")
        self.assertNotEqual(second.run_id, first.run_id)
        work = self.root / "runs" / second.run_id
        after = json.loads((work / "inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(after["style_notes"], "Keine Wiederholungen am Kapitelende.")
        # The story agent writes with the operator's standing corrections, and the script review reads them.
        material = json.loads((work / "story/ep_001/material.json").read_text(encoding="utf-8"))
        self.assertEqual(material["brief"]["style_notes"], "Keine Wiederholungen am Kapitelende.")
        self.assertEqual(briefs, [(STORY_REVIEW, "Keine Wiederholungen am Kapitelende.")])

    def test_single_group_findings_reach_the_agent_and_the_script_review_without_unknown_groups(self):
        known = {"finding_id": "f_energy", "research_group": "Vendor Labs", "source_ids": ["src_a"],
                 "unknown_group_source_ids": []}
        unknown = {"finding_id": "f_energy", "research_group": None, "source_ids": ["src_b"],
                   "unknown_group_source_ids": ["src_b"]}
        seen = []
        def model(prompt, output_type, directory, **kwargs):
            payload = json.loads(prompt.splitlines()[-1])
            if "single_group_findings" in payload:
                seen.append((output_type, payload["single_group_findings"]))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.script_pipeline.single_group_findings", return_value=[known, unknown]), \
             patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        # The reviewer only sees findings it can attribute to a named group; a row whose group is unknown stays in
        # the research quality report and never becomes an attribution requirement on the script. The story agent is
        # told the same rows, so it attributes them as it writes (2026-11-01).
        self.assertEqual(seen, [(ScriptReview, [known])])
        material = json.loads((self.root / "runs" / run.run_id / "story/ep_001/material.json").read_text(encoding="utf-8"))
        self.assertEqual(material["single_group_findings"], [known])

    def test_a_script_review_that_skips_a_segment_is_asked_again_and_the_run_completes(self):
        reviews = []
        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ScriptReview:
                reviews.append(prompt)
                if len(reviews) == 1:
                    value.claim_checks.pop()
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(reviews), 2)
        self.assertIn("Script review must check every segment's claim preservation exactly once.", reviews[1])
        # A malformed review is not a finding about the script: no repair was asked for it.
        self.assertNotIn(EpisodeScript, self.calls)

    def test_a_missing_attribution_is_a_limitation_and_never_blocks(self):
        note = "Keine hörbare Zuschreibung für f_energy; die Folge nennt nicht, wessen Messung es ist."
        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ScriptReview:
                value.limitations = [note]
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed")
        # A limitation never starts a repair.
        self.assertNotIn(EpisodeScript, self.calls)
        report = read_yaml(self.root / "reports/script_quality.yaml")
        self.assertEqual(report["episodes"]["ep_001"]["model_review"]["limitations"], [note])

    def script_rejection(self):
        """A script whose chapters break the structure rule, reported exactly as the adapters report it."""
        broken = example_script().model_dump()
        broken["segments"][0]["chapter_id"] = "scene_other"
        try:
            EpisodeScript.model_validate(broken)
        except ValidationError as exc:
            return contract_rejection(exc, broken, provider="Fixture")
        self.fail("the fixture script must violate the contract")

    def rejected_repair(self, rejections):
        """The fixture model whose first script review raises a grounding point on host_b's reply, and whose repair the
        script contract rejects ``rejections`` times before it answers with the draft it was given. Returns the model,
        the repair prompts and the review prompts."""
        repairs, reviews = [], []

        def model(prompt, output_type, directory, **kwargs):
            if output_type is EpisodeScript:
                repairs.append(prompt)
                if len(repairs) <= rejections:
                    self.calls.append(output_type)
                    raise self.script_rejection()
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ScriptReview:
                reviews.append(prompt)
                if len(reviews) == 1:
                    value.issues = [ScriptIssue(category="grounding", segment_ids=[REPLY],
                                                reason="The reply asserts a finding it does not cite.")]
            return value, meta
        return model, repairs, reviews

    def test_a_contract_rejection_is_re_asked_with_the_defects_named_and_stays_charged(self):
        model, prompts, reviews = self.rejected_repair(1)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual(self.calls.count(EpisodeScript), 2)
        self.assertIn("Rejections:", prompts[1])
        self.assertIn("Kapitel müssen vollständig und zusammenhängend", prompts[1])
        self.assertTrue(prompts[1].endswith("\n" + prompts[0].rsplit("\n", 1)[1]), "the JSON payload stays the last line")
        # The review after the repair is asked without the repair's rejections.
        self.assertEqual(len(reviews), 2)
        self.assertNotIn("Rejections:", reviews[1])
        # Every attempt is a charged call of its own; nothing is refunded for rejected model work.
        work = self.root / "runs" / run.run_id
        budget = json.loads((work / "budget.json").read_text(encoding="utf-8"))
        self.assertEqual((budget["model_calls"], budget.get("refunded", [])), (len(self.calls) + len(sessions(work)), []))

    def test_a_persistent_contract_rejection_blocks_the_review_stage_after_named_retries(self):
        model, _, _ = self.rejected_repair(99)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "blocked")
        self.assertEqual(run.stages["review"].error.code, "rejected_output")
        self.assertIn("Kapitel müssen vollständig und zusammenhängend", run.stages["review"].error.message)
        self.assertIn("wiederholt", run.stages["review"].error.message)
        self.assertEqual(self.calls.count(EpisodeScript), 3)

    def test_an_episode_the_agent_wrote_is_kept_when_a_resume_writes_the_open_one(self):
        """Transformer, 2026-10-04: 13 drafts were accepted when ep_012 stopped the writing. A resume writes the open
        episode and keeps the rest: a story run's draft the run accepted (drafts/<episode>.json with its stamp) is not
        written by a second session."""
        from podcast_automate import story_agent
        real, written, stop = story_agent.run_agent, [], [True]

        def session(pipeline, folder, *args):
            written.append(folder.name)
            if folder.name == "ep_002" and stop[0]:
                raise AppError("Kontingent erreicht", code="quota_exhausted", status="waiting_for_quota")
            return real(pipeline, folder, *args)

        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            return (second_episode(value) if output_type is SeriesPlan else value), meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.story_agent.run_agent", side_effect=session):
            stopped = run_script(self.root)
            self.assertEqual((stopped.status, stopped.stages["story"].error.code), ("waiting_for_quota", "quota_exhausted"))
            self.assertEqual(written, ["ep_001", "ep_002"])
            work = self.root / "runs" / stopped.run_id
            kept = {name: (work / name).read_bytes() for name in
                    ("drafts/ep_001.json", "drafts/ep_001.checkpoint.json", "story/ep_001/entry.json")}
            written.clear()
            stop[0] = False
            resumed = run_script(self.root, resume=True, run_id=stopped.run_id)
        self.assertEqual(resumed.status, "completed", resumed.stages["story"].error)
        self.assertEqual(written, ["ep_002"])
        self.assertEqual({name: (work / name).read_bytes() for name in kept}, kept)
        self.assertEqual(len(sessions(work)), 2, "one session per episode")

    def test_a_long_cold_open_reaches_the_quality_report_as_an_advisory(self):
        opening = example_script()
        opening.segments[0].text = " ".join(["Wort"] * 101)

        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is SeriesPlan:
                # 112 words are 0.86 estimated minutes: within 85 to 120 percent of 0.8, so the
                # only advisories are the opening itself and, since 2026-10-06, the partner's 11 of
                # those 112 words (10 percent, under the 25 percent floor).
                value.episodes[0].target_minutes = 0.8
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.claude_code.claude_command", return_value=fixtures.fake_agent(self.root.parent, opening)):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", {k: v.error for k, v in run.stages.items() if v.error})
        rows = read_yaml(self.root / "reports/script_quality.yaml")["episodes"]["ep_001"]["advisories"]
        self.assertEqual([(r["code"], r["episode_id"], r["count"], r["segment_ids"]) for r in rows],
                         [("long_cold_open", "ep_001", 101, [OPENING]), ("low_partner_share", "ep_001", 10, [])])
        self.assertIn("101", rows[0]["detail"])

    def test_changed_research_is_blocked_before_model_calls(self):
        (self.root / "research/dossier.yaml").write_text("changed", encoding="utf-8")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            with self.assertRaises(AppError) as raised:
                run_script(self.root)
        self.assertEqual(raised.exception.code, "invalid_research")
        self.assertEqual(self.calls, [])

    def test_unknown_finding_reference_prevents_export(self):
        invented = example_script()
        invented.segments[0].knowledge_refs = ["invented_claim"]
        cli = fixtures.fake_agent(self.root.parent, invented)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model), \
             patch("podcast_automate.claude_code.claude_command", return_value=cli):
            run = run_script(self.root)
        self.assertEqual(run.stages["story"].error.code, "story_agent_failed")
        self.assertIn("use only current or previously introduced finding IDs", run.stages["story"].error.message)
        self.assertEqual(run.stages["publish"].status, "pending")
        self.assertFalse((self.root / "runs" / run.run_id / "drafts/ep_001.json").exists(), "the draft is not accepted")
        self.assertFalse((self.root / "episodes/ep_001/script.yaml").exists())

    def test_quota_after_the_agent_wrote_resumes_through_cli_without_writing_again(self):
        paused = False
        def model(prompt, output_type, directory, **kwargs):
            nonlocal paused
            if output_type is ScriptReview and not paused:
                paused = True
                raise AppError("Quota", code="quota_exhausted", status="waiting_for_quota")
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            first = run_script(self.root)
            self.assertEqual(first.status, "waiting_for_quota")
            work = self.root / "runs" / first.run_id
            draft = (work / "drafts/ep_001.json").read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["resume", str(self.root), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(len(sessions(work)), 1, "the agent's draft is not written again")
        self.assertEqual((work / "drafts/ep_001.json").read_bytes(), draft)
        self.assertTrue((self.root / "episodes/ep_001/script.yaml").is_file())
        self.assertEqual(json.loads(output.getvalue())["run"]["kind"], "script")

    def persistent_review(self, category, reason):
        """The fixture model whose every script review raises the same point on host_b's reply."""
        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ScriptReview:
                value.issues = [ScriptIssue(category=category, segment_ids=[REPLY], reason=reason)]
            return value, meta
        return model

    def test_persistent_grounding_issues_block_and_keep_retry_limit_on_resume(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.persistent_review("grounding", "The claim has no supporting finding.")):
            run = run_script(self.root)
            calls = len(self.calls)
            resumed = run_script(self.root, resume=True)
        self.assertEqual(run.stages["review"].error.code, "script_review_failed")
        self.assertEqual(self.calls.count(EpisodeScript), 3, "three repairs before the run stops")
        self.assertEqual(resumed.status, "blocked")
        self.assertEqual(len(self.calls), calls)
        self.assertFalse((self.root / "episodes/ep_001/script.md").exists())

    def test_persistent_editorial_points_are_noted_without_a_repair(self):
        """Until 2026-09-29 a depth point the three repairs could not settle stopped the run; the user chose that
        clarity, depth and dialogue points become notes the reader sees before approving audio (Ontologies). A story
        run repairs only what blocks (D-181), so such a point is noted at once and the agent's telling stays."""
        with patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.persistent_review("depth", "The example is not worked through.")):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual((self.calls.count(ScriptReview), self.calls.count(EpisodeScript)), (1, 0))
        work = self.root / "runs" / run.run_id
        notes = json.loads((work / "reviews/ep_001_accepted_notes.json").read_text(encoding="utf-8"))
        self.assertEqual([n["reason"] for n in notes], ["The example is not worked through."])
        self.assertEqual(json.loads((work / "reviews/ep_001.json").read_text(encoding="utf-8"))["issues"][0]["category"], "depth")
        self.assertTrue((self.root / "episodes/ep_001/script.md").exists())

    def test_a_stuck_grounding_review_gets_three_new_repairs_only_when_asked(self):
        from podcast_automate.run_budget import approve_fresh_attempts
        with patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.persistent_review("grounding", "The claim has no supporting finding.")):
            run = run_script(self.root)
            before = self.calls.count(ScriptReview)
            record = approve_fresh_attempts(self.root, run.run_id)
            resumed = run_script(self.root, resume=True)
        self.assertEqual((record["reviews"], record["supplements"]), (["ep_001"], []))
        self.assertEqual(resumed.stages["review"].error.code, "script_review_failed")
        self.assertEqual(self.calls.count(ScriptReview) - before, 3, "one new review after each of three new repairs")
        checkpoint = json.loads((self.root / "runs" / run.run_id / "reviews/ep_001_checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["repairs"], 3)

    def first_review_advises(self, *advisories):
        """The fixture model whose first script review raises nothing and files ``advisories``; later reviews pass."""
        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ScriptReview and not kwargs["prompt_version"].endswith("+followup"):
                value.advisories = list(advisories)
            return value, meta
        return model

    def test_a_first_reviews_own_advisory_is_a_note_unless_it_concerns_evidence_or_scope(self):
        """Ontologies ep_019, 2026-10-04: the review filed a closing-structure point as an advisory in every round
        ("trimming is optional … do not block"), and the code counted it as an issue each time, until all three repairs
        were spent and the run stopped. A structure, clarity, depth or dialogue advisory is now a note."""
        structure = ScriptIssue(category="structure", segment_ids=[REPLY], reason="Two summing-up turns; trimming is optional.")
        clarity = ScriptIssue(category="clarity", segment_ids=[OPENING], reason="The opening question could be sharper.")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.first_review_advises(structure, clarity)):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual(self.calls.count(ScriptReview), 1, "no repair, so no review after one")
        work = self.root / "runs" / run.run_id
        report = json.loads((work / "reviews/ep_001.json").read_text(encoding="utf-8"))
        self.assertEqual((report["issues"], [a["category"] for a in report["advisories"]]), ([], ["structure", "clarity"]))
        notes = json.loads((work / "reviews/ep_001_accepted_notes.json").read_text(encoding="utf-8"))
        self.assertEqual([n["reason"] for n in notes], [structure.reason, clarity.reason])

    def test_a_first_reviews_evidence_advisory_still_counts_as_an_issue(self):
        grounding = ScriptIssue(category="grounding", segment_ids=[REPLY], reason="The score direction has no finding.")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.first_review_advises(grounding)):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual(self.calls.count(ScriptReview), 2, "the first review and one after the repair it caused")
        self.assertEqual(self.calls.count(EpisodeScript), 1)

    def test_a_review_stopped_under_an_earlier_version_is_reviewed_again_without_a_repair(self):
        """Ontologies ep_019, 2026-10-04: three repairs spent on a structure point, and the run stopped. Only a verdict
        given with today's review and its corpus rule stands (saved_verdict_stands), so the resume reviews the saved
        draft once more instead of stopping again; where the review files the point as an advisory it is a note, and
        no repair runs."""
        with patch("podcast_automate.scripting.CodexAdapter.structured",
                   side_effect=self.persistent_review("structure", "Three summing-up turns in a row.")):
            run = run_script(self.root)
        self.assertEqual(run.stages["review"].error.code, "script_review_failed")
        path = self.root / "runs" / run.run_id / "reviews/ep_001_checkpoint.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["repairs"], 3)
        write_json(path, {**saved, "script_review_version": "script_review.v12-source-corrected"})
        optional = ScriptIssue(category="structure", segment_ids=[REPLY], reason="Still three turns; trimming is optional.")

        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is ScriptReview:
                value.advisories = [optional]
            return value, meta
        reviews, scripts = self.calls.count(ScriptReview), self.calls.count(EpisodeScript)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            resumed = run_script(self.root, resume=True)
        self.assertEqual(resumed.status, "completed", resumed.stages["review"].error)
        self.assertEqual((self.calls.count(ScriptReview) - reviews, self.calls.count(EpisodeScript) - scripts), (1, 0))
        notes = json.loads((self.root / "runs" / run.run_id / "reviews/ep_001_accepted_notes.json").read_text(encoding="utf-8"))
        self.assertEqual([n["reason"] for n in notes], [optional.reason])

    def follow_up_model(self, basis):
        """The first review raises a grounding point on host_b's reply, which the repair rewrites. The first review
        after the repair raises a new point on host_a's opening, which no repair touched, with ``basis``; later reviews
        pass. A later repair leaves the reply as the first one wrote it."""
        followups = []

        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            version = kwargs["prompt_version"]
            if output_type is EpisodeScript and version == STORY_REPAIR_VERSION:
                if not value.segments[1].text.endswith(" A lower score is the better fit."):
                    value.segments[1].text += " A lower score is the better fit."
            if output_type is ScriptReview and not version.endswith("+followup"):
                value.issues = [ScriptIssue(category="grounding", segment_ids=[REPLY], reason="The score direction has no finding.")]
            elif output_type is ScriptReview:
                payload = json.loads(prompt.splitlines()[-1])
                followups.append((payload["previous_issues"], payload["changed_segments"]))
                if len(followups) == 1:
                    value.issues = [ScriptIssue(category="grounding", segment_ids=[OPENING],
                                                reason="The question implies a comparison no finding names.")]
                    value.issue_basis = [basis]
            return value, meta
        return model, followups

    def test_a_review_after_a_repair_turns_new_points_on_untouched_segments_into_advisories(self):
        """Ontologies, 2026-09-29: every review after a repair found new grounding points on segments no repair
        had touched, so ep_005 went through fifteen reviews without converging. The code, not the review's own
        basis, decides the scope: a point on an untouched segment is reported and does not block."""
        model, followups = self.follow_up_model("changed")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual(self.calls.count(ScriptReview), 2, "the first review and one after the single repair")
        self.assertEqual(followups, [([{"category": "grounding", "segment_ids": [REPLY],
                                        "reason": "The score direction has no finding."}], [REPLY])])
        work = self.root / "runs" / run.run_id
        report = json.loads((work / "reviews/ep_001.json").read_text(encoding="utf-8"))
        self.assertEqual((report["issues"], [a["segment_ids"] for a in report["advisories"]]), ([], [[OPENING]]))
        notes = json.loads((work / "reviews/ep_001_accepted_notes.json").read_text(encoding="utf-8"))
        self.assertEqual([n["segment_ids"] for n in notes], [[OPENING]])
        self.assertTrue(read_yaml(self.root / "episodes/ep_001/script.yaml")["segments"][1]["text"].endswith("better fit."))

    def test_a_factual_error_on_an_untouched_segment_still_blocks_a_review_after_a_repair(self):
        model, followups = self.follow_up_model("factual_error")
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual(self.calls.count(ScriptReview), 3, "the error on the opening took a second repair")
        self.assertEqual(followups[1], ([{"category": "grounding", "segment_ids": [OPENING],
                                          "reason": "The question implies a comparison no finding names."}], []))

    def test_a_segment_that_follows_its_section_where_the_finding_misstates_it_is_noted_not_repaired(self):
        """2026-10-03 (Ontologies ep_001): the review marked six segments that followed their section correctly as drift,
        each follow-up was handed those drifts back as previous issues and repeated them, and six repairs changed
        nothing; it ended only when another model judged them preserved. Such a segment is source_corrected now: noted
        in the review's limitations, never repaired, and a drift issue is judged again, not handed back."""
        reviews, repairs = [], []

        def model(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            version = kwargs["prompt_version"]
            if output_type is EpisodeScript and version == STORY_REPAIR_VERSION:
                repairs.append(version)
            if output_type is ScriptReview:
                reviews.append((version, json.loads(prompt.splitlines()[-1])))
                check = next(c for c in value.claim_checks if c.finding_ids)
                check.changed_fields = ["source"]
                if len(reviews) == 1:
                    check.verdict, check.reason = "drift", "The segment repeats the finding's narrowing of its section."
                else:
                    check.verdict, check.reason = "source_corrected", "The finding narrows the section; the segment follows it."
            return value, meta
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.stages["review"].error)
        self.assertEqual((len(reviews), len(repairs)), (2, 1), "one repair for the drift, none for the correction")
        version, payload = reviews[1]
        self.assertTrue(version.endswith("+followup"))
        self.assertFalse([i for i in payload["previous_issues"] if i["reason"].startswith("Claim drift (")])
        report = json.loads((self.root / "runs" / run.run_id / "reviews/ep_001.json").read_text(encoding="utf-8"))
        self.assertTrue(any("gibt diesen Abschnitt ungenau wieder" in note for note in report["limitations"]))
        self.assertTrue(any("gibt diesen Abschnitt ungenau wieder" in note for note in
                            read_yaml(self.root / "reports/script_quality.yaml")["episodes"]["ep_001"]["model_review"]["limitations"]))

    def test_a_source_corrected_receipt_names_the_section_it_follows(self):
        from podcast_automate.evidence_models import SegmentClaimCheck
        from podcast_automate.script_evidence import receipt_defects, source_corrections
        segment = next(s for s in example_script().segments if s.knowledge_refs)
        known, sections = set(segment.knowledge_refs), {"src_a#sec_1"}
        check = SegmentClaimCheck(segment_id=segment.segment_id, finding_ids=list(segment.knowledge_refs),
                                  verdict="source_corrected", quote=segment.text, reason="The finding drops a qualification.",
                                  changed_fields=["source"], source_refs=["src_a#sec_1"])
        self.assertEqual(receipt_defects(check, segment, known, sections), [])
        for broken in ({"source_refs": []}, {"source_refs": ["src_b#sec_9"]}, {"changed_fields": ["scope"]}):
            with self.subTest(broken=broken):
                self.assertTrue(receipt_defects(check.model_copy(update=broken), segment, known, sections))
        (note,) = source_corrections(ScriptReview(issues=[], limitations=[], claim_checks=[check]))
        self.assertIn("src_a#sec_1", note)

    def test_the_review_scope_follows_changed_segments_and_previous_issues(self):
        before = example_script()
        after = before.model_copy(deep=True)
        after.segments[1].knowledge_refs = []
        self.assertEqual(changed_segments(before, after), ["seg_002"])
        self.assertEqual(changed_segments(before, before), [])
        point = lambda segment, category="grounding": ScriptIssue(category=category, segment_ids=[segment], reason="r")
        # Of the review's own advisories in scope, only evidence and scope points count as issues (2026-10-04: a
        # structure point its reviewer called optional in every round took all three repairs of Ontologies ep_019).
        review = ScriptReview(issues=[point("seg_001"), point("seg_003"), point("seg_004"), point("seg_005")],
                              limitations=[], issue_basis=["previous", "changed", "source_contradiction", "changed"],
                              advisories=[point("seg_003", "clarity"), point("seg_002"), point("seg_001", "structure"),
                                          point("seg_003", "scope")])
        previous = ScriptReview(issues=[point("seg_001")], limitations=[])
        scoped = follow_up_scope(review, previous, ["seg_002", "seg_003"])
        self.assertEqual([(i.segment_ids[0], i.category) for i in scoped.issues],
                         [("seg_001", "grounding"), ("seg_003", "grounding"), ("seg_004", "grounding"),
                          ("seg_002", "grounding"), ("seg_003", "scope")])
        self.assertEqual([(i.segment_ids[0], i.category) for i in scoped.advisories],
                         [("seg_005", "grounding"), ("seg_003", "clarity"), ("seg_001", "structure")])

    def test_a_new_whole_episode_point_after_a_repair_is_an_advisory_unless_it_repeats_or_is_critical(self):
        """Finding of 2026-10-02: a point naming no segment was always in scope, so a new whole-episode structure point
        in a later review blocked. It now blocks only as a repeat of an earlier whole-episode point of its category,
        or as a factual or source error; the code decides, not the review's basis."""
        whole = lambda category, reason: ScriptIssue(category=category, segment_ids=[], reason=reason)
        previous = ScriptReview(issues=[whole("structure", "No worked example."), whole("depth", "Stays abstract.")],
                                limitations=[])
        review = ScriptReview(issues=[whole("structure", "Still no worked example."), whole("scope", "Drifts into training."),
                                      whole("grounding", "The year is wrong."), whole("grounding", "A claim lacks a finding.")],
                              limitations=[], issue_basis=["previous", "changed", "factual_error", "previous"],
                              advisories=[whole("structure", "The outro is thin."), whole("depth", "Stays abstract."),
                                          whole("clarity", "One term is unexplained.")])
        scoped = follow_up_scope(review, previous, ["seg_001"])
        self.assertEqual([i.reason for i in scoped.issues], ["Still no worked example.", "The year is wrong."])
        # The structure point the review filed as an advisory repeats a whole-episode category, and stays an advisory
        # all the same: only evidence and scope advisories count as issues (2026-10-04).
        self.assertEqual([i.reason for i in scoped.advisories],
                         ["Drifts into training.", "A claim lacks a finding.", "The outro is thin.", "Stays abstract.",
                          "One term is unexplained."])

    def blocked_with_a_saved_correction(self):
        """A run whose every review raises a grounding point on host_a's opening, until its three repairs are spent;
        each repair ends the reply with the same correction. Returns the run and its review checkpoint."""
        def rejected(prompt, output_type, directory, **kwargs):
            value, meta = self.model(prompt, output_type, directory, **kwargs)
            if output_type is EpisodeScript and kwargs['prompt_version'] == STORY_REPAIR_VERSION:
                if not value.segments[-1].text.endswith(' This saved correction must remain.'):
                    value.segments[-1].text += ' This saved correction must remain.'
            if output_type is ScriptReview:
                value.issues = [ScriptIssue(category='grounding', segment_ids=[OPENING], reason='Missing series metadata.')]
            return value, meta
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=rejected):
            first = run_script(self.root)
        self.assertEqual(first.status, 'blocked')
        return first, self.root / 'runs' / first.run_id / 'reviews/ep_001_checkpoint.json'

    def resume_records_reviewed_scripts(self):
        """Resume the latest run with a passing review; returns the run and the scripts its reviews were given."""
        captured = []
        def repaired_policy(prompt, output_type, directory, **kwargs):
            self.assertIsNot(output_type, EpisodeScript)
            if output_type is ScriptReview:
                captured.append(json.loads(prompt.splitlines()[-1])['script'])
            return self.model(prompt, output_type, directory, **kwargs)
        with patch('podcast_automate.scripting.CodexAdapter.structured', side_effect=repaired_policy):
            return run_script(self.root, resume=True), captured

    def test_review_policy_fix_rechecks_latest_draft_without_resetting_used_repairs(self):
        first, checkpoint = self.blocked_with_a_saved_correction()
        saved = json.loads(checkpoint.read_text(encoding='utf-8'))
        self.assertEqual(saved['repairs'], 3)
        last_draft = saved['draft']
        self.assertTrue(last_draft['segments'][-1]['text'].endswith(' This saved correction must remain.'))
        saved.pop('editorial_review_version')  # A checkpoint made by the previous implementation.
        write_json(checkpoint, saved)
        resumed, captured = self.resume_records_reviewed_scripts()
        self.assertEqual(resumed.status, 'completed')
        self.assertEqual(captured, [last_draft])
        self.assertEqual(json.loads(checkpoint.read_text(encoding='utf-8'))['repairs'], 3)
        self.assertEqual(read_yaml(self.root / 'episodes/ep_001/script.yaml'), last_draft)

    def test_script_review_version_drift_rechecks_latest_draft_without_resetting_used_repairs(self):
        first, checkpoint = self.blocked_with_a_saved_correction()
        saved = json.loads(checkpoint.read_text(encoding='utf-8'))
        self.assertEqual(saved['repairs'], 3)
        self.assertEqual(saved['script_review_version'], STORY_REVIEW)
        last_draft = saved['draft']
        saved['script_review_version'] = 'script_review.v8-evidence'  # The tag before this prompt batch.
        write_json(checkpoint, saved)
        resumed, captured = self.resume_records_reviewed_scripts()
        self.assertEqual(resumed.status, 'completed')
        self.assertEqual(captured, [last_draft])
        after = json.loads(checkpoint.read_text(encoding='utf-8'))
        self.assertEqual((after['repairs'], after['script_review_version']), (3, STORY_REVIEW))
        self.assertEqual(read_yaml(self.root / 'episodes/ep_001/script.yaml'), last_draft)

    def test_changed_project_requires_new_script_run(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run_script(self.root)
            self.config.depth_request = "A changed explanation approach"
            write_yaml(self.root / "project.yaml", self.config.model_dump())
            with self.assertRaises(AppError) as raised:
                run_script(self.root, resume=True)
        self.assertEqual(raised.exception.code, "inputs_changed")

    def test_raised_limits_or_deadline_keep_a_resumable_script_run(self):
        # Operational fields are bound as the run started (storage.bound_brief, 2026-10-02): the Studio saves the brief
        # while a run waits for quota, and a raised call limit or a longer deadline used to end the run for good.
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            first = run_script(self.root)
            self.config.research_limits = self.config.research_limits.model_copy(
                update={"model_calls": self.config.research_limits.model_calls + 250})
            self.config.runtime = self.config.runtime.model_copy(
                update={"text_timeout_seconds": self.config.runtime.text_timeout_seconds + 600})
            write_yaml(self.root / "project.yaml", self.config.model_dump())
            # `pla status` agrees with resume (2026-10-04: it bound only research runs and called this one changed).
            self.assertFalse(status(self.root, first.run_id)["project_changed"])
            resumed = run_script(self.root, resume=True, run_id=first.run_id)
            self.assertEqual((resumed.run_id, resumed.status), (first.run_id, "completed"))
            self.config.depth_request = "A changed explanation approach"
            write_yaml(self.root / "project.yaml", self.config.model_dump())
            self.assertTrue(status(self.root, first.run_id)["project_changed"])
            with self.assertRaises(AppError) as raised:
                run_script(self.root, resume=True, run_id=first.run_id)
        self.assertEqual(raised.exception.code, "inputs_changed")

    def test_modified_readable_script_is_reexported_without_model_calls(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            first = run_script(self.root)
            calls = len(self.calls)
            (self.root / "episodes/ep_001/script.md").write_text("broken", encoding="utf-8")
            resumed = run_script(self.root, resume=True)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(len(sessions(self.root / "runs" / first.run_id)), 1)
        self.assertIn("**Host A:**", (self.root / "episodes/ep_001/script.md").read_text(encoding="utf-8"))

    def test_resume_preserves_manual_changes_to_canonical_script(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            first = run_script(self.root)
            calls = len(self.calls)
            path = self.root / "episodes/ep_001/script.yaml"
            data = read_yaml(path)
            data["segments"][0]["text"] = "A user-edited question."
            write_yaml(path, data)
            before = path.read_bytes()
            with self.assertRaises(AppError) as raised:
                run_script(self.root, resume=True)
        self.assertEqual(raised.exception.code, "script_edited")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(len(sessions(self.root / "runs" / first.run_id)), 1)

    def test_revision_keeps_plan_and_snapshots_feedback_then_resumes(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            original = run_script(self.root, episode="ep_001")
            plan_before = (self.root / "models/series_plan.yaml").read_bytes()
            published = read_yaml(self.root / "episodes/ep_001/script.yaml")
            self.config.depth_request = "More direct, less repetition."
            write_yaml(self.root / "project.yaml", self.config.model_dump())
            revised = run_script(self.root, revise="ep_001", feedback="Dial down the hand-holding.")
            resumed = run_script(self.root, resume=True)
        self.assertEqual(revised.status, "completed")
        self.assertEqual(resumed.run_id, revised.run_id)
        self.assertNotEqual(original.run_id, revised.run_id)
        self.assertEqual(self.calls.count(SeriesPlan), 1)
        # One agent session wrote the episode and one revised it; the resume of the finished revision wrote nothing.
        self.assertEqual([len(sessions(self.root / "runs" / run.run_id)) for run in (original, revised)], [1, 1])
        self.assertEqual(revised.stages["planning"].attempts, 0)
        self.assertEqual((self.root / "models/series_plan.yaml").read_bytes(), plan_before)
        inputs = json.loads((self.root / "runs" / revised.run_id / "inputs.json").read_text(encoding="utf-8"))
        self.assertEqual(inputs["revision"]["source_run_id"], original.run_id)
        self.assertEqual(inputs["revision"]["feedback"], "Dial down the hand-holding.")
        self.assertEqual(inputs["revision"]["original_script"], published)
        self.assertFalse(read_yaml(self.root / "episodes/audio_review.yaml")["audio_approved"])

    def test_revision_rejects_changed_saved_plan(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            original = run_script(self.root)
            calls = len(self.calls)
            (self.root / "runs" / original.run_id / "series_plan.json").write_text("changed", encoding="utf-8")
            with self.assertRaises(AppError) as raised:
                run_script(self.root, revise="ep_001", feedback="Tighter.")
        self.assertEqual(raised.exception.code, "invalid_revision")
        self.assertEqual(len(self.calls), calls)

    def test_plan_rejects_cycles_and_forward_prerequisites(self):
        dossier = ResearchDossier.model_validate(read_yaml(self.root / "research/dossier.yaml"))
        plan = example_plan()
        plan.dependencies = [Dependency(before="f_energy", after="f_energy", reason="Invalid self dependency")]
        self.assertTrue(any("cycle" in item for item in validate_plan(plan, dossier)))
        plan = example_plan()
        plan.episodes[0].prerequisite_episodes = ["ep_002"]
        self.assertTrue(any("earlier" in item for item in validate_plan(plan, dossier)))

    def test_revision_can_repair_an_existing_script_that_is_too_short(self):
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            self.assertEqual(run_script(self.root, episode="ep_001").status, "completed")
            path = self.root / "episodes/ep_001/script.yaml"
            original = read_yaml(path)
            for segment in original["segments"]:
                segment["text"] = "Short."
            write_yaml(path, original)
            self.calls.clear()
            revised = run_script(self.root, revise="ep_001", feedback="Develop the missing explanation.")
        self.assertEqual(revised.status, "completed")
        # Every call of a run but the outline: the revised episode is the whole of this one-episode series, so the
        # series review reads it again.
        self.assertEqual(self.calls, RUN_CALLS[1:])


class ScriptValidationTests(unittest.TestCase):
    def test_script_rejects_spoken_metadata_and_excess_duration(self):
        script = example_script()
        script.segments[0].text = "See https://example.org/source"
        self.assertTrue(any("metadata" in item for item in validate_script(script, example_plan().episodes[0])))
        # Up to an hour since 2026-09-30 (script_models.MAX_EPISODE_MINUTES); 40 minutes pass, 65 do not.
        script.segments[0].text = "word " * 5200
        self.assertFalse(any("minutes;" in item for item in validate_script(script, example_plan().episodes[0])))
        script.segments[0].text = "word " * 8450
        self.assertTrue(any("60 minutes" in item for item in validate_script(script, example_plan().episodes[0])))

    def test_script_duration_matches_plan_without_treating_slow_estimate_as_measured_audio(self):
        entry = example_plan().episodes[0]
        entry.target_minutes = 29.5
        script = example_script()
        self.assertTrue(any("85%" in item for item in validate_script(script, entry)))
        script.segments[0].text = "word " * 3750
        self.assertEqual(validate_script(script, entry), [])

    def test_later_scenes_can_build_on_earlier_findings_without_allowing_forward_references(self):
        entry = example_plan().episodes[0]
        entry.finding_ids.append("f_consequence")
        entry.scenes.append(ScenePlan(scene_id="scene_consequence", title="A consequence",
            question="What follows?", purpose="synthesis", finding_ids=["f_consequence"],
            explanation_steps=["Apply the earlier comparison."]))
        script = example_script()
        script.chapters.append(Chapter(chapter_id="scene_consequence", title="A consequence"))
        segment = script.segments[-1]
        segment.scene_id = segment.chapter_id = "scene_consequence"
        segment.knowledge_refs = ["f_energy", "f_consequence"]
        self.assertEqual(validate_script(script, entry), [])
        script.segments[0].knowledge_refs.append("f_consequence")
        self.assertTrue(any("previously introduced" in item for item in validate_script(script, entry)))

    def test_the_dialogue_covers_the_core_and_may_cite_supporting_findings_in_any_scene(self):
        """Two tiers since 2026-10-02: only the core finding_ids must be cited."""
        entry = example_plan().episodes[0]
        entry.supporting_finding_ids = ["f_detail", "f_figure"]
        script = example_script()
        self.assertEqual(validate_script(script, entry, check_duration=False), [], "no supporting finding is required")
        script.segments[0].knowledge_refs = ["f_detail"]
        script.segments[1].knowledge_refs = ["f_energy", "f_figure"]
        self.assertEqual(validate_script(script, entry, check_duration=False), [])
        script.segments[1].knowledge_refs = ["f_figure"]
        self.assertEqual(validate_script(script, entry, check_duration=False),
                         ["The dialogue must cover every planned finding.",
                          "Core findings (finding_ids) not yet cited: f_energy. Supporting and recalled findings need "
                          "not be cited."])
        script.segments[1].knowledge_refs = ["f_energy", "f_invented"]
        self.assertTrue(any("previously introduced" in e for e in validate_script(script, entry, check_duration=False)))


class AssembledDossierScriptTests(fixtures.ScriptProjectCase):
    """A project researched as runs started since 2026-10-01 are: the dossier is assembled from every verified
    answer (question_synthesis.assemble_dossier), and the 25-word quote rule applies to the script instead."""
    assembled = True
    FINDING = "task_definition__f_energy"

    def model(self, prompt, output_type, directory, **kwargs):
        value, metadata = super().model(prompt, output_type, directory, **kwargs)
        if output_type in (SeriesPlan, EpisodeScript):
            value = output_type.model_validate_json(value.model_dump_json().replace('"f_energy"', f'"{self.FINDING}"'))
        return value, metadata

    def test_the_plan_reads_the_assembled_dossier_without_excerpts_and_the_agents_check_refuses_a_long_quote(self):
        """The agent's check.py holds its draft to the quote rule over the passages it was given, and the agent writes
        the draft again (here as the fake agent does: without the quote) before the run accepts it."""
        # The first draft reads the passage it was given out word for word; the second is the fixture's script.
        quoting = ('quoted = json.loads(json.dumps(draft))\n'
                   'quoted["segments"][1]["text"] += " Quote: " + material["sections"][0]["text"]\n'
                   'json.dump(quoted, open("draft.json", "w", encoding="utf-8"), ensure_ascii=False)\n'
                   'first = subprocess.run([sys.executable, "check.py"], capture_output=True, text=True, encoding="utf-8")\n'
                   'open("quoted_check.json", "w", encoding="utf-8").write(first.stdout)\n'
                   'json.dump(draft, open("draft.json", "w", encoding="utf-8"), ensure_ascii=False)')
        draft = EpisodeScript.model_validate_json(example_script().model_dump_json().replace('"f_energy"', f'"{self.FINDING}"'))
        cli = agent_variant(self.root.parent, 'json.dump(draft, open("draft.json", "w", encoding="utf-8"), ensure_ascii=False)',
                            quoting, draft)
        plans = []

        def model(prompt, output_type, directory, **kwargs):
            if output_type is SeriesPlan:
                plans.append(json.loads(prompt.splitlines()[-1]))
            return self.model(prompt, output_type, directory, **kwargs)
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=model), \
             patch("podcast_automate.claude_code.claude_command", return_value=cli):
            run = run_script(self.root)
        self.assertEqual(run.status, "completed", run.model_dump())
        dossier = plans[0]["dossier"]
        self.assertTrue(dossier["assembled"])
        self.assertEqual([f["id"] for f in dossier["findings"]], [self.FINDING])
        self.assertFalse({"evidence", "claim_contract"} & set(dossier["findings"][0]))
        self.assertEqual(dossier["answers"][0]["finding_ids"], [self.FINDING])
        work = self.root / "runs" / run.run_id
        material = json.loads((work / "story/ep_001/material.json").read_text(encoding="utf-8"))
        self.assertIn(TEXT, material["sections"][0]["text"], "the passage the agent quoted is the fixture source")
        check = json.loads((work / "story/ep_001/quoted_check.json").read_text(encoding="utf-8"))
        self.assertFalse(check["passed"])
        self.assertTrue(any("words of this source verbatim" in error for error in check["errors"]), check["errors"])
        published = json.loads((work / "drafts/ep_001.json").read_text(encoding="utf-8"))
        self.assertNotIn(TEXT, json.dumps(published, ensure_ascii=False))


class PublishedArchiveTests(fixtures.ScriptProjectCase):
    def test_publishing_a_new_outline_archives_the_old_series_and_a_resume_keeps_its_own(self):
        old = example_plan().episodes[0].model_copy(update={"episode_id": "ep_009", "title": "From an older outline"})
        write_yaml(self.root / "episodes/ep_009/episode_plan.yaml", old.model_dump())
        write_yaml(self.root / "episodes/ep_009/script.yaml", {"older": True})
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.model):
            run = run_script(self.root)
            self.assertEqual(run.status, "completed")
            self.assertEqual([p.name for p in sorted((self.root / "episodes").glob("ep_*"))], ["ep_001"])
            (archive,) = (self.root / "episodes/archive").iterdir()
            self.assertTrue((archive / "ep_009/script.yaml").is_file())
            run_script(self.root, resume=True, run_id=run.run_id)
        self.assertEqual(len(list((self.root / "episodes/archive").iterdir())), 1, "a resume of the outline archives nothing")
        self.assertTrue((self.root / "episodes/ep_001/script.yaml").is_file())


class EpisodeArchiveTests(unittest.TestCase):
    def test_episodes_of_another_outline_move_to_the_archive_and_this_outlines_stay(self):
        """The user's choice of 2026-10-02: a new outline's scripts were published beside the old series, and old
        episodes the new plan lacks stayed in the Studio with their recordings. They move to episodes/archive/ now;
        an episode of this outline (a resume, a revision, one episode at a time) stays where it is."""
        import tempfile
        from podcast_automate.script_artifacts import archive_other_series
        with tempfile.TemporaryDirectory() as folder:
            root, plan = Path(folder), example_plan()
            entry = plan.episodes[0]
            write_yaml(root / "episodes/ep_001/episode_plan.yaml", entry.model_dump())
            write_yaml(root / "episodes/ep_001/script.yaml", {"kept": True})
            write_yaml(root / "episodes/ep_002/episode_plan.yaml",
                       entry.model_copy(update={"episode_id": "ep_002", "title": "An older outline"}).model_dump())
            write_json(root / "episodes/ep_002/audio_latest.json", {"parts": [{"audio": "exports/ep_002/run_a/part_01.mp3"}]})
            (root / "episodes/ep_003").mkdir()
            archive = archive_other_series(root, plan, "run_new")
            self.assertEqual([p.name for p in sorted((root / "episodes").glob("ep_*"))], ["ep_001"])
            self.assertEqual([p.name for p in sorted(archive.glob("ep_*"))], ["ep_002", "ep_003"])
            self.assertEqual(json.loads((archive / "ep_002/audio_latest.json").read_text(encoding="utf-8"))["parts"][0]["audio"],
                             "exports/ep_002/run_a/part_01.mp3", "the recording stays where its report names it")
            receipt = json.loads((archive / "receipt.json").read_text(encoding="utf-8"))
            self.assertEqual((receipt["run_id"], receipt["episodes"]), ("run_new", ["ep_002", "ep_003"]))
            self.assertIsNone(archive_other_series(root, plan, "run_new"), "nothing else to archive")
            # The story agent's own chapters are the same outline's episode (D-181): on 2026-11-03 the run of Asimov v2's
            # ep_002 archived ep_001 for them, as ep_001's had archived ep_007.
            chapters = [entry.scenes[0].model_copy(update={"scene_id": "ep_001_c1", "title": "Die Nacht"})]
            write_yaml(root / "episodes/ep_001/episode_plan.yaml", entry.model_copy(update={"scenes": chapters}).model_dump())
            self.assertIsNone(archive_other_series(root, plan, "run_next"))
            # An episode at its own length is too (D-191, D-192): on 2026-11-08 publishing Geld S1's revised ep_002
            # (36 of 48 minutes) archived the revised ep_003 (38 of 58), and each new episode of a batch at 35 minutes
            # would have archived the one before.
            write_yaml(root / "episodes/ep_001/episode_plan.yaml", entry.model_copy(
                update={"scenes": chapters, "target_minutes": entry.target_minutes * 2}).model_dump())
            self.assertIsNone(archive_other_series(root, plan, "run_after_revision"))
            # A new outline whose first episode differs takes the old one along, whatever its id.
            renewed = plan.model_copy(deep=True)
            renewed.episodes[0].title = "A new first episode"
            later = archive_other_series(root, renewed, "run_newer")
            self.assertEqual([p.name for p in later.glob("ep_*")], ["ep_001"])
            self.assertEqual(len(list((root / "episodes/archive").iterdir())), 2)


class QuotationTests(unittest.TestCase):
    WORDS = ("models assign an energy to each configuration and lower energy marks a better fit between "
             "observed variables while learning and inference remain distinct operations of the same "
             "synthetic system described here").split()

    def sources(self):
        return [{"source_id": "src_a", "sections": [{"reference": "src_a#s1", "text": " ".join(self.WORDS).capitalize() + "."}]}]

    def test_an_episode_quotes_a_source_for_at_most_25_words(self):
        """2026-10-01, the user's choice: the dossier keeps every verified answer with its excerpts; the episode
        quotes one source for at most 25 words, counted in runs of at least six words the passage shares."""
        script = example_script()
        script.segments[0].text = "As the paper says, " + " ".join(self.WORDS[:20]).upper() + "!"
        self.assertEqual(quotation_errors(script, self.sources()), [])
        script.segments[1].text = "And then: " + ", ".join(self.WORDS[20:]) + "."
        (error,) = quotation_errors(script, self.sources())
        self.assertTrue(error.startswith("src_a: the dialogue repeats 31 words of this source verbatim"), error)
        # Runs shorter than six words are common phrases, not quotes.
        script.segments[1].text = " Then ".join(" ".join(self.WORDS[start:start + 5]) for start in range(20, 30, 5))
        self.assertEqual(quotation_errors(script, self.sources()), [])

    def test_the_pipeline_applies_the_quote_rule_only_to_an_assembled_dossier(self):
        from types import SimpleNamespace
        from podcast_automate.research_models import Evidence, Finding
        from podcast_automate.script_pipeline import ScriptRun
        finding = Finding(id="f_energy", kind="definition", statement="Configurations are assigned energies.",
                          evidence=[Evidence(reference="src_a#s1", excerpt="Models assign an energy")])
        script = example_script()
        script.segments[1].text += " " + " ".join(self.WORDS[:30])
        entry = example_plan().episodes[0]
        for assembled, expected in ((True, 1), (False, 0)):
            dossier = ResearchDossier(topic="Test topic", scope_note="s", findings=[finding], coverage=[],
                                      open_questions=[], assembled=assembled)
            pipeline = SimpleNamespace(dossier=dossier, context=self.sources(), sources=None)
            errors = ScriptRun.script_errors(pipeline, script, entry)
            self.assertEqual(sum("verbatim" in e for e in errors), expected, errors)

    def test_the_plan_reads_an_assembled_dossier_without_excerpts_and_a_composed_one_whole(self):
        from podcast_automate.research_models import Evidence, Finding
        finding = Finding(id="f_energy", kind="definition", statement="Configurations are assigned energies.",
                          evidence=[Evidence(reference="src_a#s1", excerpt="Models assign an energy")])
        composed = ResearchDossier(topic="Test topic", scope_note="s", findings=[finding], coverage=[], open_questions=[])
        self.assertEqual(planning_dossier(composed), composed.model_dump())
        view = planning_dossier(composed.model_copy(update={"assembled": True}))
        self.assertEqual(view["findings"], [{k: v for k, v in finding.model_dump().items()
                                             if k not in {"evidence", "claim_contract", "supporting_contracts"}}])
        self.assertNotIn("source_assessments", view)


if __name__ == "__main__":
    unittest.main()
