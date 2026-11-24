"""PokeAPI lookups for /pokemon. exorium also called some-random-api for the
description, evolution line and egg groups; that API is gone, and PokeAPI's species
and evolution-chain endpoints cover the same fields."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .fun_apis import get_json

API_BASE = "https://pokeapi.co/api/v2"
AUTOCOMPLETE_LIMIT = 25

_STAT_LABELS = {
    "hp": "HP", "attack": "Attack", "defense": "Defense",
    "special-attack": "Sp. Atk", "special-defense": "Sp. Def", "speed": "Speed",
}


@dataclass(frozen=True)
class PokemonInfo:
    name: str
    display_name: str
    artwork_url: Optional[str]
    types: list[str]
    abilities: list[str]
    stats: dict[str, int]
    description: Optional[str]
    genus: Optional[str]
    egg_groups: list[str]
    evolution_line: list[str]
    evolution_stage: int

    @property
    def bulbapedia_url(self) -> str:
        return f"https://bulbapedia.bulbagarden.net/wiki/{self.display_name.replace(' ', '_')}_(Pok%C3%A9mon)"


def normalize_name(name: str) -> str:
    cleaned = re.sub(r"[.'’:]", "", name.strip().lower())
    return re.sub(r"\s+", "-", cleaned)


def _title(slug: str) -> str:
    return slug.replace("-", " ").title()


def _english(entries: list[dict], key: str) -> Optional[str]:
    for entry in entries:
        if entry["language"]["name"] == "en":
            return entry[key]
    return None


def _walk_chain(node: dict, depth: int, line: list[tuple[str, int]]) -> None:
    line.append((node["species"]["name"], depth))
    for child in node["evolves_to"]:
        _walk_chain(child, depth + 1, line)


def summarize(pokemon: dict, species: dict, chain: dict) -> PokemonInfo:
    line: list[tuple[str, int]] = []
    _walk_chain(chain["chain"], 1, line)
    stage = next((depth for name, depth in line if name == pokemon["name"]), 1)

    description = _english(species["flavor_text_entries"], "flavor_text")
    if description:
        description = " ".join(description.split())

    return PokemonInfo(
        name=pokemon["name"],
        display_name=_english(species["names"], "name") or _title(pokemon["name"]),
        artwork_url=pokemon["sprites"]["other"]["official-artwork"]["front_default"],
        types=[_title(t["type"]["name"]) for t in sorted(pokemon["types"], key=lambda t: t["slot"])],
        abilities=[
            _title(a["ability"]["name"]) + (" (hidden)" if a["is_hidden"] else "") for a in pokemon["abilities"]
        ],
        stats={_STAT_LABELS.get(s["stat"]["name"], s["stat"]["name"]): s["base_stat"] for s in pokemon["stats"]},
        description=description,
        genus=_english(species["genera"], "genus"),
        egg_groups=[_title(g["name"]) for g in species["egg_groups"]],
        evolution_line=[_title(name) for name, _ in line],
        evolution_stage=stage,
    )


class PokeApi:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._names: Optional[list[str]] = None

    async def fetch(self, name: str) -> Optional[PokemonInfo]:
        pokemon = await get_json(self._session, f"{API_BASE}/pokemon/{normalize_name(name)}")
        if not pokemon:
            return None
        species = await get_json(self._session, pokemon["species"]["url"])
        if not species:
            return None
        chain = await get_json(self._session, species["evolution_chain"]["url"])
        if not chain:
            return None
        return summarize(pokemon, species, chain)

    async def search_names(self, prefix: str) -> list[str]:
        if self._names is None:
            listing = await get_json(self._session, f"{API_BASE}/pokemon-species?limit=2000")
            if not listing:
                return []  # retry on the next keystroke rather than caching an outage
            self._names = [entry["name"] for entry in listing["results"]]
        prefix = normalize_name(prefix)
        return [n for n in self._names if n.startswith(prefix)][:AUTOCOMPLETE_LIMIT]
