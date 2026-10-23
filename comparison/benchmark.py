"""Benchmark: run the same operations on PostgreSQL and MongoDB and time them.

Fairness rules we follow (explain these in the demo):
* Same data in both databases (both are loaded from the same normalised records).
* Equivalent indexes on both sides (unique name, type lookup, attack + id, total + id).
* Same machine, both databases local, connections reused (CONN_MAX_AGE / MongoClient pool).
* Warm-up calls first, so caches are warm for both.
* Calls are interleaved and the order alternates every iteration, so background
  noise hits both databases equally.
* DEBUG is switched off while timing, because Django's debug mode records every
  SQL query and would slow the PostgreSQL side only.
* We report the MEDIAN (robust against outliers), plus mean, p95, min and max.
* Two layers are measured:
    db   calling the repository functions directly (database work + Python mapping)
    api  full HTTP request through Django and DRF, as a client would see it
* In the db layer, read operations are also timed with hand-written SQL
  (sql_api/raw.py, series "postgresql_raw"). That separates the cost of
  PostgreSQL itself from the cost of the Django ORM.
* Nothing here calls PokeAPI. The data used is what is already in the databases.

To test bigger data sets without touching the public API, --scale N inserts
N-1 synthetic copies of every Pokemon (new ids from 100000 upwards) into both
databases, runs the benchmark, then removes the copies.
"""
import json
import os
import platform
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

import django
import pymongo
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import override_settings

from core.mongo import get_db, mongo_is_available, mongo_server_version
from mongo_api import importer as mongo_importer
from mongo_api import repository as mongo_repo
from mongo_api.schema import POKEMON, TEAMS
from sql_api import importer as sql_importer
from sql_api import raw as sql_raw
from sql_api import repository as sql_repo
from sql_api.models import Ability, Pokemon, PokemonAbility, PokemonType, Team, Type

BACKENDS = ("postgresql", "mongodb")
RAW_SQL = "postgresql_raw"
REPOSITORIES = {"postgresql": sql_repo, "mongodb": mongo_repo, RAW_SQL: sql_raw}
API_PREFIX = {"postgresql": "/api/sql/", "mongodb": "/api/mongo/"}
SYNTHETIC_ID_OFFSET = 100_000
SEARCH_TERM = "ar"
MIN_ATTACK = 80


class BenchmarkError(Exception):
    pass


@dataclass
class Context:
    ids: list
    names: list
    common_type: str
    records: list


@dataclass
class Operation:
    key: str
    label: str
    description: str
    kind: str                                   # read | aggregate | write
    function: str                               # repository function it exercises
    db: Callable                                # (backend, ctx, i) -> None
    api: Optional[Callable] = None              # (client, prefix, ctx, i) -> None
    max_iterations: Optional[int] = None        # cap for slow operations


# --- operations --------------------------------------------------------------------------------

def _repo(backend):
    return REPOSITORIES[backend]


def _get(client, url):
    response = client.get(url, HTTP_ACCEPT="application/json")
    if response.status_code != 200:
        raise BenchmarkError(f"GET {url} returned HTTP {response.status_code}")
    return response


def _team_payload(ctx, i):
    start = i % max(1, len(ctx.ids) - 6)
    members = [{"pokemon": pid, "nickname": ""} for pid in ctx.ids[start:start + 6]]
    return {"name": f"benchmark-team-{i}", "members": members}


def _db_team_cycle(backend, ctx, i):
    repo = _repo(backend)
    team = repo.create_team(_team_payload(ctx, i))
    repo.get_team(team["id"])
    repo.delete_team(team["id"])


def _api_team_cycle(client, prefix, ctx, i):
    response = client.post(f"{prefix}teams/", _team_payload(ctx, i), content_type="application/json")
    if response.status_code != 201:
        raise BenchmarkError(f"POST {prefix}teams/ returned HTTP {response.status_code}")
    team_id = response.json()["id"]
    _get(client, f"{prefix}teams/{team_id}/")
    client.delete(f"{prefix}teams/{team_id}/")


def _db_bulk_upsert(backend, ctx, i):
    if backend == "postgresql":
        sql_importer.import_pokemon(ctx.records)
    else:
        mongo_importer.import_pokemon(ctx.records, setup=False)


