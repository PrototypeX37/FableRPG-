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

from __future__ import annotations

import asyncio
import json
import time as _time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

import discord
from discord.ext import commands

from cogs.scheduler import Timer
from classes.classes import Ranger, SantasHelper, Thief
from classes.classes import from_string as class_from_string
from utils.eval import evaluate as _evaluate
from utils.i18n import _, locale_doc
from utils.misc import nice_join


# -----------------------------
# Cross-process cooldown checks
# -----------------------------

def user_on_cooldown(cooldown: int, identifier: str | None = None):
    """Cross-process per-user cooldown stored in Redis.

    Key: cd:<user_id>:<cmd_id>
    - cmd_id defaults to ctx.command.qualified_name
    - if identifier provided, cmd_id = identifier (stable key)

    Raises discord.py CommandOnCooldown with retry_after=TTL.
    """

    async def predicate(ctx: commands.Context):
        cmd_id = identifier or ctx.command.qualified_name
        key = f"cd:{ctx.author.id}:{cmd_id}"

        ttl = await ctx.bot.redis.execute_command("TTL", key)
        # TTL returns:
        #  -2 key does not exist
        #  -1 key exists but has no expire
        # >=0 seconds
        if ttl == -2:
            await ctx.bot.redis.execute_command("SET", key, cmd_id, "EX", int(cooldown))
            return True

        # If key exists with no expiry, treat as on cooldown “forever” -> force set expire
        if ttl == -1:
            await ctx.bot.redis.execute_command("EXPIRE", key, int(cooldown))
            raise commands.CommandOnCooldown(
                ctx, float(cooldown), commands.BucketType.user
            )

        raise commands.CommandOnCooldown(ctx, float(ttl), commands.BucketType.user)

    return commands.check(predicate)


def guild_on_cooldown(cooldown: int):
    """Cross-process per-guild cooldown stored in Redis."""

    async def predicate(ctx: commands.Context):
        data = getattr(ctx, "character_data", None)
        if not data:
            guild_id = await ctx.bot.pool.fetchval(
                'SELECT guild FROM profile WHERE "user"=$1;', ctx.author.id
            )
        else:
            guild_id = data["guild"]

        key = f"guildcd:{guild_id}:{ctx.command.qualified_name}"
        ttl = await ctx.bot.redis.execute_command("TTL", key)
        if ttl == -2:
            await ctx.bot.redis.execute_command(
                "SET", key, ctx.command.qualified_name, "EX", int(cooldown)
            )
            return True

        if ttl == -1:
            await ctx.bot.redis.execute_command("EXPIRE", key, int(cooldown))
            raise commands.CommandOnCooldown(
                ctx, float(cooldown), commands.BucketType.guild
            )

        raise commands.CommandOnCooldown(ctx, float(ttl), commands.BucketType.guild)

    return commands.check(predicate)


def alliance_on_cooldown(cooldown: int):
    """Cross-process per-alliance cooldown stored in Redis."""

    async def predicate(ctx: commands.Context):
        data = getattr(ctx, "character_data", None)
        if not data:
            alliance_id = await ctx.bot.pool.fetchval(
                'SELECT alliance FROM guild WHERE "id"=(SELECT guild FROM profile WHERE "user"=$1);',
                ctx.author.id,
            )
        else:
            guild_id = data["guild"]
            alliance_id = await ctx.bot.pool.fetchval(
                'SELECT alliance FROM guild WHERE "id"=$1;', guild_id
            )

        key = f"alliancecd:{alliance_id}:{ctx.command.qualified_name}"
        ttl = await ctx.bot.redis.execute_command("TTL", key)
        if ttl == -2:
            await ctx.bot.redis.execute_command(
                "SET", key, ctx.command.qualified_name, "EX", int(cooldown)
            )
            return True

        if ttl == -1:
            await ctx.bot.redis.execute_command("EXPIRE", key, int(cooldown))
            raise commands.CommandOnCooldown(
                ctx, float(cooldown), commands.BucketType.guild
            )

        raise commands.CommandOnCooldown(ctx, float(ttl), commands.BucketType.guild)

    return commands.check(predicate)


