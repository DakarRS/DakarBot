from dakarbot.fun_apis import FOX_URL, fetch_fox
from dakarbot.pokemon import API_BASE, PokeApi, normalize_name, summarize
from tests.conftest import FakeResponse

POKEMON = {
    "name": "ivysaur",
    "sprites": {"other": {"official-artwork": {"front_default": "https://img/ivysaur.png"}}},
    "types": [{"slot": 1, "type": {"name": "grass"}}, {"slot": 2, "type": {"name": "poison"}}],
    "abilities": [
        {"ability": {"name": "overgrow"}, "is_hidden": False},
        {"ability": {"name": "chlorophyll"}, "is_hidden": True},
    ],
    "stats": [
        {"base_stat": 60, "stat": {"name": "hp"}},
        {"base_stat": 62, "stat": {"name": "attack"}},
        {"base_stat": 63, "stat": {"name": "defense"}},
        {"base_stat": 80, "stat": {"name": "special-attack"}},
        {"base_stat": 80, "stat": {"name": "special-defense"}},
        {"base_stat": 60, "stat": {"name": "speed"}},
    ],
    "species": {"url": f"{API_BASE}/pokemon-species/2/"},
}
SPECIES = {
    "names": [{"name": "Bisasam", "language": {"name": "de"}}, {"name": "Ivysaur", "language": {"name": "en"}}],
    "genera": [{"genus": "Seed Pokémon", "language": {"name": "en"}}],
    "flavor_text_entries": [
        {"flavor_text": "Ein Text", "language": {"name": "de"}},
        {"flavor_text": "When the bulb on\nits back grows\x0clarge, it appears", "language": {"name": "en"}},
    ],
    "egg_groups": [{"name": "monster"}, {"name": "plant"}],
    "evolution_chain": {"url": f"{API_BASE}/evolution-chain/1/"},
}
CHAIN = {
    "chain": {
        "species": {"name": "bulbasaur"},
        "evolves_to": [{
            "species": {"name": "ivysaur"},
            "evolves_to": [{"species": {"name": "venusaur"}, "evolves_to": []}],
        }],
    }
}


def test_summarize_extracts_everything():
    info = summarize(POKEMON, SPECIES, CHAIN)
    assert info.display_name == "Ivysaur"
    assert info.artwork_url == "https://img/ivysaur.png"
    assert info.types == ["Grass", "Poison"]
    assert info.abilities == ["Overgrow", "Chlorophyll (hidden)"]
    assert info.stats == {"HP": 60, "Attack": 62, "Defense": 63, "Sp. Atk": 80, "Sp. Def": 80, "Speed": 60}
    assert info.description == "When the bulb on its back grows large, it appears"
    assert info.genus == "Seed Pokémon"
    assert info.egg_groups == ["Monster", "Plant"]
    assert info.evolution_line == ["Bulbasaur", "Ivysaur", "Venusaur"]
    assert info.evolution_stage == 2


def test_normalize_name():
    assert normalize_name("  Mr. Mime ") == "mr-mime"
    assert normalize_name("Farfetch'd") == "farfetchd"
    assert normalize_name("PIKACHU") == "pikachu"


async def test_fetch_follows_species_and_chain(fake_session):
    session = fake_session({
        f"{API_BASE}/pokemon/ivysaur": FakeResponse(json_data=POKEMON),
        f"{API_BASE}/pokemon-species/2/": FakeResponse(json_data=SPECIES),
        f"{API_BASE}/evolution-chain/1/": FakeResponse(json_data=CHAIN),
    })
    info = await PokeApi(session).fetch("Ivysaur")
    assert info.display_name == "Ivysaur"


async def test_fetch_unknown_pokemon_returns_none(fake_session):
    assert await PokeApi(fake_session({})).fetch("notamon") is None


async def test_names_are_cached_and_filtered(fake_session):
    listing = {"results": [{"name": "pikachu"}, {"name": "pichu"}, {"name": "bulbasaur"}]}
    session = fake_session({f"{API_BASE}/pokemon-species?limit=2000": FakeResponse(json_data=listing)})
    api = PokeApi(session)
    assert await api.search_names("pi") == ["pikachu", "pichu"]
    assert await api.search_names("bulb") == ["bulbasaur"]
    assert len(session.requested) == 1


async def test_fetch_fox(fake_session):
    session = fake_session({FOX_URL: FakeResponse(json_data={"image": "https://randomfox.ca/images/1.jpg"})})
    assert await fetch_fox(session) == "https://randomfox.ca/images/1.jpg"


async def test_fetch_fox_handles_outage(fake_session):
    session = fake_session({FOX_URL: FakeResponse(status=503)})
    assert await fetch_fox(session) is None
