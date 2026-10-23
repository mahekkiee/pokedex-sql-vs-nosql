"""python manage.py import_mongo --limit 151

Reads Pokemon from the PokeAPI cache (fetching any that are missing) and
upserts them into MongoDB, one document per Pokemon.
"""
import time

from django.core.management.base import BaseCommand, CommandError

from core.mongo import mongo_is_available
from mongo_api.importer import import_pokemon
from pokeapi_client.client import PokeAPIError, load_records


class Command(BaseCommand):
    help = "Import Pokemon from the PokeAPI cache into MongoDB."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=int, default=1)
        parser.add_argument("--limit", type=int, default=151)
        parser.add_argument("--offline", action="store_true",
                            help="Only use the cache; never call PokeAPI")

    def handle(self, *args, start, limit, offline, **options):
        if not mongo_is_available():
            raise CommandError("MongoDB is not reachable. Start it with: docker compose up -d")
        try:
            records = load_records(start=start, limit=limit, offline=offline)
        except PokeAPIError as exc:
            raise CommandError(str(exc)) from exc
        started = time.perf_counter()
        result = import_pokemon(records)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.stdout.write(self.style.SUCCESS(
            f"MongoDB: upserted {result['pokemon']} Pokemon documents "
            f"({result['upserted']} new, {result['modified']} changed) in {elapsed_ms:.0f} ms"
        ))
