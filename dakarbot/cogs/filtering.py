"""Filtering cog: enforces bad-word/caps/invite/cooldown/spam checks on every message
(and content checks again on edit), runs auto-responses, and owns the /bad-word and
/auto-response command groups for managing the DB-backed filters.

Ports modbot's AutoModEventListener.js/AutoModMessageEditEventListener.js/
AutoResponseEventListener.js (see filtering/checks.py for the actual dispatch logic
and its two documented behavior fixes) and src/commands/settings/{BadWord,
AutoResponse}Command.js plus their bad-word/auto-response subcommand folders.

Cloud Vision image-text scanning (modbot's `enableVision` flag) is out of scope —
see filtering/models.py's module docstring.

Simplifications versus modbot, both enabled by pycord commands having a much larger
option budget than Discord's 5-field modal cap (which is what forced modbot's add/
edit flows into a two-stage modal-plus-Confirmation-row dance in the first place):
- add/edit are each a single slash command here, no modal/confirmation-row needed.
- edit is a partial update (only the fields you pass change), matching this
  codebase's own /moderation edit convention, rather than requiring the full record
  to be re-entered every time.
- channel scoping is a single optional channel (or "global") rather than modbot's
  multi-select channel list, matching the option types this codebase already uses
  for other single-channel settings (log-channel, invites override). Multi-channel
  scoping for one filter isn't supported; running `edit` again replaces the channel.

Deliberately NOT using `from __future__ import annotations`: see moderation/base.py's
module docstring for why (pycord reads discord.Option(...) off raw annotations).
"""

import logging
from typing import Optional

import discord
from discord.ext import commands, tasks

from ..bot import DakarBot, DakarCog
from ..error_reporting import restart_crashed_loop
from ..filtering import checks, models, tracking
from ..filtering.models import AutoResponse, BadWord, parse_trigger
from ..moderation.models import Punishment, PunishmentAction
from ..ui import Icon, LayoutView, format_list_or_inline
from .moderation._util import parse_duration_option

logger = logging.getLogger("dakarbot.filtering")

MANAGE_GUILD = discord.Permissions(manage_guild=True)

TRIGGER_TYPE_CHOICES = [
    discord.OptionChoice(name="Include (substring)", value="include"),
    discord.OptionChoice(name="Match (exact)", value="match"),
    discord.OptionChoice(name="Regex", value="regex"),
    discord.OptionChoice(name="Phishing domain", value="phishing"),
]

BAD_WORD_PUNISHMENT_CHOICES = [
    discord.OptionChoice(name="None", value="none"),
    discord.OptionChoice(name="Strike", value="strike"),
    discord.OptionChoice(name="Mute", value="mute"),
    discord.OptionChoice(name="Softban", value="softban"),
    discord.OptionChoice(name="Kick", value="kick"),
    discord.OptionChoice(name="Ban", value="ban"),
]

_LIST_CHUNK_SIZE = 3800
_NO_SCOPE_ERROR = "❌ You must set `global` to True or pick a channel."


async def _bad_word_autocomplete(ctx: discord.AutocompleteContext) -> list[discord.OptionChoice]:
    bot: DakarBot = ctx.interaction.client  # type: ignore[assignment]
    guild_id = ctx.interaction.guild_id
    if guild_id is None or bot.db is None:
        return []
    words = await models.get_all_bad_words(bot.db, guild_id)
    return [
        discord.OptionChoice(name=f"#{w.id} - {w.trigger.as_content_string()[:80]}", value=str(w.id))
        for w in words[:25]
    ]


async def _auto_response_autocomplete(ctx: discord.AutocompleteContext) -> list[discord.OptionChoice]:
    bot: DakarBot = ctx.interaction.client  # type: ignore[assignment]
    guild_id = ctx.interaction.guild_id
    if guild_id is None or bot.db is None:
        return []
    responses = await models.get_all_auto_responses(bot.db, guild_id)
    return [
        discord.OptionChoice(name=f"#{r.id} - {r.trigger.as_content_string()[:80]}", value=str(r.id))
        for r in responses[:25]
    ]


