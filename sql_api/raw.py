"""The read queries again, hand-written in SQL instead of using the ORM.

Why this file exists: the benchmark showed the ORM version is slower than
MongoDB, but most of that time is spent in Python (building queries and
model objects), not inside PostgreSQL. These functions return exactly the same
JSON as repository.py, using one SQL statement each, so the benchmark can
separate "cost of PostgreSQL" from "cost of the Django ORM".

They are only used by the benchmark and the tests; the API uses repository.py.
"""
from django.db import connection
from rest_framework.exceptions import NotFound, ValidationError

from core.constants import SORTABLE_FIELDS, STAT_FIELDS

# Correlated sub-query: the type names of pokemon p, in slot order.
TYPES_SUBQUERY = """
    COALESCE((SELECT array_agg(t.name ORDER BY pt.slot)
              FROM sql_api_pokemontype pt JOIN sql_api_type t ON t.id = pt.type_id
              WHERE pt.pokemon_id = p.id), '{}') AS types
"""

ABILITIES_SUBQUERY = """
    COALESCE((SELECT json_agg(json_build_object('name', a.name, 'is_hidden', pa.is_hidden, 'slot', pa.slot)
                              ORDER BY pa.slot)
              FROM sql_api_pokemonability pa JOIN sql_api_ability a ON a.id = pa.ability_id
              WHERE pa.pokemon_id = p.id), '[]') AS abilities
"""


def _rows(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c.name for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _summary(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "types": list(row["types"]),
        "sprite_url": row["sprite_url"], "total_stats": row["total_stats"],
    }


def _order_sql(ordering: str) -> str:
    field = ordering.lstrip("-")
    if field not in SORTABLE_FIELDS:  # whitelist: column names cannot be query parameters
        raise ValidationError({"ordering": f"Cannot sort by '{field}'."})
    direction = "DESC" if ordering.startswith("-") else "ASC"
    column = 'p.name COLLATE "C"' if field == "name" else f"p.{field}"
    return f"{column} {direction}" + ("" if field == "id" else ", p.id ASC")


def get_pokemon(id_or_name) -> dict:
    value = str(id_or_name).strip().lower()
    where, param = ("p.id = %s", int(value)) if value.isdigit() else ("p.name = %s", value)
    rows = _rows(
        f"SELECT p.*, {TYPES_SUBQUERY}, {ABILITIES_SUBQUERY} FROM sql_api_pokemon p WHERE {where}",
        [param],
    )
    if not rows:
        raise NotFound(f"No Pokemon called '{id_or_name}'.")
    row = rows[0]
    return {
        "id": row["id"], "name": row["name"], "height": row["height"], "weight": row["weight"],
        "base_experience": row["base_experience"], "sprite_url": row["sprite_url"],
        "types": list(row["types"]), "abilities": row["abilities"],
        "stats": {field: row[field] for field in STAT_FIELDS}, "total_stats": row["total_stats"],
    }


def list_pokemon(type_name=None, search=None, min_attack=None, ordering="id", limit=20, offset=0):
    conditions, params = [], []
    if type_name:
        conditions.append(
            "EXISTS (SELECT 1 FROM sql_api_pokemontype pt JOIN sql_api_type t ON t.id = pt.type_id "
            "WHERE pt.pokemon_id = p.id AND t.name = %s)"
        )
        params.append(type_name.lower())
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("p.name ILIKE %s")
        params.append(f"%{escaped}%")
    if min_attack is not None:
        conditions.append("p.attack >= %s")
        params.append(min_attack)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    # COUNT(*) OVER () returns the total match count on every row: one round trip, not two.
    rows = _rows(
        f"SELECT p.id, p.name, p.sprite_url, p.total_stats, {TYPES_SUBQUERY}, COUNT(*) OVER () AS total "
        f"FROM sql_api_pokemon p {where} ORDER BY {_order_sql(ordering)} LIMIT %s OFFSET %s",
        [*params, limit, offset],
    )
    if rows:
        count = rows[0]["total"]
    else:
        count = _rows(f"SELECT COUNT(*) AS total FROM sql_api_pokemon p {where}", params)[0]["total"]
    return {"count": count, "limit": limit, "offset": offset, "results": [_summary(r) for r in rows]}


def list_types() -> list:
    rows = _rows(
        "SELECT t.name, COUNT(*) AS pokemon_count FROM sql_api_type t "
        "JOIN sql_api_pokemontype pt ON pt.type_id = t.id "
        'GROUP BY t.name ORDER BY t.name COLLATE "C"'
    )
    return [{"name": r["name"], "pokemon_count": r["pokemon_count"]} for r in rows]


def stats_by_type() -> list:
    fields = STAT_FIELDS + ("total_stats",)
    averages = ", ".join(f"AVG(p.{f}) AS avg_{f}" for f in fields)
    rows = _rows(
        f"SELECT t.name AS type, COUNT(*) AS count, {averages} "
        "FROM sql_api_pokemontype pt "
        "JOIN sql_api_type t ON t.id = pt.type_id "
        "JOIN sql_api_pokemon p ON p.id = pt.pokemon_id "
        'GROUP BY t.name ORDER BY t.name COLLATE "C"'
    )
    return [
        {"type": r["type"], "count": r["count"], **{f"avg_{f}": round(float(r[f"avg_{f}"]), 2) for f in fields}}
        for r in rows
    ]


def top_by_stat(stat="attack", limit=10) -> list:
    if stat not in STAT_FIELDS + ("total_stats",):
        raise ValidationError({"stat": f"Unknown stat '{stat}'."})
    rows = _rows(
        f"SELECT p.id, p.name, p.sprite_url, p.total_stats, p.{stat} AS value, {TYPES_SUBQUERY} "
        f"FROM sql_api_pokemon p ORDER BY p.{stat} DESC, p.id ASC LIMIT %s",
        [limit],
    )
    return [{**_summary(r), "value": r["value"]} for r in rows]
