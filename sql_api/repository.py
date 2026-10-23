"""PostgreSQL data access through Django's ORM.

mongo_api/repository.py has the same functions with the same inputs and
outputs. Comparing the two files side by side is the core of the project.

Things to notice on the SQL side:
* Reading one Pokemon touches 5 tables. Django's prefetch_related runs one
  query per related set (3 queries in total) and stitches them in Python.
* Filtering by type is a JOIN through the pokemon_type link table.
* Aggregations (COUNT, AVG ... GROUP BY) are done by the database.
* Writes that touch several tables are wrapped in a transaction.
"""
from django.db import transaction
from django.db.models import Avg, Count, F, Prefetch
from django.db.models.functions import Collate
from rest_framework.exceptions import NotFound, ValidationError

from core.constants import STAT_FIELDS
from core.utils import iso

from .models import Pokemon, PokemonAbility, PokemonType, Team, TeamMember, Type

# --- helpers -----------------------------------------------------------------------------

TYPES_PREFETCH = Prefetch(
    "type_slots", queryset=PokemonType.objects.select_related("type").order_by("slot")
)
ABILITIES_PREFETCH = Prefetch(
    "ability_slots", queryset=PokemonAbility.objects.select_related("ability").order_by("slot")
)


def _order_by(ordering: str):
    """Translate "-attack" into ORDER BY attack DESC, id ASC (id breaks ties)."""
    descending = ordering.startswith("-")
    field = ordering.lstrip("-")
    if field == "name":
        # Use byte order ("C" collation) so names sort exactly like MongoDB sorts them.
        expression = Collate("name", "C")
    else:
        expression = F(field)
    expression = expression.desc() if descending else expression.asc()
    return [expression] if field == "id" else [expression, "id"]


def _summary(pokemon) -> dict:
    return {
        "id": pokemon.id,
        "name": pokemon.name,
        "types": [slot.type.name for slot in pokemon.type_slots.all()],
        "sprite_url": pokemon.sprite_url,
        "total_stats": pokemon.total_stats,
    }


def _detail(pokemon) -> dict:
    return {
        "id": pokemon.id,
        "name": pokemon.name,
        "height": pokemon.height,
        "weight": pokemon.weight,
        "base_experience": pokemon.base_experience,
        "sprite_url": pokemon.sprite_url,
        "types": [slot.type.name for slot in pokemon.type_slots.all()],
        "abilities": [
            {"name": slot.ability.name, "is_hidden": slot.is_hidden, "slot": slot.slot}
            for slot in pokemon.ability_slots.all()
        ],
        "stats": {field: getattr(pokemon, field) for field in STAT_FIELDS},
        "total_stats": pokemon.total_stats,
    }


# --- Pokemon -------------------------------------------------------------------------------

def list_pokemon(type_name=None, search=None, min_attack=None, ordering="id", limit=20, offset=0):
    queryset = Pokemon.objects.all()
    if type_name:
        queryset = queryset.filter(type_slots__type__name=type_name.lower())  # JOIN
    if search:
        queryset = queryset.filter(name__icontains=search)                   # ILIKE-style match
    if min_attack is not None:
        queryset = queryset.filter(attack__gte=min_attack)
    count = queryset.count()
    page = queryset.order_by(*_order_by(ordering)).prefetch_related(TYPES_PREFETCH)[offset:offset + limit]
    return {"count": count, "limit": limit, "offset": offset, "results": [_summary(p) for p in page]}


def get_pokemon(id_or_name) -> dict:
    value = str(id_or_name).strip().lower()
    lookup = {"id": int(value)} if value.isdigit() else {"name": value}
    pokemon = (
        Pokemon.objects.filter(**lookup)
        .prefetch_related(TYPES_PREFETCH, ABILITIES_PREFETCH)
        .first()
    )
    if pokemon is None:
        raise NotFound(f"No Pokemon called '{id_or_name}'.")
    return _detail(pokemon)


def list_types() -> list:
    rows = (
        Type.objects.annotate(pokemon_count=Count("pokemon_slots"))
        .filter(pokemon_count__gt=0)
        .order_by(Collate("name", "C"))
    )
    return [{"name": t.name, "pokemon_count": t.pokemon_count} for t in rows]


