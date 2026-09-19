import re
import unittest
from pathlib import Path

from podcast_automate import prompts
from podcast_automate.prompts import PLACEHOLDERS, available, fragment, instructions, placeholders

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "podcast_automate"


class PromptFileTests(unittest.TestCase):
    def test_every_prompt_file_is_one_clean_paragraph_with_known_placeholders(self):
        names = available()
        self.assertGreater(len(names), 40)
        for name in names:
            with self.subTest(prompt=name):
                text = prompts.text(name)
                self.assertTrue(text)
                self.assertNotIn("\n", text)
                self.assertEqual(text, text.strip())
                self.assertNotIn("  ", text)
                self.assertLessEqual(placeholders(name), PLACEHOLDERS)
                filled = instructions(name, language="de-DE", maximum=3)
                self.assertNotIn("{", filled)
                self.assertEqual(filled, filled.rstrip())
                self.assertTrue(fragment(name).endswith(" "))
                self.assertFalse(fragment(name).endswith("  "))

    def test_placeholders_are_filled_and_missing_values_are_an_error(self):
        self.assertIn("Select at most 4 distinct", instructions("research_discovery", maximum=4))
        self.assertTrue(instructions("question_reader", language="en-US").endswith("Write answers in en-US."))
        with self.assertRaises(KeyError):
            instructions("question_reader", maximum=2)

    def test_code_and_prompt_files_reference_each_other_completely(self):
        referenced = set()
        for path in PACKAGE.glob("*.py"):
            referenced.update(re.findall(r'(?:fragment|instructions)\("([a-z_]+)"', path.read_text(encoding="utf-8")))
        files = set(available())
        self.assertEqual(referenced - files, set(), "code names prompt files that do not exist")
        self.assertEqual(files - referenced, set(), "prompt files that no code uses")

    def test_the_audit_rules_are_present_where_their_checks_expect_them(self):
        continuity = prompts.text("continuity")
        self.assertIn("established_terms", continuity)
        self.assertIn("one short recall clause per term per episode", continuity)
        self.assertIn("under 90 words", prompts.text("episode_framing"))
        for name in ("write_episode", "dialogue_polish"):
            with self.subTest(prompt=name):
                self.assertIn("illustrative exactly once", prompts.text(name))
        self.assertIn("demanding_passages", prompts.text("dialogue_polish_review"))
        self.assertIn("resolved_by", prompts.text("dialogue_polish_review"))
        self.assertIn("terms the words the hosts will actually say", prompts.text("teaching_design"))
        self.assertIn("include at least one source not authored by the organisation making the claim",
                      prompts.text("research_discovery"))
        # WP14 attribution: the writer attributes once, the reviewer notes a miss as a limitation.
        self.assertIn("attribute it audibly once in this episode", prompts.text("write_episode"))
        self.assertIn("note a missing attribution in limitations, never as an issue", prompts.text("script_review"))
        self.assertIn("use a comparison only where the sources support it", prompts.text("plain_language"))

    def test_pilot_specific_examples_no_longer_live_in_the_general_rules(self):
        self.assertNotIn("tokenizer", prompts.text("continuity"))
        self.assertNotIn("map", prompts.text("plain_language").split())

    def test_shared_rules_compose_without_double_spaces(self):
        from podcast_automate.editorial import CONTINUITY, EPISODE_FRAMING, TEACHING_SCOPE, TERMINOLOGY
        from podcast_automate.research import PLAIN_LANGUAGE
        combined = TERMINOLOGY + TEACHING_SCOPE + CONTINUITY + EPISODE_FRAMING + PLAIN_LANGUAGE
        self.assertNotIn("  ", combined)
        self.assertTrue(combined.endswith(". "))


if __name__ == "__main__":
    unittest.main()
