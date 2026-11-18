import io
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from streetlab import geocoding as geo


class GeocodingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.config = self.folder / "config.json"
        self.config.write_text(json.dumps({"endpoint": geo.ENDPOINT, "usage": "non-commercial"}))
        self.cache = self.folder / "cache.sqlite3"

    def search(self, name="プラハ", **kwargs):
        return geo.search_locations(name, config_path=self.config, cache_path=self.cache, **kwargs)

    def response(self, results):
        return io.BytesIO(json.dumps({"results": results}).encode())

    def candidate(self, **updates):
        return {"id": 1, "name": "Prague", "latitude": 50.08, "longitude": 14.43,
                "elevation": 0, "timezone": "Europe/Prague", **updates}

    def test_unicode_country_cache_and_provenance(self):
        with patch.object(geo, "urlopen", return_value=self.response([self.candidate()])) as get:
            first = self.search(country="cz")
            second = self.search(country="CZ")
        get.assert_called_once()
        query = parse_qs(urlsplit(get.call_args.args[0].full_url).query)
        self.assertEqual(query["name"], ["プラハ"])
        self.assertEqual(query["countryCode"], ["CZ"])
        self.assertFalse(first["from_cache"])
        self.assertTrue(second["from_cache"])
        self.assertEqual(second["results"][0]["elevation"], 0)
        self.assertEqual(second["attribution"]["data_source"], "GeoNames")

    def test_ambiguous_candidates_preserved_and_missing_elevation_not_zero(self):
        candidate = self.candidate()
        candidate.pop("elevation")
        with patch.object(geo, "urlopen", return_value=self.response([candidate, self.candidate(id=2)])):
            result = self.search()
        self.assertEqual(len(result["results"]), 2)
        self.assertNotIn("elevation", result["results"][0])

    def test_empty_result_is_cached(self):
        with patch.object(geo, "urlopen", return_value=self.response([])) as get:
            self.assertEqual(self.search()["results"], [])
            self.assertTrue(self.search()["from_cache"])
            get.assert_called_once()

    def test_failed_request_counts_and_is_not_retried(self):
        with patch.object(geo, "urlopen", side_effect=HTTPError(geo.ENDPOINT, 429, "limit", {}, None)) as get:
            with self.assertRaises(HTTPError):
                self.search()
            get.assert_called_once()
        with closing(sqlite3.connect(self.cache)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cache").fetchone()[0], 0)

    def test_quota_blocks_before_request(self):
        with patch.object(geo, "urlopen", return_value=self.response([])):
            self.search()
        now = geo.time.time()
        with closing(sqlite3.connect(self.cache)) as db, db:
            db.executemany("INSERT INTO attempts VALUES (?)", [(now,)] * 499)
        with patch.object(geo, "urlopen") as get:
            with self.assertRaises(RuntimeError):
                self.search("Berlin")
            get.assert_not_called()

    def test_invalid_request_and_response_rejected(self):
        with patch.object(geo, "urlopen") as get:
            for kwargs in [{"count": 0}, {"count": True}, {"country": "CZE"}]:
                with self.assertRaises(ValueError):
                    self.search(**kwargs)
            get.assert_not_called()
        with patch.object(geo, "urlopen", return_value=self.response([self.candidate(latitude=999)])):
            with self.assertRaises(ValueError):
                self.search()
        with closing(sqlite3.connect(self.cache)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM cache").fetchone()[0], 0)
