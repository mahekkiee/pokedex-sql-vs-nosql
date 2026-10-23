from django.test import SimpleTestCase
from pymongo.errors import DuplicateKeyError, WriteError

from core.mongo import get_db
from core.testing import ApiContractTests, MongoTestMixin
from core.utils import utc_now
from pokeapi_client.testdata import load_sample_records

from . import repository
from .importer import import_pokemon, to_document
from .schema import POKEMON, TEAMS, ensure_schema


def reset_collections():
    db = get_db()
    db.drop_collection(POKEMON)
    db.drop_collection(TEAMS)
    ensure_schema(db)


class MongoApiContractTests(MongoTestMixin, ApiContractTests, SimpleTestCase):
    """The shared API tests, run against MongoDB. SimpleTestCase: no SQL allowed here."""

    prefix = "/api/mongo/"
    backend = "mongodb"

    def load_sample_data(self, records):
        reset_collections()
        import_pokemon(records)


class MongoSchemaTests(MongoTestMixin, SimpleTestCase):
    def setUp(self):
        reset_collections()
        self.records = load_sample_records()
        import_pokemon(self.records)
        self.db = get_db()

    def test_one_document_per_pokemon(self):
        self.assertEqual(self.db[POKEMON].count_documents({}), len(self.records))
        bulbasaur = self.db[POKEMON].find_one({"_id": 1})
        self.assertEqual(bulbasaur["types"], ["grass", "poison"])
        self.assertEqual(bulbasaur["stats"]["attack"], 49)

    def test_reimport_does_not_duplicate(self):
        import_pokemon(self.records)
        self.assertEqual(self.db[POKEMON].count_documents({}), len(self.records))

    def test_validator_rejects_a_document_without_a_name(self):
        bad = to_document(dict(self.records[0], id=9001))
        del bad["name"]
        with self.assertRaises(WriteError):
            self.db[POKEMON].insert_one(bad)

    def test_validator_rejects_wrong_types(self):
        bad = to_document(dict(self.records[0], id=9002, name="stringy"))
        bad["stats"]["attack"] = "very high"
        with self.assertRaises(WriteError):
            self.db[POKEMON].insert_one(bad)

    def test_validator_rejects_a_team_of_seven(self):
        now = utc_now()
        members = [{"slot": 1, "pokemon_id": 1, "nickname": ""}] * 7
        with self.assertRaises(WriteError):
            self.db[TEAMS].insert_one({"name": "Seven", "members": members, "created_at": now, "updated_at": now})

    def test_unique_name_index(self):
        duplicate = to_document(dict(self.records[0], id=9003))  # same name as bulbasaur
        with self.assertRaises(DuplicateKeyError):
            self.db[POKEMON].insert_one(duplicate)

    def test_indexes_exist(self):
        names = set(self.db[POKEMON].index_information())
        self.assertTrue({"name_unique", "types", "attack_desc_id", "total_stats_desc_id"} <= names)

    def test_no_foreign_keys_a_deleted_pokemon_leaves_a_gap(self):
        team = repository.create_team({"name": "Gap", "members": [{"pokemon": 25, "nickname": ""}]})
        self.db[POKEMON].delete_one({"_id": 25})  # MongoDB allows this; PostgreSQL would refuse
        self.assertIsNone(repository.get_team(team["id"])["members"][0]["pokemon"])