OPERATIONS = [
    Operation(
        "get_by_id", "Get one Pokemon by id", "Primary key lookup returning types, abilities and stats.",
        "read", "get_pokemon",
        db=lambda b, c, i: _repo(b).get_pokemon(c.ids[i % len(c.ids)]),
        api=lambda cl, p, c, i: _get(cl, f"{p}pokemon/{c.ids[i % len(c.ids)]}/"),
    ),
    Operation(
        "get_by_name", "Get one Pokemon by name", "Lookup on the unique name index.",
        "read", "get_pokemon",
        db=lambda b, c, i: _repo(b).get_pokemon(c.names[i % len(c.names)]),
        api=lambda cl, p, c, i: _get(cl, f"{p}pokemon/{c.names[i % len(c.names)]}/"),
    ),
    Operation(
        "list_page", "List a page of 20", "First pages of the list ordered by id, with each Pokemon's types.",
        "read", "list_pokemon",
        db=lambda b, c, i: _repo(b).list_pokemon(limit=20, offset=(i % 5) * 20),
        api=lambda cl, p, c, i: _get(cl, f"{p}pokemon/?limit=20&offset={(i % 5) * 20}"),
    ),
    Operation(
        "filter_type", "Filter by type", "All Pokemon of the most common type (up to 50).",
        "read", "list_pokemon",
        db=lambda b, c, i: _repo(b).list_pokemon(type_name=c.common_type, limit=50),
        api=lambda cl, p, c, i: _get(cl, f"{p}pokemon/?type={c.common_type}&limit=50"),
    ),
    Operation(
        "filter_type_attack", "Type + attack filter, sorted",
        f"Most common type with attack >= {MIN_ATTACK}, highest attack first.",
        "read", "list_pokemon",
        db=lambda b, c, i: _repo(b).list_pokemon(
            type_name=c.common_type, min_attack=MIN_ATTACK, ordering="-attack", limit=20),
        api=lambda cl, p, c, i: _get(
            cl, f"{p}pokemon/?type={c.common_type}&min_attack={MIN_ATTACK}&ordering=-attack"),
    ),
    Operation(
        "search_name", "Search by name", f"Case-insensitive 'contains' search for '{SEARCH_TERM}'.",
        "read", "list_pokemon",
        db=lambda b, c, i: _repo(b).list_pokemon(search=SEARCH_TERM, limit=20),
        api=lambda cl, p, c, i: _get(cl, f"{p}pokemon/?search={SEARCH_TERM}"),
    ),
    Operation(
        "top_attack", "Top 10 by attack", "Sort on an indexed stat and take the first 10.",
        "read", "top_by_stat",
        db=lambda b, c, i: _repo(b).top_by_stat("attack", 10),
        api=lambda cl, p, c, i: _get(cl, f"{p}stats/top/?stat=attack&limit=10"),
    ),
    Operation(
        "count_per_type", "Count Pokemon per type", "GROUP BY type (SQL) vs $unwind + $group (MongoDB).",
        "aggregate", "list_types",
        db=lambda b, c, i: _repo(b).list_types(),
        api=lambda cl, p, c, i: _get(cl, f"{p}types/"),
    ),
    Operation(
        "avg_stats_per_type", "Average stats per type", "Seven averages per type across the whole data set.",
        "aggregate", "stats_by_type",
        db=lambda b, c, i: _repo(b).stats_by_type(),
        api=lambda cl, p, c, i: _get(cl, f"{p}stats/by-type/"),
    ),
    Operation(
        "team_cycle", "Team: create, read, delete",
        "Insert a 6-member team, read it back with a join ($lookup), delete it.",
        "write", "create_team / get_team / delete_team",
        db=_db_team_cycle, api=_api_team_cycle,
    ),
    Operation(
        "bulk_upsert", "Bulk upsert all Pokemon", "Re-import every Pokemon (insert or update), as the importer does.",
        "write", "importer.import_pokemon",
        db=_db_bulk_upsert, api=None, max_iterations=10,
    ),
]

INSPECTABLE_OPERATIONS = [op for op in OPERATIONS if op.key != "bulk_upsert"]


# --- context and data ---------------------------------------------------------------------------

def check_databases():
    try:
        connection.ensure_connection()
    except Exception as exc:  # noqa: BLE001
        raise BenchmarkError(f"PostgreSQL is not reachable: {exc}") from exc
    if not mongo_is_available():
        raise BenchmarkError("MongoDB is not reachable. Start it with: docker compose up -d")


def build_context(with_records=False) -> Context:
    ids = sql_repo.sample_ids(50)
    if not ids:
        raise BenchmarkError("No data yet. Run: python manage.py import_sql and import_mongo")
    if ids != mongo_repo.sample_ids(50):
        raise BenchmarkError(
            "PostgreSQL and MongoDB hold different Pokemon. Import both with the same --limit."
        )
    names = [sql_repo.get_pokemon(pid)["name"] for pid in ids[:20]]
    types = sql_repo.list_types()
    common_type = max(types, key=lambda t: (t["pokemon_count"], t["name"]))["name"]
    records = []
    if with_records:
        base_ids = list(Pokemon.objects.filter(id__lt=SYNTHETIC_ID_OFFSET).values_list("id", flat=True))
        records = [sql_repo.get_pokemon(pid) for pid in base_ids]  # same shape as a normalised record
    return Context(ids=ids, names=names, common_type=common_type, records=records)


