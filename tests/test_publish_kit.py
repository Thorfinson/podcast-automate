import contextlib
import io
import json
import unittest
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript, Segment
from podcast_automate.publish_kit import (DESCRIPTION_LIMIT, PROMPT_VERSION, EpisodeDescriptions, author_names,
                                          build_publish_kit, chapter_marks, chapter_problems, compose_description,
                                          description_defects, episode_sources, saved_kit, source_line, sources_markdown)
from podcast_automate.research_models import SourceDocument, SourceIndex, SourceSection
from podcast_automate.runner import manifest_path
from podcast_automate.scripting import run_script
from podcast_automate.storage import file_hash, read_yaml, write_json, write_yaml
from tests import script_fixtures as fixtures

SHORT = "Die Folge zeigt, wie ein Modell zwei Möglichkeiten vergleicht und warum eine niedrigere Bewertung hier die bessere Wahl ist."
LONG = ("Wie vergleicht ein Modell zwei Möglichkeiten? Die Folge geht von einem konkreten Vergleich aus und erklärt, "
        "welche Zahl das Modell jeder Möglichkeit zuordnet und was eine niedrige Zahl bedeutet.\n\n"
        "Dabei wird auch deutlich, wo der Vergleich an seine Grenze kommt: Die Bewertung sagt nichts darüber, "
        "wie das Modell gelernt hat. Diese Frage bleibt für eine spätere Folge offen, und auch die Grenzen des "
        "Beispiels werden genannt, damit kein falscher Eindruck entsteht.")


def document(source_id, title, *, url="", final_url="", authors=(), published="", source_type="unknown", citation=""):
    return SourceDocument(id=source_id, type="html", title=title, authors=list(authors), published_date=published,
                          imported_at="2026-10-06T00:00:00+00:00", url=url, final_url=final_url, reliability_note="",
                          uncertainties=[], raw_path=f"sources/raw/run_x/{source_id}.html", raw_hash="0" * 64,
                          text_hash="0" * 64, sections=[SourceSection(id="sec_0001", text="Text.")],
                          source_type=source_type, citation=citation)


def script_with(chapters, refs=()):
    return EpisodeScript(episode_id="ep_001", title="Titel", purpose="deep_dive",
        chapters=[Chapter(chapter_id=f"c_{n}", title=title) for n, title in enumerate(chapters)],
        segments=[Segment(segment_id=f"s_{n}", scene_id=f"c_{n}", chapter_id=f"c_{n}", speaker_id="host_a",
                          text="Text.", knowledge_refs=list(refs[n] if n < len(refs) else []))
                  for n in range(len(chapters))])


