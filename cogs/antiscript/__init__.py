from __future__ import annotations

"""
The IdleRPG Discord Bot
Copyright (C) 2018-2021 Diniboy and Gelbpunkt
Copyright (C) 2023-2024 Lunar (PrototypeX37)
Copyright (C) 2026 Danaelis

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
"""
The EchoesOfOlympus / FableRPG Discord Bot
AntiScript Cog (daily thresholds + starterpack once/account)

Assumptions:
- Postgres (asyncpg) pool available at: bot.pool
- This cog is placed at: cogs/antiscript/__init__.py
- You load it with: await bot.load_extension("cogs.antiscript")
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Callable, Dict, List

import discord
from discord.ext import commands

# Reset reference: 01:00 in UTC+2 (same as your $daily reset logic)
RESET_TZ = timezone(timedelta(hours=2))  # UTC+2
RESET_HOUR = 1


def period_key(now_utc: Optional[datetime] = None) -> int:
    """Return yyyymmdd for the current AntiScript day in reset timezone (UTC+2)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    local = now_utc.astimezone(RESET_TZ)
    # Keep the period boundary aligned with RESET_HOUR (not midnight).
    today_reset = local.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if local < today_reset:
        local = local - timedelta(days=1)
    return local.year * 10000 + local.month * 100 + local.day


def next_reset_unix(now_utc: Optional[datetime] = None) -> int:
    """Return unix timestamp for the next reset moment (1am UTC+2)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    local = now_utc.astimezone(RESET_TZ)

    today_reset = local.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if local < today_reset:
        nxt_local = today_reset
    else:
        nxt_local = today_reset + timedelta(days=1)

    nxt_utc = nxt_local.astimezone(timezone.utc)
    return int(nxt_utc.timestamp())


def fmt_blocked(command_key: str) -> str:
    ts = next_reset_unix()
    return f"You've reached the daily threshold for `{command_key}`. This will reset <t:{ts}:R>."


def daily_command_limit(command_key: str, limit: int) -> Callable:
    """
    Decorator to block a command after N uses per day (reset at 1am UTC+2).
    Kept near module top so other cogs can import it even during reload cycles.
    """

    async def predicate(ctx: commands.Context) -> bool:
        cog: Optional["AntiScript"] = ctx.bot.get_cog("AntiScript")
        if cog is None:
            return True  # fail-open if cog missing

        normalized_key = cog.normalize_command_key(command_key)
        ok = await cog.check_and_increment_command_use(
            user_id=ctx.author.id,
            command_name=normalized_key,
            limit=limit,
            increment=0,  # check only; increment happens on successful completion
        )
        if ok:
            setattr(ctx, "_antiscript_track_info", (normalized_key, int(limit)))
            return True

        await ctx.send(fmt_blocked(normalized_key))
        return False

    return commands.check(predicate)


