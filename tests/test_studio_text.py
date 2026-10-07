"""The Studio's interface language (D-152, studio_text): the catalogs, the language a request is answered in, the saved
choice, the Studio's own messages and the rule that only the Studio's boundary speaks through the catalogs."""
import ast
import json
import re
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from podcast_automate import studio_progress, studio_messages, studio_text
from podcast_automate.errors import AppError
from podcast_automate.studio_messages import user_text
from podcast_automate.studio_text import Localized, resolve, t, using

PACKAGE = Path(studio_text.__file__).parent
PLACEHOLDER = re.compile(r"\{(\w+)\}")
# The modules that may speak through the catalogs: the Studio's own boundary. Prompts, hashes, receipts, the command
# line and the pipeline's messages keep their language.
BOUNDARY = re.compile(r"(studio\w*|production_report)\.py")


def forms(value):
    return list(value.values()) if isinstance(value, dict) else [value]


class ResolutionTests(unittest.TestCase):
    def test_a_saved_choice_wins_else_the_best_browser_language_else_english_and_german_without_a_header(self):
        cases = [(None, "auto", "de"), ("", "auto", "de"), ("   ", "auto", "de"),
                 ("en-US,en;q=0.9", "auto", "en"), ("de-AT,de;q=0.9,en;q=0.8", "auto", "de"),
                 ("fr-FR,fr;q=0.9,en;q=0.5,de;q=0.6", "auto", "de"), ("en;q=0.4,de;q=0.8", "auto", "de"),
                 ("EN-gb", "auto", "en"), ("en,de", "auto", "en"), ("de,en", "auto", "de"),
                 ("fr-FR", "auto", "en"), ("*", "auto", "en"), ("de;q=0,fr", "auto", "en"), ("de;q=x,en;q=0.1", "auto", "en"),
                 ("en-US", "de", "de"), (None, "en", "en"), ("de", "en", "en"), ("de", "nonsense", "de")]
        for header, setting, expected in cases:
            with self.subTest(header=header, setting=setting):
                self.assertEqual(resolve(header, setting), expected)

    def test_outside_a_request_the_studio_speaks_german(self):
        self.assertIsNone(studio_text.requested())
        self.assertEqual(studio_text.current_language(), "de")
        with using("en"):
            self.assertEqual((studio_text.requested(), studio_text.current_language()), ("en", "en"))
            with using("fr"):
                self.assertEqual(studio_text.current_language(), "de", "an unknown language counts as no header")
        self.assertIsNone(studio_text.requested())


class SettingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()

    def add_project(self, name="old"):
        (self.workspace / "projects" / name).mkdir(parents=True)
        (self.workspace / "projects" / name / "project.yaml").write_text("topic: x\n", encoding="utf-8")

    def test_a_new_workspace_follows_the_browser_and_one_with_earlier_projects_stays_german(self):
        self.assertEqual(studio_text.setting(self.workspace), "auto")
        self.add_project()
        self.assertEqual(studio_text.setting(self.workspace), "de", "current users must not flip to their browser's language")
        self.assertFalse(studio_text.setting_path(self.workspace).exists())

    def test_the_first_project_of_a_new_workspace_keeps_auto(self):
        studio_text.remember_auto(self.workspace)
        self.add_project("first")
        self.assertEqual(studio_text.setting(self.workspace), "auto")
        self.assertEqual(json.loads(studio_text.setting_path(self.workspace).read_text(encoding="utf-8")), {"ui_language": "auto"})
        # A workspace with projects but no choice is left to the legacy default.
        other = self.workspace / "second"
        (other / "projects/old").mkdir(parents=True)
        (other / "projects/old/project.yaml").write_text("topic: x\n", encoding="utf-8")
        studio_text.remember_auto(other)
        self.assertFalse(studio_text.setting_path(other).exists())
        self.assertEqual(studio_text.setting(other), "de")

    def test_the_choice_lives_in_studio_ui_json_and_only_known_values_are_saved(self):
        self.add_project()
        for value in ("en", "auto", "de"):
            with self.subTest(value=value):
                self.assertEqual(studio_text.save_setting(self.workspace, value), value)
                self.assertEqual(json.loads((self.workspace / ".studio/ui.json").read_text(encoding="utf-8")),
                                 {"ui_language": value})
                self.assertEqual(studio_text.setting(self.workspace), value)
        with using("en"), self.assertRaises(AppError) as refused:
            studio_text.save_setting(self.workspace, "fr")
        self.assertEqual(refused.exception.code, "invalid_request")
        self.assertEqual(str(refused.exception), "Choose Automatic, German or English as the language.")
        self.assertEqual(studio_text.setting(self.workspace), "de")
        # An unreadable file counts as no choice.
        (self.workspace / ".studio/ui.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(studio_text.setting(self.workspace), "de")


class CatalogTests(unittest.TestCase):
    def test_the_catalogs_load_from_the_installed_package(self):
        for language in studio_text.LANGUAGES:
            with self.subTest(language=language):
                source = files("podcast_automate").joinpath("locales", f"{language}.json")
                self.assertEqual(studio_text.catalog(language), json.loads(source.read_text(encoding="utf-8")))
        with self.assertRaises(ValueError):
            studio_text.catalog("fr")

    def test_german_and_english_have_the_same_keys_placeholders_and_plural_shapes_and_no_markup(self):
        german, english = studio_text.catalog("de"), studio_text.catalog("en")
        self.assertEqual(sorted(german), sorted(english))
        for key in german:
            with self.subTest(key=key):
                de, en = german[key], english[key]
                self.assertEqual(type(de), type(en))
                if isinstance(de, dict):
                    self.assertEqual(set(de), {"one", "other"})
                    self.assertEqual(set(en), {"one", "other"})
                names = [set().union(*(PLACEHOLDER.findall(text) for text in forms(value))) for value in (de, en)]
                self.assertEqual(names[0], names[1])
                for text in forms(de) + forms(en):
                    self.assertIsInstance(text, str)
                    self.assertTrue(text, "no empty value")
                    self.assertNotRegex(text, r"[<>]|&[#A-Za-z]", "values are plain text; the page escapes them")

    def test_text_fills_placeholders_chooses_plurals_and_falls_back_to_english_then_the_key(self):
        with using("en"):
            one, many = t("server.audio.capacity", count=1), t("server.audio.capacity", count=3)
        self.assertEqual((one.language, one.key), ("en", "server.audio.capacity"))
        self.assertEqual(str(many), "All 3 recording places are taken. Finish or stop an episode being recorded.")
        self.assertNotEqual(str(one), str(many))
        self.assertEqual(t("server.audio.capacity", count=1),
                         "Alle 1 Plätze für die Vertonung sind belegt. Eine laufende Folge fertigstellen oder anhalten.")
        self.assertEqual(t("server.request.work_size"), "Das Werk darf höchstens {mb} MB groß sein.",
                         "a missing parameter leaves its placeholder")
        catalogs = {"de": {"both": "Beide"}, "en": {"both": "Both", "only.en": "Only English"}}
        with patch.object(studio_text, "catalog", side_effect=lambda language: catalogs[language]), \
                patch.object(studio_text, "_missing", set()), self.assertLogs("podcast_automate.studio_text", "WARNING"):
            self.assertEqual((t("only.en"), t("only.en").language), ("Only English", "en"))
            self.assertEqual((t("no.such.key"), t("no.such.key").language), ("no.such.key", "en"))
            self.assertEqual(studio_text.merged("de"), {"both": "Beide", "only.en": "Only English"})

    def test_locale_js_carries_the_choice_the_language_and_english_under_the_active_catalog(self):
        script = studio_text.locale_script("de", "auto")
        self.assertTrue(script.startswith("const STUDIO_LOCALE=") and script.endswith(";\n"))
        data = json.loads(script[len("const STUDIO_LOCALE="):-2])
        self.assertEqual((data["setting"], data["language"]), ("auto", "de"))
        self.assertEqual(data["catalog"], studio_text.merged("de"))
        catalogs = {"de": {"line": "a b"}, "en": {"line": "x"}}
        with patch.object(studio_text, "catalog", side_effect=lambda language: catalogs[language]):
            self.assertIn("a\\u2028b", studio_text.locale_script("de", "de"))


class LocalizedTests(unittest.TestCase):
    def test_the_studios_own_message_reaches_an_apperror_with_its_key_and_language(self):
        with using("en"):
            error = AppError(t("server.project.not_found"), code="unknown_project")
        message = error.args[0]
        self.assertIsInstance(message, Localized)
        self.assertEqual((str(error), message.key, message.language), ("Project not found.", "server.project.not_found", "en"))
        self.assertEqual(json.dumps({"error": message}), '{"error": "Project not found."}')
        self.assertEqual(studio_messages.language_of(message), "en")
        self.assertEqual(studio_messages.language_of("Projekt nicht gefunden."), "de")
        self.assertEqual(studio_messages.language_of("The answer is missing its evidence."), "en")

    def test_a_stop_message_reads_in_the_interface_language_and_names_its_own(self):
        rejected = ("Codex answered, but the answer violates its output contract: findings.0.support: Input should be x. "
                    "Return the complete answer again with exactly these defects corrected.")
        english = user_text(rejected, None, "en")
        self.assertEqual((english["message"], english["message_language"]),
                         ("The answer from Codex did not match the expected format (findings.0.support).", "en"))
        self.assertIn("output contract", english["detail"])
        known = user_text("Unanchored review disagreement; no automatic new research.", None, "en")
        self.assertEqual((known["message"], known["message_language"]), (
            "The overall review raises an objection it cannot anchor in evidence it has read. No automatic follow-up "
            "research starts for it.", "en"))
        unknown = "The verified answer was missing its evidence for every criterion."
        self.assertEqual(user_text(unknown, None, "en"), {"message": unknown, "detail": None, "file": None,
                                                         "message_language": "en"})
        self.assertEqual(user_text(unknown)["message"], "Eine automatische Prüfung hat ein Ergebnis abgewiesen.")
        german = user_text("Abo-Kontingent erreicht. Siehe src_0cca2395cb73c72c#sec_6891d807643ea0ef.", None, "en")
        self.assertEqual((german["message"], german["message_language"]), ("Abo-Kontingent erreicht. Siehe Quellenstelle.", "de"),
                         "a German pipeline message stays German throughout")
        self.assertEqual(user_text("See src_0cca2395cb73c72c#sec_6891d807643ea0ef because the evidence is missing.", None,
                                   "en")["message"], "See source passage because the evidence is missing.")
        with using("en"):
            own = t("server.job.stopped")
        self.assertEqual(user_text(own, None, "en")["message_language"], "en")
        self.assertEqual(user_text("Angehalten. Fertige Arbeit bleibt gespeichert.")["message_language"], "de")


class BoundaryTests(unittest.TestCase):
    def sources(self):
        return {path.name: path.read_text(encoding="utf-8") for path in sorted(PACKAGE.glob("*.py"))}

    def test_only_the_studios_boundary_imports_the_catalogs(self):
        importers = set()
        for name, source in self.sources().items():
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.ImportFrom):
                    modules = {node.module or ""} | {f"{node.module or ''}.{alias.name}".lstrip(".") for alias in node.names}
                    if any(module.split(".")[-1] == "studio_text" for module in modules):
                        importers.add(name)
                elif isinstance(node, ast.Import) and any(alias.name.endswith("studio_text") for alias in node.names):
                    importers.add(name)
        self.assertIn("studio.py", importers)
        self.assertEqual({name for name in importers if not BOUNDARY.fullmatch(name)}, set(),
                         "prompts, hashes, receipts, the CLI and pipeline messages keep their language")

    def test_every_key_the_python_side_names_is_in_both_catalogs(self):
        german, english = studio_text.catalog("de"), studio_text.catalog("en")
        named = set()
        for name, source in self.sources().items():
            if not BOUNDARY.fullmatch(name) or name == "studio_text.py":
                continue
            for node in ast.walk(ast.parse(source)):
                # A whole key; a prefix completed at run time is covered below, a file name is no key.
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                        re.fullmatch(r"(server|message|progress|pipeline)(\.\w[\w-]*)+", node.value) and \
                        not node.value.endswith((".json", ".yaml", ".md")):
                    named.add(node.value)
        named |= {f"progress.activity.{schema}" for schema in studio_progress.ACTIVITIES}
        named |= {f"message.exception.{name}" for name in studio_messages.EXCEPTIONS}
        named |= set(studio_messages.ENGLISH.values())
        self.assertGreater(len(named), 100)
        self.assertEqual(sorted(key for key in named if key not in german or key not in english), [])


if __name__ == "__main__":
    unittest.main()
