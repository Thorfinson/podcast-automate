import io
import json
import unittest
import zipfile
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock
from urllib.parse import unquote

from podcast_automate.downloads import episode_filename, podcast_download, podcast_zip
from podcast_automate.errors import AppError
from podcast_automate.storage import project_hash, project_lock, write_json, write_yaml
from tests.script_fixtures import example_script
from tests import test_studio


class DownloadTests(unittest.TestCase):
    setUp = test_studio.StudioHttpTests.setUp
    request = test_studio.StudioHttpTests.request

    def episode(self, number, parts=1):
        episode = f"ep_{number:03}"
        folder = self.root / "episodes" / episode
        script = example_script().model_copy(update={"episode_id": episode, "title": f"Übersetzung: Erklärung {number}?"})
        write_yaml(folder / "script.yaml", script.model_dump())
        (folder / "script.md").write_text("Review", encoding="utf-8")
        rows = []
        for part in range(1, parts + 1):
            relative = f"exports/{episode}/run_test/part_{part:02}/audio.mp3"
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"episode {number} part {part}".encode())
            rows.append({"audio": relative, "part": part})
        if parts:
            write_json(folder / "audio_latest.json", {"parts": rows})
        return rows

    def test_zip_includes_all_current_parts_in_order_and_no_private_or_old_files(self):
        rows = self.episode(1, 2) + self.episode(2)
        old = self.root / "exports/ep_001/old.mp3"
        old.write_bytes(b"old recording")
        (self.root / "cache/private.txt").write_text("private")
        status, body, headers = self.request("/download/example/podcast.zip")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        self.assertIn("Alle%20Folgen.zip", headers["Content-Disposition"])
        self.assertEqual(int(headers["Content-Length"]), len(body))
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 3)
            self.assertIn("Folge 01", names[0])
            self.assertIn("Teil 01 von 02", names[0])
            self.assertIn("Teil 02 von 02", names[1])
            self.assertIn("Folge 02", names[2])
            for name, row in zip(names, rows, strict=True):
                self.assertEqual(Path(name).parent, Path("."))
                self.assertNotIn(self.config.topic, name)
                self.assertIn("Übersetzung", name)
                self.assertNotIn(":", name)
                self.assertEqual(archive.read(name), (self.root / row["audio"]).read_bytes())
        self.assertTrue(old.is_file())

    def test_individual_download_has_readable_unicode_filename_and_preserves_seeking(self):
        relative = self.episode(2)[0]["audio"]
        status, body, headers = self.request("/download/example/file/" + relative, headers={"Range": "bytes=0-6"})
        self.assertEqual((status, body), (206, b"episode"))
        self.assertIn('filename="A test project - Folge 02 - Ubersetzung', headers["Content-Disposition"])
        self.assertIn("Übersetzung Erklärung 2.mp3", unquote(headers["Content-Disposition"]))
        self.assertEqual(headers["Content-Range"], "bytes 0-6/16")
        # Playback remains inline and uses the original immutable export URL.
        status, _, headers = self.request("/media/example/" + relative)
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Disposition", headers)

    def test_partial_series_is_labelled_and_expected_unwritten_episodes_are_counted(self):
        self.episode(1)
        write_json(self.root / "studio/outline.json", {"run_id": "run_plan"})
        write_json(self.root / "runs/run_plan/series_plan.json", {"episodes": [
            {"episode_id": "ep_001"}, {"episode_id": "ep_002"}, {"episode_id": "ep_003"}]})
        download = podcast_download(self.root)
        self.assertEqual(download.finished_episodes, 1)
        self.assertEqual(download.episode_count, 3)
        self.assertIn("1 von 3 Folgen", download.filename)

    def test_missing_part_fails_the_bundle_instead_of_silently_omitting_it(self):
        rows = self.episode(1, 2)
        other = self.episode(2)[0]["audio"]
        (self.root / rows[1]["audio"]).unlink()
        status, body, _ = self.request("/download/example/podcast.zip")
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["code"], "missing_audio")
        self.assertEqual(self.request("/download/example/file/" + other)[0], 200)

    def test_no_audio_and_unpublished_or_escaped_paths_are_rejected(self):
        self.assertEqual(self.request("/download/example/podcast.zip")[0], 400)
        self.episode(1)
        for path in ("../../project.yaml", "exports/ep_001/unpublished.mp3"):
            self.assertNotEqual(self.request("/download/example/file/" + path)[0], 200)
        write_json(self.root / "episodes/ep_001/audio_latest.json", {"parts": [{"audio": "../../private.mp3"}]})
        self.assertNotEqual(self.request("/download/example/podcast.zip")[0], 200)
        self.assertEqual(self.request("/download/example/podcast.zip", headers={"Origin": "https://elsewhere.example"})[0], 403)

    def test_downloads_stay_available_while_this_studio_runs_a_text_job(self):
        self.episode(1)
        write_json(self.root / "studio/job.json", {"id": "r", "action": "research", "status": "running", "run": None})
        worker = Mock()
        worker.poll.return_value = None
        self.app.process, self.app.process_root = worker, self.root
        with project_lock(self.root):
            # Stands in for the research worker's exclusive lock; research never writes exports.
            self.assertEqual(self.request("/download/example/podcast.zip")[0], 200)
            self.assertEqual(self.request("/download/example/file/exports/ep_001/run_test/part_01/audio.mp3")[0], 200)
            write_json(self.root / "studio/job.json", {"id": "q", "action": "audio", "status": "running", "run": None})
            status, body, _ = self.request("/download/example/podcast.zip")
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body)["code"], "project_busy")

    def test_zip_cleanup_and_lock_survive_an_aborted_download(self):
        self.episode(1)
        with self.assertRaises(ConnectionResetError):
            with podcast_zip(self.root) as (path, _):
                self.assertTrue(path.is_file())
                with self.assertRaises(AppError):
                    self.app.delete("example", {"confirm_id": "example", "config_hash": project_hash(self.config)})
                raise ConnectionResetError("client left")
        self.assertFalse(path.exists())
        self.assertTrue((self.root / "project.yaml").exists())

    def test_names_are_bounded_and_safe_on_windows_mac_and_linux(self):
        name = episode_filename('.. / Bad:"<>|*?\r\n' + "Ä" * 250, "ep_012", "話" * 300, 1, 2, 3)
        self.assertLessEqual(len(name.encode("utf-8")), 128)
        self.assertFalse(any(c in name for c in '<>:"/\\|?*\r\n'))
        self.assertNotIn("…", name)
        self.assertIn("Folge 12", name)
        self.assertTrue(name.endswith("Teil 02 von 03.mp3"))

    def test_extraction_budget_includes_zip_folder_and_member_names(self):
        self.config.topic = "Die Entwicklung der Transformer Architektur von Attention is all you need zu Deep Seek 4.1 Flash" * 3
        write_yaml(self.root / "project.yaml", self.config.model_dump(mode="json"))
        self.episode(1, 2)
        script = example_script().model_copy(update={"title": "Über die ursprüngliche Architektur und ihre Weiterentwicklung " * 10})
        write_yaml(self.root / "episodes/ep_001/script.yaml", script.model_dump())
        # Explorer's default destination adds the ZIP's entire base name as
        # another folder, including when extracting next to the project export.
        bases = [PureWindowsPath("C:/Users/Ein langer Benutzername/Downloads"),
                 PureWindowsPath("C:/Projekte/podcast-automate/projects/die-entwicklung-der-transformer-architek-13325a/downloads")]
        with podcast_zip(self.root) as (path, filename), zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                self.assertLessEqual(len(member.encode("utf-8")), 92)
                for base in bases:
                    extracted = str(base / Path(filename).stem / member)
                    self.assertLess(len(extracted.encode("utf-16-le")) // 2, 260, extracted)
            target = self.workspace / "extracted" / Path(filename).stem
            archive.extractall(target)
            self.assertEqual(sorted(p.read_bytes() for p in target.iterdir()), [b"episode 1 part 1", b"episode 1 part 2"])

    def test_omitting_repeated_topic_leaves_room_for_meaningful_episode_titles(self):
        title = "Wie der ursprüngliche Transformer aus einem Satz eine Übersetzung macht"
        self.assertEqual(episode_filename("Transformer", "ep_001", title, 1, 1, 1, in_archive=True),
                         f"Folge 01 - {title}.mp3")
