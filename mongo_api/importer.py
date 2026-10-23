"""Load normalised PokeAPI records into MongoDB.

One record becomes ONE document; nothing has to be split across collections.
bulk_write sends all upserts to the server in one round trip.
"""
from pymongo import ReplaceOne

from core.mongo import get_db

from .schema import POKEMON, ensure_schema


def to_document(record: dict) -> dict:
    return {
        "_id": record["id"],
        "name": record["name"],
        "height": record["height"],
        "weight": record["weight"],
        "base_experience": record["base_experience"],
        "sprite_url": record["sprite_url"],
        "types": list(record["types"]),
        "abilities": [dict(a) for a in record["abilities"]],
        "stats": dict(record["stats"]),
        "total_stats": record["total_stats"],
    }


def import_pokemon(records, *, setup=True) -> dict:
    db = get_db()
    if setup:
        ensure_schema(db)
    if not records:
        return {"pokemon": 0, "upserted": 0, "modified": 0}
    operations = [ReplaceOne({"_id": r["id"]}, to_document(r), upsert=True) for r in records]
    result = db[POKEMON].bulk_write(operations, ordered=False)
    return {
        "pokemon": len(records),
        "upserted": result.upserted_count,
        "modified": result.modified_count,
    }


def delete_pokemon_with_id_at_least(min_id: int) -> int:
    """Remove synthetic benchmark copies (ids >= min_id)."""
    return get_db()[POKEMON].delete_many({"_id": {"$gte": min_id}}).deleted_count
