from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from core.testing import ApiContractTests
from pokeapi_client.testdata import load_sample_records

from . import raw, repository
from .importer import import_pokemon
from .models import Ability, Pokemon, PokemonAbility, PokemonType, Team, TeamMember, Type


class SqlApiContractTests(ApiContractTests, TestCase):
    """The shared API tests, run against PostgreSQL."""

    prefix = "/api/sql/"
    backend = "postgresql"

    def load_sample_data(self, records):
        import_pokemon(records)


class SqlImporterTests(TestCase):
    def setUp(self):
        self.records = load_sample_records()
        import_pokemon(self.records)

    def test_rows_are_spread_over_normalised_tables(self):
        self.assertEqual(Pokemon.objects.count(), len(self.records))
        self.assertEqual(PokemonType.objects.count(), sum(len(r["types"]) for r in self.records))
        self.assertEqual(PokemonAbility.objects.count(), sum(len(r["abilities"]) for r in self.records))
        # each type name is stored once, however many Pokemon use it
        self.assertEqual(Type.objects.count(), len({t for r in self.records for t in r["types"]}))
        self.assertEqual(Ability.objects.count(), len({a["name"] for r in self.records for a in r["abilities"]}))

    def test_reimport_updates_in_place_and_keeps_teams(self):
        team = Team.objects.create(name="Keep me")
        TeamMember.objects.create(team=team, pokemon_id=25, slot=1)
        changed = [dict(r) for r in self.records]
        changed[0] = dict(changed[0], height=99)
        import_pokemon(changed)
        self.assertEqual(Pokemon.objects.get(id=changed[0]["id"]).height, 99)
        self.assertEqual(Pokemon.objects.count(), len(self.records))
        self.assertEqual(team.members.count(), 1)

    def test_foreign_key_protects_pokemon_used_in_a_team(self):
        team = Team.objects.create(name="Mascot")
        TeamMember.objects.create(team=team, pokemon_id=25, slot=1)
        with self.assertRaises(ProtectedError):
            Pokemon.objects.filter(id=25).delete()

    def test_database_rejects_slot_seven(self):
        team = Team.objects.create(name="Too big")
        with self.assertRaises(IntegrityError), transaction.atomic():
            TeamMember.objects.create(team=team, pokemon_id=1, slot=7)

    def test_database_rejects_duplicate_names(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pokemon.objects.create(
                id=5000, name="pikachu", height=1, weight=1, hp=1, attack=1, defense=1,
                special_attack=1, special_defense=1, speed=1, total_stats=6,
            )


class SqlRawQueryTests(TestCase):
    """The hand-written SQL in raw.py must return exactly what the ORM version returns."""

    def setUp(self):
        import_pokemon(load_sample_records())

    def test_raw_sql_matches_orm(self):
        for name in ("pikachu", "1", "150"):
            self.assertEqual(raw.get_pokemon(name), repository.get_pokemon(name))
        for kwargs in (
            {}, {"type_name": "fire"}, {"search": "a", "ordering": "-name"},
            {"min_attack": 80, "ordering": "-attack"}, {"ordering": "-total_stats", "limit": 3, "offset": 2},
            {"type_name": "dragon"}, {"limit": 5, "offset": 100},
        ):
            with self.subTest(**kwargs):
                self.assertEqual(raw.list_pokemon(**kwargs), repository.list_pokemon(**kwargs))
        self.assertEqual(raw.list_types(), repository.list_types())
        self.assertEqual(raw.stats_by_type(), repository.stats_by_type())
        self.assertEqual(raw.top_by_stat("speed", 4), repository.top_by_stat("speed", 4))

    def test_search_wildcards_are_escaped(self):
        self.assertEqual(raw.list_pokemon(search="%")["count"], 0)
        self.assertEqual(raw.list_pokemon(search="_")["count"], 0)
