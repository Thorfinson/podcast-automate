"""The deterministic corpus probe over declared research gaps.

Model-free throughout: the probe is the existing lexical reader search, so these tests use a
small hand-built index and the real ``SourceReader``.
"""
import unittest
from types import SimpleNamespace

from podcast_automate.research_gap_probe import (coverage_terms, gap_id, key_terms, probe, settle, statuses,
                                                 suffix, unread)
from podcast_automate.research_models import QuestionCoverage, SourceDocument, SourceIndex, SourceSection
from podcast_automate.research_reader import SourceReader

# The passage the September 2026 audit found in a stored section while ep_003 told the
# listener the rule was missing from the sources.
V3_BIAS_RULE = ("At the end of each step, we will decrease the bias term by γ if its corresponding "
                "expert is overloaded, and increase it by γ if its corresponding expert is underloaded, "
                "where γ is a hyper-parameter called bias update speed.")
UNRELATED = ("The minimum deployment unit of the prefilling stage consists of four nodes with "
             "thirty-two accelerators sharing one attention partition.")
# The gap of finding f13 as the sample dossier states it: German words over an English corpus.
F13_GAP = ("Die genaue V3-Regel für den Auslastungsausgleich und der vollständige Berechnungsweg "
           "der zusätzlichen Vorhersagemodule fehlen.")
# What a composing model is asked to add to such a row: search words in the sources' language.
F13_TERMS = ["expert", "load", "balancing", "bias", "rule", "overloaded"]
# The same gap in the corpus's language, sharing whole words with the section.
BIAS_GAP = "The exact rule that updates the bias term of an overloaded expert is missing from the excerpts."


def index(*sections, source_id="src_v3", url="https://example.org/v3"):
    return SourceIndex(sources=[SourceDocument(
        id=source_id, type="html", title="DeepSeek-V3 Technical Report", authors=[], published_date="2024-12-27",
        imported_at="2026-09-19T00:00:00+00:00", url=url, final_url=url, reliability_note="", uncertainties=[],
        raw_path="sources/v3.html", raw_hash="0" * 64, text_hash="1" * 64,
        sections=[SourceSection(id=f"sec_{i:03d}", text=text) for i, text in enumerate(sections, 1)])],
        failures=[])


class KeyTerms(unittest.TestCase):
    def test_distinctive_words_come_first_and_stop_words_are_dropped(self):
        found = key_terms("Welche genaue Steuerungsregel verteilt in V3 die Expertenlast?")
        self.assertEqual(found[:3], ["steuerungsregel", "expertenlast", "verteilt"])
        self.assertNotIn("die", found)
        self.assertNotIn("v3", found)

    def test_german_function_words_are_not_key_terms(self):
        found = key_terms("Welche Regel gilt hier für sie, wenn dabei unter ihren Experten dann dort "
                          "nach Auslastung verteilt wird?")
        for word in ("welche", "hier", "fur", "sie", "wenn", "dabei", "unter", "ihren", "dann", "dort", "nach", "wird"):
            self.assertNotIn(word, found)
        self.assertEqual(set(found) & {"regel", "experten", "auslastung", "verteilt"},
                         {"regel", "experten", "auslastung", "verteilt"})

    def test_the_list_is_bounded_and_stable(self):
        text = " ".join("begriffsbildung" + "abcdefghijklmnopqrstuvwxyz"[i] for i in range(20))
        self.assertEqual(len(key_terms(text)), 12)
        self.assertEqual(key_terms(text), key_terms(text))


