"""Pages that present the comparison: benchmark dashboard, query inspector, data model."""
from django.shortcuts import render

from .benchmark import BenchmarkError, load_latest
from .datamodel import (
    code_size_report, mongo_collections, mongo_example, sql_example, sql_tables,
)
from .inspector import inspect_operations

LAYER_NAMES = {
    "db": "Database layer (repository functions called directly)",
    "api": "API layer (full HTTP request through Django REST framework)",
}


def _bar_rows(rows):
    """Add bar widths (percent of the slower backend) for the template."""
    prepared = []
    for row in rows:
        sql_ms = row["postgresql"]["median_ms"]
        mongo_ms = row["mongodb"]["median_ms"]
        raw_ms = row.get("postgresql_raw", {}).get("median_ms")
        longest = max(sql_ms, mongo_ms, raw_ms or 0) or 1
        prepared.append({
            **row,
            "sql_width": max(1.0, round(sql_ms / longest * 100, 1)),
            "mongo_width": max(1.0, round(mongo_ms / longest * 100, 1)),
            "raw_width": max(1.0, round(raw_ms / longest * 100, 1)) if raw_ms is not None else None,
        })
    return prepared


def _wins(rows):
    wins = {"postgresql": 0, "mongodb": 0}
    for row in rows:
        wins[row["faster"]] += 1
    return wins


def dashboard(request):
    results = load_latest()
    context = {"results": results, "code_size": code_size_report(), "layer_names": LAYER_NAMES}
    if results:
        available = list(results["layers"].keys())
        layer = request.GET.get("layer", available[0])
        if layer not in available:
            layer = available[0]
        rows = _bar_rows(results["layers"][layer])
        storage = results.get("storage", {})
        context.update({
            "layer": layer,
            "layers": available,
            "rows": rows,
            "wins": _wins(rows),
            "storage": storage,
        })
    return render(request, "comparison/dashboard.html", context)


def queries(request):
    try:
        ctx, rows = inspect_operations()
        error = None
    except BenchmarkError as exc:
        ctx, rows, error = None, [], str(exc)
    return render(request, "comparison/queries.html", {"rows": rows, "ctx": ctx, "error": error})


def data_model(request):
    error = None
    try:
        tables = sql_tables()
        collections = mongo_collections()
        sql_ex = sql_example(1)
        mongo_ex = mongo_example(1)
        example_id = 1 if sql_ex and mongo_ex else None
    except Exception as exc:  # noqa: BLE001 - show the problem on the page instead of a 500
        tables, collections, sql_ex, mongo_ex, example_id = [], [], {}, "", None
        error = str(exc)
    return render(request, "comparison/model.html", {
        "tables": tables,
        "collections": collections,
        "sql_example": sql_ex,
        "mongo_example": mongo_ex,
        "example_id": example_id,
        "error": error,
    })
