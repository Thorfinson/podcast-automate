import contextlib
import io
import json
import unittest
from unittest.mock import patch

from podcast_automate.cli import main
from podcast_automate.errors import AppError
from podcast_automate.models import Chapter, EpisodeScript, Segment
from podcast_automate.publish_kit import (DESCRIPTION_LIMIT, PODCAST_PROMPT_VERSION, PROMPT_VERSION,
                                          EpisodeDescriptions, author_names, build_podcast_kit, build_publish_kit,
                                          chapter_marks, chapter_problems, compose_description, description_defects,
                                          episode_sources, podcast_sources, podcast_sources_markdown,
                                          podcast_transcript, saved_kit, saved_podcast_kit, source_line,
                                          sources_markdown)
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
# The end of every description since 2026-10-07 (D-154): PRODUCT.md's transparency note and the AI notice.
TRANSPARENCY_DE = ("Dieser Output ist eine quellengebundene Synthese. Er ersetzt keine fachliche, rechtliche, medizinische "
                   "oder wissenschaftliche Begutachtung. Unsichere oder widersprüchliche Quellenlagen werden markiert.\n"
                   "KI-Hinweis: Das Skript dieser Folge wurde von einem Sprachmodell geschrieben und wird von "
                   "synthetischen Stimmen gesprochen.")
NOTE_DE = TRANSPARENCY_DE.split("\n")[0]
# The podcast's own kit (2026-10-07, D-165): its texts and the AI notice that names the podcast's scripts.
PODCAST_SHORT = ("Der Podcast erklärt, wie ein Modell Möglichkeiten vergleicht und warum eine niedrigere Bewertung "
                 "dabei die bessere Wahl ist.")
PODCAST_LONG = ("Wie vergleicht ein Modell Möglichkeiten? Der Podcast beginnt mit einem konkreten Vergleich und erklärt, "
                "welche Zahl das Modell jeder Möglichkeit zuordnet und was eine niedrige Zahl bedeutet.\n\n"
                "Die Folgen zeigen auch, wo dieser Vergleich an seine Grenze kommt: Die Bewertung sagt nichts darüber, "
                "wie das Modell gelernt hat. Diese Frage bleibt offen, und auch die Grenzen des Beispiels werden "
                "genannt, damit kein falscher Eindruck entsteht.")
AI_PODCAST_DE = ("KI-Hinweis: Die Skripte dieses Podcasts wurden von einem Sprachmodell geschrieben und werden von "
                 "synthetischen Stimmen gesprochen.")
TRANSCRIPT_NOTE_DE = ("Der freigegebene Text aller Folgen in Skriptreihenfolge. Die Zeitmarken der Kapitel stammen aus "
                      "der gemessenen Montage der Aufnahme.")


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
        # D-154 (2026-10-07): the transparency note ends the description and counts against the limit; the count of
        # the sources left out comes right before it, where the text used to end.
        note = TRANSPARENCY_DE
        self.assertTrue(text.endswith(f"Weitere Quellen: {20 - listed}\n\n{note}"), text[-400:])
        self.assertIn("\n\nKapitel\n00:00 Anfang\n02:00 Ende\n\nQuellen\nA. Autor (2020): Quelle 0. https://", text)
        with self.assertRaises(AppError) as caught:
            compose_description("x" * DESCRIPTION_LIMIT, chapters, [], "de-DE")
        self.assertEqual(caught.exception.code, "description_too_long")
        # A text and chapters that fit only without the note are refused too: the note is never cut.
        head = len("\n\nKapitel\n00:00 Anfang\n02:00 Ende")
        fits_without_note = "x" * (DESCRIPTION_LIMIT - head)
        with self.assertRaises(AppError) as caught:
            compose_description(fits_without_note, chapters, [], "de-DE")
        self.assertEqual(caught.exception.code, "description_too_long")
        fitting = "x" * (DESCRIPTION_LIMIT - head - len("\n\n" + note))
        self.assertEqual(compose_description(fitting, chapters, [], "de-DE"),
                         (fitting + "\n\nKapitel\n00:00 Anfang\n02:00 Ende\n\n" + note, 0))

    def test_the_long_limit_leaves_room_for_the_note_and_usual_episodes_keep_their_prompt(self):
        """D-154: the note counts against the 4,000 characters, so the longest description an episode with dozens of
        chapters may ask for shrinks by its length; a usual episode still asks for LONG_CHARS[1], so its saved
        descriptions are reused without a model call."""
        from podcast_automate.publish_kit import LONG_CHARS, SOURCE_RESERVE, TIMESTAMP_WIDTH, long_limit
        self.assertEqual(long_limit(script_with(["Kapitel"] * 12), "de-DE"), LONG_CHARS[1])
        # Forty long chapter titles: without the note the limit would still be LONG_CHARS[1]; with it, less.
        many = script_with([f"Ein recht langer Kapiteltitel Nummer {n:02d}" for n in range(40)])
        chapters = len("\n\nKapitel") + sum(TIMESTAMP_WIDTH + len(c.title) + 1 for c in many.chapters)
        self.assertGreaterEqual(DESCRIPTION_LIMIT - chapters - SOURCE_RESERVE, LONG_CHARS[1])
        self.assertEqual(long_limit(many, "de-DE"),
                         DESCRIPTION_LIMIT - chapters - SOURCE_RESERVE - len("\n\n" + TRANSPARENCY_DE))
        self.assertTrue(LONG_CHARS[0] < long_limit(many, "de-DE") < LONG_CHARS[1])


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


