"""Jev as a second finder in the gap probe: the client, the cached scan and the merge. No request leaves a test."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from podcast_automate.errors import AppError
from podcast_automate.jev import DECISIONS_ENDPOINT, JEV_HITS, JEV_MODEL, JevClient, candidate_sections, scan
from podcast_automate.research_gap_probe import probe, settle
from tests.test_gap_probe import BIAS_GAP, F13_GAP, V3_BIAS_RULE, index

LONG_UNRELATED = ("The minimum deployment unit of the prefilling stage consists of four nodes with thirty-two "
                  "accelerators sharing one attention partition, and the decoding stage uses a larger unit of forty "
                  "nodes so that the routed experts can be spread over more devices.")
REFERENCES = "References\n" + "\n".join(f"[{n}] Some Author. A paper title. https://example.org/{n}" for n in range(5))


class Response:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return self.body


def refused(code, text="{}"):
    return HTTPError(DECISIONS_ENDPOINT, code, "refused", {}, io.BytesIO(text.encode()))


class ClientTests(unittest.TestCase):
    QUESTIONS = {"g0": {"type": "noul", "instructions": "i", "criteria": {"true": "t", "false": "f"}}}

    def test_a_request_carries_model_state_and_questions_and_the_key_only_in_its_header(self):
        sent = []

        def answer(request, timeout):
            sent.append(request)
            return Response({"answers": {"g0": {"type": "noul", "noul": 0.9}}, "usage": {"cost": 0.00002}})
        with patch("podcast_automate.jev.build_opener") as build:
            build.return_value.open.side_effect = answer
            answers, cost = JevClient("test-key").decide("A passage.", self.QUESTIONS)
        self.assertEqual((answers, cost), ({"g0": {"type": "noul", "noul": 0.9}}, 0.00002))
        request = sent[0]
        self.assertEqual(request.full_url, DECISIONS_ENDPOINT)
        self.assertEqual(json.loads(request.data), {"model": JEV_MODEL, "state": "A passage.", "questions": self.QUESTIONS})
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertNotIn(b"test-key", request.data)

    def test_a_rate_limit_is_asked_again_and_a_refusal_stops_with_its_reason(self):
        with patch("podcast_automate.jev.build_opener") as build, patch("podcast_automate.jev.time.sleep") as pause:
            build.return_value.open.side_effect = [refused(429), Response({"answers": {}, "usage": {"cost": 0}})]
            self.assertEqual(JevClient("test-key").decide("s", self.QUESTIONS), ({}, 0.0))
            self.assertEqual(pause.call_count, 1)
            for code, text, expected in ((401, "{}", "openrouter_key_required"), (402, "{}", "openrouter_credits"),
                                         (404, '{"error": "No endpoints: ZDR policy"}', "openrouter_privacy"),
                                         (400, "{}", "jev_unavailable")):
                build.return_value.open.side_effect = [refused(code, text)]
                with self.subTest(code=code), self.assertRaises(AppError) as stopped:
                    JevClient("test-key").decide("s", self.QUESTIONS)
                self.assertEqual(stopped.exception.code, expected)
            build.return_value.open.side_effect = [refused(503)] * 4
            with self.assertRaises(AppError) as silent:
                JevClient("test-key").decide("s", self.QUESTIONS)
            self.assertEqual(silent.exception.code, "jev_unavailable")

    def test_without_a_key_nothing_is_sent(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}), patch("podcast_automate.jev.build_opener") as build:
            with self.assertRaises(AppError) as missing:
                JevClient().decide("s", self.QUESTIONS)
        self.assertEqual(missing.exception.code, "openrouter_key_required")
        build.assert_not_called()


class Fake:
    """Jev as the tests need it: a probability per (section text fragment, gap text), 0.05 otherwise."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def require_key(self):
        pass

    def decide(self, state, questions):
        self.requests.append(sorted(questions))
        return {name: {"type": "noul", "noul": next((p for (fragment, gap), p in self.answers.items()
                                                    if fragment in state and gap in question["instructions"]), 0.05)}
                for name, question in questions.items()}, 0.0001


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp()) / "jev_scan.jsonl"
        self.corpus = index(LONG_UNRELATED, V3_BIAS_RULE, "Short heading", REFERENCES)

    def test_only_sections_that_can_hold_an_answer_are_asked(self):
        self.assertEqual([row[0] for row in candidate_sections(self.corpus)], ["src_v3#sec_001", "src_v3#sec_002"])

    def test_the_scan_batches_gaps_keeps_every_answer_and_resumes_without_asking_again(self):
        gaps = {f"gap_{n:02d}": f"missing thing number {n:02d}" for n in range(10)}
        gaps["gap_f13"] = F13_GAP
        client = Fake({("decrease the bias term", F13_GAP): 0.83})
        found, summary = scan(client, self.corpus, gaps, self.cache)
        self.assertEqual(len(client.requests), 4, "two sections, eleven gaps, eight to a request")
        self.assertEqual([(ref, p) for ref, _, p, _ in found["gap_f13"]], [("src_v3#sec_002", 0.83)])
        self.assertEqual(found["gap_00"], [], "below the threshold nothing is proposed")
        self.assertEqual((summary["sections"], summary["requests"], summary["model"]), (2, 4, JEV_MODEL))
        again, repeated = scan(Fake({}), self.corpus, gaps, self.cache)
        self.assertEqual((again, repeated["requests"]), (found, 0))
        # A new gap is the only thing asked on the next scan.
        newcomer = Fake({})
        scan(newcomer, self.corpus, {**gaps, "gap_new": "another missing thing"}, self.cache)
        self.assertEqual(newcomer.requests, [["g0"], ["g0"]])

    def test_above_the_ceiling_each_gap_is_asked_about_its_best_ranked_sections_only(self):
        """Orlagau, 2026-10-09: 81,010 sections and 33 gaps made 405,050 requests. Above MAX_REQUESTS a gap is asked
        about the sections the probe's words rank first, and a resumed scan proposes only what was asked."""
        corpus = index(V3_BIAS_RULE, *(f"{LONG_UNRELATED} Variant {n}." for n in range(3)))
        gaps = {"gap_bias": BIAS_GAP, "gap_unit": "The size of the minimum deployment unit for decoding is missing."}
        everything = Fake({("Variant 2", BIAS_GAP): 0.9, ("decrease the bias term", BIAS_GAP): 0.83})
        _, full = scan(everything, corpus, gaps, self.cache)
        self.assertEqual(full["requests"], 4, "four sections, two gaps in one request each")
        self.cache.unlink()
        client = Fake(everything.answers)
        with patch("podcast_automate.jev.MAX_REQUESTS", 2):
            found, summary = scan(client, corpus, gaps, self.cache)
        self.assertEqual((len(client.requests), summary["requests"], summary["max_requests"]), (2, 2, 2))
        self.assertEqual([ref for ref, *_ in found["gap_bias"]], ["src_v3#sec_001"],
                         "the bias section ranks first for its gap; the variant is not asked about it")
        # Answers cached by an unlimited scan propose nothing that the ceiling did not ask.
        scan(Fake(everything.answers), corpus, gaps, self.cache)
        with patch("podcast_automate.jev.MAX_REQUESTS", 2):
            resumed, again = scan(Fake({}), corpus, gaps, self.cache)
        self.assertEqual((resumed, again["requests"]), (found, 0))


