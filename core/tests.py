from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from .testing import MongoTestMixin
from .utils import iso


def both_apis_routed():
    """True once /api/sql/ and /api/mongo/ both exist (later commits add them)."""
    try:
        reverse("sql_api:root")
        reverse("mongo_api:root")
        return True
    except NoReverseMatch:
        return False


class UtilsTests(TestCase):
    def test_iso_rounds_to_milliseconds_and_uses_z(self):
        from datetime import datetime, timezone
        value = datetime(2026, 10, 23, 9, 30, 15, 123456, tzinfo=timezone.utc)
        self.assertEqual(iso(value), "2026-10-23T09:30:15.123Z")
        self.assertEqual(iso(value.replace(tzinfo=None)), "2026-10-23T09:30:15.123Z")


class PageTests(MongoTestMixin, TestCase):
    def test_home_page_shows_both_databases(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PostgreSQL")
        self.assertContains(response, "MongoDB")

    def test_health_reports_both_connections(self):
        data = self.client.get("/api/health/", HTTP_ACCEPT="application/json").json()
        self.assertTrue(data["postgresql"]["ok"])
        self.assertTrue(data["mongodb"]["ok"])

    def test_api_index_links_to_both_backends(self):
        if not both_apis_routed():
            self.skipTest("/api/sql/ and /api/mongo/ are not both routed yet")
        data = self.client.get("/api/", HTTP_ACCEPT="application/json").json()
        self.assertTrue(data["postgresql"].endswith("/api/sql/"))
        self.assertTrue(data["mongodb"].endswith("/api/mongo/"))

    def test_backend_roots_list_endpoints(self):
        if not both_apis_routed():
            self.skipTest("/api/sql/ and /api/mongo/ are not both routed yet")
        for prefix, backend in (("/api/sql/", "postgresql"), ("/api/mongo/", "mongodb")):
            data = self.client.get(prefix, HTTP_ACCEPT="application/json").json()
            self.assertEqual(data["backend"], backend)
            self.assertIn("pokemon-list", data["endpoints"])
