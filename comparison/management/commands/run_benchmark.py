"""python manage.py run_benchmark --iterations 200 [--scale 20] [--layers db,api]

Times the same operations on PostgreSQL and MongoDB and saves the results to
benchmark_results/latest.json (shown at /compare/). Never calls PokeAPI.
"""
from django.core.management.base import BaseCommand, CommandError

from comparison.benchmark import OPERATIONS, BenchmarkError, run_benchmark, save_results


class Command(BaseCommand):
    help = "Benchmark PostgreSQL vs MongoDB on identical operations."

    def add_arguments(self, parser):
        parser.add_argument("--iterations", type=int, default=200, help="Timed runs per operation")
        parser.add_argument("--warmup", type=int, default=10, help="Untimed runs first")
        parser.add_argument("--scale", type=int, default=1,
                            help="Multiply the data set with synthetic copies (e.g. 20)")
        parser.add_argument("--layers", default="db,api", help="db, api or db,api")
        parser.add_argument("--keep-copies", action="store_true",
                            help="Leave the synthetic copies in the databases afterwards")
        parser.add_argument("--only", default="",
                            help="Comma-separated operation keys to run, e.g. team_cycle,bulk_upsert")
        parser.add_argument("--no-save", action="store_true",
                            help="Print the results but do not overwrite benchmark_results/latest.json")

    def handle(self, *args, iterations, warmup, scale, layers, keep_copies, only, no_save, **options):
        chosen = tuple(layer.strip() for layer in layers.split(",") if layer.strip())
        if not chosen or any(layer not in ("db", "api") for layer in chosen):
            raise CommandError("--layers must be db, api or db,api")
        if iterations < 1 or scale < 1 or scale > 200:
            raise CommandError("--iterations must be >= 1 and --scale between 1 and 200")
        keys = [key.strip() for key in only.split(",") if key.strip()] or None
        unknown = set(keys or []) - {op.key for op in OPERATIONS}
        if unknown:
            raise CommandError(f"Unknown operation(s): {', '.join(sorted(unknown))}. "
                               f"Choose from: {', '.join(op.key for op in OPERATIONS)}")
        try:
            results = run_benchmark(
                iterations=iterations, warmup=warmup, scale=scale, layers=chosen,
                keep_copies=keep_copies, only=keys, progress=self.stdout.write,
            )
        except BenchmarkError as exc:
            raise CommandError(str(exc)) from exc
        if no_save:
            self.stdout.write("Not saved (--no-save).")
            return
        path = save_results(results)
        self.stdout.write(self.style.SUCCESS(f"Saved {path}. Open http://127.0.0.1:8000/compare/"))
