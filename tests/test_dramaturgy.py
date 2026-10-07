import unittest
from types import SimpleNamespace

from podcast_automate.dramaturgy import (DRAMATURGIES, ENDINGS, OPENINGS, STANCES, catalog, label, variety_defects)


def design(dramaturgy="", opening="", stance="", endings=()):
    return SimpleNamespace(dramaturgy=dramaturgy, opening=opening, partner_stance=stance,
                           scenes=[SimpleNamespace(ending=ending) for ending in endings] or [SimpleNamespace(ending="")])


class VarietyTests(unittest.TestCase):
    """The user (2026-10-06): after two episodes a listener should not be able to guess the structure."""

    previous = [{"episode_id": "ep_001", "dramaturgy": "mystery", "opening": "scene", "partner_stance": "skeptic"},
                {"episode_id": "ep_002", "dramaturgy": "debate", "opening": "number", "partner_stance": "learner"}]

    def test_a_dramaturgy_differs_from_the_two_episodes_before_and_opening_and_stance_from_the_last(self):
        self.assertEqual(variety_defects(design("discovery", "scene", "skeptic",
                                                ("open_question", "surprise", "conclusion")), self.previous), [])
        for repeated, expected in ((design("mystery"), "dramaturgy mystery"), (design("debate"), "dramaturgy debate"),
                                   (design("discovery", opening="number"), "opening number"),
                                   (design("discovery", stance="learner"), "partner_stance learner")):
            with self.subTest(expected):
                self.assertTrue(any(expected in error for error in variety_defects(repeated, self.previous)))
        # Three episodes back may come again.
        self.assertEqual(variety_defects(design("mystery"), [*self.previous, {"episode_id": "ep_003",
                                                             "dramaturgy": "myth_check"}][1:]), [])

    def test_chapter_endings_vary_and_only_the_last_concludes(self):
        same = design("discovery", endings=("recap", "recap", "recap", "conclusion"))
        self.assertTrue(any("at least two kinds" in error for error in variety_defects(same, [])))
        for endings in (("recap", "conclusion", "conclusion"), ("recap", "surprise", "open_question"),
                        ("recap", "", "conclusion")):
            with self.subTest(endings):
                self.assertTrue(variety_defects(design("discovery", endings=endings), []))
        self.assertEqual(variety_defects(design("discovery", endings=("recap", "recap", "conclusion")), []), [])

    def test_a_design_without_devices_validates_as_before(self):
        self.assertEqual(variety_defects(design(), self.previous), [])

    def test_the_catalogs_are_labelled_in_german_and_offered_to_the_model(self):
        offered = catalog()
        for name, table in (("dramaturgies", DRAMATURGIES), ("openings", OPENINGS), ("stances", STANCES),
                            ("chapter_endings", ENDINGS)):
            self.assertEqual(set(offered[name]), set(table))
            self.assertTrue(all(text for text in offered[name].values()))
        self.assertGreaterEqual(len(DRAMATURGIES), 6)
        self.assertEqual(label(DRAMATURGIES, "debate"), "Streitgespräch")
        self.assertEqual(label(ENDINGS, "unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()
