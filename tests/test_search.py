"""Tests for core/search.py (network mocked)."""
import unittest
from unittest import mock

from core import search
from core.search import SearchError, search_web


class SearchTests(unittest.TestCase):
    def test_maps_keys_and_filters_bad_urls(self):
        raw = [
            {"title": "One", "href": "https://one.test/a", "body": "first"},
            {"title": "Two", "url": "http://two.test/", "description": "second"},
            {"title": "Bad", "url": "javascript:alert(1)", "body": "x"},
            {"title": "NoScheme", "url": "//two.test/x", "body": "y"},
            "not-a-dict",
        ]
        with mock.patch.object(search, "_raw", return_value=raw):
            res = search_web("query", 8)
        self.assertEqual([r["url"] for r in res], ["https://one.test/a", "http://two.test/"])
        self.assertEqual(res[0], {"title": "One", "url": "https://one.test/a", "snippet": "first"})
        self.assertEqual(res[1]["snippet"], "second")

    def test_limit_capped_at_10(self):
        raw = [{"title": str(i), "url": f"https://x.test/{i}", "body": "b"} for i in range(30)]
        with mock.patch.object(search, "_raw", return_value=raw) as f:
            res = search_web("q", 99)
        self.assertEqual(len(res), 10)
        self.assertEqual(f.call_args[0][1], 10)

    def test_empty_query_raises(self):
        for q in ("", "   ", None):
            with self.assertRaises(SearchError):
                search_web(q)

    def test_backend_error_becomes_search_error(self):
        with mock.patch.object(search, "_raw", side_effect=RuntimeError("boom")):
            with self.assertRaises(SearchError) as cm:
                search_web("q")
        self.assertIn("unavailable", str(cm.exception))

    def test_no_results_is_empty_list(self):
        with mock.patch.object(search, "_raw", return_value=[]):
            self.assertEqual(search_web("q"), [])

    def test_results_capped_by_limit(self):
        raw = [{"title": str(i), "url": f"https://x.test/{i}", "body": "b"} for i in range(5)]
        with mock.patch.object(search, "_raw", return_value=raw):
            self.assertEqual(len(search_web("q", 3)), 3)


if __name__ == "__main__":
    unittest.main()
