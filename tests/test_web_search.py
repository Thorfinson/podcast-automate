import io
import json
import unittest
from urllib.error import HTTPError, URLError

from podcast_automate.errors import AppError
from podcast_automate.web_search import ENDPOINT, USD_PER_REQUEST, PerplexitySearch

KEY = "pplx-test-key-0123456789abcdef"


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Stands in for the no-redirect opener; records each request, answers with ``reply`` or raises it."""

    def __init__(self, reply):
        self.reply, self.requests = reply, []

    def open(self, request, timeout):
        self.requests.append(request)
        if isinstance(self.reply, BaseException):
            raise self.reply
        return Response(json.dumps(self.reply).encode("utf-8"))


class PerplexitySearchTests(unittest.TestCase):
    """The Perplexity Search API as the pipeline calls it (D-151); no request leaves the machine in these tests."""

    def test_a_request_names_the_queries_and_carries_the_key_only_in_its_header(self):
        opener = Opener({"id": "r1", "results": [
            {"title": " Paper  one ", "url": "https://example.org/a", "snippet": "first", "date": "2025-01-02"},
            {"title": "Same again", "url": "https://example.org/a", "snippet": "duplicate"},
            {"title": "No address"}, {"title": "Mail", "url": "mailto:someone@example.org"},
            {"title": "Paper two", "url": "https://example.org/b", "snippet": "x" * 3000}]})
        found = PerplexitySearch(KEY, opener=opener).search(["energy  models", "critique of energy models", " "],
                                                           languages=["en"])
        request = opener.requests[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual((request.full_url, request.get_method()), (ENDPOINT, "POST"))
        self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
        self.assertNotIn(KEY, request.full_url + request.data.decode("utf-8"))
        self.assertEqual((body["query"], body["max_results"], body["search_language_filter"]),
                         (["energy models", "critique of energy models"], 10, ["en"]))
        self.assertEqual([row["url"] for row in found["results"]], ["https://example.org/a", "https://example.org/b"])
        self.assertEqual((found["results"][0]["title"], len(found["results"][1]["snippet"])), ("Paper one", 2000))
        self.assertEqual((found["usd"], found["request_id"]), (USD_PER_REQUEST, "r1"))

    def test_failures_get_codes_a_run_can_act_on(self):
        expectations = {401: ("perplexity_authentication", "blocked"), 402: ("perplexity_credits", "waiting_for_quota"),
                        429: ("perplexity_rate_limit", "waiting_for_quota"), 422: ("perplexity_request", "blocked"),
                        500: ("perplexity_failed", "failed")}
        for status, (code, state) in expectations.items():
            error = HTTPError(ENDPOINT, status, "x", {}, io.BytesIO(b"{}"))
            with self.subTest(status=status), self.assertRaises(AppError) as caught:
                PerplexitySearch(KEY, opener=Opener(error)).search(["q"])
            self.assertEqual((caught.exception.code, caught.exception.status), (code, state))
        with self.assertRaises(AppError) as caught:
            PerplexitySearch(KEY, opener=Opener(URLError("down"))).search(["q"])
        self.assertEqual(caught.exception.code, "perplexity_failed")
        with self.assertRaises(AppError) as caught:
            PerplexitySearch(KEY, opener=Opener({"unexpected": True})).search(["q"])
        self.assertEqual(caught.exception.code, "perplexity_failed")

    def test_no_key_a_key_in_a_query_or_in_the_answer_never_searches_or_is_kept(self):
        opener = Opener({"results": []})
        with self.assertRaises(AppError) as missing:
            PerplexitySearch("", opener=opener).search(["q"])
        self.assertEqual(missing.exception.code, "perplexity_key_required")
        with self.assertRaises(AppError) as leaked:
            PerplexitySearch(KEY, opener=opener).search(["find " + KEY])
        self.assertEqual(leaked.exception.code, "credential_in_prompt")
        self.assertEqual(opener.requests, [])
        with self.assertRaises(AppError) as echoed:
            PerplexitySearch(KEY, opener=Opener({"results": [{"url": "https://example.org/" + KEY}]})).search(["q"])
        self.assertEqual(echoed.exception.code, "credential_in_response")


if __name__ == "__main__":
    unittest.main()