class Probe(unittest.TestCase):
    def test_a_gap_whose_answer_stands_in_a_stored_section_is_reported_unread(self):
        rows = probe(index(UNRELATED, V3_BIAS_RULE), {"gap_one": BIAS_GAP})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "hits_unread")
        self.assertEqual([hit["reference"] for hit in rows[0]["hits"]], ["src_v3#sec_002"])
        self.assertGreaterEqual(rows[0]["hits"][0]["key_term_matches"], 2)
        self.assertIn("decrease the bias term", rows[0]["hits"][0]["preview"])

    def test_a_gap_with_no_lexical_overlap_reports_no_hits(self):
        rows = probe(index(UNRELATED, V3_BIAS_RULE),
                     {"gap_two": "Which controlled comparisons separate tokenizer choices from curriculum design?"})
        self.assertEqual(rows[0]["status"], "no_hits")
        self.assertEqual(rows[0]["hits"], [])

    def test_a_single_matching_key_term_is_not_a_hit(self):
        rows = probe(index(V3_BIAS_RULE), {"gap_three": "Which dataset shaped the expert vocabulary?"})
        self.assertEqual(rows[0]["status"], "no_hits")

    def test_the_hit_list_is_bounded(self):
        rows = probe(index(*[V3_BIAS_RULE] * 9), {"gap_four": "The bias term update rule for an overloaded expert is missing."},
                     limit=3)
        self.assertEqual(len(rows[0]["hits"]), 3)

    def test_a_german_gap_over_an_english_corpus_alone_finds_nothing(self):
        """A term-overlap probe cannot cross languages; this is the documented limit, not a pass.

        The audit's own case has exactly this shape: the German gap text of finding f13 against
        the English V3 report shares no whole word with the section holding the bias rule.
        """
        rows = probe(index(UNRELATED, V3_BIAS_RULE), {"gap_f13": F13_GAP})
        self.assertEqual(rows[0]["status"], "no_hits")

    def test_gap_terms_in_the_corpus_language_find_the_section(self):
        """The same gap with the coverage row's ``gap_terms`` reaches the section: three of the
        six supplied words stand in it as whole tokens (expert, overloaded, bias)."""
        rows = probe(index(UNRELATED, V3_BIAS_RULE), {"gap_f13": F13_GAP}, gap_terms={"gap_f13": F13_TERMS})
        self.assertEqual(rows[0]["status"], "hits_unread")
        self.assertEqual([hit["reference"] for hit in rows[0]["hits"]], ["src_v3#sec_002"])
        self.assertEqual(rows[0]["hits"][0]["key_term_matches"], 3)
        self.assertEqual(rows[0]["key_terms"][:6], F13_TERMS)
        # The German words are still there, after the supplied ones.
        self.assertIn("auslastungsausgleich", rows[0]["key_terms"])

    def test_the_same_gap_in_the_corpus_language_is_caught(self):
        english = ("The exact rule that updates the bias term of an overloaded expert in V3 is missing, and so "
                   "is the constraint that stabilises the extended residual connections.")
        rows = probe(index(UNRELATED, V3_BIAS_RULE), {"gap_f13": english})
        self.assertEqual(rows[0]["status"], "hits_unread")
        self.assertEqual([hit["reference"] for hit in rows[0]["hits"]], ["src_v3#sec_002"])

    def test_key_terms_count_whole_tokens_only(self):
        """The reader's ranking credits "rule" inside "overruled" and "load" inside "download";
        the probe's threshold must not, or every long word would carry a gap past it."""
        corpus = index("The download was overruled by the committee.")
        gap = "The rule that balances the load is missing."
        ranked = SourceReader(corpus).search(gap, key_terms=["rule", "load"])["candidates"]
        self.assertEqual([c["key_term_matches"] for c in ranked], [2])
        rows = probe(corpus, {"gap_five": gap})
        self.assertEqual(rows[0]["status"], "no_hits")
        self.assertEqual(rows[0]["hits"], [])


class CoverageTerms(unittest.TestCase):
    def test_terms_are_keyed_like_the_gaps_and_answered_rows_carry_none(self):
        dossier = SimpleNamespace(coverage=[
            QuestionCoverage(question_id="q1", status="answered", finding_ids=["f1"], gap=""),
            QuestionCoverage(question_id="q4", status="partial", finding_ids=["f13"], gap=F13_GAP, gap_terms=F13_TERMS)])
        self.assertEqual(coverage_terms(dossier), {gap_id(F13_GAP): F13_TERMS})
        self.assertTrue(gap_id(F13_GAP).startswith("gap_"))

    def test_a_coverage_row_written_before_the_field_existed_still_loads(self):
        row = QuestionCoverage.model_validate({"question_id": "q4", "status": "partial", "finding_ids": [], "gap": F13_GAP})
        self.assertEqual(row.gap_terms, [])
        self.assertEqual(coverage_terms(SimpleNamespace(coverage=[row])), {})
        # And such a gap is probed by its text alone.
        rows = probe(index(UNRELATED, V3_BIAS_RULE), {gap_id(F13_GAP): F13_GAP}, gap_terms=coverage_terms(SimpleNamespace(coverage=[row])))
        self.assertEqual(rows[0]["status"], "no_hits")


class Settling(unittest.TestCase):
    def setUp(self):
        self.row = probe(index(UNRELATED, V3_BIAS_RULE), {"gap_one": BIAS_GAP})[0]

    def test_reading_every_hit_confirms_a_gap_that_still_stands(self):
        settled = settle(self.row, read_refs=["src_v3#sec_002"])
        self.assertEqual(settled["status"], "hits_read_confirmed")
        self.assertEqual(settled["unread_references"], [])
        self.assertEqual(unread([settled]), [])

    def test_a_partially_read_gap_keeps_blocking_and_names_what_is_left(self):
        settled = settle(self.row, read_refs=["src_v3#sec_001"])
        self.assertEqual(settled["status"], "hits_unread")
        self.assertEqual(settled["unread_references"], ["src_v3#sec_002"])
        self.assertEqual(len(unread([settled])), 1)

    def test_an_answered_gap_is_resolved_whatever_was_read(self):
        self.assertEqual(settle(self.row, resolved=True)["status"], "resolved")

    def test_a_gap_without_hits_never_becomes_unread(self):
        empty = {**self.row, "hits": [], "status": "no_hits"}
        self.assertEqual(settle(empty)["status"], "no_hits")

    def test_the_review_payload_carries_statuses_without_previews(self):
        rows = statuses([self.row])
        self.assertEqual(rows, [{"gap_id": "gap_one", "text": self.row["text"], "status": "hits_unread",
                                 "references": ["src_v3#sec_002"]}])
        self.assertNotIn("preview", str(rows))

    def test_every_status_has_a_readable_sentence(self):
        for status in ("no_hits", "hits_unread", "hits_unowned", "hits_read_confirmed", "resolved"):
            with self.subTest(status=status):
                line = suffix({**self.row, "status": status})
                self.assertTrue(line.startswith("Korpusprobe:"))
        self.assertIn("keine Folge nutzt", suffix({**self.row, "status": "hits_unowned"}))


if __name__ == "__main__":
    unittest.main()