def remove_synthetic_copies() -> dict:
    return {
        "postgresql": sql_importer.delete_pokemon_with_id_at_least(SYNTHETIC_ID_OFFSET),
        "mongodb": mongo_importer.delete_pokemon_with_id_at_least(SYNTHETIC_ID_OFFSET),
    }


def add_synthetic_copies(records, scale) -> int:
    copies = [
        dict(r, id=r["id"] + k * SYNTHETIC_ID_OFFSET, name=f"{r['name']}-copy{k}")
        for k in range(1, scale)
        for r in records
    ]
    for start in range(0, len(copies), 5000):
        chunk = copies[start:start + 5000]
        sql_importer.import_pokemon(chunk)
        mongo_importer.import_pokemon(chunk, setup=False)
    return len(copies)


# --- measuring ---------------------------------------------------------------------------------------

def summarise(samples) -> dict:
    ordered = sorted(samples)
    p95_index = max(0, int(round(0.95 * (len(ordered) - 1))))
    return {
        "median_ms": round(statistics.median(ordered), 4),
        "mean_ms": round(statistics.fmean(ordered), 4),
        "p95_ms": round(ordered[p95_index], 4),
        "min_ms": round(ordered[0], 4),
        "max_ms": round(ordered[-1], 4),
        "samples": len(ordered),
    }


def time_all(call, iterations, warmup, backends=BACKENDS) -> dict:
    samples = {backend: [] for backend in backends}
    for i in range(warmup):
        for backend in backends:
            call(backend, i)
    for i in range(iterations):
        order = backends if i % 2 == 0 else tuple(reversed(backends))
        for backend in order:
            started = time.perf_counter_ns()
            call(backend, i)
            samples[backend].append((time.perf_counter_ns() - started) / 1_000_000)
    return {backend: summarise(values) for backend, values in samples.items()}


def verdict(stats) -> dict:
    sql_ms, mongo_ms = stats["postgresql"]["median_ms"], stats["mongodb"]["median_ms"]
    faster = "postgresql" if sql_ms <= mongo_ms else "mongodb"
    slower_ms, faster_ms = max(sql_ms, mongo_ms), min(sql_ms, mongo_ms)
    ratio = round(slower_ms / faster_ms, 2) if faster_ms > 0 else None
    return {"faster": faster, "ratio": ratio}


# --- environment and storage ----------------------------------------------------------------------------

def postgres_version() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version")
        return cursor.fetchone()[0].split()[0]


def storage_report() -> dict:
    """How much space the Pokemon data takes in each database.

    Two views are reported:
    * logical: the bytes of the rows / documents themselves
      (PostgreSQL: sum of pg_column_size of every row; MongoDB: BSON size of every document)
    * on_disk: what the files take, including indexes
      (PostgreSQL: pg_total_relation_size; MongoDB: storageSize + totalIndexSize, compressed)
    Measured right after loading, before the benchmark writes anything. Both
    databases are compacted first (VACUUM FULL / compact) so that space left
    over from earlier imports and benchmark runs is not counted.
    """
    tables = [m._meta.db_table for m in (Pokemon, PokemonType, PokemonAbility, Type, Ability)]
    logical, on_disk = {}, {}
    with connection.cursor() as cursor:
        if not connection.in_atomic_block:  # VACUUM cannot run inside a transaction (e.g. in tests)
            for table in tables:
                cursor.execute(f'VACUUM FULL ANALYZE "{table}"')
        for table in tables:
            cursor.execute(f'SELECT COALESCE(SUM(pg_column_size(t.*)), 0) FROM "{table}" AS t')
            logical[table] = int(cursor.fetchone()[0])
            cursor.execute("SELECT pg_total_relation_size(%s)", [table])
            on_disk[table] = int(cursor.fetchone()[0])
    db = get_db()
    try:
        db.command("compact", POKEMON)
    except Exception:  # noqa: BLE001 - compact needs extra rights on hosted clusters; sizes are still valid
        pass
    stats = next(db[POKEMON].aggregate([{"$collStats": {"storageStats": {}}}]))["storageStats"]
    return {
        "postgresql": {
            "logical_bytes": sum(logical.values()),
            "on_disk_bytes": sum(on_disk.values()),
            "tables": {t: {"logical_bytes": logical[t], "on_disk_bytes": on_disk[t]} for t in tables},
        },
        "mongodb": {
            "logical_bytes": int(stats.get("size", 0)),
            "on_disk_bytes": int(stats.get("storageSize", 0)) + int(stats.get("totalIndexSize", 0)),
            "storage_bytes": int(stats.get("storageSize", 0)),
            "index_bytes": int(stats.get("totalIndexSize", 0)),
            "avg_document_bytes": int(stats.get("avgObjSize", 0)),
        },
    }