class AntiScript(commands.Cog):
    """
    AntiScript:
    - Per-command daily thresholds (per Discord account)
    - Starterpack claim once per Discord account
    - GM tools to reset + view daily usage
    - GM-only $antiscript help
    """

    # Keep these in one place so help + stats stay consistent
    DAILY_LIMITS: Dict[str, int] = {
        "pve": 36,
        "springpve": 36,
        "battletower_fight": 108,
        "steal": 18,
    }
    COMMAND_ALIASES: Dict[str, str] = {
        "springpve": "springpve",
        "spve": "springpve",
        "battletower": "battletower_fight",
        "battletowerfight": "battletower_fight",
        "battletower_fight": "battletower_fight",
    }

    STARTERPACK_ONCE_KEY = "starterpack_claim"  # for display purposes only

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @classmethod
    def normalize_command_key(cls, command_key: str) -> str:
        raw = str(command_key or "").strip().lower()
        raw = raw.replace("-", "_").replace(" ", "_")
        return cls.COMMAND_ALIASES.get(raw, raw)

    # -------------------------
    # Per-command daily limit
    # -------------------------
    async def get_command_uses_today(self, user_id: int, command_name: str) -> int:
        p = period_key()
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT uses
                FROM antiscript_command_daily
                WHERE user_id=$1 AND command=$2 AND period=$3
                """,
                user_id,
                command_name,
                p,
            )
        return int(row["uses"]) if row else 0

    async def check_and_increment_command_use(
        self,
        user_id: int,
        command_name: str,
        limit: int,
        increment: int = 1,
    ) -> bool:
        """
        Returns True if allowed (and optionally increments usage),
        False if blocked (already at/over limit).
        """
        normalized_key = self.normalize_command_key(command_name)
        p = period_key()
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT uses
                FROM antiscript_command_daily
                WHERE user_id=$1 AND command=$2 AND period=$3
                """,
                user_id,
                normalized_key,
                p,
            )
            uses = int(row["uses"]) if row else 0
            if uses >= limit:
                return False

            if increment <= 0:
                return True

            await conn.execute(
                """
                INSERT INTO antiscript_command_daily (user_id, command, period, uses)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, command, period)
                DO UPDATE SET uses = antiscript_command_daily.uses + $4,
                              updated_at = NOW()
                """,
                user_id,
                normalized_key,
                p,
                increment,
            )
            return True

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context):
        track_info = getattr(ctx, "_antiscript_track_info", None)
        if not track_info:
            return
        setattr(ctx, "_antiscript_track_info", None)
        command_key, limit = track_info

        try:
            await self.check_and_increment_command_use(
                user_id=ctx.author.id,
                command_name=command_key,
                limit=int(limit),
                increment=1,
            )
        except Exception:
            # Best effort only: never break command completion flow.
            pass

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception):
        # Ensure pending flag never leaks between commands.
        if getattr(ctx, "_antiscript_track_info", None):
            setattr(ctx, "_antiscript_track_info", None)

    # -------------------------
    # Starterpack once per Discord account
    # -------------------------
    async def starterpack_already_claimed(self, user_id: int) -> bool:
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM antiscript_starterpack_claimed WHERE user_id=$1",
                user_id,
            )
        return row is not None

    async def mark_starterpack_claimed(self, user_id: int) -> None:
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO antiscript_starterpack_claimed (user_id)
                VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
                """,
                user_id,
            )

    # -------------------------
    # GM-only user-facing help command: $antiscript help
    # -------------------------
    @commands.group(name="antiscript", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antiscript(self, ctx: commands.Context):
        # Default to help if they just do $antiscript
        await self.antiscript_help(ctx)

    @antiscript.command(name="help")
    @commands.has_permissions(administrator=True)
    async def antiscript_help(self, ctx: commands.Context):
        ts = next_reset_unix()

        # Build limits text
        limits_lines = [
            f"- `$pve`: **{self.DAILY_LIMITS['pve']} / day**",
            f"- `$springpve`: **{self.DAILY_LIMITS['springpve']} / day**",
            f"- `$battletower fight`: **{self.DAILY_LIMITS['battletower_fight']} / day**",
            f"- `$steal`: **{self.DAILY_LIMITS['steal']} / day**",
            f"- `$starterpack claim`: **once per Discord account**",
        ]

        gm_lines = [
            "- `$gmantiscript resetcmd <user> <command_key>` (today only)",
            "- `$gmantiscript resetstarter <user>`",
            "- `$gmantiscript wipeuser <user>` (today + starterpack)",
            "- `$gmantiscript stats <user> [command_key]`",
            "",
            "**command_key values:** `pve`, `springpve` (alias: `spve`), `battletower_fight`, `steal`",
        ]

        await ctx.send(
            "**AntiScript (GM-only)**\n"
            f"Daily limits reset at **01:00 UTC-2** (next reset <t:{ts}:R>).\n\n"
            "**Limits:**\n"
            + "\n".join(limits_lines)
            + "\n\n**GM Commands:**\n"
            + "\n".join(gm_lines)
        )

    # -------------------------
    # GM tools
    # -------------------------
    @commands.group(name="gmantiscript", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def gmantiscript(self, ctx: commands.Context):
        await ctx.send(
            "GM AntiScript commands:\n"
            "`gmantiscript resetcmd <user> <command_key>`\n"
            "`gmantiscript resetstarter <user>`\n"
            "`gmantiscript wipeuser <user>`\n"
            "`gmantiscript stats <user> [command_key]`\n"
            "Tip: `$antiscript help` (GM-only) shows limits + keys."
        )

    @gmantiscript.command(name="resetcmd")
    @commands.has_permissions(administrator=True)
    async def gm_resetcmd(self, ctx: commands.Context, user: discord.User, command_key: str):
        normalized_key = self.normalize_command_key(command_key)
        p = period_key()
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM antiscript_command_daily
                WHERE user_id=$1 AND command=$2 AND period=$3
                """,
                user.id,
                normalized_key,
                p,
            )
        await ctx.send(
            f"Reset daily usage for {user.mention} on `{normalized_key}` (period {p})."
        )

    @gmantiscript.command(name="resetstarter")
    @commands.has_permissions(administrator=True)
    async def gm_resetstarter(self, ctx: commands.Context, user: discord.User):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM antiscript_starterpack_claimed WHERE user_id=$1",
                user.id,
            )
        await ctx.send(f"Starterpack claim reset for {user.mention}.")

    @gmantiscript.command(name="wipeuser")
    @commands.has_permissions(administrator=True)
    async def gm_wipeuser(self, ctx: commands.Context, user: discord.User):
        p = period_key()
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM antiscript_command_daily WHERE user_id=$1 AND period=$2",
                user.id,
                p,
            )
            await conn.execute(
                "DELETE FROM antiscript_starterpack_claimed WHERE user_id=$1",
                user.id,
            )
        await ctx.send(f"Wiped AntiScript state for {user.mention} (period {p} + starterpack).")

    @gmantiscript.command(name="stats")
    @commands.has_permissions(administrator=True)
    async def gm_stats(self, ctx: commands.Context, user: discord.User, command_key: Optional[str] = None):
        """
        Show how many times USER used tracked commands today.
        - If command_key provided: show only that key.
        - Else: show all known tracked keys + any keys found in DB for today.
        """
        p = period_key()
        ts = next_reset_unix()

        async with self.bot.pool.acquire() as conn:
            if command_key:
                normalized_key = self.normalize_command_key(command_key)
                row = await conn.fetchrow(
                    """
                    SELECT uses
                    FROM antiscript_command_daily
                    WHERE user_id=$1 AND command=$2 AND period=$3
                    """,
                    user.id,
                    normalized_key,
                    p,
                )
                uses = int(row["uses"]) if row else 0
                await ctx.send(
                    f"**AntiScript stats** for {user.mention}\n"
                    f"- Period: `{p}` (resets <t:{ts}:R>)\n"
                    f"- `{normalized_key}` used: **{uses}** time(s) today"
                )
                return

            rows = await conn.fetch(
                """
                SELECT command, uses
                FROM antiscript_command_daily
                WHERE user_id=$1 AND period=$2
                ORDER BY command ASC
                """,
                user.id,
                p,
            )

        db_map: Dict[str, int] = {r["command"]: int(r["uses"]) for r in rows}

        # Ensure we always show configured daily keys
        keys: List[str] = list(self.DAILY_LIMITS.keys())
        for k in db_map.keys():
            if k not in keys:
                keys.append(k)

        lines = [f"- `{k}`: **{db_map.get(k, 0)}**" for k in keys]
        if not lines:
            lines = ["- No tracked usage found for today."]

        await ctx.send(
            f"**AntiScript stats** for {user.mention}\n"
            f"- Period: `{p}` (resets <t:{ts}:R>)\n"
            + "\n".join(lines)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiScript(bot))
