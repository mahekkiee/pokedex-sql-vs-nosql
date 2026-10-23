"""MongoDB data access through PyMongo.

Same functions, inputs and outputs as sql_api/repository.py.

Things to notice on the MongoDB side:
* Reading one Pokemon is one find_one(): types, abilities and stats are
  already inside the document, so there is nothing to join.
* Filtering by type matches a value inside an array ({"types": "fire"}),
  helped by a multikey index.
* Aggregations use the aggregation pipeline ($unwind, $group, $sort).
* Teams reference Pokemon by _id, so reading a team uses $lookup (a join).
* There are no multi-table writes here, so no transaction is needed: a single
  document write is always atomic in MongoDB.
"""
import re

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING
from rest_framework.exceptions import NotFound, ValidationError

from core.constants import STAT_FIELDS
from core.mongo import get_db
from core.utils import iso, utc_now

from .schema import POKEMON, TEAMS

SUMMARY_FIELDS = {"name": 1, "types": 1, "sprite_url": 1, "total_stats": 1}


def _pokemon():
    return get_db()[POKEMON]


def _teams():
    return get_db()[TEAMS]


def _field(name: str) -> str:
    """API field name -> document path."""
    if name == "id":
        return "_id"
    if name in STAT_FIELDS:
        return f"stats.{name}"
    return name


def _sort(ordering: str):
    """Translate "-attack" into [("stats.attack", -1), ("_id", 1)] (_id breaks ties)."""
    direction = DESCENDING if ordering.startswith("-") else ASCENDING
    path = _field(ordering.lstrip("-"))
    return [(path, direction)] if path == "_id" else [(path, direction), ("_id", ASCENDING)]


def _summary(doc) -> dict:
    return {
        "id": doc["_id"],
        "name": doc["name"],
        "types": doc["types"],
        "sprite_url": doc.get("sprite_url", ""),
        "total_stats": doc["total_stats"],
    }


def _detail(doc) -> dict:
    return {
        "id": doc["_id"],
        "name": doc["name"],
        "height": doc["height"],
        "weight": doc["weight"],
        "base_experience": doc.get("base_experience"),
        "sprite_url": doc.get("sprite_url", ""),
        "types": doc["types"],
        "abilities": [
            {"name": a["name"], "is_hidden": a["is_hidden"], "slot": a["slot"]}
            for a in doc["abilities"]
        ],
        "stats": {field: doc["stats"][field] for field in STAT_FIELDS},
        "total_stats": doc["total_stats"],
    }


# --- Pokemon -------------------------------------------------------------------------------

def build_filter(type_name=None, search=None, min_attack=None) -> dict:
    query = {}
    if type_name:
        query["types"] = type_name.lower()                        # matches inside the array
    if search:
        query["name"] = {"$regex": re.escape(search), "$options": "i"}
    if min_attack is not None:
        query["stats.attack"] = {"$gte": min_attack}              # dot notation into the sub-document
    return query


def list_pokemon(type_name=None, search=None, min_attack=None, ordering="id", limit=20, offset=0):
    query = build_filter(type_name, search, min_attack)
    collection = _pokemon()
    count = collection.count_documents(query)
    cursor = collection.find(query, SUMMARY_FIELDS).sort(_sort(ordering)).skip(offset).limit(limit)
    return {"count": count, "limit": limit, "offset": offset, "results": [_summary(d) for d in cursor]}


def get_pokemon(id_or_name) -> dict:
    value = str(id_or_name).strip().lower()
    query = {"_id": int(value)} if value.isdigit() else {"name": value}
    doc = _pokemon().find_one(query)
    if doc is None:
        raise NotFound(f"No Pokemon called '{id_or_name}'.")
    return _detail(doc)


