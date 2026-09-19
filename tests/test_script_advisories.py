"""Deterministic advisory counters over a finished script; German patterns, fixed sentences."""
import unittest

from podcast_automate.models import Chapter, EpisodeScript, Segment
from podcast_automate.script_advisories import (advisories, established_terms, hedging_hits, humanised, long_cold_open,
                                                over_target_duration, redefined_terms, repeated_hedging)
from podcast_automate.script_artifacts import script_metrics
from podcast_automate.script_models import EpisodePlan, ScenePlan


def script(*texts, episode_id="ep_002"):
    return EpisodeScript(episode_id=episode_id, title="Eine Folge", purpose="deep_dive",
        chapters=[Chapter(chapter_id="scene_main", title="Ein Kapitel")],
        segments=[Segment(segment_id=f"seg_{i:03d}", scene_id="scene_main", chapter_id="scene_main",
                          speaker_id="host_a" if i % 2 else "host_b", text=text)
                  for i, text in enumerate(texts, 1)])


def plan(minutes=20.0):
    return EpisodePlan(episode_id="ep_002", title="Eine Folge", central_question="Warum?",
        target_minutes=minutes, prerequisite_episodes=["ep_001"], finding_ids=["f_one"],
        deferred_questions=[], scenes=[ScenePlan(scene_id="scene_main", title="Ein Kapitel",
            question="Warum?", purpose="explanation", finding_ids=["f_one"],
            explanation_steps=["Erklären."])])


class RedefinedTerms(unittest.TestCase):
    def test_each_listed_definition_pattern_is_counted(self):
        for sentence in ("Ein Aufmerksamkeitskopf ist eine gewichtete Auswahl.",
                         "Der Aufmerksamkeitskopf, also die gewichtete Auswahl, greift wieder.",
                         "Das nennen wir Aufmerksamkeitskopf.",
                         "Dieser Schritt heißt Aufmerksamkeitskopf.",
                         "Ein Aufmerksamkeitskopf bedeutet gewichtete Auswahl.",
                         "Unter dem Aufmerksamkeitskopf versteht man eine gewichtete Auswahl."):
            with self.subTest(sentence=sentence):
                rows = redefined_terms(script(sentence, sentence), ["Aufmerksamkeitskopf"], "de-DE")
                self.assertEqual([r["code"] for r in rows], ["redefined_term"])
                self.assertEqual(rows[0]["count"], 2)
                self.assertEqual(rows[0]["segment_ids"], ["seg_001", "seg_002"])

    def test_a_single_recall_clause_stays_below_the_advisory(self):
        rows = redefined_terms(script("Ein Aufmerksamkeitskopf ist eine gewichtete Auswahl.",
                                      "Damit vergleichen wir zwei Sätze."), ["Aufmerksamkeitskopf"], "de-DE")
        self.assertEqual(rows, [])

    def test_ordinary_use_of_an_established_term_never_counts(self):
        rows = redefined_terms(script("Der Aufmerksamkeitskopf gewichtet hier stärker.",
                                      "In unserem Beispiel greift der Aufmerksamkeitskopf zweimal.",
                                      "Der Aufmerksamkeitskopf liefert dann eine andere Reihenfolge."),
                               ["Aufmerksamkeitskopf"], "de-DE")
        self.assertEqual(rows, [])

    def test_a_term_that_was_never_established_is_not_reported(self):
        self.assertEqual(redefined_terms(script("Ein Vektor ist eine Liste von Zahlen.",
                                                "Ein Vektor ist wieder eine Liste."), [], "de-DE"), [])

    def test_patterns_do_not_fire_on_another_language(self):
        rows = redefined_terms(script("An attention head is a weighted selection.",
                                      "An attention head is a weighted selection."), ["attention head"], "en-US")
        self.assertEqual(rows, [])

    def test_established_terms_come_from_reviewed_rows_only_and_fall_back_to_the_id(self):
        context = [{"episode_id": "ep_001", "status": "reviewed_teaching_plan",
                    "teaching_design": {"concepts": [{"concept_id": "attention_head", "terms": ["Aufmerksamkeitskopf"]},
                                                     {"concept_id": "position_code", "terms": []}]}},
                   {"episode_id": "ep_000", "status": "outline_only", "outline": {"title": "Nur Gliederung"}}]
        self.assertEqual(established_terms(context), ["Aufmerksamkeitskopf", "position code"])
        self.assertEqual(established_terms([]), [])
        self.assertEqual(humanised("attention_head"), "attention head")


