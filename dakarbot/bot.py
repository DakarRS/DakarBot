"""DakarBot client: intents, presence, and cog loading."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from . import settings
from .channel_settings import ChannelSettings
from .db import Database
from .error_reporting import DiscordLogHandler, ErrorReporter
from .guild_settings import GuildSettings

logger = logging.getLogger("dakarbot")

COGS_PACKAGE = "dakarbot.cogs"
COGS_DIR = Path(__file__).parent / "cogs"


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    intents.bans = True
    intents.message_content = True
    intents.guild_messages = True
    intents.guild_reactions = True
    intents.dm_messages = True
    return intents


class DakarBot(commands.AutoShardedBot):
    def __init__(self):
        super().__init__(
            intents=build_intents(),
            allowed_mentions=discord.AllowedMentions(everyone=False, users=True, roles=True),
            status=discord.Status.dnd,
            activity=discord.CustomActivity(name="Watching you"),
            owner_ids=set(settings.OWNER_IDS),
            debug_guilds=settings.DEBUG_GUILD_IDS,
        )
        self.db: Optional[Database] = None
        self.guild_settings: Optional[GuildSettings] = None
        self.channel_settings: Optional[ChannelSettings] = None
        # Messages the bot itself deleted, so the logging cog's on_message_delete
        # doesn't log its own housekeeping deletes as if a user's message vanished.
        # Delete-on-read, no TTL — mirrors modbot's Bot.js `deletedMessages` Set
        # exactly, including its latent (harmless) leak if the gateway event never
        # arrives for a message the bot deleted (e.g. the channel itself is deleted
        # first).
        self.recently_deleted_message_ids: set[int] = set()
        self._load_cogs()

    def _load_cogs(self) -> None:
        # Deliberately not pycord's load_extensions("dakarbot.cogs", recursive=True):
        # that resolves the folder relative to the process's cwd (via
        # os.path.join(*name.split("."))), not this file's location, so it silently
        # loads zero cogs — no error — whenever the bot isn't launched with cwd
        # exactly at the project root (e.g. run from an IDE). Discovering entries
        # ourselves via __file__ and loading each by its resolved dotted module name
        # sidesteps that: name-based load_extension goes through normal import
        # resolution (sys.path), which is cwd-independent.
        #
        # A cog is either a flat dakarbot/cogs/<name>.py file, or a package
        # dakarbot/cogs/<name>/__init__.py — the latter lets a large cog split its
        # command implementations across multiple files while still presenting one
        # loadable extension. Either way, names starting with `_` are skipped (not
        # a cog — shared helpers live there instead, e.g. a package's internal
        # command/modal/view modules).
        names = set()
        for path in COGS_DIR.iterdir():
            if path.name.startswith("_"):
                continue
            if path.is_file() and path.suffix == ".py":
                names.add(path.stem)
            elif path.is_dir() and (path / "__init__.py").is_file():
                names.add(path.name)

        for name in sorted(names):
            extension = f"{COGS_PACKAGE}.{name}"
            try:
                self.load_extension(extension)
                logger.info("Loaded cog %s", extension)
            except Exception:
                logger.exception("Failed to load cog %s", extension)

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        # pycord has no setup_hook (that's a discord.py-2.x-only feature — it doesn't
        # exist anywhere in this package, so overriding it silently never runs).
        # Overriding start() instead runs this in the same loop, before login +
        # gateway connect, which is the actual timing we need: cogs are loaded
        # synchronously in __init__ (above) and access self.db/self.guild_settings
        # lazily, only when a command executes, by which point this has long finished.
        if settings.ERROR_CHANNEL_ID:
            reporter = ErrorReporter(self, settings.ERROR_CHANNEL_ID)
            reporter.start()
            logging.getLogger().addHandler(DiscordLogHandler(reporter))
        self.db = await Database.connect()
        self.guild_settings = GuildSettings(self.db)
        self.channel_settings = ChannelSettings(self.db)
        await super().start(token, reconnect=reconnect)

    async def delete_message(self, message: discord.Message, reason: Optional[str] = None) -> None:
        """Delete a message on the bot's own behalf (e.g. automod removing a rule-
        violating message) without the logging cog reporting it as a generic user-
        vanished deletion. Marks the id before deleting, matching modbot's
        Bot.delete(). If `reason` is given, dispatches on_automod_delete so the
        logging cog can still log the deletion attributed to that specific reason —
        matching Bot.js's own delete(message, reason) calling logMessageDeletion —
        rather than the message just silently disappearing from the log entirely."""
        self.recently_deleted_message_ids.add(message.id)
        await message.delete()
        if reason:
            self.dispatch("automod_delete", message, reason)

    async def close(self) -> None:
        if self.db is not None:
            await self.db.close()
        await super().close()

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        # pycord's default only prints; logging it gets it reported.
        logger.exception("Unhandled error in event %s", event_method)

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")


class DakarCog(commands.Cog):
    """Shared base for DakarBot's cogs: bot/db/guild_settings access."""

    def __init__(self, bot: DakarBot):
        self.bot = bot

    @property
    def db(self) -> Database:
        assert self.bot.db is not None
        return self.bot.db

    @property
    def guild_settings(self) -> GuildSettings:
        assert self.bot.guild_settings is not None
        return self.bot.guild_settings

    @property
    def channel_settings(self) -> ChannelSettings:
        assert self.bot.channel_settings is not None
        return self.bot.channel_settings