class KitCase(unittest.TestCase):
    """A published fixture episode, a fake text model and a recording; deliberately contains no test methods."""

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

            def billed(self, search=False):
                return False

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


class PublishKitTests(KitCase):
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

    def test_a_kit_of_the_layout_before_the_note_is_rebuilt_without_a_model_call(self):
        """D-154 changed description.txt, so kit.json's version moved to publish_kit.v2: a v1 kit is no longer shown or
        zipped, and rebuilding it reuses the saved descriptions (their own version stayed publish_kit.v1)."""
        from podcast_automate.publish_kit import DESCRIPTIONS_VERSION, KIT_VERSION
        self.assertEqual((KIT_VERSION, DESCRIPTIONS_VERSION), ("publish_kit.v2", "publish_kit.v1"))
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([{"short": SHORT, "long": LONG}], [])):
            build_publish_kit(self.root, "ep_001")
        folder = self.root / "episodes/ep_001/publish"
        self.assertTrue((folder / "description.txt").read_text(encoding="utf-8").endswith(TRANSPARENCY_DE + "\n"))
        old = json.loads((folder / "kit.json").read_text(encoding="utf-8"))
        write_json(folder / "kit.json", {**old, "version": "publish_kit.v1"})
        self.assertIsNone(saved_kit(self.root, "ep_001"))
        with patch("podcast_automate.publish_kit.AdapterPool", side_effect=AssertionError("no model call")):
            rebuilt = build_publish_kit(self.root, "ep_001")
        self.assertTrue(rebuilt["descriptions_reused"])
        self.assertEqual(saved_kit(self.root, "ep_001")["version"], "publish_kit.v2")

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
        # The CLI's own messages are English since D-156; the fact is the same: no recording, no time marks.
        self.assertIn("not recorded yet", data["message"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["publish-kit", str(self.root), "--episode", "ep_999", "--json"])
        self.assertEqual((code, json.loads(output.getvalue())["code"]), (1, "unknown_episode"))


class PodcastTextTests(unittest.TestCase):
    """The whole podcast's source list and transcript (2026-10-07, D-165), built from the data alone."""

    def test_one_work_in_several_episodes_is_one_source_that_names_them_all(self):
        def row(source_id, title, url=""):
            return {"source_ids": [source_id], "title": title, "authors": ["A. Autor"], "year": "2020", "url": url,
                    "source_type": "paper", "finding_ids": ["f_x"]}
        first = [row("src_a", "Attention is All you Need", "https://arxiv.org/abs/1706.03762"), row("src_b", "Ein Buch")]
        # The same work under another title through the research's work id, and under the same title with punctuation.
        second = [row("src_c", "Attention Is All You Need (Preprint)"), row("src_d", "Ein Buch.", "https://example.org/buch"),
                  row("src_e", "Neu")]
        merged = podcast_sources([("ep_001", first, {"src_a": "work_attention"}),
                                  ("ep_002", second, {"src_c": "work_attention"})])
        self.assertEqual([(r["title"], r["source_ids"], r["episodes"], r["url"]) for r in merged],
                         [("Attention is All you Need", ["src_a", "src_c"], ["ep_001", "ep_002"],
                           "https://arxiv.org/abs/1706.03762"),
                          ("Ein Buch", ["src_b", "src_d"], ["ep_001", "ep_002"], "https://example.org/buch"),
                          ("Neu", ["src_e"], ["ep_002"], "")])
        self.assertEqual(podcast_sources_markdown("Thema", merged, {"ep_001": 1, "ep_002": 2}, "de-DE").splitlines(), [
            "# Quellen: Thema", "",
            "Die Quellen der Rechercheergebnisse, auf die sich die Folgen stützen, in der Reihenfolge ihrer ersten "
            "Verwendung; in Klammern die Folgen, die sie nutzen.", "",
            "1. A. Autor (2020): *Attention is All you Need*. <https://arxiv.org/abs/1706.03762> (in Folge 01, Folge 02)",
            "2. A. Autor (2020): *Ein Buch*. <https://example.org/buch> (in Folge 01, Folge 02)",
            "3. A. Autor (2020): *Neu*. (in Folge 02)"])
        self.assertEqual(podcast_sources_markdown("Thema", [], {}, "de-DE"),
                         "# Quellen: Thema\n\nKeine Folge stützt sich auf eine Quelle der Recherche.\n")

    def test_the_transcript_reads_every_episode_with_its_chapter_marks_and_names_one_without_a_recording(self):
        one = script_with(["Anfang", "Ende"])
        two = EpisodeScript(episode_id="ep_002", title="Zweite", purpose="deep_dive",
                            chapters=[Chapter(chapter_id="c_0", title="Mitte")],
                            segments=[Segment(segment_id="s_0", scene_id="c_0", chapter_id="c_0", speaker_id="host_b",
                                              text="Zwei.", knowledge_refs=[])])
        episodes = [{"script": one, "number": 1, "recording": {"run_id": "run_x"},
                     "chapters": chapter_marks(one, {"starts": [0.4, 65.0]})},
                    {"script": two, "number": 2, "recording": None, "chapters": chapter_marks(two, None)}]
        self.assertEqual(podcast_transcript("Thema", episodes, {"host_a": "Anna", "host_b": "Ben"}, "de-DE"),
                         f"# Thema\n\n{TRANSCRIPT_NOTE_DE}\n\n## Folge 01: Titel\n\n### 00:00 Anfang\n\n**Anna:** Text.\n\n"
                         "### 01:05 Ende\n\n**Anna:** Text.\n\n## Folge 02: Zweite\n\n"
                         "Noch nicht vertont: Die Kapitel stehen ohne Zeitmarken.\n\n### Mitte\n\n**Ben:** Zwei.\n\n"
                         f"## Transparenzhinweis\n\n{NOTE_DE}\n\n{AI_PODCAST_DE}\n")


class PodcastKitTests(KitCase):
    """The whole podcast's kit on the published fixture episode (2026-10-07, D-165)."""
    ANSWER = {"short": PODCAST_SHORT, "long": PODCAST_LONG}

    def transcript(self, *, timed):
        """publish/transcript.md as the fixture's one published episode gives it."""
        script = EpisodeScript.model_validate(read_yaml(self.root / "episodes/ep_001/script.yaml"))
        self.assertEqual({segment.chapter_id for segment in script.segments}, {"scene_example"})
        turns = "".join(f"**{'Host A' if segment.speaker_id == 'host_a' else 'Host B'}:** {segment.text}\n\n"
                        for segment in script.segments)
        untimed = "" if timed else "Noch nicht vertont: Die Kapitel stehen ohne Zeitmarken.\n\n"
        return (f"# Test topic\n\n{TRANSCRIPT_NOTE_DE}\n\n## Folge 01: {script.title}\n\n{untimed}"
                f"### {'00:00 ' if timed else ''}A concrete comparison\n\n{turns}"
                f"## Transparenzhinweis\n\n{NOTE_DE}\n\n{AI_PODCAST_DE}\n")

    def test_one_call_writes_the_kit_and_a_rebuild_or_a_recording_calls_no_model(self):
        prompts = []
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([dict(self.ANSWER)], prompts)):
            kit = build_podcast_kit(self.root)
        self.assertEqual([version for version, _ in prompts], [PODCAST_PROMPT_VERSION])
        payload = json.loads(prompts[0][1].splitlines()[-1])
        self.assertEqual((payload["series_topic"], payload["central_question"]), ("Test topic", "Test topic"))
        self.assertEqual(payload["episodes"], [{
            "number": 1, "title": "A model compares possibilities", "central_question": "How are possibilities compared?",
            "series_role": "Shows how a model compares possibilities, the base of the answer.",
            "chapters": ["A concrete comparison"]}])
        self.assertNotIn("transcript", payload, "the series is described from its plans, not from hours of text")
        folder = self.root / "publish"
        self.assertEqual((kit["folder"], kit["descriptions_reused"], kit["transcript"]),
                         ("publish", False, {"episodes": 1, "recorded": 0}))
        self.assertEqual((folder / "description_short.txt").read_text(encoding="utf-8"), PODCAST_SHORT + "\n")
        self.assertEqual((folder / "description.txt").read_text(encoding="utf-8"),
                         f"{PODCAST_LONG}\n\n{NOTE_DE}\n{AI_PODCAST_DE}\n")
        self.assertEqual((folder / "transcript.md").read_text(encoding="utf-8"), self.transcript(timed=False))
        self.assertEqual((folder / "sources.md").read_text(encoding="utf-8").splitlines()[-1],
                         "1. Test Author: *Fixture paper*. <https://example.org/paper0> (in Folge 01)")
        self.assertEqual([(row["title"], row["episodes"]) for row in kit["sources"]], [("Fixture paper", ["ep_001"])])
        self.assertEqual(saved_podcast_kit(self.root)["files"], kit["files"])
        first = (folder / "kit.json").read_bytes()

        with patch("podcast_automate.publish_kit.AdapterPool", side_effect=AssertionError("no model call")):
            again = build_podcast_kit(self.root)
            self.assertTrue(again["descriptions_reused"])
            self.assertEqual((folder / "kit.json").read_bytes(), first, "an unchanged kit is rewritten byte for byte")
            self.record()
            self.assertIsNone(saved_podcast_kit(self.root), "a new recording changes the transcript's time marks")
            recorded = build_podcast_kit(self.root)
        self.assertEqual((recorded["transcript"], recorded["episodes"][0]["recording"]["run_id"]),
                         ({"episodes": 1, "recorded": 1}, "run_20261006_120000_000000_rec00001"))
        self.assertEqual((folder / "transcript.md").read_text(encoding="utf-8"), self.transcript(timed=True))
        self.assertIsNotNone(saved_podcast_kit(self.root))

        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([dict(self.ANSWER)], prompts)):
            build_podcast_kit(self.root, fresh=True)
        self.assertEqual(len(prompts), 2)
        calls = sorted((self.root / "studio/publish_kit/podcast").glob("*/budget.json"))
        self.assertEqual([json.loads(path.read_text(encoding="utf-8"))["model_calls"] for path in calls], [1, 1])

    def test_the_studio_builds_shows_and_zips_the_kit_of_the_current_recordings(self):
        import zipfile
        from podcast_automate.downloads import podcast_zip
        from podcast_automate.studio import podcast_kit_view
        from podcast_automate.studio_worker import perform
        self.record()
        self.assertIsNone(podcast_kit_view(self.root))
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([dict(self.ANSWER)], [])):
            result = perform(self.root, {"action": "publish_kit", "text": {}, "podcast": True})
        self.assertEqual(result["podcast_kit"], {
            "folder": "publish", "episodes": 1, "recorded": 1, "sources": 1, "reused": False,
            "characters": len(f"{PODCAST_LONG}\n\n{NOTE_DE}\n{AI_PODCAST_DE}")})
        view = podcast_kit_view(self.root)
        self.assertEqual((view["short"], view["episodes"], view["recorded"], view["sources_total"]),
                         (PODCAST_SHORT, 1, 1, 1))
        self.assertTrue(view["description"].startswith(PODCAST_LONG))
        with podcast_zip(self.root) as (archive, _):
            with zipfile.ZipFile(archive) as bundle:
                names = bundle.namelist()
                transcript = bundle.read("Begleitmaterial Podcast/transcript.md").decode("utf-8")
        self.assertEqual(sorted(name for name in names if name.startswith("Begleitmaterial Podcast/")),
                         ["Begleitmaterial Podcast/description.txt", "Begleitmaterial Podcast/description_short.txt",
                          "Begleitmaterial Podcast/sources.md", "Begleitmaterial Podcast/transcript.md"])
        self.assertEqual(transcript, self.transcript(timed=True))
        # A newer recording is not covered yet: the page offers a rebuild and the ZIP leaves the old kit out.
        self.record("run_20261006_130000_000000_rec00002")
        self.assertEqual(podcast_kit_view(self.root), {"outdated": True})
        with podcast_zip(self.root) as (archive, _):
            with zipfile.ZipFile(archive) as bundle:
                self.assertFalse(any(name.startswith("Begleitmaterial Podcast/") for name in bundle.namelist()))

    def test_the_studio_serves_the_current_kits_transcript_and_sources_on_their_own(self):
        """The user's report of 2026-10-07: the page offered the two descriptions only, while the transcript lay in
        publish/ and in the ZIP with every MP3. The route serves the files the ZIP carries, while the ZIP would."""
        import http.client
        import shutil
        import tempfile
        import threading
        from pathlib import Path
        from urllib.parse import unquote
        from podcast_automate.studio import make_server
        self.record()
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([dict(self.ANSWER)], [])):
            build_podcast_kit(self.root)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        workspace = Path(temp.name).resolve()
        shutil.copytree(self.root, workspace / "projects/example")
        self.root = workspace / "projects/example"
        server = make_server(workspace, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def get(name):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", f"/download/example/kit/{name}")
            response = connection.getresponse()
            result = response.status, response.read(), dict(response.getheaders())
            connection.close()
            return result

        status, body, headers = get("transcript.md")
        self.assertEqual((status, body), (200, (self.root / "publish/transcript.md").read_bytes()))
        self.assertEqual(body.decode("utf-8"), self.transcript(timed=True))
        self.assertEqual(headers["Content-Type"], "text/markdown; charset=utf-8")
        self.assertTrue(unquote(headers["Content-Disposition"]).endswith("Test topic - transcript.md"))
        status, body, headers = get("sources.md")
        self.assertEqual((status, body), (200, (self.root / "publish/sources.md").read_bytes()))
        self.assertEqual(get("description.txt")[2]["Content-Type"], "text/plain; charset=utf-8")
        for name in ("kit.json", "descriptions.json", "..%2Fproject.yaml"):
            self.assertNotEqual(get(name)[0], 200, name)
        self.assertEqual(json.loads(get("kit.json")[1])["code"], "missing_kit")
        # A newer recording is not covered yet: like the ZIP, the route leaves the old transcript out.
        self.record("run_20261006_130000_000000_rec00002")
        status, body, _ = get("transcript.md")
        self.assertEqual((status, json.loads(body)["code"]), (400, "missing_kit"))

    def test_an_edited_script_stops_the_kit_and_a_podcast_without_a_published_script_has_none(self):
        script = read_yaml(self.root / "episodes/ep_001/script.yaml")
        script["segments"][0]["text"] = "Was vergleicht dieses Modell eigentlich?"
        write_yaml(self.root / "episodes/ep_001/script.yaml", script)
        with patch("podcast_automate.publish_kit.AdapterPool", side_effect=AssertionError("no model call")):
            with self.assertRaises(AppError) as caught:
                build_podcast_kit(self.root)
            self.assertEqual(caught.exception.code, "script_edited")
            self.assertIsNone(saved_podcast_kit(self.root))
            # A folder no publish pointer names is not part of the series.
            pointer = self.root / "episodes/ep_001/latest.json"
            write_json(pointer, {**json.loads(pointer.read_text(encoding="utf-8")), "episode_ids": []})
            with self.assertRaises(AppError) as caught:
                build_podcast_kit(self.root)
        self.assertEqual(caught.exception.code, "no_published_script")

    def test_the_cli_writes_the_podcast_kit_and_needs_a_scope(self):
        output = io.StringIO()
        with patch("podcast_automate.publish_kit.AdapterPool", self.pool([dict(self.ANSWER)], [])), \
                contextlib.redirect_stdout(output):
            code = main(["publish-kit", str(self.root), "--podcast", "--json"])
        data = json.loads(output.getvalue())
        self.assertEqual((code, data["status"], data["kit"]["folder"]), (0, "completed", "publish"))
        self.assertIn("(episodes: 1, recorded: 0, sources: 1)", data["message"])
        self.assertIn("no time marks", data["message"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["publish-kit", str(self.root), "--json"])


if __name__ == "__main__":
    unittest.main()
