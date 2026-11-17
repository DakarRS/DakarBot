"""Posts errors and warnings to a Discord channel.

Everything funnels through Python logging: command, component, listener and task
errors are logged with context, and DiscordLogHandler forwards the records here. That
way existing logger.exception(...) calls in cogs get reported too, for free.
"""
from __future__ import annotations

import asyncio
import io
import logging
import time
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import discord
from discord.ext import tasks

if TYPE_CHECKING:
    from .bot import DakarBot

# Our own failures are logged here and never forwarded, or a broken channel would loop.
logger = logging.getLogger("dakarbot.error_reporting")

DEDUPE_WINDOW_SECONDS = 600
QUEUE_LIMIT = 100
INLINE_TRACEBACK_LIMIT = 1800
MESSAGE_LIMIT = 1000


@dataclass
class Report:
    level: int
    logger_name: str
    message: str
    traceback: Optional[str]
    created: float
    fingerprint: str
    suppressed: int = 0


def _fingerprint(record: logging.LogRecord) -> str:
    # Same logger + message template + exception type and origin = the same problem.
    parts = [record.name, str(record.msg)]
    if record.exc_info and record.exc_info[1] is not None:
        exc = record.exc_info[1]
        parts.append(type(exc).__name__)
        frames = traceback.extract_tb(exc.__traceback__)
        if frames:
            parts.append(f"{frames[-1].filename}:{frames[-1].lineno}")
    return "|".join(parts)


def _should_report(record: logging.LogRecord) -> bool:
    if record.name.startswith(logger.name):
        return False
    # Library warnings (missing voice deps, rate limits) are noise; ours are worth seeing.
    if record.levelno >= logging.ERROR:
        return True
    return record.levelno >= logging.WARNING and record.name.startswith("dakarbot")


class ErrorReporter:
    def __init__(self, bot: "DakarBot", channel_id: int):
        self.bot = bot
        self.channel_id = channel_id
        self.queue: asyncio.Queue[Report] = asyncio.Queue(maxsize=QUEUE_LIMIT)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_sent: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}
        self._worker: Optional[asyncio.Task] = None

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._worker = self.loop.create_task(self._run())

    def submit(self, record: logging.LogRecord) -> None:
        """Called from logging, possibly off the event loop thread."""
        if self.loop is None or self.loop.is_closed():
            return
        tb = None
        if record.exc_info and record.exc_info[1] is not None:
            tb = "".join(traceback.format_exception(*record.exc_info))
        report = Report(
            level=record.levelno,
            logger_name=record.name,
            message=record.getMessage(),
            traceback=tb,
            created=record.created,
            fingerprint=_fingerprint(record),
        )
        self.loop.call_soon_threadsafe(self._enqueue, report)

    def _enqueue(self, report: Report) -> None:
        now = time.monotonic()
        last = self._last_sent.get(report.fingerprint)
        if last is not None and now - last < DEDUPE_WINDOW_SECONDS:
            self._suppressed[report.fingerprint] = self._suppressed.get(report.fingerprint, 0) + 1
            return
        self._last_sent[report.fingerprint] = now
        report.suppressed = self._suppressed.pop(report.fingerprint, 0)
        try:
            self.queue.put_nowait(report)
        except asyncio.QueueFull:
            pass

    async def _run(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            report = await self.queue.get()
            try:
                await self._send(report)
            except Exception:
                logger.exception("Failed to post error report to channel %s", self.channel_id)

    async def _send(self, report: Report) -> None:
        channel = self.bot.get_channel(self.channel_id) or await self.bot.fetch_channel(self.channel_id)
        container, file = build_report(report)
        kwargs = {"file": file} if file else {}
        await channel.send(
            view=_view(container), allowed_mentions=discord.AllowedMentions.none(), **kwargs
        )


def _view(container: discord.ui.Container) -> discord.ui.BaseView:
    from .ui import LayoutView  # ui imports this module for its on_error hook

    return LayoutView(container)


def build_report(report: Report) -> tuple[discord.ui.Container, Optional[discord.File]]:
    is_error = report.level >= logging.ERROR
    title = "❌ Error" if is_error else "⚠️ Warning"
    if report.traceback:
        title += f" — `{report.traceback.strip().splitlines()[-1][:120]}`"

    message = report.message
    if len(message) > MESSAGE_LIMIT:
        message = message[:MESSAGE_LIMIT] + "…"

    items: list[discord.ui.Item] = [
        discord.ui.TextDisplay(f"## {title}"),
        discord.ui.TextDisplay(message or "*(no message)*"),
    ]

    file = None
    if report.traceback:
        tb = report.traceback.replace("```", "`​``")
        if len(tb) > INLINE_TRACEBACK_LIMIT:
            file = discord.File(io.BytesIO(report.traceback.encode()), filename="traceback.txt")
            tb = "…" + tb[-INLINE_TRACEBACK_LIMIT:]
        items += [discord.ui.Separator(), discord.ui.TextDisplay(f"```py\n{tb}\n```")]

    footer = f"-# `{report.logger_name}` · <t:{int(report.created)}:R>"
    if report.suppressed:
        footer += f" · {report.suppressed} similar suppressed"
    if file:
        footer += " · full traceback attached"
    items.append(discord.ui.TextDisplay(footer))

    color = discord.Color.red() if is_error else discord.Color.orange()
    return discord.ui.Container(*items, color=color), file


class DiscordLogHandler(logging.Handler):
    def __init__(self, reporter: ErrorReporter):
        super().__init__(level=logging.WARNING)
        self.reporter = reporter

    def emit(self, record: logging.LogRecord) -> None:
        if not _should_report(record):
            return
        try:
            self.reporter.submit(record)
        except Exception:
            self.handleError(record)


LOOP_RESTART_DELAY_SECONDS = 30


def restart_crashed_loop(loop: tasks.Loop, error: BaseException) -> None:
    """pycord stops a task loop on its first exception and only prints it; report it
    and bring the loop back, or e.g. bans would silently stop expiring."""
    name = loop.coro.__name__
    logging.getLogger("dakarbot.tasks").error(
        "Background task %s crashed; restarting in %ss", name, LOOP_RESTART_DELAY_SECONDS, exc_info=error
    )
    asyncio.get_running_loop().call_later(
        LOOP_RESTART_DELAY_SECONDS, lambda: loop.is_running() or loop.start()
    )


def describe_interaction(interaction: discord.Interaction) -> str:
    user = interaction.user
    where = f"**{interaction.guild.name}** (`{interaction.guild.id}`)" if interaction.guild else "DMs"
    channel = f" in <#{interaction.channel_id}>" if interaction.channel_id else ""
    who = f"{user} (`{user.id}`)" if user else "unknown user"
    return f"by {who} on {where}{channel}"


async def tell_user(interaction: discord.Interaction, text: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException:
        pass  # interaction expired; the report still goes out