def next_day_cooldown():
    """Cross-process per-user cooldown until next UTC midnight."""

    async def predicate(ctx: commands.Context):
        key = f"cd:{ctx.author.id}:{ctx.command.qualified_name}"
        ttl = await ctx.bot.redis.execute_command("TTL", key)
        if ttl == -2:
            seconds_until_utc_midnight = int(86400 - (_time.time() % 86400))
            await ctx.bot.redis.execute_command(
                "SET", key, ctx.command.qualified_name, "EX", seconds_until_utc_midnight
            )
            return True

        if ttl == -1:
            seconds_until_utc_midnight = int(86400 - (_time.time() % 86400))
            await ctx.bot.redis.execute_command("EXPIRE", key, seconds_until_utc_midnight)
            raise commands.CommandOnCooldown(
                ctx, float(seconds_until_utc_midnight), commands.BucketType.user
            )

        raise commands.CommandOnCooldown(ctx, float(ttl), commands.BucketType.user)

    return commands.check(predicate)


# -----------------------------
# discord.py cooldown reader
# -----------------------------

def _get_dpy_retry_after(cmd: commands.Command, ctx: commands.Context) -> float:
    """Return remaining cooldown seconds for discord.py in-memory cooldowns.

    IMPORTANT: this is local to the cluster/shard process.
    It will only reflect calls seen by THIS process.

    Returns 0.0 if not on cooldown or if command has no discord.py cooldown.
    """
    try:
        mapping = getattr(cmd, "_buckets", None)
        if mapping is None:
            return 0.0

        msg = getattr(ctx, "message", None)
        if msg is None:
            # Minimal proxy for slash/hybrid contexts where ctx.message may be None.
            class _Proxy:
                __slots__ = ("author", "guild", "channel")

                def __init__(self, _ctx: commands.Context):
                    self.author = _ctx.author
                    self.guild = _ctx.guild
                    self.channel = _ctx.channel

            msg = _Proxy(ctx)

        bucket = mapping.get_bucket(msg)
        if bucket is None:
            return 0.0

        retry = bucket.get_retry_after(_time.time())
        return float(retry or 0.0)
    except Exception:
        return 0.0


# -----------------------------
# Cog
# -----------------------------


