"""python manage.py import_sql --limit 151

Reads Pokemon from the PokeAPI cache (fetching any that are missing) and
upserts them into PostgreSQL. Run `python manage.py migrate` first.
"""
import time

from django.core.management.base import BaseCommand, CommandError

from pokeapi_client.client import PokeAPIError, load_records
from sql_api.importer import import_pokemon


class Command(BaseCommand):
    help = "Import Pokemon from the PokeAPI cache into PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=int, default=1)
        parser.add_argument("--limit", type=int, default=151)
        parser.add_argument("--offline", action="store_true",
                            help="Only use the cache; never call PokeAPI")

    def handle(self, *args, start, limit, offline, **options):
        try:
            records = load_records(start=start, limit=limit, offline=offline)
        except PokeAPIError as exc:
            raise CommandError(str(exc)) from exc
        started = time.perf_counter()
        result = import_pokemon(records)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.stdout.write(self.style.SUCCESS(
            f"PostgreSQL: upserted {result['pokemon']} Pokemon, {result['types']} types, "
            f"{result['abilities']} abilities in {elapsed_ms:.0f} ms"
        ))
