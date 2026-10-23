"""Turn a PokeAPI /pokemon/{id} response into one flat, database-neutral record.

Example output:
{
    "id": 1, "name": "bulbasaur", "height": 7, "weight": 69, "base_experience": 64,
    "sprite_url": "https://.../1.png",
    "types": ["grass", "poison"],
    "abilities": [{"name": "overgrow", "is_hidden": False, "slot": 1}, ...],
    "stats": {"hp": 45, "attack": 49, "defense": 49, "special_attack": 65,
              "special_defense": 65, "speed": 45},
    "total_stats": 318,
}
Both importers start from this record, so the two databases hold the same data.
"""
from core.constants import STAT_FIELDS

# PokeAPI uses hyphens; we use Python/SQL friendly names.
STAT_NAME_MAP = {
    "hp": "hp",
    "attack": "attack",
    "defense": "defense",
    "special-attack": "special_attack",
    "special-defense": "special_defense",
    "speed": "speed",
}


def normalize_pokemon(raw: dict) -> dict:
    stats = {field: 0 for field in STAT_FIELDS}
    for entry in raw.get("stats", []):
        field = STAT_NAME_MAP.get(entry["stat"]["name"])
        if field:
            stats[field] = int(entry["base_stat"])

    types = [t["type"]["name"] for t in sorted(raw.get("types", []), key=lambda t: t["slot"])]
    abilities = [
        {"name": a["ability"]["name"], "is_hidden": bool(a["is_hidden"]), "slot": int(a["slot"])}
        for a in sorted(raw.get("abilities", []), key=lambda a: a["slot"])
    ]
    sprites = raw.get("sprites") or {}

    return {
        "id": int(raw["id"]),
        "name": raw["name"],
        "height": int(raw.get("height") or 0),        # decimetres
        "weight": int(raw.get("weight") or 0),        # hectograms
        "base_experience": raw.get("base_experience"),  # can be null in PokeAPI
        "sprite_url": sprites.get("front_default") or "",
        "types": types,
        "abilities": abilities,
        "stats": stats,
        "total_stats": sum(stats.values()),
    }
