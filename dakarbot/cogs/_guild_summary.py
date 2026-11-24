"""Server summary shared by /owner info server and the bot join/leave notices."""
from typing import Optional

import discord


def guild_summary_container(
    guild: discord.Guild, title: str, color: discord.Color, footer: Optional[str] = None
) -> discord.ui.Container:
    lines = [
        f"**ID:** {guild.id}",
        f"**Owner:** <@{guild.owner_id}> (`{guild.owner_id}`)",
        f"**Members:** {guild.member_count or 0:,}",
        f"**Channels:** {len(guild.text_channels)} text, {len(guild.voice_channels)} voice",
        f"**Created:** <t:{int(guild.created_at.timestamp())}:D>",
    ]
    if guild.me is not None and guild.me.joined_at:
        lines.append(f"**Bot joined:** <t:{int(guild.me.joined_at.timestamp())}:D>")
    heading = discord.ui.TextDisplay(f"## {title}\n{discord.utils.escape_markdown(guild.name)}")
    top = discord.ui.Section(heading, accessory=discord.ui.Thumbnail(url=guild.icon.url)) if guild.icon else heading
    items: list[discord.ui.Item] = [top, discord.ui.TextDisplay("\n".join(lines))]
    if footer:
        items.append(discord.ui.TextDisplay(f"-# {footer}"))
    return discord.ui.Container(*items, color=color)


def parse_snowflake(raw: str) -> Optional[int]:
    raw = raw.strip()
    return int(raw) if raw.isdigit() and len(raw) <= 20 else None


def guild_icon_container(guild: discord.Guild) -> discord.ui.Container:
    url = guild.icon.with_size(4096).url
    return discord.ui.Container(
        discord.ui.TextDisplay(f"## Icon of {discord.utils.escape_markdown(guild.name)}"),
        discord.ui.MediaGallery(discord.MediaGalleryItem(url)),
        discord.ui.TextDisplay(f"[Download]({url})"),
        color=discord.Color.blue(),
    )
