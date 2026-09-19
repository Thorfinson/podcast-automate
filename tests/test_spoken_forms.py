"""The table that decides what the speech engine hears, and the report a reader gets."""
import unittest

from podcast_automate.models import Chapter, EpisodeScript, Segment
from podcast_automate.spoken_forms import SpokenForms, applied, apply, load_forms, report, spoken_text


def table(*pairs):
    return SpokenForms(entries=[{"written": written, "spoken": spoken} for written, spoken in pairs])


def script(*texts):
    return EpisodeScript(episode_id="ep_001", title="Eine Folge", purpose="deep_dive",
        chapters=[Chapter(chapter_id="scene_main", title="Kapitel")],
        segments=[Segment(segment_id=f"seg_{i:03d}", scene_id="scene_main", chapter_id="scene_main",
                          speaker_id="host_a" if i % 2 else "host_b", text=text)
                  for i, text in enumerate(texts, 1)])


class Apply(unittest.TestCase):
    def test_the_longest_written_form_wins_and_no_replacement_feeds_another(self):
        forms = table(("V3", "Version drei"), ("DeepSeek-V3.2-Exp", "DeepSeek Version drei Punkt zwei Exp"))
        self.assertEqual(apply("DeepSeek-V3.2-Exp nutzt V3 weiter.", forms),
                         "DeepSeek Version drei Punkt zwei Exp nutzt Version drei weiter.")

    def test_replacement_is_case_sensitive(self):
        forms = table(("H800", "H achthundert"))
        self.assertEqual(apply("Auf H800 und auf h800.", forms), "Auf H achthundert und auf h800.")

    def test_only_whole_tokens_are_replaced_and_a_hyphen_ends_a_token(self):
        # Behaviour change of 19 September 2026: a hyphen is a boundary, so the entry reaches
        # "KL-Term", the compound the report lists under abbreviations. Letters still bind.
        forms = table(("KL", "Kullback-Leibler"))
        self.assertEqual(apply("Der KL-Term und KLASSE und KL.", forms),
                         "Der Kullback-Leibler-Term und KLASSE und Kullback-Leibler.")

    def test_a_dot_between_characters_binds_while_hyphen_dash_and_slash_separate(self):
        # Deliberate change: "H800" used to stay untouched inside "H800-GPUs"; now it fires there,
        # because a hyphen separates tokens. A dot between characters does not: "V3" leaves
        # "V3.2-Exp" alone and "1.000" leaves "1.000.000" alone. A trailing dot still ends a sentence.
        self.assertEqual(apply("H800-GPUs, H800–Karten und H800/H100.", table(("H800", "H achthundert"))),
                         "H achthundert-GPUs, H achthundert–Karten und H achthundert/H100.")
        self.assertEqual(apply("DeepSeek-V3.2-Exp nutzt V3 weiter. Auch V3.", table(("V3", "Version drei"))),
                         "DeepSeek-V3.2-Exp nutzt Version drei weiter. Auch Version drei.")
        self.assertEqual(apply("1.000.000 Stück, davon 1.000 hier.", table(("1.000", "tausend"))),
                         "1.000.000 Stück, davon tausend hier.")
        self.assertEqual(apply("Die V3.2 ist da.", table(("V3.2", "drei Punkt zwei"))), "Die drei Punkt zwei ist da.")

    def test_an_empty_table_returns_the_text_unchanged(self):
        self.assertEqual(apply("Unverändert.", SpokenForms()), "Unverändert.")
        self.assertEqual(applied("Unverändert.", SpokenForms()), {})

    def test_the_applied_count_reports_what_actually_fired(self):
        self.assertEqual(applied("H800, H800 und V3.", table(("H800", "x"), ("V3", "y"), ("H100", "z"))),
                         {"H800": 2, "V3": 1})

    def test_a_per_segment_override_replaces_the_table_entirely(self):
        forms = table(("H800", "H achthundert"))
        segment = script("Auf H800 gerechnet.").segments[0]
        self.assertEqual(spoken_text(segment, forms), "Auf H achthundert gerechnet.")
        self.assertEqual(spoken_text(segment, forms, {"seg_001": "Auf Ha achthundert gerechnet."}),
                         "Auf Ha achthundert gerechnet.")
        self.assertEqual(spoken_text(segment, forms, {"seg_002": "Andere Folge."}),
                         "Auf H achthundert gerechnet.")


