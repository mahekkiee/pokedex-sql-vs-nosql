import tempfile
from unittest import mock

from django.test import SimpleTestCase

from .client import PokeAPIClient, PokeAPIError, slim_pokemon
from .normalize import normalize_pokemon
from .testdata import load_sample_raw, load_sample_records


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class NormalizeTests(SimpleTestCase):
    def test_bulbasaur_is_flattened(self):
        bulbasaur = normalize_pokemon(load_sample_raw()[0])
        self.assertEqual(bulbasaur["id"], 1)
        self.assertEqual(bulbasaur["name"], "bulbasaur")
        self.assertEqual(bulbasaur["types"], ["grass", "poison"])
        self.assertEqual(bulbasaur["stats"]["attack"], 49)
        self.assertEqual(bulbasaur["stats"]["special_attack"], 65)
        self.assertEqual(bulbasaur["total_stats"], 318)
        self.assertEqual(bulbasaur["abilities"][0], {"name": "overgrow", "is_hidden": False, "slot": 1})

    def test_every_sample_has_six_stats_and_types(self):
        for record in load_sample_records():
            self.assertEqual(len(record["stats"]), 6)
            self.assertGreaterEqual(len(record["types"]), 1)
            self.assertEqual(record["total_stats"], sum(record["stats"].values()))


class ClientCacheTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = load_sample_raw()[4]  # pikachu
        self.session = mock.Mock()
        self.session.headers = {}

    def make_client(self, **kwargs):
        return PokeAPIClient(cache_dir=self.tmp.name, delay=0, session=self.session, **kwargs)

    def test_second_call_comes_from_cache(self):
        self.session.get.return_value = FakeResponse(200, self.raw)
        client = self.make_client()
        first = client.get_pokemon(25)
        second = client.get_pokemon(25)
        self.assertEqual(first, second)
        self.assertEqual(self.session.get.call_count, 1)
        self.assertEqual(client.network_calls, 1)
        self.assertEqual(client.cache_hits, 1)

    def test_offline_mode_never_calls_the_network(self):
        client = self.make_client(offline=True)
        with self.assertRaises(PokeAPIError):
            client.get_pokemon(25)
        self.session.get.assert_not_called()

    @mock.patch("pokeapi_client.client.time.sleep")
    def test_429_waits_for_retry_after_then_succeeds(self, sleep):
        self.session.get.side_effect = [
            FakeResponse(429, headers={"Retry-After": "3"}),
            FakeResponse(200, self.raw),
        ]
        with self.assertLogs("pokeapi_client.client", "WARNING"):
            pokemon = self.make_client().get_pokemon(25)
        self.assertEqual(pokemon["name"], "pikachu")
        sleep.assert_any_call(3.0)

    def test_404_is_reported(self):
        self.session.get.return_value = FakeResponse(404)
        with self.assertRaises(PokeAPIError):
            self.make_client().get_pokemon(99999)

    def test_slim_drops_moves_but_keeps_what_we_use(self):
        raw = dict(self.raw, moves=[{"move": {"name": "thunder-shock"}}] * 100, game_indices=[1, 2])
        slim = slim_pokemon(raw)
        self.assertNotIn("moves", slim)
        self.assertNotIn("game_indices", slim)
        for key in ("id", "name", "types", "abilities", "stats", "sprites"):
            self.assertIn(key, slim)
