import asyncio

import pytest

from dakarbot.economy.balances import Balances, InsufficientFunds
from dakarbot.economy.shop import MAX_ITEMS, Shop, ShopError

GUILD, OTHER_GUILD = 1, 2
ALICE, BOB = 10, 20
ROLE = 555


async def _no_grant(item) -> None:
    pass


async def test_add_and_list_items_sorted_by_price(db):
    shop = Shop(db)
    await shop.add(GUILD, "Trophy", 500, description="Shiny")
    await shop.add(GUILD, "VIP", 100, role_id=ROLE)
    items = await shop.list(GUILD)
    assert [(i.name, i.price, i.role_id, i.description) for i in items] == [
        ("VIP", 100, ROLE, None), ("Trophy", 500, None, "Shiny"),
    ]
    assert await shop.list(OTHER_GUILD) == []


async def test_item_names_are_unique_per_guild_ignoring_case(db):
    shop = Shop(db)
    await shop.add(GUILD, "Trophy", 500)
    with pytest.raises(ShopError):
        await shop.add(GUILD, "trophy", 300)
    await shop.add(OTHER_GUILD, "Trophy", 300)


async def test_price_must_be_positive(db):
    with pytest.raises(ShopError):
        await Shop(db).add(GUILD, "Free", 0)


async def test_shop_is_limited_in_size(db):
    shop = Shop(db)
    for n in range(MAX_ITEMS):
        await shop.add(GUILD, f"Item {n}", 10)
    with pytest.raises(ShopError):
        await shop.add(GUILD, "One too many", 10)


async def test_get_is_case_insensitive(db):
    shop = Shop(db)
    await shop.add(GUILD, "Trophy", 500)
    assert (await shop.get(GUILD, "TROPHY")).name == "Trophy"
    assert await shop.get(GUILD, "Nope") is None


async def test_buy_charges_and_adds_to_inventory(db):
    shop = Shop(db)
    await Balances(db).set(GUILD, ALICE, 1200)
    await shop.add(GUILD, "Trophy", 500)
    purchase = await shop.buy(GUILD, ALICE, "trophy", _no_grant)
    assert (purchase.item.name, purchase.balance) == ("Trophy", 700)
    await shop.buy(GUILD, ALICE, "Trophy", _no_grant)
    assert await Balances(db).get(GUILD, ALICE) == 200
    assert [(e.name, e.count) for e in await shop.inventory(GUILD, ALICE)] == [("Trophy", 2)]
    assert await shop.inventory(GUILD, BOB) == []


async def test_buy_with_insufficient_funds_changes_nothing(db):
    shop = Shop(db)
    await Balances(db).set(GUILD, ALICE, 100)
    await shop.add(GUILD, "Trophy", 500)
    with pytest.raises(InsufficientFunds):
        await shop.buy(GUILD, ALICE, "Trophy", _no_grant)
    assert await Balances(db).get(GUILD, ALICE) == 100
    assert await shop.inventory(GUILD, ALICE) == []


async def test_buy_unknown_item_fails(db):
    with pytest.raises(ShopError):
        await Shop(db).buy(GUILD, ALICE, "Nope", _no_grant)


async def test_role_grant_receives_the_item(db):
    shop = Shop(db)
    await Balances(db).set(GUILD, ALICE, 100)
    await shop.add(GUILD, "VIP", 100, role_id=ROLE)
    granted = []

    async def grant(item):
        granted.append(item.role_id)

    await shop.buy(GUILD, ALICE, "VIP", grant)
    assert granted == [ROLE]


async def test_failed_grant_refunds_purchase(db):
    shop = Shop(db)
    await Balances(db).set(GUILD, ALICE, 100)
    await shop.add(GUILD, "VIP", 100, role_id=ROLE)

    async def failing_grant(item):
        raise ShopError("Couldn't give you that role.")

    with pytest.raises(ShopError):
        await shop.buy(GUILD, ALICE, "VIP", failing_grant)
    assert await Balances(db).get(GUILD, ALICE) == 100
    assert await shop.inventory(GUILD, ALICE) == []


async def test_role_item_cannot_be_bought_twice(db):
    shop = Shop(db)
    await Balances(db).set(GUILD, ALICE, 300)
    await shop.add(GUILD, "VIP", 100, role_id=ROLE)
    await shop.buy(GUILD, ALICE, "VIP", _no_grant)
    with pytest.raises(ShopError):
        await shop.buy(GUILD, ALICE, "VIP", _no_grant)
    assert await Balances(db).get(GUILD, ALICE) == 200


async def test_concurrent_buys_cannot_overspend(db):
    shop = Shop(db)
    await Balances(db).set(GUILD, ALICE, 1000)
    await shop.add(GUILD, "Trophy", 400)
    results = await asyncio.gather(
        *(shop.buy(GUILD, ALICE, "Trophy", _no_grant) for _ in range(5)), return_exceptions=True
    )
    assert sum(not isinstance(r, Exception) for r in results) == 2
    assert await Balances(db).get(GUILD, ALICE) == 200


async def test_concurrent_role_buys_only_charge_once(db):
    shop = Shop(db)
    await Balances(db).set(GUILD, ALICE, 1000)
    await shop.add(GUILD, "VIP", 100, role_id=ROLE)
    await asyncio.gather(*(shop.buy(GUILD, ALICE, "VIP", _no_grant) for _ in range(5)), return_exceptions=True)
    assert await Balances(db).get(GUILD, ALICE) == 900


async def test_removed_item_leaves_shop_but_stays_in_inventory(db):
    shop = Shop(db)
    await Balances(db).set(GUILD, ALICE, 500)
    await shop.add(GUILD, "Trophy", 500)
    await shop.buy(GUILD, ALICE, "Trophy", _no_grant)
    assert await shop.remove(GUILD, "trophy") is True
    assert await shop.list(GUILD) == []
    assert await shop.get(GUILD, "Trophy") is None
    assert [e.name for e in await shop.inventory(GUILD, ALICE)] == ["Trophy"]
    assert await shop.remove(GUILD, "Trophy") is False


async def test_removed_name_can_be_reused(db):
    shop = Shop(db)
    await shop.add(GUILD, "Trophy", 500)
    await shop.remove(GUILD, "Trophy")
    await shop.add(GUILD, "Trophy", 300)
    assert (await shop.get(GUILD, "Trophy")).price == 300