def list_types() -> list:
    pipeline = [
        {"$unwind": "$types"},                                     # one row per (pokemon, type)
        {"$group": {"_id": "$types", "pokemon_count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    return [{"name": row["_id"], "pokemon_count": row["pokemon_count"]}
            for row in _pokemon().aggregate(pipeline)]


def stats_by_type() -> list:
    group = {"_id": "$types", "count": {"$sum": 1}}
    for field in STAT_FIELDS:
        group[f"avg_{field}"] = {"$avg": f"$stats.{field}"}
    group["avg_total_stats"] = {"$avg": "$total_stats"}
    pipeline = [{"$unwind": "$types"}, {"$group": group}, {"$sort": {"_id": 1}}]
    keys = [f"avg_{field}" for field in STAT_FIELDS] + ["avg_total_stats"]
    return [
        {"type": row["_id"], "count": row["count"], **{k: round(float(row[k]), 2) for k in keys}}
        for row in _pokemon().aggregate(pipeline)
    ]


def top_by_stat(stat="attack", limit=10) -> list:
    if stat not in STAT_FIELDS + ("total_stats",):
        raise ValidationError({"stat": f"Unknown stat '{stat}'."})
    path = _field(stat)
    projection = dict(SUMMARY_FIELDS, **{path: 1})
    cursor = _pokemon().find({}, projection).sort([(path, DESCENDING), ("_id", ASCENDING)]).limit(limit)
    results = []
    for doc in cursor:
        value = doc["total_stats"] if stat == "total_stats" else doc["stats"][stat]
        results.append({**_summary(doc), "value": value})
    return results


# --- Teams (a reference + $lookup on read) -------------------------------------------------------

def _team_pipeline(match: dict) -> list:
    return [
        {"$match": match},
        {"$sort": {"created_at": -1, "_id": -1}},
        {"$lookup": {                       # LEFT OUTER JOIN pokemon ON pokemon._id = members.pokemon_id
            "from": POKEMON,
            "localField": "members.pokemon_id",
            "foreignField": "_id",
            "as": "pokemon_docs",
            "pipeline": [{"$project": SUMMARY_FIELDS}],
        }},
    ]


def _team_dict(doc) -> dict:
    by_id = {p["_id"]: p for p in doc.get("pokemon_docs", [])}
    members = []
    for member in sorted(doc["members"], key=lambda m: m["slot"]):
        pokemon = by_id.get(member["pokemon_id"])
        members.append({
            "slot": member["slot"],
            "nickname": member["nickname"],
            "pokemon": _summary(pokemon) if pokemon else None,
        })
    return {
        "id": str(doc["_id"]),
        "name": doc["name"],
        "created_at": iso(doc["created_at"]),
        "updated_at": iso(doc["updated_at"]),
        "members": members,
    }


def _object_id(team_id) -> ObjectId:
    try:
        return ObjectId(str(team_id))
    except (InvalidId, TypeError):
        raise NotFound(f"No team with id '{team_id}'.") from None


def _check_members_exist(members) -> None:
    # Nothing like a FOREIGN KEY exists in MongoDB, so the application checks references.
    wanted = {m["pokemon"] for m in members}
    found = {d["_id"] for d in _pokemon().find({"_id": {"$in": list(wanted)}}, {"_id": 1})}
    missing = sorted(wanted - found)
    if missing:
        raise ValidationError({"members": [f"Unknown Pokemon id(s): {missing}"]})


def _member_docs(members) -> list:
    return [
        {"slot": slot, "pokemon_id": m["pokemon"], "nickname": m.get("nickname", "")}
        for slot, m in enumerate(members, start=1)
    ]


def list_teams() -> list:
    return [_team_dict(doc) for doc in _teams().aggregate(_team_pipeline({}))]


def get_team(team_id) -> dict:
    docs = list(_teams().aggregate(_team_pipeline({"_id": _object_id(team_id)})))
    if not docs:
        raise NotFound(f"No team with id '{team_id}'.")
    return _team_dict(docs[0])


def create_team(data) -> dict:
    _check_members_exist(data["members"])
    now = utc_now()
    result = _teams().insert_one({
        "name": data["name"],
        "members": _member_docs(data["members"]),
        "created_at": now,
        "updated_at": now,
    })
    return get_team(result.inserted_id)


def update_team(team_id, data) -> dict:
    oid = _object_id(team_id)
    _check_members_exist(data["members"])
    result = _teams().update_one(
        {"_id": oid},
        {"$set": {"name": data["name"], "members": _member_docs(data["members"]), "updated_at": utc_now()}},
    )
    if result.matched_count == 0:
        raise NotFound(f"No team with id '{team_id}'.")
    return get_team(oid)


def delete_team(team_id) -> None:
    if _teams().delete_one({"_id": _object_id(team_id)}).deleted_count == 0:
        raise NotFound(f"No team with id '{team_id}'.")


# --- used by the benchmark and the home page -----------------------------------------------------

def count_pokemon() -> int:
    return _pokemon().count_documents({})


def sample_ids(limit=50) -> list:
    cursor = _pokemon().find({"_id": {"$lt": 100000}}, {"_id": 1}).sort("_id", ASCENDING).limit(limit)
    return [d["_id"] for d in cursor]
