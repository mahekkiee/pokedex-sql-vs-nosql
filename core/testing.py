"""Test helpers shared by every app.

ApiContractTests is the most important piece: ONE set of tests that both
sql_api and mongo_api run. If both backends pass it, the two APIs really are
the same API. Expected values are computed in plain Python from the sample
records, so the tests do not trust either database.
"""
import unittest
from statistics import fmean

from django.test import override_settings

from .constants import STAT_FIELDS
from .mongo import get_client, mongo_is_available

TEST_MONGO_DB = "pokedex_test"


class MongoTestMixin:
    """Point MongoDB at a throwaway database for the duration of a test class.

    Django creates a separate test database for PostgreSQL automatically.
    MongoDB is outside the ORM, so we do the same thing by hand: switch
    MONGO_DB_NAME to "pokedex_test" and drop it when the tests finish.
    Tests are skipped (not failed) when MongoDB is not running.
    """

    @classmethod
    def setUpClass(cls):
        if not mongo_is_available():
            raise unittest.SkipTest("MongoDB is not running; start it with: docker compose up -d")
        cls._mongo_settings = override_settings(MONGO_DB_NAME=TEST_MONGO_DB)
        cls._mongo_settings.enable()
        get_client().drop_database(TEST_MONGO_DB)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        get_client().drop_database(TEST_MONGO_DB)
        cls._mongo_settings.disable()


