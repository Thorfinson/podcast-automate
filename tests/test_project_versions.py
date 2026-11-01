import contextlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.models import TopicBrief
from podcast_automate.project_versions import create_version, imported_library, view
from podcast_automate.research import latest_research_run
from podcast_automate.storage import init_project, read_yaml, write_json, write_yaml
from podcast_automate.studio import make_server

COMPLETED = "run_20260901_000000_000000_aaaaaaaa"
STOPPED = "run_20260902_000000_000000_bbbbbbbb"
UPLOAD = "inputs/uploads/" + "a" * 64 + ".md"
WORK = "inputs/works/" + "b" * 64 + ".pdf"


def research_run(root, run_id, status):
    """A research run as far as a new version reads it: its manifest and one stored source."""
    write_yaml(root / "runs" / run_id / "run_manifest.yaml", {"run_id": run_id, "kind": "research", "status": status})
    for kind, name in (("processed", "src_a.json"), ("raw", "src_a.html")):
        path = root / "sources" / kind / run_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(run_id, encoding="utf-8")


class VersionCase(unittest.TestCase):
    """A podcast with inputs, Studio choices, one completed and one newer stopped research run, and an episode."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        # Resolved as Studio() resolves it (an 8.3 TEMP on Windows differs).
        self.workspace = Path(temp.name).resolve()
        self.projects = self.workspace / "projects"
        self.root = self.projects / "a-podcast"
        init_project(self.root, TopicBrief(topic="A podcast", focus_questions=["Why does it hold?"],
                                           local_sources=[UPLOAD, "notes/own.txt"],
                                           voice_profile={"host_a": "Aiden", "host_b": "Vivian"}))
        for relative, text in ((UPLOAD, "attached"), ("notes/own.txt", "own notes"), ("style_notes.md", "Calm.\n"),
                               ("studio/chat.json", "[]"), ("episodes/ep_001/notes.txt", "old episode")):
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_text(text, encoding="utf-8")
        write_json(self.root / "inputs/attachments.json", [{"id": "a" * 64, "name": "a.md", "path": UPLOAD,
                                                           "bytes": 8, "characters": 8}])
        (self.root / WORK).parent.mkdir(parents=True)
        (self.root / WORK).write_bytes(b"%PDF-1.4 book")
        write_json(self.root / "inputs/works.json", [{"id": "work_" + "b" * 16, "citation": "A Book (2020)", "path": WORK,
                                                     "sha256": "b" * 64, "bytes": 13, "added_at": "2026-10-01T00:00:00+00:00",
                                                     "tasks": ["task_old"], "run_id": COMPLETED}])
        write_json(self.root / "studio/text.json", {"provider": "claude_code", "model": "claude-haiku-5-5"})
        write_json(self.root / "studio/allowances.json", {"fresh_attempts": 2, "extra_calls": 250})
        research_run(self.root, COMPLETED, "completed")
        research_run(self.root, STOPPED, "blocked")


class CreateVersionTests(VersionCase):
    def test_a_new_version_takes_the_inputs_and_none_of_the_runs(self):
        """The user's wish of 2026-10-07: make an old podcast again from its inputs with the current pipeline."""
        target = create_version(self.root)
        self.assertEqual(target.parent, self.projects)
        self.assertTrue(target.name.startswith("a-podcast-v2-"), target.name)
        self.assertEqual(read_yaml(target / "project.yaml"), read_yaml(self.root / "project.yaml"))
        for relative in (UPLOAD, WORK, "inputs/attachments.json", "notes/own.txt", "style_notes.md", "studio/text.json"):
            self.assertEqual((target / relative).read_bytes(), (self.root / relative).read_bytes(), relative)
        # A provided work was named for the old research's questions; the new research plans its own.
        works = json.loads((target / "inputs/works.json").read_text(encoding="utf-8"))
        self.assertEqual((works[0]["tasks"], works[0]["run_id"], works[0]["path"]), ([], None, WORK))
        # Runs, episodes, the chat and the approvals stay with the old version.
        for relative in (f"runs/{COMPLETED}", f"runs/{STOPPED}", "episodes/ep_001", "studio/chat.json",
                         "studio/allowances.json", f"sources/processed/{STOPPED}"):
            self.assertFalse((target / relative).exists(), relative)
        self.assertTrue((self.root / "episodes/ep_001/notes.txt").is_file())
        # A German project without a Jev choice gets today's default.
        self.assertTrue(json.loads((target / "studio/jev_probe.json").read_text(encoding="utf-8"))["enabled"])
        # The newest completed research run comes along as the starting library; the newer stopped one does not.
        self.assertEqual((target / f"sources/raw/{COMPLETED}/src_a.html").read_text(encoding="utf-8"), COMPLETED)
        self.assertEqual(view(target), {"version": 2, "from": "a-podcast",
                                        "library": {"run_id": COMPLETED, "documents": 1, "from_version": 1}})
        self.assertEqual(latest_research_run(target), COMPLETED)
        self.assertEqual(sorted(path.name for path in self.projects.iterdir()), sorted(["a-podcast", target.name]))

    def test_versions_count_up_per_podcast_and_carry_the_library_on(self):
        second = create_version(self.root)
        third = create_version(second)
        self.assertEqual(view(third), {"version": 3, "from": second.name,
                                       "library": {"run_id": COMPLETED, "documents": 1, "from_version": 1}})
        self.assertEqual(view(create_version(self.root))["version"], 4)
        # A version's own completed research seeds its next run before the library it brought along.
        research_run(second, "run_20261007_000000_000000_cccccccc", "completed")
        self.assertEqual(latest_research_run(second), "run_20261007_000000_000000_cccccccc")
        self.assertEqual(imported_library(second), COMPLETED)

    def test_a_podcast_without_completed_research_starts_without_a_library(self):
        write_yaml(self.root / "runs" / COMPLETED / "run_manifest.yaml", {"kind": "research", "status": "running"})
        target = create_version(self.root)
        self.assertIsNone(view(target)["library"])
        self.assertFalse((target / "sources/processed" / STOPPED).exists())
        self.assertIsNone(latest_research_run(target))

    def test_a_failed_copy_leaves_no_half_project(self):
        with patch("podcast_automate.project_versions.shutil.copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                create_version(self.root)
        self.assertEqual([path.name for path in self.projects.iterdir()], ["a-podcast"])

    def test_the_cli_creates_the_next_version_and_names_its_library(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["new-version", str(self.root), "--to", str(self.projects / "second-try"), "--json"])
        data = json.loads(output.getvalue())
        self.assertEqual((code, data["status"], data["version"]["version"]), (0, "created", 2))
        self.assertTrue((self.projects / "second-try/project.yaml").is_file())
        self.assertIn(f"--seed-corpus {COMPLETED}", data["message"])


class StudioVersionTests(VersionCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.workspace, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, data=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request("GET" if data is None else "POST", path, None if data is None else json.dumps(data),
                           headers={"X-Studio-Token": self.server.studio.token, "Content-Type": "application/json"})
        response = connection.getresponse()
        status, body = response.status, json.loads(response.read())
        connection.close()
        return status, body

    def test_the_studio_makes_a_new_version_and_names_it_everywhere(self):
        status, made = self.request("/api/projects/a-podcast/new_version", {})
        self.assertEqual((status, made["version"]), (200, 2), made)
        boot = self.request("/api/bootstrap")[1]
        self.assertTrue(boot["capabilities"]["project_versions"])
        self.assertEqual(sorted((p["id"], p["version"]) for p in boot["projects"]), [("a-podcast", 1), (made["id"], 2)])
        detail = self.request(f"/api/projects/{made['id']}")[1]
        self.assertEqual(detail["version"]["library"], {"run_id": COMPLETED, "documents": 1, "from_version": 1})
        self.assertEqual(detail["config"]["topic"], "A podcast")
        cards = {card["id"]: card for card in self.request("/api/projects")[1]["projects"]}
        self.assertEqual((cards["a-podcast"]["version"], cards[made["id"]]["version"]), (1, 2))
        # The Studio's own runtime settings, as for a new project.
        self.assertEqual(read_yaml(self.projects / made["id"] / "project.yaml")["runtime"],
                         self.server.studio.runtime().model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