class Sharding(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.router: Optional[asyncio.Task] = None
        self.pubsub = bot.redis.pubsub()
        self._messages: Dict[str, List[asyncio.Future]] = {}

        asyncio.create_task(self.register_sub())

        # Only shard 0 listens to DM interactions and forwards
        if 0 in getattr(self.bot, "shard_ids", []):
            self.bot.add_listener(self.on_raw_interaction)

    def cog_unload(self):
        asyncio.create_task(self.unregister_sub())

    async def register_sub(self):
        await self.pubsub.subscribe(self.bot.config.database.redis_shard_announce_channel)
        self.router = asyncio.create_task(self.event_handler())

    async def unregister_sub(self):
        if self.router and not self.router.cancelled():
            self.router.cancel()
        await self.pubsub.unsubscribe(self.bot.config.database.redis_shard_announce_channel)

    async def event_handler(self):
        """Main router.

        Possible messages:
        - {"scope":<bot/launcher>, "action":"<name>", "args":{...}, "command_id":"<uuid4>"}
        - {"output": <any>, "command_id":"<uuid4>"}
        - {"type":"raw_interaction", "data":{...}}
        """

        async for message in self.pubsub.listen():
            if message.get("type") != "message":
                continue

            try:
                payload = json.loads(message.get("data"))
            except Exception:
                continue

            # Cross-process event
            if payload.get("type") == "raw_interaction" and 0 not in getattr(self.bot, "shard_ids", []):
                data = payload.get("data")
                if data:
                    self.bot._connection.parse_interaction_create(data)

            # RPC call
            action = payload.get("action")
            if action and hasattr(self, action):
                if payload.get("scope") != "bot":
                    continue
                args = payload.get("args") or {}
                asyncio.create_task(
                    getattr(self, action)(**args, command_id=payload.get("command_id"))
                )

            # RPC output
            if (
                payload.get("command_id")
                and payload.get("command_id") in self._messages
                and "output" in payload
            ):
                for fut in self._messages[payload["command_id"]]:
                    if not fut.done():
                        fut.set_result(payload["output"])
                        break

    # -------- RPC targets --------

    async def reload_bans(self, command_id: int):
        await self.bot.load_bans()

    async def clear_donator_cache(self, user_id: int, command_id: int):
        self.bot.get_donator_rank.invalidate(self.bot, user_id)

    async def remove_timer(self, timer_id: int, command_id: int) -> None:
        self.bot.dispatch("timer_remove", timer_id)

    @staticmethod
    def parse_timestamp(timestamp: str) -> datetime:
        try:
            return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%f")
        except ValueError:
            return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S")

    async def add_timer(self, *args, **kwargs) -> None:
        kwargs["start"] = self.parse_timestamp(kwargs["start"])
        kwargs["end"] = self.parse_timestamp(kwargs["end"])
        timer = Timer(record=kwargs)
        self.bot.dispatch("timer_add", timer)

    async def guild_count(self, command_id: str):
        payload = {"output": len(self.bot.guilds), "command_id": command_id}
        await self.bot.redis.execute_command(
            "PUBLISH",
            self.bot.config.database.redis_shard_announce_channel,
            json.dumps(payload),
        )

    async def send_latency_and_shard_count(self, command_id: str):
        payload = {
            "output": {
                f"{self.bot.cluster_id}": [
                    self.bot.cluster_name,
                    self.bot.shard_ids,
                    round(self.bot.latency * 1000),
                ]
            },
            "command_id": command_id,
        }
        await self.bot.redis.execute_command(
            "PUBLISH",
            self.bot.config.database.redis_shard_announce_channel,
            json.dumps(payload),
        )

    async def evaluate(self, code: str, command_id: str):
        if code.startswith("```") and code.endswith("```"):
            code = "\n".join(code.split("\n")[1:-1])
        code = code.strip("` \n")
        payload = {"output": await _evaluate(self.bot, code), "command_id": command_id}
        await self.bot.redis.execute_command(
            "PUBLISH",
            self.bot.config.database.redis_shard_announce_channel,
            json.dumps(payload),
        )

    async def latency(self, command_id: str):
        payload = {"output": round(self.bot.latency * 1000, 2), "command_id": command_id}
        await self.bot.redis.execute_command(
            "PUBLISH",
            self.bot.config.database.redis_shard_announce_channel,
            json.dumps(payload),
        )

    async def handler(
        self,
        action: str,
        expected_count: int,
        args: dict | None = None,
        _timeout: int = 2,
        scope: str = "bot",
    ):
        """Send an RPC to all clusters (or launcher) and collect results."""
        args = args or {}

        command_id = f"{uuid4()}"
        if expected_count > 0:
            self._messages[command_id] = [asyncio.Future() for _ in range(expected_count)]
            results = []

        payload: dict = {"scope": scope, "action": action, "command_id": command_id}
        if args:
            payload["args"] = args

        await self.bot.redis.execute_command(
            "PUBLISH",
            self.bot.config.database.redis_shard_announce_channel,
            json.dumps(payload),
        )

        if expected_count <= 0:
            return None

        try:
            done, _ = await asyncio.wait(self._messages[command_id], timeout=_timeout)
            for fut in done:
                results.append(fut.result())
        except asyncio.TimeoutError:
            pass
        finally:
            self._messages.pop(command_id, None)

        return results

    async def on_raw_interaction(self, interaction_data: dict[str, Any]) -> None:
        payload = {"type": "raw_interaction", "data": interaction_data}
        await self.bot.redis.execute_command(
            "PUBLISH",
            self.bot.config.database.redis_shard_announce_channel,
            json.dumps(payload),
        )

    # -----------------------------
    # Cooldowns UI (prefix command)
    # -----------------------------

    @commands.command(aliases=["cooldowns", "t", "cds"], brief=_("Lists all your cooldowns"))
    @locale_doc
    async def timers(self, ctx: commands.Context):
        _("""Lists all your cooldowns, including adventure and guild adventure status.""")

        def fmt_td(value) -> str:
            """value can be seconds(int/float) or timedelta"""
            if value is None:
                return "0s"
            if isinstance(value, timedelta):
                total = int(value.total_seconds())
            else:
                total = int(float(value))

            if total < 0:
                total = 0

            days = total // 86400
            hours = (total % 86400) // 3600
            minutes = (total % 3600) // 60
            seconds = total % 60

            parts = []
            if days:
                parts.append(f"{days}d")
            if hours or days:
                parts.append(f"{hours}h")
            if minutes or hours or days:
                parts.append(f"{minutes}m")
            # only show seconds if not showing days (keeps it pretty)
            if seconds and days == 0:
                parts.append(f"{seconds}s")

            return " ".join(parts) if parts else "0s"

        def norm_cmd(s: str) -> str:
            return " ".join((s or "").strip().lower().split())

        # --- Sections you want to see
        # Note: keys must match either Redis cmd_id or discord.py qualified_name.
        SECTIONS: List[tuple[str, set[str]]] = [
            ("🏞️ Adventure", {
                # Status lines handled separately (always shown)
                "pve",
                "bt fight",
                "btower fight",
                "battletower fight",
                "battletower_fight",
                "battle tower fight",
                "dragonchallenge party",
                "dragon challenge party",
                "dragonparty",
                "dragon party",
            }),
            ("❤️ Family", {"date", "child", "familyevent", "marriage"}),
            ("✨ Class", {"class", "bless", "steal", "scout", "gift"}),
            ("🧑‍🎤 Character", {"race", "follow", "redeemcd", "redeemweapontokens"}),
            ("🐾 Pets", {
                "pets feed", "pets train", "pets treat", "pets pet", "pets play",
                "splice", "pets splice"
            }),
            ("💰 Economy", {"daily", "boosterdaily", "cratesdaily", "trade", "trader"}),
        ]

        SECTION_MAP: Dict[str, str] = {}
        for sec_name, cmds in SECTIONS:
            for c in cmds:
                SECTION_MAP[norm_cmd(c)] = sec_name

        SECTION_ORDER = [name for name, _ in SECTIONS] + ["📦 Other"]
        TRACKED_COMMANDS: List[tuple[str, List[str]]] = [
            ("pve", ["pve"]),
            ("battletower fight", [
                "battletower fight",
                "battle tower fight",
                "bt fight",
                "btower fight",
                "battletower_fight",
            ]),
            ("dragonchallenge party", [
                "dragonchallenge party",
                "dragon challenge party",
                "dragonparty",
                "dragon party",
            ]),
            ("date", ["date"]),
            ("child", ["child"]),
            ("familyevent", ["familyevent"]),
            ("marriage", ["marriage"]),
            ("class", ["class"]),
            ("bless", ["bless"]),
            ("steal", ["steal"]),
            ("scout", ["scout"]),
            ("gift", ["gift"]),
            ("race", ["race"]),
            ("follow", ["follow"]),
            ("redeemcd", ["redeemcd"]),
            ("redeemweapontokens", ["redeemweapontokens"]),
            ("pets feed", ["pets feed"]),
            ("pets train", ["pets train"]),
            ("pets treat", ["pets treat"]),
            ("pets pet", ["pets pet"]),
            ("pets play", ["pets play"]),
            ("splice", ["splice", "pets splice"]),
            ("daily", ["daily"]),
            ("boosterdaily", ["boosterdaily"]),
            ("cratesdaily", ["cratesdaily"]),
            ("trade", ["trade"]),
            ("trader", ["trader"]),
        ]
        CLASS_REQUIREMENTS = {
            "scout": Ranger,
            "steal": Thief,
            "gift": SantasHelper,
        }

        # --- 1) Load Redis cooldowns
        combined: Dict[str, Dict[str, Any]] = {}
        try:
            cooldown_keys = await self.bot.redis.execute_command(
                "KEYS", f"cd:{ctx.author.id}:*"
            )
        except Exception as e:
            return await ctx.send(f"Redis error while fetching cooldowns: {e}")

        for raw in cooldown_keys or []:
            key = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
            ttl = await self.bot.redis.execute_command("TTL", key)
            if ttl is None:
                continue
            ttl = int(ttl)
            if ttl < 0:
                continue

            cmd_id = key.replace(f"cd:{ctx.author.id}:", "")
            cmd_norm = norm_cmd(cmd_id)

            combined[cmd_norm] = {
                "display": cmd_id,
                "ttl": ttl,
                "source": "redis",
            }

        # --- 2) Merge discord.py in-memory cooldowns for commands you care about
        # Only add if Redis doesn't already have them.
        wanted_norms = set(SECTION_MAP.keys()) | {
            # common variants / typos you mentioned
            "bt fight",
            "btower fight",
            "battle tower fight",
        }

        for cmd in self.bot.walk_commands():
            qn = getattr(cmd, "qualified_name", "") or ""
            qn_norm = norm_cmd(qn)
            if qn_norm not in wanted_norms:
                continue

            retry = _get_dpy_retry_after(cmd, ctx)
            if retry <= 0:
                continue

            if qn_norm in combined:  # prefer Redis
                continue

            combined[qn_norm] = {
                "display": qn,
                "ttl": int(retry),
                "source": "local",  # shard-local
            }

        # --- 3) Adventure status lines (ALWAYS shown)
        # Personal adventure
        try:
            adv = await self.bot.get_adventure(ctx.author)
            if not adv:
                adv_line = "🏞️ Adventure — not on an adventure"
            else:
                is_completed = bool(adv[2])
                remain = adv[1]
                if is_completed:
                    adv_line = "🏞️ Adventure — finished ✅"
                else:
                    adv_line = f"🏞️ Adventure — finishes in {fmt_td(remain)}"
        except Exception:
            adv_line = "🏞️ Adventure — (status unavailable)"

        # Guild adventure (fix: DB fallback + correct variable names)
        try:
            guild_id = 0

            if getattr(ctx, "character_data", None):
                guild_id = int(ctx.character_data.get("guild") or 0)

            if not guild_id:
                guild_id = await self.bot.pool.fetchval(
                    'SELECT guild FROM profile WHERE "user"=$1;',
                    ctx.author.id
                )
                guild_id = int(guild_id or 0)

            if not guild_id:
                guild_adv_line = "🏞️ Guild Adventure — not in a guild"
            else:
                gadv = await self.bot.get_guild_adventure(guild_id)
                if not gadv:
                    guild_adv_line = "🏞️ Guild Adventure — not on an adventure"
                else:
                    is_completed = bool(gadv[2])
                    remain = gadv[1]
                    if is_completed:
                        guild_adv_line = "🏞️ Guild Adventure — finished ✅"
                    else:
                        guild_adv_line = f"🏞️ Guild Adventure — finishes in {fmt_td(remain)}"
        except Exception:
            guild_adv_line = "🏞️ Guild Adventure — (status unavailable)"

        # --- 4) Bucket all cooldown entries into sections
        buckets: Dict[str, List[Dict[str, Any]]] = {name: [] for name in SECTION_ORDER}

        for cmd_norm, info in combined.items():
            section = SECTION_MAP.get(cmd_norm, "📦 Other")
            buckets[section].append(info)

        # Sort each section by soonest first
        for sec in buckets:
            buckets[sec].sort(key=lambda x: int(x.get("ttl", 0)))

        # --- 5) Build "commands left to use" list
        async def has_required_class(display_name: str) -> bool:
            req_class = CLASS_REQUIREMENTS.get(display_name)
            if req_class is None:
                return True

            if not getattr(ctx, "character_data", None):
                ctx.character_data = await self.bot.pool.fetchrow(
                    'SELECT * FROM profile WHERE "user"=$1;',
                    ctx.author.id,
                )
            if not ctx.character_data:
                return False

            player_classes = [
                c
                for raw_class in (ctx.character_data.get("class") or [])
                if (c := class_from_string(raw_class))
            ]
            return any(c.in_class_line(req_class) for c in player_classes)

        active_cooldown_cmds = set(combined.keys())
        prefix = getattr(ctx, "clean_prefix", "$")
        commands_left_to_use: List[str] = []

        for display_name, aliases in TRACKED_COMMANDS:
            resolved_commands = [
                cmd for alias in aliases if (cmd := self.bot.get_command(alias)) is not None
            ]
            if not resolved_commands:
                continue

            # Include both alias text and canonical command names.
            # This avoids alias/canonical cooldown key mismatches (e.g. boosterdaily -> donatordaily).
            tracked_norms = {norm_cmd(a) for a in aliases}
            for cmd in resolved_commands:
                tracked_norms.add(norm_cmd(getattr(cmd, "name", "")))
                tracked_norms.add(norm_cmd(getattr(cmd, "qualified_name", "")))

            if active_cooldown_cmds.intersection(tracked_norms):
                continue

            if not await has_required_class(display_name):
                continue

            commands_left_to_use.append(f"• `{prefix}{display_name}`")

        # --- 6) Render embed
        embed = discord.Embed(title=_("Cooldowns"), color=discord.Color.blurple())

        # Always show Running section
        embed.add_field(
            name="⏱️ Running",
            value=f"{adv_line}\n{guild_adv_line}",
            inline=False,
        )

        any_cd = False
        for sec in SECTION_ORDER:
            items = buckets.get(sec) or []
            if not items:
                continue
            any_cd = True

            lines: List[str] = []
            for it in items:
                suffix = " (local)" if it.get("source") == "local" else ""
                disp = (it.get("display") or "").strip()
                lines.append(f"• **{disp}** — {fmt_td(it.get('ttl'))}{suffix}")

            # Field value max 1024 chars: chunk if needed
            chunks: List[str] = []
            buf: List[str] = []
            buf_len = 0
            for line in lines:
                if buf and buf_len + len(line) + 1 > 1024:
                    chunks.append("\n".join(buf))
                    buf = [line]
                    buf_len = len(line) + 1
                else:
                    buf.append(line)
                    buf_len += len(line) + 1
            if buf:
                chunks.append("\n".join(buf))

            for i, chunk in enumerate(chunks):
                name = sec if i == 0 else f"{sec} (cont.)"
                embed.add_field(name=name, value=chunk, inline=False)

        # Add spacing + second title section
        left_chunks: List[str] = []
        left_lines = commands_left_to_use or ["• None"]
        buf: List[str] = []
        buf_len = 0
        for line in left_lines:
            if buf and buf_len + len(line) + 1 > 1024:
                left_chunks.append("\n".join(buf))
                buf = [line]
                buf_len = len(line) + 1
            else:
                buf.append(line)
                buf_len += len(line) + 1
        if buf:
            left_chunks.append("\n".join(buf))

        if len(embed.fields) < 24 and left_chunks:
            embed.add_field(name="\u200b", value="\u200b", inline=False)
            for i, chunk in enumerate(left_chunks):
                if len(embed.fields) >= 25:
                    break
                section_name = "Commands left to use" if i == 0 else "Commands left to use (cont.)"
                embed.add_field(name=section_name, value=chunk, inline=False)

        if not any_cd:
            embed.set_footer(text=_("No active cooldowns."))
        else:
            embed.set_footer(
                text=_("(local) cooldowns are shard-local and may not reflect other clusters.")
            )

        await ctx.send(embed=embed)

    # -----------------------------
    # Cluster info
    # -----------------------------

    @commands.command(aliases=["botstatus", "shards"], brief=_("Show the clusters"))
    @locale_doc
    async def clusters(self, ctx: commands.Context):
        _("""Lists all clusters and their current status.""")
        launcher_res = await self.handler("statuses", 1, scope="launcher")
        if not launcher_res:
            return await ctx.send(_("Launcher is dead, that is really bad."))

        process_status = launcher_res[0]
        process_res = await self.handler(
            "send_latency_and_shard_count", self.bot.cluster_count, scope="bot"
        )

        actual_status = []
        for cluster_id, cluster_data in process_status.items():
            process_data = discord.utils.find(lambda x: cluster_id in x, process_res)
            if process_data:
                cluster_data["latency"] = f"{process_data[cluster_id][2]}ms"
            else:
                cluster_data["latency"] = "NaN"
            cluster_data["cluster_id"] = cluster_id
            cluster_data["started_at"] = datetime.fromtimestamp(cluster_data["started_at"])
            actual_status.append(cluster_data)

        status = "\n".join(
            [
                f"Cluster #{i['cluster_id']} ({i['name']}), shards {nice_join(i['shard_list'])}: "
                f"{'Active' if i['active'] else 'Inactive'} {i['status']}, latency {i['latency']}. "
                f"Started at: {i['started_at']}"
                for i in actual_status
            ]
        )
        await ctx.send(status)


async def setup(bot):
    await bot.add_cog(Sharding(bot))