def stats_by_type() -> list:
    """SELECT type.name, COUNT(*), AVG(hp), ... FROM pokemon_type JOIN ... GROUP BY type.name"""
    averages = {f"avg_{f}": Avg(f"pokemon__{f}") for f in STAT_FIELDS + ("total_stats",)}
    rows = (
        PokemonType.objects.values("type__name")
        .annotate(count=Count("pokemon_id"), **averages)
        .order_by(Collate("type__name", "C"))
    )
    return [
        {
            "type": row["type__name"],
            "count": row["count"],
            **{key: round(float(row[key]), 2) for key in averages},
        }
        for row in rows
    ]


def top_by_stat(stat="attack", limit=10) -> list:
    if stat not in STAT_FIELDS + ("total_stats",):
        raise ValidationError({"stat": f"Unknown stat '{stat}'."})
    rows = (
        Pokemon.objects.order_by(F(stat).desc(), "id")
        .prefetch_related(TYPES_PREFETCH)[:limit]
    )
    return [{**_summary(p), "value": getattr(p, stat)} for p in rows]


# --- Teams (a reference + JOIN on read) ------------------------------------------------------

def _team_queryset():
    members = TeamMember.objects.select_related("pokemon").prefetch_related(
        Prefetch(
            "pokemon__type_slots",
            queryset=PokemonType.objects.select_related("type").order_by("slot"),
        )
    ).order_by("slot")
    return Team.objects.prefetch_related(Prefetch("members", queryset=members))


def _team_dict(team) -> dict:
    return {
        "id": team.id,
        "name": team.name,
        "created_at": iso(team.created_at),
        "updated_at": iso(team.updated_at),
        "members": [
            {"slot": m.slot, "nickname": m.nickname, "pokemon": _summary(m.pokemon)}
            for m in team.members.all()
        ],
    }


def _team_id(team_id) -> int:
    try:
        return int(team_id)
    except (TypeError, ValueError):
        raise NotFound(f"No team with id '{team_id}'.") from None


def _check_members_exist(members) -> None:
    wanted = {m["pokemon"] for m in members}
    found = set(Pokemon.objects.filter(id__in=wanted).values_list("id", flat=True))
    missing = sorted(wanted - found)
    if missing:
        raise ValidationError({"members": [f"Unknown Pokemon id(s): {missing}"]})


def _replace_members(team, members, *, clear=True) -> None:
    if clear:
        team.members.all().delete()
    TeamMember.objects.bulk_create([
        TeamMember(team=team, pokemon_id=m["pokemon"], slot=slot, nickname=m.get("nickname", ""))
        for slot, m in enumerate(members, start=1)
    ])


def list_teams() -> list:
    return [_team_dict(t) for t in _team_queryset().order_by("-created_at", "-id")]


def get_team(team_id) -> dict:
    team = _team_queryset().filter(id=_team_id(team_id)).first()
    if team is None:
        raise NotFound(f"No team with id '{team_id}'.")
    return _team_dict(team)


@transaction.atomic
def create_team(data) -> dict:
    _check_members_exist(data["members"])
    team = Team.objects.create(name=data["name"])
    _replace_members(team, data["members"], clear=False)  # a new team has no members to remove
    return get_team(team.id)


@transaction.atomic
def update_team(team_id, data) -> dict:
    team = Team.objects.select_for_update().filter(id=_team_id(team_id)).first()
    if team is None:
        raise NotFound(f"No team with id '{team_id}'.")
    _check_members_exist(data["members"])
    team.name = data["name"]
    team.save()
    _replace_members(team, data["members"])
    return get_team(team.id)


def delete_team(team_id) -> None:
    deleted, _ = Team.objects.filter(id=_team_id(team_id)).delete()
    if not deleted:
        raise NotFound(f"No team with id '{team_id}'.")


# --- used by the benchmark and the home page -----------------------------------------------------

def count_pokemon() -> int:
    return Pokemon.objects.count()


def sample_ids(limit=50) -> list:
    return list(Pokemon.objects.filter(id__lt=100000).order_by("id").values_list("id", flat=True)[:limit])