class ApiContractTests:
    """Mix into a TestCase and set `prefix` and `load_sample_data()`."""

    prefix = ""  # "/api/sql/" or "/api/mongo/"
    backend = ""

    def load_sample_data(self, records):  # pragma: no cover - implemented by subclasses
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        from pokeapi_client.testdata import load_sample_records  # imported here so core has no hard dependency

        self.records = load_sample_records()
        self.by_id = {r["id"]: r for r in self.records}
        self.load_sample_data(self.records)

    # helpers ---------------------------------------------------------------------------
    def get(self, path, expected_status=200):
        response = self.client.get(self.prefix + path, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, expected_status, response.content[:300])
        return response

    def names(self, response):
        return [p["name"] for p in response.json()["results"]]

    # pokemon list ----------------------------------------------------------------------
    def test_list_returns_every_pokemon_in_id_order(self):
        data = self.get("pokemon/").json()
        self.assertEqual(data["count"], len(self.records))
        self.assertEqual([p["id"] for p in data["results"]], sorted(self.by_id))
        self.assertEqual(set(data["results"][0]), {"id", "name", "types", "sprite_url", "total_stats"})

    def test_backend_header_and_timing(self):
        response = self.get("pokemon/")
        self.assertEqual(response["X-Backend"], self.backend)
        self.assertGreaterEqual(float(response["X-DB-Time-ms"]), 0)

    def test_filter_by_type(self):
        expected = [r["name"] for r in self.records if "fire" in r["types"]]
        self.assertEqual(self.names(self.get("pokemon/?type=fire")), expected)

    def test_search_is_case_insensitive(self):
        expected = [r["name"] for r in self.records if "char" in r["name"]]
        self.assertEqual(self.names(self.get("pokemon/?search=CHAR")), expected)

    def test_min_attack_with_descending_order(self):
        matching = [r for r in self.records if r["stats"]["attack"] >= 80]
        expected = [r["name"] for r in sorted(matching, key=lambda r: (-r["stats"]["attack"], r["id"]))]
        self.assertEqual(self.names(self.get("pokemon/?min_attack=80&ordering=-attack")), expected)

    def test_order_by_name(self):
        expected = sorted(r["name"] for r in self.records)
        self.assertEqual(self.names(self.get("pokemon/?ordering=name")), expected)

    def test_limit_and_offset(self):
        data = self.get("pokemon/?limit=3&offset=2").json()
        self.assertEqual([p["id"] for p in data["results"]], sorted(self.by_id)[2:5])
        self.assertEqual((data["count"], data["limit"], data["offset"]), (len(self.records), 3, 2))

    def test_bad_query_parameters_return_400(self):
        self.get("pokemon/?ordering=colour", expected_status=400)
        self.get("pokemon/?limit=0", expected_status=400)
        self.get("pokemon/?min_attack=-5", expected_status=400)

    # pokemon detail ----------------------------------------------------------------------
    def test_detail_by_id_and_by_name_match_source_data(self):
        pikachu = self.by_id[25]
        by_id = self.get("pokemon/25/").json()
        by_name = self.get("pokemon/PIKACHU/").json()
        self.assertEqual(by_id, by_name)
        self.assertEqual(by_id, pikachu)

    def test_unknown_pokemon_returns_404(self):
        self.get("pokemon/missingno/", expected_status=404)
        self.get("pokemon/99999/", expected_status=404)

    # aggregations ------------------------------------------------------------------------
    def test_type_counts(self):
        counts = {}
        for r in self.records:
            for t in r["types"]:
                counts[t] = counts.get(t, 0) + 1
        expected = [{"name": t, "pokemon_count": counts[t]} for t in sorted(counts)]
        self.assertEqual(self.get("types/").json(), expected)

    def test_average_stats_by_type(self):
        rows = {row["type"]: row for row in self.get("stats/by-type/").json()}
        fire = [r for r in self.records if "fire" in r["types"]]
        self.assertEqual(rows["fire"]["count"], len(fire))
        for field in STAT_FIELDS:
            self.assertEqual(rows["fire"][f"avg_{field}"], round(fmean(r["stats"][field] for r in fire), 2))
        self.assertEqual(rows["fire"]["avg_total_stats"], round(fmean(r["total_stats"] for r in fire), 2))

    def test_top_by_stat(self):
        expected = sorted(self.records, key=lambda r: (-r["stats"]["speed"], r["id"]))[:3]
        data = self.get("stats/top/?stat=speed&limit=3").json()
        self.assertEqual([p["name"] for p in data], [r["name"] for r in expected])
        self.assertEqual([p["value"] for p in data], [r["stats"]["speed"] for r in expected])
        self.get("stats/top/?stat=luck", expected_status=400)

    # teams ---------------------------------------------------------------------------------
    def test_team_create_read_update_delete(self):
        body = {"name": "Kanto starters", "members": [
            {"pokemon": 1, "nickname": "Bulby"}, {"pokemon": 4}, {"pokemon": 7},
        ]}
        created = self.client.post(self.prefix + "teams/", body, content_type="application/json")
        self.assertEqual(created.status_code, 201, created.content)
        team = created.json()
        self.assertEqual(team["name"], "Kanto starters")
        self.assertEqual([m["pokemon"]["name"] for m in team["members"]], ["bulbasaur", "charmander", "squirtle"])
        self.assertEqual([m["slot"] for m in team["members"]], [1, 2, 3])
        self.assertEqual(team["members"][0]["nickname"], "Bulby")

        url = f"{self.prefix}teams/{team['id']}/"
        self.assertEqual(self.client.get(url).json(), team)
        self.assertEqual(len(self.get("teams/").json()), 1)

        updated = self.client.put(url, {"name": "Mascot", "members": [{"pokemon": 25}]},
                                  content_type="application/json")
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()["name"], "Mascot")
        self.assertEqual([m["pokemon"]["id"] for m in updated.json()["members"]], [25])

        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)

    def test_team_validation(self):
        too_many = {"name": "Seven", "members": [{"pokemon": 1}] * 7}
        unknown = {"name": "Ghost", "members": [{"pokemon": 4242}]}
        nameless = {"members": []}
        for body in (too_many, unknown, nameless):
            response = self.client.post(self.prefix + "teams/", body, content_type="application/json")
            self.assertEqual(response.status_code, 400, body)
        self.assertEqual(self.get("teams/").json(), [])

    def test_unknown_team_id_returns_404(self):
        self.get("teams/not-an-id/", expected_status=404)
        self.get("teams/123456/", expected_status=404)
