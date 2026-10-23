"""Query inspector: what does each operation actually send to the database?

For every benchmark operation we run it once on each backend and capture:
* PostgreSQL: the SQL statements Django generated (CaptureQueriesContext)
* MongoDB: the commands PyMongo sent (core.mongo.recorder, a CommandListener)
plus the Python source of the repository function on each side.
This is the "queries" part of the comparison, shown live on /compare/queries/.
"""
import inspect

import sqlparse
from bson import json_util
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.mongo import recorder

from .benchmark import INSPECTABLE_OPERATIONS, REPOSITORIES, build_context

JSON_OPTIONS = json_util.JSONOptions(json_mode=json_util.JSONMode.RELAXED)


def format_sql(sql: str) -> str:
    return sqlparse.format(sql, reindent=True, keyword_case="upper")


def format_mongo(command: dict) -> str:
    return f"{command['command']}:\n" + json_util.dumps(command["body"], indent=2, json_options=JSON_OPTIONS)


def function_source(backend: str, function_names: str) -> str:
    repo = REPOSITORIES[backend]
    first = function_names.split("/")[0].strip()
    func = getattr(repo, first, None)
    return inspect.getsource(func) if func else ""


def inspect_operations():
    ctx = build_context()
    rows = []
    for op in INSPECTABLE_OPERATIONS:
        with CaptureQueriesContext(connection) as captured:
            op.db("postgresql", ctx, 0)
        with recorder.record() as commands:
            op.db("mongodb", ctx, 0)
        rows.append({
            "key": op.key,
            "label": op.label,
            "description": op.description,
            "function": op.function,
            "sql": [format_sql(q["sql"]) for q in captured.captured_queries],
            "mongo": [format_mongo(c) for c in commands],
            "sql_source": function_source("postgresql", op.function),
            "mongo_source": function_source("mongodb", op.function),
        })
    return ctx, rows
