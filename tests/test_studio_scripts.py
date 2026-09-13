import json
import tempfile
import unittest
from pathlib import Path

from podcast_automate.scripting import script_review_signature
from podcast_automate.studio_scripts import script_previews
from podcast_automate.storage import digest, file_hash, write_json
from tests.test_scripting import example_plan, example_script


class StudioScriptPreviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / "runs/run_preview"
        self.run = {"kind": "script", "run_id": "run_preview", "status": "running", "input_hash": "abc"}
        self.plan = example_plan()
        second = self.plan.episodes[0].model_copy(deep=True, update={"episode_id": "ep_002"})
        self.plan.episodes.append(second)
        write_json(self.work / "series_plan.json", self.plan.model_dump())
        write_json(self.work / "script_request.json", {"episode": None})
        self.script = example_script().model_dump()
        self.draft("ep_001")

    def draft(self, identifier, complete=True):
        path = self.work / "drafts" / f"{identifier}.json"
        write_json(path, {**self.script, "episode_id": identifier})
        if complete:
            write_json(path.with_suffix(".checkpoint.json"), {"sha256": file_hash(path)})

    def polished(self):
        folder = self.work / "polishing/ep_001"
        self.polish = {**self.script, "title": "Polished dialogue"}
        write_json(folder / "script.json", self.polish)
        write_json(folder / "checkpoint.json", {"candidate": self.polish})
        write_json(folder / "result.json", {"status": "passed", "original_digest": digest(self.script),
                                           "polished_digest": digest(self.polish)})

    def reviewed(self):
        self.polished()
        report = {"issues": []}
        write_json(self.work / "reviewed/ep_001.json", self.polish)
        write_json(self.work / "reviews/ep_001.json", report)
        signature = script_review_signature(self.run["input_hash"], file_hash(self.work / "polishing/ep_001/script.json"),
                                            self.plan, self.plan.episodes[0], self.work)
        write_json(self.work / "reviews/ep_001_checkpoint.json", {"draft": self.polish, "review": report, "input_hash": signature})
        write_json(self.work / "reviews/ep_001_teaching.json", {"status": "passed", "script_digest": digest(self.polish)})

    def test_complete_episode_is_readable_while_next_is_unfinished_without_any_publish(self):
        self.draft("ep_002", complete=False)
        before = {str(p): p.read_bytes() for p in self.work.rglob("*.json")}
        previews = script_previews(self.root, self.run)
        self.assertEqual([e["script"]["episode_id"] for e in previews], ["ep_001"])
        self.assertEqual(previews[0]["state"], "draft")
        self.assertTrue(previews[0]["preview"])
        self.assertNotIn("readable_hash", previews[0])
        self.assertFalse((self.root / "episodes").exists())
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.work.rglob("*.json")})
        self.draft("ep_002")
        self.assertEqual(len(script_previews(self.root, self.run)), 2)

    def test_preview_follows_completed_polishing_and_review_checkpoints(self):
        self.polished()
        self.assertEqual(script_previews(self.root, self.run)[0]["state"], "polished")
        self.reviewed()
        self.assertEqual(script_previews(self.root, self.run)[0]["state"], "reviewed")
        write_json(self.work / "reviews/ep_001_teaching.json", {"status": "needs_revision", "script_digest": digest(self.polish)})
        self.assertEqual(script_previews(self.root, self.run)[0]["state"], "polished")
        self.assertEqual(script_previews(self.root, {**self.run, "status": "completed"}), [])

    def test_old_review_cannot_be_presented_as_current_after_polish_changes(self):
        self.reviewed()
        updated = {**self.polish, "title": "A new polish"}
        folder = self.work / "polishing/ep_001"
        write_json(folder / "script.json", updated)
        write_json(folder / "checkpoint.json", {"candidate": updated})
        write_json(folder / "result.json", {"status": "passed", "original_digest": digest(self.script), "polished_digest": digest(updated)})
        preview = script_previews(self.root, self.run)[0]
        self.assertEqual(preview["state"], "polished")
        self.assertEqual(preview["script"]["title"], "A new polish")

    def test_incomplete_or_changed_checkpoint_does_not_hide_other_episodes(self):
        self.draft("ep_002")
        (self.work / "drafts/ep_001.json").write_text('{"incomplete":', encoding="utf-8")
        self.assertEqual([e["script"]["episode_id"] for e in script_previews(self.root, self.run)], ["ep_002"])
        write_json(self.work / "script_request.json", {"episode": "ep_001"})
        self.assertEqual(script_previews(self.root, self.run), [])

    def test_path_injection_and_unrelated_run_never_provide_previews(self):
        write_json(self.work / "series_plan.json", {"episodes": [{"episode_id": "../../private"}]})
        self.assertEqual(script_previews(self.root, self.run), [])
        self.assertEqual(script_previews(self.root, {**self.run, "kind": "episode_audio"}), [])