class DescriptionCheckTests(unittest.TestCase):
    def test_a_calm_german_answer_passes_and_each_defect_is_named(self):
        clean = EpisodeDescriptions(short=SHORT, long=LONG)
        self.assertEqual(description_defects(clean, language="de-DE", max_long=1500), [])
        cases = {"web addresses": LONG + " Mehr unter https://example.org/paper0 und dort.",
                 "internal ids": LONG + " Das belegt f_energy aus der Recherche.",
                 "Markdown": "## Überblick\n\n" + LONG,
                 "timestamps": LONG + " Der Vergleich beginnt bei 05:54 und ist dann vorbei.",
                 "exclamation": LONG + " Das ist wirklich die beste Folge!",
                 "write in de-DE": "How does a model compare two possibilities? This episode starts from a concrete "
                                   "comparison and explains which number the model assigns to each possibility and what "
                                   "a low number means for it. It also shows where the comparison reaches its limit: the "
                                   "score says nothing about how the model learned, and that question is left for later.",
                 "characters": "Zu kurz und ohne Inhalt für die Liste."}
        for expected, text in cases.items():
            with self.subTest(defect=expected):
                errors = description_defects(EpisodeDescriptions(short=SHORT, long=text), language="de-DE", max_long=1500)
                self.assertTrue(any(expected in error and error.startswith("long:") for error in errors), errors)

    def test_the_chapters_keep_spotifys_form_and_their_problems_are_named(self):
        script = script_with(["Anfang", "Mitte", "Ende"])
        recording = {"starts": [0.4, 354.36, 380.0]}
        rows = chapter_marks(script, recording)
        self.assertEqual([row["timestamp"] for row in rows], ["00:00", "05:54", "06:20"])
        self.assertEqual(chapter_problems(rows), [{"code": "chapter_too_short", "chapter_id": "c_1"}])
        long = chapter_marks(script, {"starts": [0.0, 1800.0, 3725.9]})
        self.assertEqual([row["timestamp"] for row in long], ["00:00:00", "00:30:00", "01:02:05"])
        untimed = chapter_marks(script, None)
        self.assertEqual(([row["timestamp"] for row in untimed], chapter_problems(untimed)),
                         ([None, None, None], [{"code": "no_recording"}]))
        self.assertEqual(chapter_problems(chapter_marks(script_with(["A", "B"]), {"starts": [0.0, 100.0]})),
                         [{"code": "too_few_chapters", "count": 2}])

    def test_the_description_fits_the_platform_limit_and_counts_the_sources_left_out(self):
        chapters = chapter_marks(script_with(["Anfang", "Ende"]), {"starts": [0.0, 120.0]})
        sources = [{"title": f"Quelle {n}", "authors": ["A. Autor"], "year": "2020",
                    "url": "https://example.org/" + "x" * 300 + str(n)} for n in range(20)]
        text, listed = compose_description(LONG, chapters, sources, "de-DE")
        self.assertLessEqual(len(text), DESCRIPTION_LIMIT)
        self.assertTrue(0 < listed < 20)
        self.assertTrue(text.endswith(f"Weitere Quellen: {20 - listed}"))
        self.assertIn("\n\nKapitel\n00:00 Anfang\n02:00 Ende\n\nQuellen\nA. Autor (2020): Quelle 0. https://", text)
        with self.assertRaises(AppError) as caught:
            compose_description("x" * DESCRIPTION_LIMIT, chapters, [], "de-DE")
        self.assertEqual(caught.exception.code, "description_too_long")


class SourceResolutionTests(unittest.TestCase):
    def test_sources_follow_first_use_merge_copies_of_one_work_and_never_name_an_idea_source(self):
        documents = {
            "src_a": document("src_a", "Attention is All you Need", url="https://papers.nips.cc/a.pdf",
                              final_url="https://proceedings.neurips.cc/a.pdf", published="2017",
                              authors=["Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit"]),
            "src_b": document("src_b", "Attention is all you need.", url="https://arxiv.org/abs/1706.03762",
                              final_url="https://arxiv.org/abs/1706.03762"),
            "src_c": document("src_c", "Meine Notizen", source_type="idea"),
            "src_d": document("src_d", "Ein Buch, 2019", url="inputs/works/buch.pdf", citation="Ein Buch, 2019",
                              published="2019", source_type="primary_work"),
        }
        refs = {"f_one": ["src_d#sec_0001"], "f_two": ["src_a#sec_0001", "src_c#sec_0001", "src_x#sec_0001"],
                "f_three": ["src_b#sec_0001"]}
        script = script_with(["A", "B", "C"], refs=[["f_one"], ["f_two", "f_missing"], ["f_three", "f_one"]])
        rows, omitted, unresolved = episode_sources(script, refs, documents, {})
        self.assertEqual([row["source_ids"] for row in rows], [["src_d"], ["src_a", "src_b"]])
        self.assertEqual((rows[0]["url"], rows[0]["year"]), ("", "2019"), "a provided work's local path stays local")
        self.assertEqual(rows[1]["finding_ids"], ["f_two", "f_three"])
        self.assertEqual(source_line(rows[1]),
                         "Ashish Vaswani et al. (2017): Attention is All you Need. https://proceedings.neurips.cc/a.pdf")
        self.assertEqual(omitted, [{"source_id": "src_c", "reason": "idea_source", "finding_ids": ["f_two"]}])
        self.assertEqual(unresolved, {"findings": ["f_missing"], "references": ["src_x#sec_0001"]})

    def test_author_lists_from_pdf_metadata_and_titles_that_end_in_a_question_read_cleanly(self):
        """As the Transformer research stored them on 2026-09-30."""
        self.assertEqual(author_names(["Mor Geva ; Roei Schuster ; Jonathan Berant"]),
                         ["Mor Geva", "Roei Schuster", "Jonathan Berant"])
        self.assertEqual(author_names(["Ruibin Xiong†*", "Yunchang Yang*"]), ["Ruibin Xiong", "Yunchang Yang"])
        self.assertEqual(author_names(["Kaplan, Jared"]), ["Kaplan, Jared"])
        row = {"title": "Are Sixteen Heads Really Better than One?", "authors": ["Paul Michel", "Omer Levy"],
               "year": "2019", "url": "https://arxiv.org/pdf/1905.10650"}
        self.assertEqual(source_line(row),
                         "Paul Michel, Omer Levy (2019): Are Sixteen Heads Really Better than One? https://arxiv.org/pdf/1905.10650")
        self.assertIn("1. Paul Michel, Omer Levy (2019): *Are Sixteen Heads Really Better than One*? <https://",
                      sources_markdown("Titel", [row], "de-DE"))
        self.assertEqual(source_line({**row, "authors": [], "year": "", "url": ""}), row["title"])


class PublishKitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.script_project(self)
        self.root = self.fixture.root.resolve()
        with patch("podcast_automate.scripting.CodexAdapter.structured", side_effect=self.fixture.model):
            self.assertEqual(run_script(self.root).status, "completed")
        self.work = manifest_path(self.root, json.loads((self.root / "episodes/ep_001/latest.json").read_text(
            encoding="utf-8"))["run_id"]).parent

    def pool(self, answers, prompts):
        class Pool:
            def __init__(self, runtime, text_generation, api_key=None):
                pass

            def structured(self, prompt, schema, directory, prompt_version):
                prompts.append((prompt_version, prompt))
                return schema(**answers.pop(0)), {}
        return Pool

    def record(self, run_id="run_20261006_120000_000000_rec00001", start=0.0):
        """A finished recording of the current script, as episode_audio's assembly and publish leave it."""
        folder = self.root / "exports/ep_001" / run_id
        folder.mkdir(parents=True)
        (folder / "audio.mp3").write_bytes(b"mp3")
        write_json(folder / "chapters.json", {"version": "1.0", "chapters": [
            {"chapter_id": "scene_example", "title": "A concrete comparison", "start_seconds": start, "end_seconds": 95.0}]})
        write_json(self.root / "episodes/ep_001/audio_latest.json", {
            "run_id": run_id, "script_sha256": file_hash(self.root / "episodes/ep_001/script.yaml"),
            "parts": [{"part": 1, "audio": f"exports/ep_001/{run_id}/audio.mp3", "duration_seconds": 95.0}]})
        return folder

    def test_one_call_writes_the_kit_and_a_rebuild_or_a_recording_of_the_same_script_calls_no_model(self):
        prompts = []
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([{"short": SHORT, "long": LONG}], prompts)):
            kit = build_publish_kit(self.root, "ep_001")
        self.assertEqual([version for version, _ in prompts], [PROMPT_VERSION])
        payload = json.loads(prompts[0][1].splitlines()[-1])
        self.assertEqual((payload["language"], payload["episode"]["central_question"], payload["transcript"][0]["chapter"]),
                         ("de-DE", "How are possibilities compared?", "A concrete comparison"))
        folder = self.root / "episodes/ep_001/publish"
        self.assertEqual((kit["folder"], kit["recording"], kit["descriptions_reused"]), ("episodes/ep_001/publish", None, False))
        self.assertEqual((folder / "description_short.txt").read_text(encoding="utf-8"), SHORT + "\n")
        description = (folder / "description.txt").read_text(encoding="utf-8")
        self.assertTrue(description.startswith(LONG + "\n\nKapitel\nA concrete comparison\n\nQuellen\n"), description)
        self.assertEqual([(row["title"], row["authors"], row["url"], row["finding_ids"]) for row in kit["sources"]],
                         [("Fixture paper", ["Test Author"], "https://example.org/paper0", ["f_energy"])])
        self.assertIn("Test Author: Fixture paper. https://example.org/paper0", description)
        self.assertIn("1. Test Author: *Fixture paper*. <https://example.org/paper0>",
                      (folder / "sources.md").read_text(encoding="utf-8"))
        self.assertEqual(kit["script_sha256"], file_hash(self.root / "episodes/ep_001/script.yaml"))
        self.assertEqual(saved_kit(self.root, "ep_001")["files"], kit["files"])
        first = (folder / "kit.json").read_bytes()

        refused = patch("podcast_automate.publish_kit.AdapterPool", side_effect=AssertionError("no model call"))
        with refused:
            again = build_publish_kit(self.root, "ep_001")
            self.assertTrue(again["descriptions_reused"])
            self.assertEqual((folder / "kit.json").read_bytes(), first, "an unchanged kit is rewritten byte for byte")
            export = self.record()
            recorded = build_publish_kit(self.root, "ep_001")
        self.assertEqual(recorded["folder"], f"{export.relative_to(self.root).as_posix()}/publish")
        self.assertEqual(recorded["recording"]["chapters_sha256"], file_hash(export / "chapters.json"))
        self.assertEqual(recorded["chapter_problems"], [{"code": "too_few_chapters", "count": 1}])
        self.assertIn("\n\nKapitel\n00:00 A concrete comparison\n\nQuellen\n",
                      (export / "publish/description.txt").read_text(encoding="utf-8"))
        self.assertEqual(saved_kit(self.root, "ep_001")["folder"], recorded["folder"])
        self.record("run_20261006_130000_000000_rec00002")
        self.assertIsNone(saved_kit(self.root, "ep_001"), "a newer recording needs its own kit")

    def test_the_studio_builds_shows_and_zips_the_kit_of_the_current_recording(self):
        """The Studio's side (2026-10-06): the worker job builds the kits, the recording page shows the two texts to
        copy, and the podcast ZIP carries the kit next to the recording it belongs to."""
        import zipfile
        from podcast_automate.downloads import podcast_zip
        from podcast_automate.studio import publish_view
        from podcast_automate.studio_worker import perform
        self.record()
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([{"short": SHORT, "long": LONG}], [])):
            result = perform(self.root, {"action": "publish_kit", "text": {}, "episodes": ["ep_001"]})
        self.assertEqual(result["publish_kit"]["ep_001"]["reused"], False)
        view = publish_view(self.root, self.root / "episodes/ep_001")
        self.assertEqual((view["short"], view["recorded"]), (SHORT, True))
        self.assertTrue(view["description"].startswith(LONG))
        with podcast_zip(self.root) as (archive, _):
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                self.assertEqual(bundle.read("Folge 01 - Begleitmaterial/description_short.txt").decode("utf-8"),
                                 SHORT + "\n")
        self.assertEqual(sorted(name for name in names if "Begleitmaterial" in name),
                         ["Folge 01 - Begleitmaterial/description.txt", "Folge 01 - Begleitmaterial/description_short.txt",
                          "Folge 01 - Begleitmaterial/sources.md"])
        # A newer recording has no kit yet: the ZIP and the page leave it out instead of showing an old one.
        self.record("run_20261006_130000_000000_rec00002")
        self.assertIsNone(publish_view(self.root, self.root / "episodes/ep_001"))
        with podcast_zip(self.root) as (archive, _):
            with zipfile.ZipFile(archive) as bundle:
                self.assertFalse(any("Begleitmaterial" in name for name in bundle.namelist()))

    def test_a_rejected_answer_is_corrected_and_fresh_asks_again(self):
        prompts = []
        answers = [{"short": SHORT, "long": LONG + " Alle Quellen unter https://example.org/paper0 nachlesen."},
                   {"short": SHORT, "long": LONG}, {"short": SHORT, "long": LONG}]
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool(answers, prompts)):
            kit = build_publish_kit(self.root, "ep_001")
            self.assertEqual(kit["descriptions"]["long"], LONG)
            self.assertEqual(len(prompts), 2)
            self.assertIn("no web addresses", prompts[1][1])
            build_publish_kit(self.root, "ep_001", fresh=True)
        self.assertEqual(len(prompts), 3)
        calls = sorted((self.root / "studio/publish_kit/ep_001").glob("*/budget.json"))
        self.assertEqual([json.loads(path.read_text(encoding="utf-8"))["model_calls"] for path in calls], [2, 1])

    def test_supplement_evidence_counts_and_an_edited_script_is_refused(self):
        folder = self.work / "teaching/ep_001/supplement"
        book = document("src_book", "Ein Buch, 2019", url="inputs/works/b.pdf", citation="Ein Buch, 2019",
                        source_type="primary_work")
        notes = document("src_notes", "Meine Notizen", source_type="idea")
        write_json(folder / "source_index.json", SourceIndex(sources=[book, notes], failures=[]).model_dump())
        write_json(folder / "evidence.json", {"value": {"explanations": [{
            "questions": ["Wie?"], "finding_ids": ["f_energy"], "explanation": "Erklärung.",
            "evidence": [{"reference": "src_book#sec_0001", "excerpt": "Text."},
                         {"reference": "src_notes#sec_0001", "excerpt": "Text."}]}], "remaining_gaps": []}})
        write_json(folder / "receipt.json", {"binding": "test", "outputs": {}})
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([{"short": SHORT, "long": LONG}], [])):
            kit = build_publish_kit(self.root, "ep_001")
        self.assertEqual([(row["title"], row["url"]) for row in kit["sources"]],
                         [("Fixture paper", "https://example.org/paper0"), ("Ein Buch, 2019", "")])
        self.assertEqual(kit["omitted_sources"], [{"source_id": "src_notes", "reason": "idea_source", "finding_ids": ["f_energy"]}])
        self.assertNotIn("Notizen", (self.root / "episodes/ep_001/publish/description.txt").read_text(encoding="utf-8"))

        script = read_yaml(self.root / "episodes/ep_001/script.yaml")
        script["segments"][0]["text"] = "Was vergleicht dieses Modell eigentlich?"
        write_yaml(self.root / "episodes/ep_001/script.yaml", script)
        with self.assertRaises(AppError) as caught:
            build_publish_kit(self.root, "ep_001")
        self.assertEqual(caught.exception.code, "script_edited")
        self.assertIsNone(saved_kit(self.root, "ep_001"))

    def test_the_cli_writes_the_kit_of_one_episode(self):
        output = io.StringIO()
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([{"short": SHORT, "long": LONG}], [])), \
                contextlib.redirect_stdout(output):
            code = main(["publish-kit", str(self.root), "--episode", "ep_001", "--json"])
        data = json.loads(output.getvalue())
        self.assertEqual((code, data["status"], data["kit"]["folder"]), (0, "completed", "episodes/ep_001/publish"))
        self.assertIn("noch nicht vertont", data["message"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["publish-kit", str(self.root), "--episode", "ep_999", "--json"])
        self.assertEqual((code, json.loads(output.getvalue())["code"]), (1, "unknown_episode"))


if __name__ == "__main__":
    unittest.main()