def write_concern_description() -> str:
    """MongoDB's default (w:1) acknowledges a write before it is flushed to the journal on disk,
    while PostgreSQL waits for its WAL to be flushed on every COMMIT. Add ?journal=true to
    MONGO_URI to make MongoDB wait for the journal too (like-for-like durability)."""
    concern = get_db().write_concern.document
    return "journaled (j:true)" if concern.get("j") else "default (w:1, not waiting for journal)"


def environment() -> dict:
    import psycopg

    return {
        "python": platform.python_version(),
        "django": django.get_version(),
        "postgresql": postgres_version(),
        "psycopg": psycopg.__version__,
        "mongodb": mongo_server_version(),
        "pymongo": pymongo.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
    }


# --- main entry point -------------------------------------------------------------------------------------

def run_benchmark(iterations=200, warmup=10, scale=1, layers=("db", "api"),
                  keep_copies=False, only=None, progress=print) -> dict:
    check_databases()
    with override_settings(DEBUG=False):
        removed = remove_synthetic_copies()
        if any(removed.values()):
            progress(f"Removed leftover synthetic copies: {removed}")
        ctx = build_context(with_records=True)
        copies = 0
        try:
            if scale > 1:
                progress(f"Adding {scale - 1} synthetic copies of each of {len(ctx.records)} Pokemon...")
                copies = add_synthetic_copies(ctx.records, scale)
            counts = {"postgresql": sql_repo.count_pokemon(), "mongodb": mongo_repo.count_pokemon()}
            progress(f"Pokemon in each database: {counts}")
            storage = storage_report()

            results = {}
            client = Client()
            for layer in layers:
                rows = []
                for op in OPERATIONS:
                    if (layer == "api" and op.api is None) or (only and op.key not in only):
                        continue
                    n = min(iterations, op.max_iterations or iterations)
                    w = min(warmup, max(1, n // 5))
                    backends = BACKENDS
                    if layer == "db":
                        call = lambda backend, i, op=op: op.db(backend, ctx, i)  # noqa: E731
                        if hasattr(sql_raw, op.function):
                            backends = BACKENDS + (RAW_SQL,)
                    else:
                        call = lambda backend, i, op=op: op.api(client, API_PREFIX[backend], ctx, i)  # noqa: E731
                    stats = time_all(call, n, w, backends)
                    row = {
                        "key": op.key, "label": op.label, "description": op.description,
                        "kind": op.kind, "function": op.function, "iterations": n,
                        **stats, **verdict(stats),
                    }
                    rows.append(row)
                    raw = f"   raw SQL {stats[RAW_SQL]['median_ms']:>8.3f} ms" if RAW_SQL in stats else ""
                    progress(
                        f"  [{layer}] {op.label:<32} SQL {stats['postgresql']['median_ms']:>8.3f} ms"
                        f"   Mongo {stats['mongodb']['median_ms']:>8.3f} ms{raw}"
                    )
                results[layer] = rows
        finally:
            if copies and not keep_copies:
                remove_synthetic_copies()
            # Remove any benchmark team left behind by an error.
            Team.objects.filter(name__startswith="benchmark-team-").delete()
            get_db()[TEAMS].delete_many({"name": {"$regex": "^benchmark-team-"}})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "iterations": iterations,
        "warmup": warmup,
        "scale": scale,
        "synthetic_copies": copies,
        "pokemon_count": counts,
        "context": {"common_type": ctx.common_type, "search_term": SEARCH_TERM, "min_attack": MIN_ATTACK},
        "mongo_write_concern": write_concern_description(),
        "environment": environment(),
        "storage": storage,
        "layers": results,
    }


def save_results(results, directory=None):
    directory = directory or settings.BENCHMARK_RESULTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    text = json.dumps(results, indent=2)
    (directory / f"run_{stamp}.json").write_text(text, encoding="utf-8")
    latest = directory / "latest.json"
    latest.write_text(text, encoding="utf-8")
    return latest


def load_latest(directory=None):
    path = (directory or settings.BENCHMARK_RESULTS_DIR) / "latest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
