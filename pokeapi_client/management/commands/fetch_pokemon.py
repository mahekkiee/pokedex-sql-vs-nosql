"""python manage.py fetch_pokemon --limit 151

Downloads Pokemon from PokeAPI into the local cache (.cache/pokeapi/).
Already-cached Pokemon are skipped, so running it twice makes no extra calls.
"""
import time

from django.core.management.base import BaseCommand, CommandError

from pokeapi_client.client import PokeAPIClient, PokeAPIError


class Command(BaseCommand):
    help = "Download Pokemon from PokeAPI into the local cache (polite, one request at a time)."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=int, default=1, help="First PokeAPI id (default 1)")
        parser.add_argument("--limit", type=int, default=151, help="How many Pokemon (default 151)")
        parser.add_argument("--delay", type=float, default=None,
                            help="Seconds between network calls (default from settings)")

    def handle(self, *args, start, limit, delay, **options):
        if limit < 1 or limit > 1025:
            raise CommandError("--limit must be between 1 and 1025")
        client = PokeAPIClient(delay=delay)
        started = time.perf_counter()
        for index, pokemon_id in enumerate(range(start, start + limit), start=1):
            try:
                pokemon = client.get_pokemon(pokemon_id)
            except PokeAPIError as exc:
                raise CommandError(str(exc)) from exc
            if index % 25 == 0 or index == limit:
                self.stdout.write(f"  {index}/{limit}  last: #{pokemon['id']} {pokemon['name']}")
        elapsed = time.perf_counter() - started
        self.stdout.write(self.style.SUCCESS(
            f"Done in {elapsed:.1f}s. Network calls: {client.network_calls}, "
            f"served from cache: {client.cache_hits}. Cache folder: {client.cache_dir}"
        ))
