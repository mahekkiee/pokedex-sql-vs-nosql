import json
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from core.mongo import get_db
from core.testing import MongoTestMixin
from mongo_api.importer import import_pokemon as import_mongo
from mongo_api.schema import POKEMON, TEAMS, ensure_schema
from pokeapi_client.testdata import load_sample_records
from sql_api.importer import import_pokemon as import_sql
from sql_api.models import Pokemon

from .benchmark import SYNTHETIC_ID_OFFSET, load_latest, run_benchmark, save_results
from .datamodel import code_size_report

PARITY_URLS = [
    "pokemon/",
    "pokemon/?type=water",
    "pokemon/?type=fire&min_attack=60&ordering=-attack",
    "pokemon/?search=a&ordering=-name",
    "pokemon/?ordering=-total_stats&limit=3&offset=1",
    "pokemon/1/",
    "pokemon/gengar/",
    "types/",
    "stats/by-type/",
    "stats/top/?stat=total_stats&limit=5",
    "stats/top/?stat=special_defense",
    "teams/",
]


class BothBackendsTestCase(MongoTestMixin, TestCase):
    def setUp(self):
        db = get_db()
        db.drop_collection(POKEMON)
        db.drop_collection(TEAMS)
        ensure_schema(db)
        self.records = load_sample_records()
        import_sql(self.records)
        import_mongo(self.records)


class ParityTests(BothBackendsTestCase):
    """The two APIs must return identical JSON for identical requests."""

    def test_read_endpoints_are_identical(self):
        for url in PARITY_URLS:
            with self.subTest(url=url):
                sql = self.client.get("/api/sql/" + url, HTTP_ACCEPT="application/json")
                mongo = self.client.get("/api/mongo/" + url, HTTP_ACCEPT="application/json")
                self.assertEqual(sql.status_code, 200)
                self.assertEqual(sql.json(), mongo.json())

    def test_team_responses_match_apart_from_id_and_timestamps(self):
        body = {"name": "Parity", "members": [{"pokemon": 6, "nickname": "Zard"}, {"pokemon": 130}]}
        results = []
        for prefix in ("/api/sql/", "/api/mongo/"):
            data = self.client.post(prefix + "teams/", body, content_type="application/json").json()
            for key in ("id", "created_at", "updated_at"):
                data.pop(key)
            results.append(data)
        self.assertEqual(results[0], results[1])


class BenchmarkTests(BothBackendsTestCase):
    def test_small_benchmark_run_produces_results_and_cleans_up(self):
        results = run_benchmark(iterations=3, warmup=1, scale=2, progress=lambda *_: None)
        self.assertEqual(results["pokemon_count"], {"postgresql": 16, "mongodb": 16})
        keys = [row["key"] for row in results["layers"]["db"]]
        self.assertIn("avg_stats_per_type", keys)
        self.assertIn("bulk_upsert", keys)
        self.assertNotIn("bulk_upsert", [row["key"] for row in results["layers"]["api"]])
        for row in results["layers"]["db"]:
            self.assertIn(row["faster"], ("postgresql", "mongodb"))
            self.assertGreater(row["postgresql"]["median_ms"], 0)
        # synthetic copies and benchmark teams are removed afterwards
        self.assertFalse(Pokemon.objects.filter(id__gte=SYNTHETIC_ID_OFFSET).exists())
        self.assertEqual(get_db()[POKEMON].count_documents({"_id": {"$gte": SYNTHETIC_ID_OFFSET}}), 0)
        self.assertEqual(get_db()[TEAMS].count_documents({}), 0)

        with tempfile.TemporaryDirectory() as tmp:
            path = save_results(results, Path(tmp))
            self.assertEqual(json.loads(path.read_text())["scale"], 2)
            self.assertEqual(load_latest(Path(tmp))["iterations"], 3)

    def test_code_size_report_counts_both_sides(self):
        report = code_size_report()
        self.assertGreater(report["postgresql"]["total"], 0)
        self.assertGreater(report["mongodb"]["total"], 0)


class PageTests(BothBackendsTestCase):
    def test_dashboard_without_results_explains_what_to_run(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(BENCHMARK_RESULTS_DIR=Path(tmp)):
            response = self.client.get("/compare/")
        self.assertContains(response, "run_benchmark")

    def test_dashboard_with_results(self):
        results = run_benchmark(iterations=2, warmup=1, progress=lambda *_: None)
        with tempfile.TemporaryDirectory() as tmp, override_settings(BENCHMARK_RESULTS_DIR=Path(tmp)):
            save_results(results, Path(tmp))
            for layer in ("db", "api"):
                response = self.client.get(f"/compare/?layer={layer}")
                self.assertContains(response, "Average stats per type")

    def test_query_inspector_shows_sql_and_mongo_commands(self):
        response = self.client.get("/compare/queries/")
        self.assertContains(response, "SELECT")
        self.assertContains(response, "aggregate")
        self.assertContains(response, "$lookup")

    def test_data_model_page(self):
        response = self.client.get("/compare/model/")
        self.assertContains(response, "sql_api_pokemontype")
        self.assertContains(response, "$jsonSchema")
        self.assertContains(response, "bulbasaur")
