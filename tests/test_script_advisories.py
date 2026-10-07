"""Deterministic advisory counters over a finished script; German and English patterns, fixed sentences."""
import unittest

from podcast_automate.models import Chapter, EpisodeScript, Segment
from podcast_automate.script_advisories import (LONG_TURN_LIMIT, LONG_TURN_WORDS, PARTNER_SHARE_FLOOR, TURN_WORDS,
                                                advisories, dialogue_shape, established_terms, hedging_hits, humanised,
                                                long_cold_open, long_turns, low_partner_share, over_target_duration,
                                                redefined_terms, repeated_hedging, turns)
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

    def test_patterns_do_not_fire_on_a_language_without_patterns(self):
        # English has patterns since 2026-10-02 (EnglishPatterns); a third language still gets none.
        rows = redefined_terms(script("An attention head is a weighted selection.",
                                      "An attention head is a weighted selection."), ["attention head"], "fr-FR")
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
        self.assertEqual(repeated_hedging(script("C'est hypothetisch.", "Aussi hypothetisch.",
                                                 "Encore hypothetisch."), "fr-FR"), [])
        # The German words are no English hedge either.
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


def spoken(*turns_spec):
    """A script of (chapter, speaker, words) segments, for counting what the ear gets."""
    chapters = list(dict.fromkeys(chapter for chapter, _, _ in turns_spec))
    return EpisodeScript(episode_id="ep_002", title="Eine Folge", purpose="deep_dive",
        chapters=[Chapter(chapter_id=chapter, title=chapter) for chapter in chapters],
        segments=[Segment(segment_id=f"seg_{i:03d}", scene_id=chapter, chapter_id=chapter, speaker_id=speaker,
                          text=" ".join(["Wort"] * count))
                  for i, (chapter, speaker, count) in enumerate(turns_spec, 1)])


class DialogueShape(unittest.TestCase):
    """2026-10-06: the expert spoke 70 to 87 % of the words of three finished series, with 9 to 25 turns over 120
    words; the rewrites the user liked gave the partner about 35 % and no turn over about 55 words."""

    def test_consecutive_segments_of_one_host_are_one_turn_also_across_a_chapter(self):
        episode = spoken(("scene_a", "host_a", 30), ("scene_b", "host_a", 60), ("scene_b", "host_b", 10))
        self.assertEqual(turns(episode), [("host_a", ["seg_001", "seg_002"], 90), ("host_b", ["seg_003"], 10)])
        self.assertEqual(dialogue_shape(episode), {"partner_share": 0.1, "turns": 2, "long_turns": [
            {"speaker_id": "host_a", "segment_ids": ["seg_001", "seg_002"], "words": 90}]})
        self.assertEqual(TURN_WORDS, 80)

    def test_more_long_turns_than_a_worked_example_needs_are_reported(self):
        long = LONG_TURN_WORDS + 1
        within = [("scene_a", "host_a", long), ("scene_a", "host_b", 60)] * LONG_TURN_LIMIT
        self.assertEqual(long_turns(spoken(*within, ("scene_a", "host_a", LONG_TURN_WORDS))), [])
        rows = long_turns(spoken(*within, ("scene_a", "host_a", 70), ("scene_a", "host_a", 90)))
        self.assertEqual([(r["code"], r["count"], r["segment_ids"]) for r in rows],
                         [("long_turns", 3, ["seg_001", "seg_003", "seg_005", "seg_006"])])
        self.assertIn("160", rows[0]["detail"])

    def test_a_partner_under_the_floor_is_reported_in_whole_percent(self):
        self.assertEqual(PARTNER_SHARE_FLOOR, 0.25)
        rows = low_partner_share(spoken(("scene_a", "host_a", 76), ("scene_a", "host_b", 24)))
        self.assertEqual([(r["code"], r["count"], r["segment_ids"]) for r in rows], [("low_partner_share", 24, [])])
        self.assertEqual(low_partner_share(spoken(("scene_a", "host_a", 75), ("scene_a", "host_b", 25))), [])
        # The shape of the rewrite the user liked: no row at all.
        liked = spoken(*[("scene_a", "host_a", 50), ("scene_a", "host_b", 27)] * 6)
        self.assertEqual(advisories(liked, plan(minutes=3.5), script_metrics(liked), language="de-DE"), [])