def _scope_text(is_global: bool, channel_ids: list[int]) -> str:
    if is_global:
        return "global"
    return format_list_or_inline([f"<#{c}>" for c in channel_ids])


def _bad_word_overview_line(word: BadWord) -> str:
    return f"**#{word.id}** {_scope_text(word.is_global, word.channel_ids)} — {word.trigger.as_string()} → {word.punishment.action.value}"


def _auto_response_overview_line(response: AutoResponse) -> str:
    return f"**#{response.id}** {_scope_text(response.is_global, response.channel_ids)} — {response.trigger.as_string()}"


def _bad_word_detail(word: BadWord) -> str:
    lines = [
        f"## Bad-word #{word.id}",
        f"**Trigger:** {word.trigger.as_string()}",
        f"**Scope:** {_scope_text(word.is_global, word.channel_ids)}",
        f"**Punishment:** {word.punishment.action.value}"
        + (f" for {word.punishment.duration}s" if word.punishment.duration else "")
        + (f" (x{word.punishment.count})" if word.punishment.count else ""),
        f"**Priority:** {word.priority}",
        f"**Response:** {word.response}",
    ]
    if word.dm:
        lines.append(f"**DM:** {word.dm}")
    return "\n".join(lines)


def _auto_response_detail(response: AutoResponse) -> str:
    return "\n".join(
        [
            f"## Auto-response #{response.id}",
            f"**Trigger:** {response.trigger.as_string()}",
            f"**Scope:** {_scope_text(response.is_global, response.channel_ids)}",
            f"**Response:** {response.response}",
        ]
    )


def _resolve_punishment_update(
    word: BadWord, punishment: Optional[str], duration: Optional[str], strike_count: Optional[int]
) -> tuple[Optional[Punishment], Optional[str]]:
    """Returns (new_punishment, None) if any punishment field was provided, or
    (None, None) if none were touched. Returns (None, error) on a bad duration."""
    if punishment is None and duration is None and strike_count is None:
        return None, None
    try:
        duration_seconds = parse_duration_option(duration) if duration is not None else word.punishment.duration
    except ValueError as exc:
        return None, str(exc)
    action = PunishmentAction(punishment) if punishment is not None else word.punishment.action
    count = strike_count if strike_count is not None else word.punishment.count
    return Punishment(action=action, duration=duration_seconds, count=count), None


async def _send_chunked(ctx: discord.ApplicationContext, title: str, lines: list[str]) -> None:
    if not lines:
        await ctx.respond(f"No {title.lower()} configured.", ephemeral=True)
        return

    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n\n{line}" if current else line
        if len(candidate) > _LIST_CHUNK_SIZE:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)

    for index, chunk in enumerate(chunks):
        heading = f"## {title}"
        if len(chunks) > 1:
            heading += f"\n-# Page {index + 1}/{len(chunks)}"
        text = f"{heading}\n\n{chunk}"
        view = LayoutView(discord.ui.Container(discord.ui.TextDisplay(text), color=discord.Color.blue()))
        if index == 0:
            await ctx.respond(view=view, ephemeral=True)
        else:
            await ctx.followup.send(view=view, ephemeral=True)


