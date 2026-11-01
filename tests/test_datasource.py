"""Data layer tests: SQLite store + the caching remote client.

The remote client is tested entirely offline by swapping `urlopen`, because a
test suite that needs the network is a test suite you stop running.
"""

import io
import json
import os
import warnings
import sys
import tempfile
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datasource import campus_db, github_api  # noqa: E402

# The fake HTTPError objects used below carry a file handle that CPython
# closes during GC; that noise hides real failures in the summary line.
warnings.simplefilter("ignore", ResourceWarning)


class CampusStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = campus_db.open_db(self.tmp.name)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def test_seed_is_idempotent(self):
        before = self.conn.execute("SELECT COUNT(*) n FROM courses").fetchone()["n"]
        campus_db.seed(self.conn)
        after = self.conn.execute("SELECT COUNT(*) n FROM courses").fetchone()["n"]
        self.assertEqual(before, after)
        self.assertGreaterEqual(before, 10)

    def test_search_by_name_and_teacher(self):
        self.assertEqual(campus_db.search_courses(self.conn, "智能体")[0]["id"], "CS401")
        self.assertTrue(campus_db.search_courses(self.conn, "王敏"))
        self.assertEqual(campus_db.search_courses(self.conn, "不存在的课"), [])

    def test_search_returns_seats_left(self):
        row = campus_db.search_courses(self.conn, "CS401")[0]
        self.assertEqual(row["seats_left"], row["capacity"] - row["enrolled"])

    def test_grades_and_gpa(self):
        rows = campus_db.get_grades(self.conn, "2021001")
        self.assertEqual(len(rows), 6)
        summary = campus_db.compute_gpa(self.conn, "2021001")
        self.assertAlmostEqual(summary["credits"], 20.0)
        self.assertTrue(2.5 < summary["gpa"] <= 4.0)
        self.assertIsNone(campus_db.compute_gpa(self.conn, "9999999"))

    def test_full_course_rejects_enroll(self):
        # CS302 is seeded at capacity.
        with self.assertRaises(ValueError) as ctx:
            campus_db.apply_course_change(self.conn, "2021001", "CS302", "enroll")
        self.assertIn("已满", str(ctx.exception))

    def test_course_change_is_persisted(self):
        new_count = campus_db.apply_course_change(self.conn, "2021001", "CS401", "enroll")
        row = self.conn.execute("SELECT enrolled FROM courses WHERE id='CS401'").fetchone()
        self.assertEqual(row["enrolled"], new_count)
        logged = self.conn.execute("SELECT COUNT(*) n FROM course_changes").fetchone()["n"]
        self.assertEqual(logged, 1)


class FakeResponse:
    def __init__(self, payload, headers=None):
        self._body = json.dumps(payload).encode()
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class GithubCacheTest(unittest.TestCase):
    def setUp(self):
        self.original_cache = github_api.CACHE_PATH
        self.original_urlopen = github_api.urllib.request.urlopen
        handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        handle.close()
        self.temp_cache = handle.name
        github_api.CACHE_PATH = self.temp_cache
        self.calls = 0

    def tearDown(self):
        github_api.CACHE_PATH = self.original_cache
        github_api.urllib.request.urlopen = self.original_urlopen
        if os.path.exists(self.temp_cache):
            os.unlink(self.temp_cache)

    def _patch(self, behaviour):
        def fake(request_obj, timeout=None):
            self.calls += 1
            return behaviour(request_obj)
        github_api.urllib.request.urlopen = fake

    def test_second_call_hits_cache(self):
        payload = {"total_count": 1, "items": [{"number": 7, "title": "t", "state": "open",
                                                "labels": [], "user": {"login": "x"}}]}
        self._patch(lambda req: FakeResponse(payload, {"ETag": '"abc"', "X-RateLimit-Remaining": "42"}))
        first = github_api.search_issues("cache me", limit=1, ttl=600)
        second = github_api.search_issues("cache me", limit=1, ttl=600)
        self.assertEqual(self.calls, 1, "第二次不该再打网络")
        self.assertEqual(first["_meta"]["cache"], "miss")
        self.assertEqual(second["_meta"]["cache"], "hit")
        self.assertEqual(second["total"], 1)

    def test_304_revalidates_without_refetching_body(self):
        payload = {"total_count": 0, "items": []}
        self._patch(lambda req: FakeResponse(payload, {"ETag": '"v1"'}))
        github_api.search_issues("etag", limit=1, ttl=0)

        def not_modified(request_obj):
            raise urllib.error.HTTPError(
                "u", 304, "Not Modified", {"ETag": '"v1"'}, io.BytesIO(b""))

        self._patch(not_modified)
        again = github_api.search_issues("etag", limit=1, ttl=0)
        self.assertEqual(again["_meta"]["cache"], "revalidated")

    def test_offline_falls_back_to_stale_cache(self):
        payload = {"total_count": 2, "items": []}
        self._patch(lambda req: FakeResponse(payload, {"ETag": '"v2"'}))
        github_api.search_issues("stale", limit=1, ttl=0)

        def offline(request_obj):
            raise urllib.error.URLError("no network")

        self._patch(offline)
        degraded = github_api.search_issues("stale", limit=1, ttl=0)
        self.assertEqual(degraded["_meta"]["cache"], "stale")
        self.assertTrue(degraded["_meta"]["stale"])
        self.assertEqual(degraded["total"], 2)

    def test_offline_without_cache_raises(self):
        def offline(request_obj):
            raise urllib.error.URLError("no network")

        self._patch(offline)
        with self.assertRaises(github_api.ExternalApiError) as ctx:
            github_api.search_issues("never seen", limit=1)
        self.assertIn("无缓存", str(ctx.exception))

    def test_rate_limit_403_reports_reason(self):
        def limited(request_obj):
            raise urllib.error.HTTPError(
                "u", 403, "rate limited",
                {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "999"}, io.BytesIO(b""))

        self._patch(limited)
        with self.assertRaises(github_api.ExternalApiError):
            github_api.search_issues("limit", limit=1)

    def test_write_requires_confirm(self):
        with self.assertRaises(github_api.ExternalApiError):
            github_api.create_comment(1, "hi", confirm=False)

    def test_write_dry_run_does_not_touch_network(self):
        result = github_api.create_comment(1, "hi", confirm=True, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.calls, 0)


if __name__ == "__main__":
    unittest.main()
