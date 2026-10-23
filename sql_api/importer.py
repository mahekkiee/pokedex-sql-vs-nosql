"""Load normalised PokeAPI records into PostgreSQL.

One record becomes rows in up to five tables (Pokemon, Type, Ability and the
two link tables). Everything runs inside one transaction, so a failure halfway
leaves the database unchanged. Re-running the import updates rows in place
("upsert"), so teams that reference these Pokemon are not affected.
"""
from django.db import transaction

from core.constants import STAT_FIELDS

from .models import Ability, Pokemon, PokemonAbility, PokemonType, Type

POKEMON_UPDATE_FIELDS = [
    "name", "height", "weight", "base_experience", "sprite_url", *STAT_FIELDS, "total_stats",
]


def _pokemon_row(record):
    return Pokemon(
        id=record["id"],
        name=record["name"],
        height=record["height"],
        weight=record["weight"],
        base_experience=record["base_experience"],
        sprite_url=record["sprite_url"],
        total_stats=record["total_stats"],
        **{field: record["stats"][field] for field in STAT_FIELDS},
    )


@transaction.atomic
def import_pokemon(records) -> dict:
    if not records:
        return {"pokemon": 0, "types": 0, "abilities": 0}

    # 1. Lookup tables: insert names that do not exist yet.
    type_names = {name for r in records for name in r["types"]}
    ability_names = {a["name"] for r in records for a in r["abilities"]}
    Type.objects.bulk_create([Type(name=n) for n in type_names], ignore_conflicts=True)
    Ability.objects.bulk_create([Ability(name=n) for n in ability_names], ignore_conflicts=True)
    type_ids = dict(Type.objects.filter(name__in=type_names).values_list("name", "id"))
    ability_ids = dict(Ability.objects.filter(name__in=ability_names).values_list("name", "id"))

    # 2. Pokemon rows: INSERT ... ON CONFLICT (id) DO UPDATE
    Pokemon.objects.bulk_create(
        [_pokemon_row(r) for r in records],
        update_conflicts=True,
        unique_fields=["id"],
        update_fields=POKEMON_UPDATE_FIELDS,
        batch_size=1000,
    )

    # 3. Link tables: replace the links of the imported Pokemon.
    ids = [r["id"] for r in records]
    PokemonType.objects.filter(pokemon_id__in=ids).delete()
    PokemonAbility.objects.filter(pokemon_id__in=ids).delete()
    PokemonType.objects.bulk_create(
        [
            PokemonType(pokemon_id=r["id"], type_id=type_ids[name], slot=slot)
            for r in records
            for slot, name in enumerate(r["types"], start=1)
        ],
        batch_size=2000,
    )
    PokemonAbility.objects.bulk_create(
        [
            PokemonAbility(
                pokemon_id=r["id"], ability_id=ability_ids[a["name"]],
                slot=a["slot"], is_hidden=a["is_hidden"],
            )
            for r in records
            for a in r["abilities"]
        ],
        batch_size=2000,
    )
    return {"pokemon": len(records), "types": len(type_names), "abilities": len(ability_names)}


def delete_pokemon_with_id_at_least(min_id: int) -> int:
    """Remove synthetic benchmark copies (ids >= min_id). Link rows go too (CASCADE)."""
    _total, per_model = Pokemon.objects.filter(id__gte=min_id).delete()
    return per_model.get("sql_api.Pokemon", 0)