class FilteringCog(DakarCog, name="Filtering"):
    def __init__(self, bot: DakarBot) -> None:
        super().__init__(bot)
        self._cleanup_cooldowns.start()

    def cog_unload(self) -> None:
        self._cleanup_cooldowns.cancel()

    @tasks.loop(seconds=5)
    async def _cleanup_cooldowns(self) -> None:
        tracking.cleanup_stale()

    @_cleanup_cooldowns.error
    async def _cleanup_cooldowns_error(self, error: BaseException) -> None:
        restart_crashed_loop(self._cleanup_cooldowns, error)

    # ---- listeners ----

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if self.bot.db is None or self.bot.guild_settings is None:
            return
        await checks.check_message(self.bot, self.db, self.guild_settings, message)
        await checks.check_auto_response(self.db, message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if self.bot.db is None or self.bot.guild_settings is None:
            return
        await checks.check_message_edit(self.bot, self.db, self.guild_settings, after)

    # ---- /bad-word ----

    bad_word = discord.SlashCommandGroup(
        "bad-word", "Manage words/phrases that get automatically filtered.", default_member_permissions=MANAGE_GUILD
    )

    @bad_word.command(name="add", description="Add a new bad-word filter.")
    async def bad_word_add(
        self,
        ctx: discord.ApplicationContext,
        trigger_type: discord.Option(str, description="How to match the trigger", choices=TRIGGER_TYPE_CHOICES),
        trigger: discord.Option(str, description="The trigger content"),
        global_: discord.Option(bool, description="Apply to every channel?", name="global", default=False),
        channel: discord.Option(
            discord.TextChannel, description="Channel to scope this to (if not global)", required=False, default=None
        ),
        response: discord.Option(
            str, description="Message shown when triggered ('disabled' = none)", required=False, default="disabled"
        ),
        punishment: discord.Option(
            str, description="Punishment to apply", choices=BAD_WORD_PUNISHMENT_CHOICES, default="none"
        ),
        duration: discord.Option(str, description="e.g. '1d' (mute/ban only)", required=False, default=None),
        strike_count: discord.Option(
            int, description="Strikes to apply (strike punishment only)", required=False, default=None
        ),
        priority: discord.Option(int, description="Higher priority filters are checked first", default=0),
        dm: discord.Option(str, description="Direct message to send the user", required=False, default=None),
    ) -> None:
        if not global_ and channel is None:
            await ctx.respond(_NO_SCOPE_ERROR, ephemeral=True)
            return

        parsed_trigger, error = parse_trigger(trigger_type, trigger)
        if error:
            await ctx.respond(error, ephemeral=True)
            return

        try:
            duration_seconds = parse_duration_option(duration)
        except ValueError as exc:
            await ctx.respond(str(exc), ephemeral=True)
            return

        word = BadWord(
            id=None,
            guild_id=ctx.guild.id,
            trigger=parsed_trigger,
            punishment=Punishment(
                action=PunishmentAction(punishment), duration=duration_seconds, count=strike_count
            ),
            response=response or "disabled",
            dm=dm,
            is_global=global_,
            channel_ids=[channel.id] if channel else [],
            priority=priority,
        )
        word = await models.create_bad_word(self.db, word)
        await ctx.respond(
            view=LayoutView(discord.ui.Container(discord.ui.TextDisplay(_bad_word_detail(word)), color=discord.Color.green()))
        )

    @bad_word.command(name="edit", description="Edit an existing bad-word filter.")
    async def bad_word_edit(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="Bad-word id", autocomplete=_bad_word_autocomplete),
        trigger_type: discord.Option(
            str, description="How to match the trigger", choices=TRIGGER_TYPE_CHOICES, required=False, default=None
        ),
        trigger: discord.Option(str, description="The trigger content", required=False, default=None),
        global_: discord.Option(
            bool, description="Apply to every channel?", name="global", required=False, default=None
        ),
        channel: discord.Option(
            discord.TextChannel, description="Channel to scope this to (if not global)", required=False, default=None
        ),
        response: discord.Option(
            str, description="Message shown when triggered ('disabled' = none)", required=False, default=None
        ),
        punishment: discord.Option(
            str, description="Punishment to apply", choices=BAD_WORD_PUNISHMENT_CHOICES, required=False, default=None
        ),
        duration: discord.Option(str, description="e.g. '1d' (mute/ban only)", required=False, default=None),
        strike_count: discord.Option(
            int, description="Strikes to apply (strike punishment only)", required=False, default=None
        ),
        priority: discord.Option(
            int, description="Higher priority filters are checked first", required=False, default=None
        ),
        dm: discord.Option(str, description="Direct message to send the user", required=False, default=None),
    ) -> None:
        word = await models.get_bad_word(self.db, ctx.guild.id, id)
        if word is None:
            await ctx.respond(f"❌ No bad-word with id #{id} found.", ephemeral=True)
            return

        if trigger_type is not None or trigger is not None:
            new_trigger, error = parse_trigger(
                trigger_type or word.trigger.type.value, trigger or word.trigger.content
            )
            if error:
                await ctx.respond(error, ephemeral=True)
                return
            word.trigger = new_trigger

        if global_ is not None:
            word.is_global = global_
        if channel is not None:
            word.channel_ids = [channel.id]
        if not word.is_global and not word.channel_ids:
            await ctx.respond(_NO_SCOPE_ERROR, ephemeral=True)
            return

        new_punishment, error = _resolve_punishment_update(word, punishment, duration, strike_count)
        if error:
            await ctx.respond(error, ephemeral=True)
            return
        if new_punishment is not None:
            word.punishment = new_punishment

        if response is not None:
            word.response = response
        if priority is not None:
            word.priority = priority
        if dm is not None:
            word.dm = None if dm == "disabled" else dm

        await models.update_bad_word(self.db, word)
        await ctx.respond(
            view=LayoutView(discord.ui.Container(discord.ui.TextDisplay(_bad_word_detail(word)), color=discord.Color.green()))
        )

    @bad_word.command(name="delete", description="Delete a bad-word filter.")
    async def bad_word_delete(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="Bad-word id", autocomplete=_bad_word_autocomplete),
    ) -> None:
        word = await models.get_bad_word(self.db, ctx.guild.id, id)
        if word is None:
            await ctx.respond(f"❌ No bad-word with id #{id} found.", ephemeral=True)
            return
        await models.delete_bad_word(self.db, ctx.guild.id, id)
        await ctx.respond(f"🗑️ Deleted bad-word #{id}.")

    @bad_word.command(name="show", description="Show a single bad-word filter.")
    async def bad_word_show(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="Bad-word id", autocomplete=_bad_word_autocomplete),
    ) -> None:
        word = await models.get_bad_word(self.db, ctx.guild.id, id)
        if word is None:
            await ctx.respond(f"❌ No bad-word with id #{id} found.", ephemeral=True)
            return

        delete_button = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger)

        async def _delete(interaction: discord.Interaction) -> None:
            await models.delete_bad_word(self.db, ctx.guild.id, id)
            await interaction.response.edit_message(
                view=LayoutView(discord.ui.TextDisplay(f"🗑️ Deleted bad-word #{id}."))
            )

        delete_button.callback = _delete
        container = discord.ui.Container(discord.ui.TextDisplay(_bad_word_detail(word)), color=discord.Color.blue())
        view = LayoutView(container, discord.ui.ActionRow(delete_button), timeout=180)
        await ctx.respond(view=view, ephemeral=True)

    @bad_word.command(name="list", description="List all bad-word filters in this server.")
    async def bad_word_list(self, ctx: discord.ApplicationContext) -> None:
        words = await models.get_all_bad_words(self.db, ctx.guild.id)
        await _send_chunked(ctx, f"{Icon.BAD_WORD} Bad-words", [_bad_word_overview_line(w) for w in words])

    # ---- /auto-response ----

    auto_response = discord.SlashCommandGroup(
        "auto-response", "Manage automatic replies to trigger phrases.", default_member_permissions=MANAGE_GUILD
    )

    @auto_response.command(name="add", description="Add a new auto-response.")
    async def auto_response_add(
        self,
        ctx: discord.ApplicationContext,
        trigger_type: discord.Option(str, description="How to match the trigger", choices=TRIGGER_TYPE_CHOICES),
        trigger: discord.Option(str, description="The trigger content"),
        response: discord.Option(str, description="What to reply with"),
        global_: discord.Option(bool, description="Apply to every channel?", name="global", default=False),
        channel: discord.Option(
            discord.TextChannel, description="Channel to scope this to (if not global)", required=False, default=None
        ),
    ) -> None:
        if not global_ and channel is None:
            await ctx.respond(_NO_SCOPE_ERROR, ephemeral=True)
            return

        parsed_trigger, error = parse_trigger(trigger_type, trigger)
        if error:
            await ctx.respond(error, ephemeral=True)
            return

        response_obj = AutoResponse(
            id=None,
            guild_id=ctx.guild.id,
            trigger=parsed_trigger,
            response=response,
            is_global=global_,
            channel_ids=[channel.id] if channel else [],
        )
        response_obj = await models.create_auto_response(self.db, response_obj)
        await ctx.respond(
            view=LayoutView(
                discord.ui.Container(discord.ui.TextDisplay(_auto_response_detail(response_obj)), color=discord.Color.green())
            )
        )

    @auto_response.command(name="edit", description="Edit an existing auto-response.")
    async def auto_response_edit(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="Auto-response id", autocomplete=_auto_response_autocomplete),
        trigger_type: discord.Option(
            str, description="How to match the trigger", choices=TRIGGER_TYPE_CHOICES, required=False, default=None
        ),
        trigger: discord.Option(str, description="The trigger content", required=False, default=None),
        response: discord.Option(str, description="What to reply with", required=False, default=None),
        global_: discord.Option(
            bool, description="Apply to every channel?", name="global", required=False, default=None
        ),
        channel: discord.Option(
            discord.TextChannel, description="Channel to scope this to (if not global)", required=False, default=None
        ),
    ) -> None:
        response_obj = await models.get_auto_response(self.db, ctx.guild.id, id)
        if response_obj is None:
            await ctx.respond(f"❌ No auto-response with id #{id} found.", ephemeral=True)
            return

        if trigger_type is not None or trigger is not None:
            new_trigger, error = parse_trigger(
                trigger_type or response_obj.trigger.type.value, trigger or response_obj.trigger.content
            )
            if error:
                await ctx.respond(error, ephemeral=True)
                return
            response_obj.trigger = new_trigger

        if global_ is not None:
            response_obj.is_global = global_
        if channel is not None:
            response_obj.channel_ids = [channel.id]
        if not response_obj.is_global and not response_obj.channel_ids:
            await ctx.respond(_NO_SCOPE_ERROR, ephemeral=True)
            return

        if response is not None:
            response_obj.response = response

        await models.update_auto_response(self.db, response_obj)
        await ctx.respond(
            view=LayoutView(
                discord.ui.Container(discord.ui.TextDisplay(_auto_response_detail(response_obj)), color=discord.Color.green())
            )
        )

    @auto_response.command(name="delete", description="Delete an auto-response.")
    async def auto_response_delete(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="Auto-response id", autocomplete=_auto_response_autocomplete),
    ) -> None:
        response_obj = await models.get_auto_response(self.db, ctx.guild.id, id)
        if response_obj is None:
            await ctx.respond(f"❌ No auto-response with id #{id} found.", ephemeral=True)
            return
        await models.delete_auto_response(self.db, ctx.guild.id, id)
        await ctx.respond(f"🗑️ Deleted auto-response #{id}.")

    @auto_response.command(name="show", description="Show a single auto-response.")
    async def auto_response_show(
        self,
        ctx: discord.ApplicationContext,
        id: discord.Option(int, description="Auto-response id", autocomplete=_auto_response_autocomplete),
    ) -> None:
        response_obj = await models.get_auto_response(self.db, ctx.guild.id, id)
        if response_obj is None:
            await ctx.respond(f"❌ No auto-response with id #{id} found.", ephemeral=True)
            return

        delete_button = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger)

        async def _delete(interaction: discord.Interaction) -> None:
            await models.delete_auto_response(self.db, ctx.guild.id, id)
            await interaction.response.edit_message(
                view=LayoutView(discord.ui.TextDisplay(f"🗑️ Deleted auto-response #{id}."))
            )

        delete_button.callback = _delete
        container = discord.ui.Container(
            discord.ui.TextDisplay(_auto_response_detail(response_obj)), color=discord.Color.blue()
        )
        view = LayoutView(container, discord.ui.ActionRow(delete_button), timeout=180)
        await ctx.respond(view=view, ephemeral=True)

    @auto_response.command(name="list", description="List all auto-responses in this server.")
    async def auto_response_list(self, ctx: discord.ApplicationContext) -> None:
        responses = await models.get_all_auto_responses(self.db, ctx.guild.id)
        await _send_chunked(ctx, f"{Icon.AUTO_RESPONSE} Auto-responses", [_auto_response_overview_line(r) for r in responses])


def setup(bot: DakarBot) -> None:
    bot.add_cog(FilteringCog(bot))
