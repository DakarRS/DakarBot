"""Helpers shared by the currency, shop, leveling and leaderboard cogs."""
import logging
from typing import Optional

import discord

from ..guild_settings import GuildSettings
from ..ui import LayoutView

logger = logging.getLogger("dakarbot.currency")

# Handing these out would let anyone with enough coins or XP moderate or run the server.
UNSELLABLE_PERMISSIONS = discord.Permissions(
    administrator=True, manage_guild=True, manage_roles=True, manage_channels=True, manage_messages=True,
    manage_webhooks=True, ban_members=True, kick_members=True, moderate_members=True, mention_everyone=True,
)


def role_problem(role: discord.Role, author: discord.Member) -> Optional[str]:
    if role.is_default() or role.managed:
        return "That role can't be given out."
    if role.permissions.value & UNSELLABLE_PERMISSIONS.value:
        return "That role has moderation or admin permissions, so it can't be given out."
    if role >= role.guild.me.top_role:
        return "That role is above mine, so I can't give it out. Move my role higher first."
    if author != role.guild.owner and role >= author.top_role:
        return "You can only give out roles below your own highest role."
    return None


def panel(text: str, color: discord.Color) -> LayoutView:
    return LayoutView(discord.ui.Container(discord.ui.TextDisplay(text), color=color))


async def log_change(
    guild_settings: GuildSettings, guild: discord.Guild, title: str, actor: discord.abc.User,
    target: discord.abc.User, change: Optional[int], new_balance: int, detail: Optional[str] = None,
) -> None:
    """Posts to the server's currency log channel, if it has one."""
    channel_id = await guild_settings.get_currency_log_channel_id(guild.id)
    channel = guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        return
    name = await guild_settings.get_currency_name(guild.id)
    lines = [f"**Member:** {target.mention} (`{target.id}`)"]
    if detail:
        lines.append(detail)
    if change is not None:
        lines.append(f"**Change:** {change:+,} {name}")
    lines.append(f"**New balance:** {new_balance:,} {name}")
    container = discord.ui.Container(
        discord.ui.TextDisplay(f"**{title}**"),
        discord.ui.TextDisplay("\n".join(lines)),
        discord.ui.TextDisplay(f"-# By {actor.mention} (`{actor.id}`)"),
        color=discord.Color.gold(),
    )
    try:
        await channel.send(view=LayoutView(container), allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        logger.warning("Couldn't post to currency log channel %s in guild %s", channel_id, guild.id)
