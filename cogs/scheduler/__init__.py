# cogs/scheduler/__init__.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncpg
import discord
from discord.ext import commands, tasks

from classes.bot import Bot
from classes.context import Context
from classes.converters import IntGreaterThan
from cogs.help import chunks
from utils.checks import has_char
from utils.i18n import _, locale_doc

__all__ = ["Scheduling", "Timer", "setup"]

log = logging.getLogger(__name__)

# -------------------------
# Helpers
# -------------------------

_TIME_PARTS_RE = re.compile(r"(\d+(?:\.\d+)?)([smhdwMy])")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    """Ensure dt is timezone-aware UTC."""
    if not isinstance(dt, datetime):
        raise TypeError("dt must be datetime")
    if dt.tzinfo is None:
        # Treat naive datetimes as UTC (consistent, avoids server-local surprises)
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _friendly_hms(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _bot_colour(bot: Bot) -> int:
    # Fallback safe colour if config is missing
    return getattr(getattr(bot, "config", None), "game", None) and getattr(bot.config.game, "primary_colour", 0x5865F2) or 0x5865F2


# -------------------------
# Main Cog
# -------------------------

class Scheduling(commands.Cog):
    """Reminder / scheduler cog (channel reminders + adventure timers)."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._tick_seconds = 10

        # True => timestamptz, False => timestamp without tz, None => unknown
        self._end_is_timestamptz: Optional[bool] = None
        self._start_is_timestamptz: Optional[bool] = None

    async def cog_load(self) -> None:
        await self._detect_column_types()

        if not self.reminder_loop.is_running():
            self.reminder_loop.start()

        log.info(
            "Scheduling loaded; reminder loop started every %ss. end_is_timestamptz=%s start_is_timestamptz=%s",
            self._tick_seconds,
            self._end_is_timestamptz,
            self._start_is_timestamptz,
        )

    async def cog_unload(self) -> None:
        if self.reminder_loop.is_running():
            self.reminder_loop.cancel()
        log.info("Scheduling unloaded; reminder loop stopped.")

    # -------------------------
    # DB column type detection
    # -------------------------

    async def _detect_column_types(self) -> None:
        """Detect whether reminders.start/end are timestamptz or timestamp (no tz)."""
        pool = getattr(self.bot, "pool", None)
        if pool is None:
            self._end_is_timestamptz = None
            self._start_is_timestamptz = None
            return

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema='public'
                      AND table_name='reminders'
                      AND column_name IN ('start', 'end')
                    """
                )

            def parse_type(data_type: str) -> Optional[bool]:
                dt = (data_type or "").lower()
                if "with time zone" in dt:
                    return True
                if "without time zone" in dt:
                    return False
                return None

            for r in rows:
                col = r["column_name"]
                t = parse_type(r["data_type"])
                if col == "end":
                    self._end_is_timestamptz = t
                elif col == "start":
                    self._start_is_timestamptz = t

        except Exception:
            log.exception("Failed to detect reminders.start/end column types")
            self._end_is_timestamptz = None
            self._start_is_timestamptz = None

    def _due_sql(self) -> str:
        """
        SQL expression for "now" in same type as reminders.end.
        - end timestamptz => now()
        - end timestamp   => timezone('utc', now())  (timestamp without tz, UTC)
        """
        if self._end_is_timestamptz is True:
            return "now()"
        # For timestamp without tz (or unknown), compare to UTC timestamp-without-tz
        return "timezone('utc', now())"

    def _coerce_for_db(self, dt: datetime, *, col_is_timestamptz: Optional[bool]) -> datetime:
        """
        Convert dt to what the DB column expects.
        - If column is timestamptz: return aware UTC datetime
        - If column is timestamp: return naive UTC datetime
        - If unknown: prefer naive UTC (most common schema)
        """
        dt_utc = _as_utc(dt)
        if col_is_timestamptz is True:
            return dt_utc
        # timestamp without tz or unknown
        return dt_utc.replace(tzinfo=None)

    # -------------------------
    # Core loop
    # -------------------------

    @tasks.loop(seconds=10)
    async def reminder_loop(self) -> None:
        """Pick due reminders and deliver them in small batches."""
        pool = getattr(self.bot, "pool", None)
        if pool is None:
            return

        due_now = self._due_sql()

        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    rows = await conn.fetch(
                        f"""
                        SELECT id, "user", content, channel, type, start, "end"
                        FROM reminders
                        WHERE "end" <= {due_now}
                        ORDER BY "end" ASC
                        LIMIT 50
                        FOR UPDATE SKIP LOCKED;
                        """
                    )
                    if not rows:
                        return

                    log.info("Scheduler: picked %d due reminder(s).", len(rows))

                    for r in rows:
                        delivered = await self._deliver(r)
                        if delivered:
                            await conn.execute("DELETE FROM reminders WHERE id = $1;", r["id"])
                        else:
                            log.debug("Scheduler: will retry id=%s on next tick", r["id"])

        except Exception:
            log.exception("Scheduler loop error")

    @reminder_loop.before_loop
    async def _before_reminder_loop(self):
        await self.bot.wait_until_ready()
        log.info("Scheduler: bot ready; entering reminder loop.")

    # -------------------------
    # Delivery
    # -------------------------

    async def _deliver(self, r: asyncpg.Record) -> bool:
        """Deliver a single reminder row. Returns True on success."""
        try:
            user_id: int = r["user"]
            channel_id: int = r["channel"]
            content: str = r["content"]
            kind: str = r["type"]

            start_ts: datetime = r["start"]
            # DB might give naive timestamps; treat as UTC.
            start_ts = _as_utc(start_ts)

            age_str = _friendly_hms(_now_utc() - start_ts)

            channel = self.bot.get_channel(channel_id) or await self._safe_fetch_channel(channel_id)
            user = self.bot.get_user(user_id) or await self._safe_fetch_user(user_id)

            mention = user.mention if user else f"<@{user_id}>"

            if channel is not None:
                if kind != "adventure":
                    await channel.send(
                        f"{mention} {_('you wanted to be reminded about')} **{content}** {age_str} {_('ago')}."
                    )
                else:
                    await channel.send(
                        f"{mention} {_('adventure level')} **{content}** {_('is finished!')}"
                    )
                return True

            # Fallback: DM user
            if user is not None:
                if kind != "adventure":
                    await user.send(f"⏰ {_('Reminder')}: **{content}** ({age_str} {_('ago')}).")
                else:
                    await user.send(f"⏰ {_('Adventure finished')}: **{content}**.")
                return True

            log.warning("Scheduler: neither channel nor user found for reminder id=%s", r["id"])
            return False

        except discord.HTTPException as e:
            log.warning("Scheduler: HTTP error delivering id=%s: %s", r.get("id", "?"), e)
            return False
        except Exception as e:
            log.warning("Scheduler: error delivering id=%s: %s", r.get("id", "?"), e)
            return False

    async def _safe_fetch_channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        try:
            return await self.bot.fetch_channel(channel_id)
        except Exception:
            return None

    async def _safe_fetch_user(self, user_id: int) -> Optional[discord.User]:
        try:
            return await self.bot.fetch_user(user_id)
        except Exception:
            return None

    # -------------------------
    # DB Insertion + Removal
    # -------------------------

    async def create_reminder(
        self,
        content: str,
        ctx: Context,
        end: datetime,
        kind: str = "reminder",
        conn: Optional[asyncpg.Connection] = None,
    ) -> Optional[int]:
        """
        Insert reminder and return its id.
        IMPORTANT: use `kind=...` (NOT `type=...`)
        """
        pool = getattr(self.bot, "pool", None)
        if pool is None and conn is None:
            try:
                await ctx.send(_("The scheduler database is disabled on this instance."))
            except Exception:
                pass
            return None

        try:
            now = _now_utc()
            end_utc = _as_utc(end)

            now_db = self._coerce_for_db(now, col_is_timestamptz=self._start_is_timestamptz)
            end_db = self._coerce_for_db(end_utc, col_is_timestamptz=self._end_is_timestamptz)

            if conn is not None:
                reminder_id = await conn.fetchval(
                    'INSERT INTO reminders ("user", content, channel, start, "end", type) '
                    "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id;",
                    ctx.author.id,
                    content,
                    ctx.channel.id,
                    now_db,
                    end_db,
                    kind,
                )
            else:
                async with pool.acquire() as c:
                    reminder_id = await c.fetchval(
                        'INSERT INTO reminders ("user", content, channel, start, "end", type) '
                        "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id;",
                        ctx.author.id,
                        content,
                        ctx.channel.id,
                        now_db,
                        end_db,
                        kind,
                    )
            return int(reminder_id) if reminder_id is not None else None

        except Exception as e:
            try:
                await ctx.send(_("An error occurred while creating the reminder: {e}").format(e=e))
            except Exception:
                pass
            log.exception("create_reminder failed")
            return None

    async def remove_timer(self, reminder_id: int) -> None:
        """
        Backwards-compat shim: some cogs still call Scheduling.remove_timer(id).
        Safe no-op if DB disabled.
        """
        pool = getattr(self.bot, "pool", None)
        if pool is None:
            return
        try:
            await pool.execute("DELETE FROM reminders WHERE id=$1;", reminder_id)
        except Exception:
            log.exception("remove_timer failed for id=%s", reminder_id)

    # -------------------------
    # Commands
    # -------------------------

    @commands.group(
        aliases=["r", "reminder", "remindme"],
        invoke_without_command=True,
        brief=_("Reminds you about something"),
    )
    @locale_doc
    async def remind(self, ctx: Context, *, when_and_what: str):
        _(
            """<when_and_what> - The reminder subject and time, see below for more info.

Examples:
  - {prefix}remind 12h vote on top.gg
  - {prefix}remind 12m use {prefix}daily
  - {prefix}remind 1h30m check something"""
        )
        parts = when_and_what.split(maxsplit=1)
        if len(parts) != 2:
            return await ctx.send(_("Invalid reminder format."))

        time_str, subject = parts
        try:
            remind_at = self._parse_time_to_dt(time_str)
        except commands.BadArgument as e:
            return await ctx.send(str(e))

        if len(subject) > 100:
            return await ctx.send(_("Please choose a shorter reminder text."))

        diff = str(remind_at - _now_utc()).split(".")[0]
        await ctx.send(
            _("{user}, reminder set for {subject} in {time}.").format(
                user=ctx.author.mention, subject=subject, time=diff
            )
        )
        await self.create_reminder(subject, ctx, remind_at, kind="reminder")

    def _parse_time_to_dt(self, time_str: str) -> datetime:
        """Accepts '10s', '5m', '2h30m', '1d', '2w', '3M', '1y' and returns an absolute UTC datetime."""
        parts = list(_TIME_PARTS_RE.findall(time_str))
        if not parts:
            raise commands.BadArgument(_("Invalid time format."))

        td = timedelta()
        for value, unit in parts:
            value_f = float(value)
            if unit == "s":
                td += timedelta(seconds=value_f)
            elif unit == "m":
                td += timedelta(minutes=value_f)
            elif unit == "h":
                td += timedelta(hours=value_f)
            elif unit == "d":
                td += timedelta(days=value_f)
            elif unit == "w":
                td += timedelta(weeks=value_f)
            elif unit == "M":
                td += timedelta(days=value_f * 30)   # rough month
            elif unit == "y":
                td += timedelta(days=value_f * 365)  # rough year

        return _now_utc() + td

    @remind.command(brief=_("Shows a list of your running reminders."))
    @locale_doc
    async def list(self, ctx: Context):
        _(
            """Shows you a list of your currently running reminders

Reminders can be cancelled using `{prefix}reminder cancel <id>`."""
        )
        pool = getattr(self.bot, "pool", None)
        if pool is None:
            return await ctx.send(_("The scheduler database is disabled on this instance."))

        rows = await pool.fetch(
            'SELECT id, content, "end" FROM reminders WHERE "user"=$1 AND type=$2 ORDER BY "end" ASC;',
            ctx.author.id,
            "reminder",
        )
        if not rows:
            return await ctx.send(_("No running reminders."))

        now = _now_utc()
        embeds: list[discord.Embed] = []
        for chunk in chunks(rows, 5):
            embed = discord.Embed(
                title=_("{user}'s reminders").format(user=getattr(ctx, "disp", ctx.author.display_name)),
                color=_bot_colour(self.bot),
            )
            for r in chunk:
                end_ts = r["end"]
                end_ts = _as_utc(end_ts)  # treat naive as UTC
                remaining = end_ts - now
                remaining -= timedelta(microseconds=remaining.microseconds)
                embed.add_field(
                    name=str(r["id"]),
                    value=f"{r['content']} - {remaining}",
                    inline=False
                )
            embeds.append(embed)

        await self.bot.paginator.Paginator(extras=embeds).paginate(ctx)

    @remind.command(aliases=["remove", "rm", "delete", "del"], brief=_("Remove running reminders"))
    @locale_doc
    async def cancel(self, ctx: Context, id: IntGreaterThan(0)):
        _(
            """`[id]` - A reminder ID

Cancels a running reminder using its ID.

To find a reminder's ID, use `{prefix}reminder list`."""
        )
        pool = getattr(self.bot, "pool", None)
        if pool is None:
            return await ctx.send(_("The scheduler database is disabled on this instance."))

        status = await pool.execute(
            'DELETE FROM reminders WHERE id=$1 AND "user"=$2 AND type=$3;',
            id,
            ctx.author.id,
            "reminder",
        )
        if status == "DELETE 0":
            return await ctx.send(_("None of these reminder IDs belong to you."))

        await ctx.send(_("Successfully cancelled the reminder."))

    @has_char()
    @commands.command(brief=_("Enable or disable automatic adventure reminders"))
    @locale_doc
    async def adventureremind(self, ctx: Context):
        _("""Toggles automatic adventure reminders when you finish an adventure.""")
        pool = getattr(self.bot, "pool", None)
        if pool is None:
            return await ctx.send(_("The scheduler database is disabled on this instance."))

        current = await pool.fetchval(
            'SELECT adventure_reminder FROM user_settings WHERE "user"=$1;',
            ctx.author.id,
        )
        if current is None:
            await pool.execute(
                'INSERT INTO user_settings ("user", adventure_reminder) VALUES ($1, $2);',
                ctx.author.id, True
            )
            new_val = True
        else:
            new_val = await pool.fetchval(
                'UPDATE user_settings SET adventure_reminder = NOT adventure_reminder '
                'WHERE "user"=$1 RETURNING adventure_reminder;',
                ctx.author.id,
            )
        if new_val:
            await ctx.send(_("Successfully opted in to automatic adventure reminders."))
        else:
            await ctx.send(_("Opted out of automatic adventure reminders."))


