"""Describe both data models from the live databases, for /compare/model/."""
import ast
from pathlib import Path

from bson import json_util
from django.apps import apps
from django.conf import settings
from django.db import connection

from core.mongo import get_db
from mongo_api.schema import POKEMON, TEAMS
from sql_api.models import Pokemon, PokemonAbility, PokemonType

JSON_OPTIONS = json_util.JSONOptions(json_mode=json_util.JSONMode.RELAXED)


# --- PostgreSQL --------------------------------------------------------------------------------------

def _column_notes(field) -> str:
    notes = []
    if field.primary_key:
        notes.append("PRIMARY KEY")
    if field.is_relation and field.many_to_one:
        notes.append(f"FOREIGN KEY -> {field.related_model._meta.db_table}")
    if field.unique and not field.primary_key:
        notes.append("UNIQUE")
    if field.db_index and not field.unique and not field.is_relation:
        notes.append("INDEX")
    notes.append("NULL" if field.null else "NOT NULL")
    return ", ".join(notes)


def sql_tables() -> list:
    tables = []
    for model in apps.get_app_config("sql_api").get_models():
        meta = model._meta
        columns = [
            {"name": f.column, "type": f.db_type(connection), "notes": _column_notes(f)}
            for f in meta.local_fields
        ]
        constraints = [c.name for c in meta.constraints]
        indexes = [i.name for i in meta.indexes]
        tables.append({
            "table": meta.db_table,
            "model": model.__name__,
            "rows": model.objects.count(),
            "columns": columns,
            "constraints": constraints,
            "indexes": indexes,
        })
    return tables


def sql_example(pokemon_id: int) -> dict:
    """The same Pokemon as it is spread over several tables."""
    pokemon = Pokemon.objects.filter(id=pokemon_id).values().first()
    if pokemon is None:
        return {}
    type_rows = list(
        PokemonType.objects.filter(pokemon_id=pokemon_id)
        .values("pokemon_id", "type_id", "type__name", "slot")
    )
    ability_rows = list(
        PokemonAbility.objects.filter(pokemon_id=pokemon_id)
        .values("pokemon_id", "ability_id", "ability__name", "slot", "is_hidden")
    )
    return {
        "pokemon": pokemon,
        "type_rows": type_rows,
        "ability_rows": ability_rows,
        "row_count": 1 + len(type_rows) + len(ability_rows),
    }


# --- MongoDB -----------------------------------------------------------------------------------------------

def mongo_collections() -> list:
    db = get_db()
    result = []
    for name in (POKEMON, TEAMS):
        info = next(iter(db.list_collections(filter={"name": name})), None)
        if info is None:
            continue
        validator = info.get("options", {}).get("validator", {})
        indexes = {
            index_name: [f"{k} ({'asc' if v == 1 else 'desc' if v == -1 else v})" for k, v in spec["key"]]
            for index_name, spec in db[name].index_information().items()
        }
        result.append({
            "name": name,
            "documents": db[name].estimated_document_count(),
            "validator": json_util.dumps(validator, indent=2, json_options=JSON_OPTIONS),
            "indexes": indexes,
        })
    return result


def mongo_example(pokemon_id: int) -> str:
    doc = get_db()[POKEMON].find_one({"_id": pokemon_id})
    return json_util.dumps(doc, indent=2, json_options=JSON_OPTIONS) if doc else ""


# --- code size (one input to "ease of use") ------------------------------------------------------------------

CODE_FILES = {
    "postgresql": ["sql_api/models.py", "sql_api/repository.py", "sql_api/importer.py"],
    "mongodb": ["mongo_api/schema.py", "mongo_api/repository.py", "mongo_api/importer.py"],
}


def code_lines(path: Path) -> int:
    """Lines of real code: no blank lines, comments or docstrings."""
    source = path.read_text(encoding="utf-8")
    docstring_lines = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstring_lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    count = 0
    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and number not in docstring_lines:
            count += 1
    return count


def code_size_report() -> dict:
    report = {}
    for backend, files in CODE_FILES.items():
        per_file = {name: code_lines(Path(settings.BASE_DIR) / name) for name in files}
        report[backend] = {"files": per_file, "total": sum(per_file.values())}
    return report
