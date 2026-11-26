"""Right-click "Translate" on any message, and /translate for typed text. The menu
reply is private and goes into the clicker's own Discord language; /translate is
public and defaults to the same."""
from typing import Optional

import discord
from discord.ext import commands

from ..bot import EVERYWHERE, DakarBot, DakarCog
from ..translate import (
    LANGUAGES,
    MAX_INPUT_CHARS,
    TranslateError,
    language_name,
    locale_to_language,
    translate,
)
from ..ui import LayoutView

TEXT_LIMIT = 3800
NO_MENTIONS = discord.AllowedMentions.none()
LANGUAGE_CHOICES = [discord.OptionChoice(name, code) for code, name in LANGUAGES.items()]


def _same_language(detected: Optional[str], target: str) -> bool:
    return bool(detected) and detected.split("-")[0].lower() == target.split("-")[0].lower()


class TranslateCog(DakarCog, name="Translate"):
    async def _reply(self, ctx: discord.ApplicationContext, text: str, target: str,
                     source_link: Optional[str] = None) -> None:
        try:
            result = await translate(self.http_session, text, target)
        except TranslateError as error:
            await ctx.respond(str(error), ephemeral=True)
            return
        if _same_language(result.detected, target):
            await ctx.respond(f"That's already in {language_name(target)}.", ephemeral=True)
            return
        footer = f"-# {language_name(result.detected)} to {language_name(target)}"
        if source_link:
            footer += f" - [original message]({source_link})"
        if result.truncated:
            footer += f"\n-# Only the first {MAX_INPUT_CHARS:,} characters were translated."
        container = discord.ui.Container(
            discord.ui.TextDisplay(f"## Translation\n{result.text[:TEXT_LIMIT]}"),
            discord.ui.TextDisplay(footer),
            color=discord.Color.teal(),
        )
        await ctx.respond(view=LayoutView(container), allowed_mentions=NO_MENTIONS)

    @discord.message_command(name="Translate", **EVERYWHERE)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def translate_message(self, ctx: discord.ApplicationContext, message: discord.Message) -> None:
        if not message.content.strip():
            await ctx.respond("That message has no text to translate.", ephemeral=True)
            return
        await ctx.defer(ephemeral=True)
        await self._reply(ctx, message.content, locale_to_language(ctx.interaction.locale), message.jump_url)

    @discord.slash_command(name="translate", description="Translate some text.", **EVERYWHERE)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def translate_text(
        self,
        ctx: discord.ApplicationContext,
        text: discord.Option(str, description="What to translate", max_length=MAX_INPUT_CHARS),
        to: discord.Option(str, description="Language to translate into (default: your Discord language)",
                           choices=LANGUAGE_CHOICES, required=False, default=None),
    ) -> None:
        await ctx.defer()
        await self._reply(ctx, text, to or locale_to_language(ctx.interaction.locale))


def setup(bot: DakarBot) -> None:
    bot.add_cog(TranslateCog(bot))