# -------------------------
# Backward-compat Timer shim
# -------------------------

@dataclass
class _CtxLite:
    """Tiny faux context when callers pass raw IDs instead of a Context."""
    user_id: int
    channel_id: int

    @property
    def author(self):
        class _A: ...
        a = _A(); a.id = self.user_id
        return a

    @property
    def channel(self):
        class _C: ...
        c = _C(); c.id = self.channel_id
        return c


class Timer:
    """Compatibility shim for legacy imports: `from cogs.scheduler import Timer`.

    Supported:
      - Timer(bot).schedule(ctx=..., content="...", when=datetime, kind="reminder")
      - Timer(bot).in_seconds(ctx=..., content="...", seconds=123)
      - Timer(bot).remind(ctx, content="...", when=datetime)
      - Timer(bot).adventure(ctx, content="...", when=datetime)
      - Same but with user_id+channel_id instead of ctx
    """

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    def _resolve_ctx(
        self,
        ctx: Optional[Context],
        user_id: Optional[int],
        channel_id: Optional[int],
    ) -> Context:
        if ctx is not None:
            return ctx
        if user_id is None or channel_id is None:
            raise ValueError("Provide ctx or both user_id and channel_id")
        return _CtxLite(user_id=user_id, channel_id=channel_id)  # type: ignore[return-value]

    async def schedule(
        self,
        *,
        ctx: Context | None = None,
        user_id: int | None = None,
        channel_id: int | None = None,
        content: str,
        when: datetime,
        kind: str = "reminder",
    ) -> Optional[int]:
        cog: Scheduling | None = self.bot.get_cog("Scheduling")
        if not isinstance(cog, Scheduling):
            raise RuntimeError("Scheduling cog is not loaded")
        ctx_resolved = self._resolve_ctx(ctx, user_id, channel_id)
        return await cog.create_reminder(content, ctx_resolved, when, kind=kind)

    async def in_seconds(
        self,
        *,
        ctx: Context | None = None,
        user_id: int | None = None,
        channel_id: int | None = None,
        content: str,
        seconds: float,
        kind: str = "reminder",
    ) -> Optional[int]:
        when = _now_utc() + timedelta(seconds=seconds)
        return await self.schedule(
            ctx=ctx,
            user_id=user_id,
            channel_id=channel_id,
            content=content,
            when=when,
            kind=kind,
        )

    # Legacy convenience names:
    async def remind(self, ctx: Context, *, content: str, when: datetime) -> Optional[int]:
        return await self.schedule(ctx=ctx, content=content, when=when, kind="reminder")

    async def adventure(self, ctx: Context, *, content: str, when: datetime) -> Optional[int]:
        return await self.schedule(ctx=ctx, content=content, when=when, kind="adventure")


# -------------------------
# Extension entrypoint
# -------------------------

async def setup(bot: Bot):
    await bot.add_cog(Scheduling(bot))
