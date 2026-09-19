"""Alignment and reporting only; the suites never load a recogniser or touch a GPU."""
import unittest

from podcast_automate.models import Chapter, EpisodeScript, Segment
from podcast_automate.transcription_check import (FLAG_WORD_ERROR_RATE, check_segments, compare,
                                                  normalise, transcribe_segments)


def script(*texts):
    return EpisodeScript(episode_id="ep_001", title="Eine Folge", purpose="deep_dive",
        chapters=[Chapter(chapter_id="scene_main", title="Kapitel")],
        segments=[Segment(segment_id=f"seg_{i:03d}", scene_id="scene_main", chapter_id="scene_main",
                          speaker_id="host_a" if i % 2 else "host_b", text=text)
                  for i, text in enumerate(texts, 1)])


class Normalise(unittest.TestCase):
    def test_case_accents_and_punctuation_are_removed(self):
        self.assertEqual(normalise("Der Wärme-Term, also H800!"), ["der", "warme", "term", "also", "h800"])
        self.assertEqual(normalise(""), [])


class Compare(unittest.TestCase):
    def test_an_identical_transcript_has_no_error(self):
        result = compare("Wir vergleichen zwei Sätze.", "wir vergleichen zwei satze")
        self.assertEqual(result["word_error_rate"], 0.0)
        self.assertEqual((result["missing"], result["inserted"]), ([], []))

    def test_a_dropped_sentence_is_reported_with_its_words(self):
        spoken = "Der erste Satz steht hier. Der zweite Satz erklärt den Grund."
        result = compare(spoken, "Der erste Satz steht hier.")
        self.assertEqual(result["missing"], ["der", "zweite", "satz", "erklart", "den", "grund"])
        self.assertEqual(result["inserted"], [])
        self.assertGreater(result["word_error_rate"], FLAG_WORD_ERROR_RATE)

    def test_an_inserted_word_is_reported_separately(self):
        result = compare("Zwei Sätze.", "Zwei ganze Sätze.")
        self.assertEqual(result["inserted"], ["ganze"])
        self.assertEqual(result["missing"], [])

    def test_empty_expected_text_never_divides_by_zero(self):
        self.assertEqual(compare("", "")["word_error_rate"], 0.0)
        self.assertEqual(compare("", "etwas")["word_error_rate"], 1.0)


class CheckSegments(unittest.TestCase):
    def setUp(self):
        self.script = script("Der erste Satz steht hier. Der zweite Satz erklärt den Grund.",
                             "Und hier folgt die Antwort.")

    def test_a_deliberately_dropped_sentence_is_flagged(self):
        report = check_segments(self.script, {}, {"seg_001": "Der erste Satz steht hier.",
                                                  "seg_002": "Und hier folgt die Antwort."})
        self.assertEqual(report["flagged_segment_ids"], ["seg_001"])
        self.assertIn("erklart", report["segments"][0]["missing"])
        self.assertFalse(report["segments"][1]["flagged"])
        # It flags; it never blocks, and it never claims a person listened.
        self.assertFalse(report["blocking"])
        self.assertFalse(report["human_listening_reviewed"])

    def test_the_spoken_form_is_compared_not_the_script_text(self):
        episode = script("Wir rechnen auf H800.")
        heard = {"seg_001": "Wir rechnen auf Ha achthundert."}
        self.assertTrue(check_segments(episode, {}, heard)["segments"][0]["flagged"])
        spoken = {"seg_001": "Wir rechnen auf Ha achthundert."}
        self.assertFalse(check_segments(episode, spoken, heard)["segments"][0]["flagged"])

    def test_a_segment_without_a_transcript_is_recorded_as_such(self):
        report = check_segments(self.script, {}, {"seg_001": self.script.segments[0].text})
        self.assertEqual(report["segments"][1]["status"], "not_transcribed")
        self.assertIsNone(report["segments"][1]["word_error_rate"])
        self.assertEqual(report["flagged_segment_ids"], [])

    def test_the_recogniser_is_supplied_by_the_caller(self):
        calls = []
        transcripts = transcribe_segments({"seg_001": "a.wav", "seg_002": "b.wav"},
                                          lambda path: calls.append(path) or f"text of {path}")
        self.assertEqual(calls, ["a.wav", "b.wav"])
        self.assertEqual(transcripts["seg_002"], "text of b.wav")


if __name__ == "__main__":
    unittest.main()
