"""python manage.py setup_mongo

MongoDB has no migrations. This command does the equivalent job: it creates
the collections with their $jsonSchema validators and builds the indexes.
It is safe to run any number of times.
"""
from django.core.management.base import BaseCommand, CommandError

from core.mongo import mongo_is_available
from mongo_api.schema import ensure_schema


class Command(BaseCommand):
    help = "Create MongoDB collections, validators and indexes."

    def handle(self, *args, **options):
        if not mongo_is_available():
            raise CommandError("MongoDB is not reachable. Start it with: docker compose up -d")
        info = ensure_schema()
        self.stdout.write(self.style.SUCCESS(
            f"MongoDB ready. Collections: {', '.join(info['collections'])}. "
            f"Pokemon indexes: {', '.join(info['pokemon_indexes'])}"
        ))
