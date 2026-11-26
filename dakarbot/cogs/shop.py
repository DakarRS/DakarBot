"""/shop, /buy, /inventory and the admin /shop-admin group. /shop can't also be the
admin group: a group's permissions apply to all of its subcommands."""
import discord
from discord.ext import commands

from ..bot import DakarBot, DakarCog
from ..economy.balances import MAX_BALANCE, PaymentError
from ..economy.shop import MAX_DESCRIPTION_LENGTH, MAX_NAME_LENGTH, Shop, ShopError, ShopItem
from ._economy import log_change, panel, role_problem

MANAGE_GUILD = discord.Permissions(manage_guild=True)
NO_MENTIONS = discord.AllowedMentions.none()


def _describe(item: ShopItem) -> str:
    line = f"**{discord.utils.escape_markdown(item.name)}** - {item.price:,}"
    if item.role_id:
        line += f"\n-# Gives <@&{item.role_id}>"
    if item.description:
        line += f"\n-# {discord.utils.escape_markdown(item.description)}"
    return line


async def _item_names(ctx: discord.AutocompleteContext) -> list[str]:
    items = await Shop(ctx.cog.db).list(ctx.interaction.guild_id)
    typed = (ctx.value or "").lower()
    return [item.name for item in items if typed in item.name.lower()]


class ShopCog(DakarCog, name="Shop"):
    @property
    def shop(self) -> Shop:
        return Shop(self.db)

    @discord.slash_command(name="shop", description="See what's for sale in this server.")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def shop_list(self, ctx: discord.ApplicationContext) -> None:
        items = await self.shop.list(ctx.guild.id)
        if not items:
            await ctx.respond("The shop is empty. Admins can add items with /shop-admin add.", ephemeral=True)
            return
        name = await self.guild_settings.get_currency_name(ctx.guild.id)
        text = f"## Shop\nPrices are in {name}. Buy with /buy.\n\n" + "\n".join(_describe(i) for i in items)
        await ctx.respond(view=panel(text[:3900], discord.Color.gold()), allowed_mentions=NO_MENTIONS)

    @discord.slash_command(name="buy", description="Buy an item from the shop.")
    @commands.guild_only()
    @commands.cooldown(1, 3, commands.BucketType.member)
    async def buy(
        self,
        ctx: discord.ApplicationContext,
        item: discord.Option(str, description="What to buy", autocomplete=_item_names, max_length=MAX_NAME_LENGTH),
    ) -> None:
        member: discord.Member = ctx.author

        async def grant(bought: ShopItem) -> None:
            if bought.role_id is None:
                return
            role = ctx.guild.get_role(bought.role_id)
            if role is None:
                raise ShopError("That item's role no longer exists. Ask an admin to fix the shop.")
            if role in member.roles:
                raise ShopError(f"You already have the {role.name} role.")
            try:
                await member.add_roles(role, reason=f"Bought {bought.name} in the shop")
            except discord.HTTPException:
                raise ShopError("I couldn't give you that role, so you weren't charged.") from None

        try:
            purchase = await self.shop.buy(ctx.guild.id, member.id, item, grant)
        except (ShopError, PaymentError) as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        bought = purchase.item
        name = await self.guild_settings.get_currency_name(ctx.guild.id)
        text = f"You bought **{discord.utils.escape_markdown(bought.name)}** for **{bought.price:,}** {name}."
        if bought.role_id:
            text += f"\nYou now have <@&{bought.role_id}>."
        text += f"\n-# Balance: {purchase.balance:,} {name}"
        await ctx.respond(view=panel(text, discord.Color.green()), allowed_mentions=NO_MENTIONS)
        await log_change(self.guild_settings, ctx.guild, "Purchase", member, member, -bought.price,
                         purchase.balance, detail=f"**Item:** {discord.utils.escape_markdown(bought.name)}")

    @discord.slash_command(name="inventory", description="Show what you've bought, or someone else's.")
    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def inventory(
        self,
        ctx: discord.ApplicationContext,
        member: discord.Option(discord.Member, description="Whose inventory to show", required=False, default=None),
    ) -> None:
        member = member or ctx.author
        entries = await self.shop.inventory(ctx.guild.id, member.id)
        whose = "Your" if member == ctx.author else f"{member.display_name}'s"
        heading = f"## {discord.utils.escape_markdown(whose)} inventory"
        if not entries:
            await ctx.respond(view=panel(f"{heading}\nNothing yet. See /shop.", discord.Color.light_grey()))
            return
        lines = []
        for entry in entries:
            line = f"**{discord.utils.escape_markdown(entry.name)}**"
            if entry.count > 1:
                line += f" x{entry.count}"
            if entry.role_id:
                line += f" (<@&{entry.role_id}>)"
            lines.append(line)
        text = heading + "\n" + "\n".join(lines)
        await ctx.respond(view=panel(text[:3900], discord.Color.gold()), allowed_mentions=NO_MENTIONS)

    shop_admin = discord.SlashCommandGroup(
        "shop-admin", "Manage this server's shop.", default_member_permissions=MANAGE_GUILD, guild_only=True
    )

    @shop_admin.command(name="add", description="Add an item to the shop.")
    async def shop_add(
        self,
        ctx: discord.ApplicationContext,
        name: discord.Option(str, description="The item's name", max_length=MAX_NAME_LENGTH),
        price: discord.Option(int, description="How much it costs", min_value=1, max_value=MAX_BALANCE),
        role: discord.Option(discord.Role, description="A role buying it gives", required=False, default=None),
        description: discord.Option(str, description="Shown in the shop", required=False, default=None,
                                    max_length=MAX_DESCRIPTION_LENGTH),
    ) -> None:
        if role is not None and (problem := role_problem(role, ctx.author)):
            await ctx.respond(problem, ephemeral=True)
            return
        try:
            item = await self.shop.add(ctx.guild.id, name, price, role_id=role.id if role else None,
                                       description=description)
        except ShopError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        await ctx.respond(f"Added to the shop:\n{_describe(item)}", allowed_mentions=NO_MENTIONS)

    @shop_admin.command(name="remove", description="Take an item out of the shop. Owners keep it.")
    async def shop_remove(
        self,
        ctx: discord.ApplicationContext,
        name: discord.Option(str, description="Which item", autocomplete=_item_names, max_length=MAX_NAME_LENGTH),
    ) -> None:
        removed = await self.shop.remove(ctx.guild.id, name)
        safe = discord.utils.escape_markdown(name)
        await ctx.respond(f"Removed **{safe}** from the shop." if removed else f"The shop has no item called {safe}.",
                          ephemeral=not removed)


def setup(bot: DakarBot) -> None:
    bot.add_cog(ShopCog(bot))
