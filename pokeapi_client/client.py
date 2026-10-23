"""A small, polite client for https://pokeapi.co

PokeAPI's fair use policy asks clients to cache what they download, and the
project brief says to respect rate limits and never load test a public API.
So this client:

1. Looks in a local cache folder (.cache/pokeapi/) first. A cached resource is
   never requested again.
2. Waits REQUEST_DELAY seconds between real network calls (one at a time,
   no parallel requests).
3. Backs off and retries when the server answers 429 (too many requests) or
   a 5xx error, honouring the Retry-After header if there is one.
4. Sends a descriptive User-Agent.

Only the fields we use are kept in the cache. A raw Pokemon response is about
250 KB, almost all of it the "moves" list, which this project never reads.
"""
import json
import logging
import re
import time
from pathlib import Path

import requests
from django.conf import settings

from .normalize import normalize_pokemon

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4

# Top-level fields of /pokemon/{id} that the project never uses.
UNUSED_POKEMON_FIELDS = (
    "moves", "game_indices", "held_items", "location_area_encounters",
    "past_abilities", "past_types", "past_stats", "cries", "forms",
)


class PokeAPIError(Exception):
    """Raised when a resource cannot be fetched or is not cached in offline mode."""


def slim_pokemon(raw: dict) -> dict:
    """Drop fields we never use so the cache stays small (about 1-2 KB per Pokemon)."""
    slim = {key: value for key, value in raw.items() if key not in UNUSED_POKEMON_FIELDS}
    sprites = raw.get("sprites") or {}
    artwork = ((sprites.get("other") or {}).get("official-artwork") or {}).get("front_default")
    slim["sprites"] = {"front_default": sprites.get("front_default"), "official_artwork": artwork}
    return slim


def _retry_after_seconds(response, default: float) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        return max(float(value), 0.0)
    except ValueError:
        return default


class PokeAPIClient:
    def __init__(self, *, base_url=None, cache_dir=None, delay=None, timeout=None,
                 offline=False, session=None):
        conf = settings.POKEAPI
        self.base_url = (base_url or conf["BASE_URL"]).rstrip("/") + "/"
        self.cache_dir = Path(cache_dir or conf["CACHE_DIR"])
        self.delay = conf["REQUEST_DELAY"] if delay is None else delay
        self.timeout = timeout or conf["TIMEOUT"]
        self.offline = offline
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": conf["USER_AGENT"], "Accept": "application/json"})
        self._last_call = 0.0
        self.network_calls = 0
        self.cache_hits = 0

    # -- cache ----------------------------------------------------------------

    def cache_path(self, path: str) -> Path:
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", path.strip("/"))
        return self.cache_dir / f"{safe_name}.json"

    def _read_cache(self, cache_file: Path):
        with cache_file.open(encoding="utf-8") as fh:
            return json.load(fh)

    def _write_cache(self, cache_file: Path, data) -> None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache_file)  # atomic: a half-written file is never left behind

    # -- network --------------------------------------------------------------

    def _wait_for_turn(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _download(self, path: str):
        url = self.base_url + path.strip("/") + "/"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_for_turn()
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt == MAX_ATTEMPTS:
                    raise PokeAPIError(f"Could not reach {url}: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            self.network_calls += 1
            if response.status_code == 404:
                raise PokeAPIError(f"{url} returned 404 (no such resource)")
            if response.status_code == 429 or response.status_code >= 500:
                pause = _retry_after_seconds(response, default=2 ** attempt)
                logger.warning("PokeAPI answered %s for %s; waiting %.1fs before retrying",
                               response.status_code, url, pause)
                if attempt == MAX_ATTEMPTS:
                    raise PokeAPIError(f"{url} kept failing with HTTP {response.status_code}")
                time.sleep(pause)
                continue
            response.raise_for_status()
            return response.json()
        raise PokeAPIError(f"Giving up on {url}")  # pragma: no cover

    # -- public API -------------------------------------------------------------

    def get(self, path: str, transform=None):
        """Return the JSON for `path` (e.g. "pokemon/25"), from cache when possible."""
        cache_file = self.cache_path(path)
        if cache_file.exists():
            self.cache_hits += 1
            return self._read_cache(cache_file)
        if self.offline:
            raise PokeAPIError(
                f"'{path}' is not in the cache and offline mode is on. "
                "Run: python manage.py fetch_pokemon"
            )
        data = self._download(path)
        if transform is not None:
            data = transform(data)
        self._write_cache(cache_file, data)
        return data

    def get_pokemon(self, pokemon_id: int) -> dict:
        return self.get(f"pokemon/{int(pokemon_id)}", transform=slim_pokemon)

    def iter_pokemon(self, start: int = 1, limit: int = 151):
        for pokemon_id in range(start, start + limit):
            yield self.get_pokemon(pokemon_id)


def load_records(start: int = 1, limit: int = 151, offline: bool = False, client=None) -> list:
    """Fetch (or read from cache) Pokemon start..start+limit-1 and normalise them.

    The result is a list of plain dicts that both importers understand, so
    PostgreSQL and MongoDB always receive exactly the same data.
    """
    client = client or PokeAPIClient(offline=offline)
    return [normalize_pokemon(raw) for raw in client.iter_pokemon(start=start, limit=limit)]