class EnglishPatterns(unittest.TestCase):
    """2026-10-02: the English Ontologies series got no measurement at all; English carries its own patterns."""

    def test_each_english_definition_pattern_is_counted(self):
        for sentence in ("An ontology is a shared vocabulary of a domain.",
                         "The ontology, that is the shared vocabulary, comes back.",
                         "An ontology means a shared vocabulary.",
                         "We call this shared vocabulary an ontology.",
                         "This structure is known as an ontology.",
                         "By an ontology we mean a shared vocabulary."):
            with self.subTest(sentence=sentence):
                rows = redefined_terms(script(sentence, sentence), ["ontology"], "en-US")
                self.assertEqual([(r["code"], r["count"]) for r in rows], [("redefined_term", 2)])

    def test_ordinary_english_use_of_an_established_term_never_counts(self):
        self.assertEqual(redefined_terms(script("An ontology is expensive to maintain.",
                                                "The ontology grows every week.",
                                                "Teams use the ontology to align their terms."), ["ontology"], "en-US"), [])

    def test_english_hedges_count_and_back_references_or_the_ordinary_verb_do_not(self):
        rows = repeated_hedging(script("This is a thought experiment, not a real measurement.",
                                       "The numbers are made up.",
                                       "We have not actually measured this."), "en-US")
        self.assertEqual([(r["code"], r["count"]) for r in rows], [("repeated_hedging", 4)])
        self.assertEqual(rows[0]["segment_ids"], ["seg_001", "seg_002", "seg_003"])
        self.assertEqual(hedging_hits(script("In our thought experiment the value rises.",
                                             "This thought experiment shows the difference.",
                                             "The format was invented in 2017.",
                                             "That is not a real alternative.",
                                             "There is no real difference between them."), "en-US"), [])

    def test_an_english_episode_gets_every_advisory(self):
        # The partner's 14 of 130 words are also under the share floor since 2026-10-06 (low_partner_share).
        episode = script("An ontology is a shared vocabulary. " + " ".join(["word"] * 110),
                         "An ontology is again a shared vocabulary. Hypothetical, a thought experiment, not measured.")
        rows = advisories(episode, plan(), script_metrics(episode), language="en-US", terms=["ontology"])
        self.assertEqual([r["code"] for r in rows],
                         ["redefined_term", "repeated_hedging", "long_cold_open", "low_partner_share"])


class Combined(unittest.TestCase):
    def test_rows_carry_the_episode_and_arrive_in_a_stable_order(self):
        episode = script("Ein Aufmerksamkeitskopf ist eine gewichtete Auswahl. " + " ".join(["Wort"] * 110),
                         "Ein Aufmerksamkeitskopf ist wieder eine gewichtete Auswahl. Hypothetisch, "
                         "ein Gedankenbeispiel, nicht gemessen.")
        rows = advisories(episode, plan(), script_metrics(episode), language="de-DE",
                          terms=["Aufmerksamkeitskopf"])
        # The partner's share joins the end of the order since 2026-10-06; the earlier rows keep their places.
        self.assertEqual([r["code"] for r in rows],
                         ["redefined_term", "repeated_hedging", "long_cold_open", "low_partner_share"])
        self.assertTrue(all(r["episode_id"] == "ep_002" for r in rows))
        self.assertTrue(all(r["detail"] for r in rows))

    def test_each_row_carries_the_values_of_its_sentence_as_params(self):
        """D-153 (2026-10-07): the Studio words a row by its code and params in the reader's language; ``detail`` stays
        the German sentence it always was, so reports written before read as they did."""
        episode = script("Ein Aufmerksamkeitskopf ist eine gewichtete Auswahl. " + " ".join(["Wort"] * 110),
                         "Ein Aufmerksamkeitskopf ist wieder eine gewichtete Auswahl. Hypothetisch, "
                         "ein Gedankenbeispiel, nicht gemessen.")
        metrics = script_metrics(episode)
        rows = {r["code"]: r for r in advisories(episode, plan(minutes=0.5), metrics, language="de-DE",
                                                 terms=["Aufmerksamkeitskopf"])}
        self.assertEqual(set(rows), {"redefined_term", "repeated_hedging", "long_cold_open", "over_target_duration",
                                     "low_partner_share"})
        expected = {
            "redefined_term": {"term": "Aufmerksamkeitskopf", "definitions": 2},
            "repeated_hedging": {"reminders": rows["repeated_hedging"]["count"], "limit": 2},
            "long_cold_open": {"words": rows["long_cold_open"]["count"], "limit": 100},
            "over_target_duration": {"estimated_minutes": metrics["estimated_minutes"], "target_minutes": 0.5,
                                     "percent": rows["over_target_duration"]["count"], "limit_percent": 120},
            "low_partner_share": {"percent": rows["low_partner_share"]["count"], "floor_percent": 25}}
        self.assertEqual({code: row["params"] for code, row in rows.items()}, expected)
        self.assertEqual(rows["redefined_term"]["detail"], "«Aufmerksamkeitskopf» wird in dieser Folge 2-mal neu "
                                                           "definiert, obwohl der Begriff aus einer früheren Folge bekannt ist.")
        self.assertEqual(rows["long_cold_open"]["detail"],
                         f"Der erste gesprochene Abschnitt hat {rows['long_cold_open']['count']} Wörter; über 100 "
                         "beginnt die Folge ohne Atempause.")
        long = LONG_TURN_WORDS + 1
        lecture = long_turns(spoken(*[("scene_a", "host_a", long), ("scene_a", "host_b", 60)] * 3))
        self.assertEqual(lecture[0]["params"], {"turns": 3, "longest_words": long, "over_words": LONG_TURN_WORDS,
                                                "turn_words": TURN_WORDS, "limit": LONG_TURN_LIMIT})

    def test_a_clean_episode_produces_no_rows(self):
        episode = script("Willkommen zurück. Heute geht es um die Reihenfolge.",
                         "Der Aufmerksamkeitskopf gewichtet dabei stärker.")
        self.assertEqual(advisories(episode, plan(), script_metrics(episode), language="de-DE",
                                    terms=["Aufmerksamkeitskopf"]), [])


if __name__ == "__main__":
    unittest.main()
