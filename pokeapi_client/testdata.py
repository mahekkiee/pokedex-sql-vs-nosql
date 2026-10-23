"""Sample data for tests: 8 real PokeAPI responses (slimmed), so tests never call the API."""
import json
from pathlib import Path

from .normalize import normalize_pokemon

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_pokemon.json"


def load_sample_raw() -> list:
    with FIXTURE.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_sample_records() -> list:
    """bulbasaur, charmander, charizard, squirtle, pikachu, gengar, gyarados, mewtwo"""
    return [normalize_pokemon(raw) for raw in load_sample_raw()]
