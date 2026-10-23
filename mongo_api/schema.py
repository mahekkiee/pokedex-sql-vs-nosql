"""Document model, validation rules and indexes for MongoDB.

MongoDB is "schema-flexible": by default a collection accepts any document.
That is convenient but risky, so we add a $jsonSchema validator. The database
then rejects documents with missing or wrongly-typed fields, which is the
closest MongoDB gets to the column types and NOT NULL rules of PostgreSQL.

One Pokemon is ONE document. Types, abilities and stats are embedded inside it
instead of living in separate tables, because they are always read together
with the Pokemon:

{
  "_id": 1, "name": "bulbasaur", "height": 7, "weight": 69, "base_experience": 64,
  "sprite_url": "https://...",
  "types": ["grass", "poison"],
  "abilities": [{"name": "overgrow", "is_hidden": false, "slot": 1}, ...],
  "stats": {"hp": 45, "attack": 49, ...},
  "total_stats": 318
}

A team is a separate collection that REFERENCES Pokemon by _id, because a team
is edited on its own and a Pokemon can be in many teams. Reading a team uses
$lookup, MongoDB's equivalent of a LEFT OUTER JOIN.

`ensure_schema()` plays the role of `migrate` on the SQL side. It is safe to
run many times (python manage.py setup_mongo).
"""
from pymongo import ASCENDING, DESCENDING

from core.constants import MAX_TEAM_SIZE, STAT_FIELDS
from core.mongo import get_db

POKEMON = "pokemon"
TEAMS = "teams"

POKEMON_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "name", "height", "weight", "types", "abilities", "stats", "total_stats"],
        "properties": {
            "_id": {"bsonType": "int", "minimum": 1, "description": "PokeAPI id"},
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 60},
            "height": {"bsonType": "int", "minimum": 0},
            "weight": {"bsonType": "int", "minimum": 0},
            "base_experience": {"bsonType": ["int", "null"]},
            "sprite_url": {"bsonType": "string"},
            "types": {
                "bsonType": "array", "minItems": 1, "maxItems": 2,
                "items": {"bsonType": "string"},
            },
            "abilities": {
                "bsonType": "array",
                "items": {
                    "bsonType": "object",
                    "required": ["name", "is_hidden", "slot"],
                    "properties": {
                        "name": {"bsonType": "string"},
                        "is_hidden": {"bsonType": "bool"},
                        "slot": {"bsonType": "int"},
                    },
                },
            },
            "stats": {
                "bsonType": "object",
                "required": list(STAT_FIELDS),
                "properties": {f: {"bsonType": "int", "minimum": 0} for f in STAT_FIELDS},
            },
            "total_stats": {"bsonType": "int", "minimum": 0},
        },
    }
}

TEAM_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["name", "members", "created_at", "updated_at"],
        "properties": {
            "name": {"bsonType": "string", "minLength": 1, "maxLength": 50},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
            "members": {
                "bsonType": "array",
                "maxItems": MAX_TEAM_SIZE,
                "items": {
                    "bsonType": "object",
                    "required": ["slot", "pokemon_id", "nickname"],
                    "properties": {
                        "slot": {"bsonType": "int", "minimum": 1, "maximum": MAX_TEAM_SIZE},
                        "pokemon_id": {"bsonType": "int"},
                        "nickname": {"bsonType": "string", "maxLength": 30},
                    },
                },
            },
        },
    }
}

# Same indexes as the SQL side has (unique name, type lookup, attack, total).
# The sort indexes include _id because the API sorts by (value DESC, _id ASC);
# without the tie-breaker in the index MongoDB has to sort in memory.
POKEMON_INDEXES = [
    ([("name", ASCENDING)], {"name": "name_unique", "unique": True}),
    ([("types", ASCENDING)], {"name": "types"}),  # multikey index: one entry per array item
    ([("stats.attack", DESCENDING), ("_id", ASCENDING)], {"name": "attack_desc_id"}),
    ([("total_stats", DESCENDING), ("_id", ASCENDING)], {"name": "total_stats_desc_id"}),
]
TEAM_INDEXES = [
    ([("created_at", DESCENDING)], {"name": "created_at_desc"}),
]


def ensure_schema(db=None) -> dict:
    """Create the collections with validators and indexes (or update them)."""
    db = db if db is not None else get_db()
    existing = set(db.list_collection_names())
    for name, validator in ((POKEMON, POKEMON_VALIDATOR), (TEAMS, TEAM_VALIDATOR)):
        if name in existing:
            db.command("collMod", name, validator=validator,
                       validationLevel="strict", validationAction="error")
        else:
            db.create_collection(name, validator=validator,
                                 validationLevel="strict", validationAction="error")
    for keys, options in POKEMON_INDEXES:
        db[POKEMON].create_index(keys, **options)
    for keys, options in TEAM_INDEXES:
        db[TEAMS].create_index(keys, **options)
    return {
        "collections": sorted(db.list_collection_names()),
        "pokemon_indexes": sorted(db[POKEMON].index_information()),
    }