class Hedging(unittest.TestCase):
    def test_more_than_two_reminders_are_reported_once_with_their_segments(self):
        rows = repeated_hedging(script("Das ist ein Gedankenbeispiel, keine gemessene Aktivierung.",
                                       "Die Zahlen sind hypothetisch.",
                                       "Wir haben das nicht gemessen.",
                                       "Danach vergleichen wir beide Sätze."), "de-DE")
        self.assertEqual([r["code"] for r in rows], ["repeated_hedging"])
        self.assertEqual(rows[0]["count"], 4)
        self.assertEqual(rows[0]["segment_ids"], ["seg_001", "seg_002", "seg_003"])

    def test_two_reminders_are_within_the_rule(self):
        self.assertEqual(repeated_hedging(script("Ein Gedankenbeispiel, kein echter Modelllauf.",
                                                 "In unserem Beispiel steigt der Wert."), "de-DE"), [])

    def test_in_unserem_beispiel_is_not_a_hedge(self):
        self.assertEqual(repeated_hedging(script("In unserem Beispiel steigt der Wert.",
                                                 "In unserem Beispiel sinkt er wieder.",
                                                 "In unserem Beispiel bleibt er gleich.",
                                                 "In unserem Beispiel gilt das weiter."), "de-DE"), [])

    def test_other_languages_have_no_patterns(self):
        self.assertEqual(repeated_hedging(script("This is hypothetisch.", "Also hypothetisch.",
                                                 "Still hypothetisch."), "en-US"), [])

    def test_the_ordinary_verb_and_everyday_negations_are_not_hedges(self):
        # Four sentences: a single match anywhere would not exceed the limit, so each is
        # checked alone as well as together.
        sentences = ("Die Architektur wurde 2017 erfunden.",
                     "Das ist keine echte Alternative.",
                     "Es gibt kein realer Unterschied zwischen beiden.",
                     "Die Idee wurde in einem Labor erfunden.")
        self.assertEqual(hedging_hits(script(*sentences), "de-DE"), [])
        self.assertEqual(repeated_hedging(script(*sentences), "de-DE"), [])

    def test_back_references_to_a_named_example_are_not_hedges(self):
        sentences = ("In unserem Gedankenexperiment bleibt die Eingabe erhalten.",
                     "Dieses Gedankenbeispiel zeigt den Unterschied.",
                     "Im Gedankenexperiment sinkt der Wert wieder.",
                     "Das Gedankenmodell gilt weiter.")
        self.assertEqual(hedging_hits(script(*sentences), "de-DE"), [])
        self.assertEqual(repeated_hedging(script(*sentences), "de-DE"), [])

    def test_a_fresh_introduction_and_measurement_negations_still_fire(self):
        rows = repeated_hedging(script("Wir verändern hier nur ein Gedankenbeispiel; das ist kein echter Modelllauf.",
                                       "Die Zahlen sind erfunden.",
                                       "Es gibt keine konkrete Messung eines Modells dafür."), "de-DE")
        self.assertEqual([r["code"] for r in rows], ["repeated_hedging"])
        # Two reminders in the first sentence (the introduction and the measurement negation),
        # one each in the other two.
        self.assertEqual(rows[0]["count"], 4)
        self.assertEqual(rows[0]["segment_ids"], ["seg_001", "seg_002", "seg_003"])


class OpeningAndDuration(unittest.TestCase):
    def test_a_first_segment_above_one_hundred_words_is_reported(self):
        rows = long_cold_open(script(" ".join(["Wort"] * 101), "Kurz."))
        self.assertEqual([r["code"] for r in rows], ["long_cold_open"])
        self.assertEqual(rows[0]["count"], 101)
        self.assertEqual(rows[0]["segment_ids"], ["seg_001"])

    def test_exactly_one_hundred_words_stays_silent(self):
        self.assertEqual(long_cold_open(script(" ".join(["Wort"] * 100))), [])

    def test_duration_above_one_hundred_and_twenty_percent_is_reported(self):
        episode = script(" ".join(["Wort"] * 2600))
        metrics = script_metrics(episode)
        self.assertEqual(over_target_duration(episode, plan(minutes=20.0), metrics), [])
        rows = over_target_duration(episode, plan(minutes=15.0), metrics)
        self.assertEqual([r["code"] for r in rows], ["over_target_duration"])
        self.assertEqual(rows[0]["segment_ids"], [])
        # 2600 words at 130 words per minute are 20 minutes, 133 percent of a 15-minute plan;
        # the count is that whole-number percentage, like every other advisory count.
        self.assertEqual(rows[0]["count"], 133)
        self.assertIsInstance(rows[0]["count"], int)


class Combined(unittest.TestCase):
    def test_rows_carry_the_episode_and_arrive_in_a_stable_order(self):
        episode = script("Ein Aufmerksamkeitskopf ist eine gewichtete Auswahl. " + " ".join(["Wort"] * 110),
                         "Ein Aufmerksamkeitskopf ist wieder eine gewichtete Auswahl. Hypothetisch, "
                         "ein Gedankenbeispiel, nicht gemessen.")
        rows = advisories(episode, plan(), script_metrics(episode), language="de-DE",
                          terms=["Aufmerksamkeitskopf"])
        self.assertEqual([r["code"] for r in rows], ["redefined_term", "repeated_hedging", "long_cold_open"])
        self.assertTrue(all(r["episode_id"] == "ep_002" for r in rows))
        self.assertTrue(all(r["detail"] for r in rows))

    def test_a_clean_episode_produces_no_rows(self):
        episode = script("Willkommen zurück. Heute geht es um die Reihenfolge.",
                         "Der Aufmerksamkeitskopf gewichtet dabei stärker.")
        self.assertEqual(advisories(episode, plan(), script_metrics(episode), language="de-DE",
                                    terms=["Aufmerksamkeitskopf"]), [])


if __name__ == "__main__":
    unittest.main()