class Report(unittest.TestCase):
    def test_each_category_collects_its_own_tokens_with_segments(self):
        found = report(script("Wir rechnen auf H800 mit 2048 Positionen.",
                              "Die KL-Abweichung von DeepSeek-V3.2-Exp bleibt klein.",
                              "Er sagte j'ai mal aux pieds.", "Etwa 1.000.000 Tokens."), SpokenForms())["flagged"]
        # The report tokenises exactly as apply() matches: a hyphen separates ("KL", "V3.2"), a
        # dot between characters binds ("1.000.000"), so every flagged token is one a table entry
        # can address.
        self.assertEqual([row["token"] for row in found["versions"]], ["H800", "V3.2"])
        self.assertEqual([row["token"] for row in found["numbers"]], ["1.000.000", "2048"])
        self.assertEqual([row["token"] for row in found["abbreviations"]], ["KL"])
        self.assertEqual([row["token"] for row in found["foreign"]], ["j'ai"])  # contraction apostrophe
        self.assertEqual(found["numbers"][1]["segment_ids"], ["seg_001"])

    def test_a_flagged_token_disappears_once_an_entry_for_exactly_that_token_exists(self):
        text = "Die KL-Abweichung von DeepSeek-V3.2-Exp auf H800-GPUs."
        flagged = report(script(text), SpokenForms())["flagged"]
        forms = table(("KL", "Kullback-Leibler"), ("V3.2", "drei Punkt zwei"), ("H800", "H achthundert"))
        resolved = report(script(text), forms)
        self.assertEqual(sorted(flagged), ["abbreviations", "versions"])
        self.assertEqual(resolved["flagged"], {})
        self.assertEqual(resolved["applied"], {"KL": 1, "V3.2": 1, "H800": 1})

    def test_the_report_describes_the_spoken_text_not_the_script(self):
        forms = table(("H800", "H achthundert"))
        found = report(script("Wir rechnen auf H800."), forms)
        self.assertEqual(found["applied"], {"H800": 1})
        self.assertNotIn("versions", found["flagged"])

    def test_an_override_reaches_the_report_and_is_listed(self):
        found = report(script("Wir rechnen auf H800."), SpokenForms(), overrides={"seg_001": "Wir rechnen auf Ha achthundert."})
        self.assertEqual(found["overrides"], ["seg_001"])
        self.assertNotIn("versions", found["flagged"])

    def test_a_clean_script_flags_nothing_and_never_claims_a_human_checked_it(self):
        found = report(script("Wir vergleichen zwei Sätze in Ruhe."), SpokenForms())
        self.assertEqual(found["flagged"], {})
        self.assertFalse(found["human_pronunciation_reviewed"])

    def test_english_projects_use_their_own_alphabet(self):
        found = report(script("The Fassung is schön."), SpokenForms(), language="en-US")["flagged"]
        self.assertEqual([row["token"] for row in found["foreign"]], ["schön"])


class Loading(unittest.TestCase):
    def test_a_project_without_a_table_gets_an_empty_one(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(load_forms(root).entries, [])
            (root / "studio").mkdir()
            (root / "studio/spoken_forms.json").write_text(
                '{"schema_version": "1.0", "entries": [{"written": "H800", "spoken": "H achthundert"}]}',
                encoding="utf-8")
            self.assertEqual(load_forms(root).entries[0].spoken, "H achthundert")


if __name__ == "__main__":
    unittest.main()