class MergeTests(unittest.TestCase):
    def test_a_german_gap_the_words_miss_gets_jev_proposals_to_read(self):
        """The audit's own case: the term probe finds nothing (test_gap_probe), Jev names the section."""
        proposal = [("src_v3#sec_002", "DeepSeek-V3 Technical Report", 0.83, V3_BIAS_RULE[:400])]
        rows = probe(index(LONG_UNRELATED, V3_BIAS_RULE), {"gap_f13": F13_GAP}, proposals={"gap_f13": proposal})
        self.assertEqual(rows[0]["status"], "hits_unread")
        self.assertEqual([(hit["reference"], hit["via"], hit["probability"]) for hit in rows[0]["hits"]],
                         [("src_v3#sec_002", "jev", 0.83)])
        self.assertEqual(settle(rows[0], read_refs=["src_v3#sec_002"])["status"], "hits_read_confirmed")

    def test_proposals_the_words_found_are_not_repeated_and_stay_bounded(self):
        sections = [V3_BIAS_RULE] + [f"{LONG_UNRELATED} Variant {n}." for n in range(8)]
        proposals = [(f"src_v3#sec_{n:03d}", "t", 0.9 - n / 100, "p") for n in range(1, 10)]
        rows = probe(index(*sections), {"gap": "The rule that updates the bias term of an overloaded expert."},
                     proposals={"gap": proposals})
        hits = rows[0]["hits"]
        self.assertEqual(hits[0]["reference"], "src_v3#sec_001")
        self.assertNotIn("via", hits[0], "the words found it first")
        self.assertEqual([hit["reference"] for hit in hits[1:]], [f"src_v3#sec_{n:03d}" for n in range(2, 2 + JEV_HITS)])


if __name__ == "__main__":
    unittest.main()
