"""
Scheduled community mini-games for the main Echoes of Olympus server.
"""
from __future__ import annotations

import random
import time
import logging
from types import SimpleNamespace
from typing import Optional
from uuid import uuid4

import discord
from discord.ext import commands, tasks

from utils.checks import is_gm, user_is_gm


TARGET_CHANNEL_ID = 1404886173209333790
PING_ROLE_ID = 1408737208353030185
MAIN_GUILD_ID = 1323388333589528638
INTERVAL_SECONDS = 3 * 60 * 60
RETRY_SECONDS = 30 * 60
MIN_INTERVAL_SECONDS = 5 * 60
MAX_REWARD = 50_000
log = logging.getLogger(__name__)


class ScheduledMinigameContext:
    def __init__(self, bot: commands.Bot, channel: discord.TextChannel):
        self.bot = bot
        self.channel = channel
        self.guild = channel.guild
        self.author = channel.guild.me or bot.user
        self.me = channel.guild.me
        self.prefix = "$"
        self.clean_prefix = "$"
        self.command = None
        self.message = SimpleNamespace(jump_url=None)
        self.auto_minigame = True
        self.scheduled_reward = 0

    async def send(self, *args, **kwargs):
        return await self.channel.send(*args, **kwargs)


class MinigameScheduler(commands.Cog):
    LOCK_KEY = "minigame_scheduler:lock"
    NEXT_RUN_KEY = "minigame_scheduler:next_run"
    LAST_GAME_KEY = "minigame_scheduler:last_game"
    PAUSED_KEY = "minigame_scheduler:paused"
    INTERVAL_KEY = "minigame_scheduler:interval_seconds"
    ROSTER_KEY = "minigame_scheduler:roster"

    GAMES = {
        "lykaion": {
            "label": "Lykaion",
            "command": "lykaion start",
            "method": "lykaion_start",
            "args": ("normal",),
            "cog": "Lykaion",
            "state_attrs": ("active_games", "lobbies"),
            "aliases": ("lyk", "werewolf"),
        },
        "rr": {
            "label": "Russian Roulette",
            "command": "rr",
            "method": "russianroulette",
            "args": (0,),
            "cog": "Russian",
            "state_attrs": ("games",),
            "aliases": ("russianroulette", "gungame", "rrbeta", "rrb"),
        },
        "hg": {
            "label": "Hunger Games",
            "command": "hungergames",
            "method": "hungergames",
            "args": (),
            "cog": "HungerGames",
            "state_attrs": ("games",),
            "aliases": ("hungergames",),
        },
        "chariot": {
            "label": "Chariot Race",
            "command": "chariot start",
            "method": "chariot_start",
            "args": (3, 0),
            "cog": "ChariotRace",
            "state_attrs": ("active_games", "lobbies"),
            "aliases": ("cr", "chariotrace"),
        },
    }

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        if not self.scheduler_loop.is_running():
            self.scheduler_loop.start()

    async def cog_unload(self):
        if self.scheduler_loop.is_running():
            self.scheduler_loop.cancel()

    @tasks.loop(seconds=60)
    async def scheduler_loop(self):
        try:
            await self._maybe_run_due_game()
        except Exception:
            log.exception("Mini-game scheduler loop failed")

    @scheduler_loop.before_loop
    async def before_scheduler_loop(self):
        await self.bot.wait_until_ready()
        if await self.bot.redis.execute_command("GET", self.NEXT_RUN_KEY) is None:
            interval = await self._interval_seconds()
            await self._set_next_run(int(time.time()) + interval)

    async def _set_next_run(self, timestamp: int):
        await self.bot.redis.execute_command("SET", self.NEXT_RUN_KEY, int(timestamp))

    async def _is_paused(self) -> bool:
        return bool(await self.bot.redis.execute_command("GET", self.PAUSED_KEY))

    async def _interval_seconds(self) -> int:
        raw = await self.bot.redis.execute_command("GET", self.INTERVAL_KEY)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        try:
            interval = int(raw)
        except (TypeError, ValueError):
            interval = INTERVAL_SECONDS
        return max(MIN_INTERVAL_SECONDS, interval)

    async def _set_interval_seconds(self, seconds: int):
        seconds = max(MIN_INTERVAL_SECONDS, int(seconds))
        await self.bot.redis.execute_command("SET", self.INTERVAL_KEY, seconds)

    async def _enabled_games(self) -> list[str]:
        raw = await self.bot.redis.execute_command("GET", self.ROSTER_KEY)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        if not raw:
            return list(self.GAMES)
        enabled = [
            game_key.strip()
            for game_key in str(raw).split(",")
            if game_key.strip() in self.GAMES
        ]
        return enabled or list(self.GAMES)

    async def _set_enabled_games(self, game_keys: list[str]):
        unique_keys = []
        for game_key in game_keys:
            if game_key in self.GAMES and game_key not in unique_keys:
                unique_keys.append(game_key)
        if not unique_keys:
            raise ValueError("Scheduler roster cannot be empty.")
        await self.bot.redis.execute_command("SET", self.ROSTER_KEY, ",".join(unique_keys))

    def _format_duration(self, seconds: int) -> str:
        seconds = max(0, int(seconds))
        hours, rem = divmod(seconds, 3600)
        minutes, seconds = divmod(rem, 60)
        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds and not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts) if parts else "0m"

    def _parse_duration_seconds(self, amount: float, unit: str) -> int:
        unit = (unit or "hours").strip().lower()
        multipliers = {
            "m": 60,
            "min": 60,
            "mins": 60,
            "minute": 60,
            "minutes": 60,
            "h": 3600,
            "hr": 3600,
            "hrs": 3600,
            "hour": 3600,
            "hours": 3600,
        }
        if unit not in multipliers:
            raise ValueError("Unit must be minutes or hours.")
        return int(float(amount) * multipliers[unit])

    def _roster_summary(self, enabled: list[str]) -> str:
        return ", ".join(self.GAMES[game_key]["label"] for game_key in enabled)

    def _available_games_summary(self) -> str:
        return ", ".join(
            f"`{game_key}` ({config['label']})"
            for game_key, config in self.GAMES.items()
        )

    async def _target_channel(self) -> Optional[discord.TextChannel]:
        channel = self.bot.get_channel(TARGET_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(TARGET_CHANNEL_ID)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if not isinstance(channel, discord.TextChannel):
            return None

        expected_guild_id = int(
            getattr(getattr(self.bot.config, "game", None), "support_server_id", None)
            or MAIN_GUILD_ID
        )
        if not channel.guild or channel.guild.id != expected_guild_id:
            return None
        return channel

    def _normalize_game(self, game_name: Optional[str]) -> Optional[str]:
        if not game_name:
            return None
        normalized = game_name.strip().lower()
        for key, config in self.GAMES.items():
            if normalized == key or normalized in config["aliases"]:
                return key
        return None

    async def _choose_game(self, requested: Optional[str] = None) -> str:
        if requested:
            return requested

        last_game = await self.bot.redis.execute_command("GET", self.LAST_GAME_KEY)
        if isinstance(last_game, (bytes, bytearray)):
            last_game = last_game.decode()

        enabled_games = await self._enabled_games()
        choices = [key for key in enabled_games if key != last_game]
        if not choices:
            choices = enabled_games
        return random.choice(choices)

    def _game_is_active(self, game_key: str, channel_id: int) -> bool:
        config = self.GAMES[game_key]
        cog = self.bot.get_cog(config["cog"])
        if cog is None:
            return False
        for attr in config["state_attrs"]:
            state = getattr(cog, attr, None)
            if isinstance(state, dict) and channel_id in state:
                return True
        return False

    def _any_game_active(self, channel_id: int) -> bool:
        return any(self._game_is_active(game_key, channel_id) for game_key in self.GAMES)

    def _new_reward(self) -> int:
        return random.randint(1, MAX_REWARD)

    async def _invoke_game(self, game_key: str, channel: discord.TextChannel, reward: int):
        config = self.GAMES[game_key]
        ctx = ScheduledMinigameContext(self.bot, channel)
        ctx.scheduled_reward = int(reward)
        command = self.bot.get_command(config["command"])
        if command is not None and command.cog is not None:
            await command.callback(command.cog, ctx, *config["args"])
            return

        cog = self.bot.get_cog(config["cog"])
        method = getattr(cog, config.get("method", ""), None) if cog else None
        if method is None:
            await channel.send(f"Could not find the `{config['command']}` command.")
            return
        await method(ctx, *config["args"])

    async def _run_game(self, requested: Optional[str] = None, *, manual: bool = False) -> bool:
        token = str(uuid4())
        interval = await self._interval_seconds()
        lock_ttl = max(interval, RETRY_SECONDS)
        locked = await self.bot.redis.execute_command(
            "SET", self.LOCK_KEY, token, "EX", lock_ttl, "NX"
        )
        if not locked:
            return False

        try:
            channel = await self._target_channel()
            if channel is None:
                return False

            if self._any_game_active(channel.id):
                if manual:
                    await channel.send("A scheduled mini-game is already active in this channel.")
                return False

            game_key = await self._choose_game(requested)
            config = self.GAMES[game_key]
            reward = self._new_reward()
            await self.bot.redis.execute_command("SET", self.LAST_GAME_KEY, game_key)

            await channel.send(
                (
                    f"<@&{PING_ROLE_ID}> A scheduled **{config['label']}** lobby is starting now!\n"
                    f"Sponsored reward: **${reward}**"
                ),
                allowed_mentions=discord.AllowedMentions(roles=True),
            )
            await self._invoke_game(game_key, channel, reward)
            return True
        finally:
            current_token = await self.bot.redis.execute_command("GET", self.LOCK_KEY)
            if isinstance(current_token, (bytes, bytearray)):
                current_token = current_token.decode()
            if current_token == token:
                await self.bot.redis.execute_command("DEL", self.LOCK_KEY)

    async def _maybe_run_due_game(self):
        if await self._is_paused():
            return

        now = int(time.time())
        next_run = await self.bot.redis.execute_command("GET", self.NEXT_RUN_KEY)
        if isinstance(next_run, (bytes, bytearray)):
            next_run = next_run.decode()
        try:
            next_run_at = int(next_run)
        except (TypeError, ValueError):
            interval = await self._interval_seconds()
            next_run_at = now + interval
            await self._set_next_run(next_run_at)
            return

        if now < next_run_at:
            return

        started = await self._run_game()
        interval = await self._interval_seconds()
        await self._set_next_run(now + (interval if started else RETRY_SECONDS))

    @commands.group(name="minigamescheduler", aliases=["mgs"], invoke_without_command=True)
    async def minigamescheduler(self, ctx):
        if not await user_is_gm(self.bot, ctx.author):
            return await ctx.send("Mini-game scheduler commands are GM-only.")
        next_run = await self.bot.redis.execute_command("GET", self.NEXT_RUN_KEY)
        if isinstance(next_run, (bytes, bytearray)):
            next_run = next_run.decode()
        paused = await self._is_paused()
        interval = await self._interval_seconds()
        enabled_games = await self._enabled_games()
        if next_run:
            remaining = max(0, int(next_run) - int(time.time()))
            next_text = self._format_duration(remaining)
        else:
            next_text = "not scheduled yet"
        await ctx.send(
            f"Mini-game scheduler is **{'paused' if paused else 'active'}**.\n"
            f"Channel: <#{TARGET_CHANNEL_ID}>\n"
            f"Ping role: <@&{PING_ROLE_ID}>\n"
            f"Spawn interval: **{self._format_duration(interval)}**\n"
            f"Next run: **{next_text}**\n"
            f"Roster: **{self._roster_summary(enabled_games)}**\n\n"
            "Commands: `$mgs interval [amount] [minutes|hours]`, "
            "`$mgs roster`, `$mgs roster add <game>`, `$mgs roster remove <game>`, "
            "`$mgs stop`"
        )

    @minigamescheduler.command(name="on")
    @is_gm()
    async def minigamescheduler_on(self, ctx):
        await self.bot.redis.execute_command("DEL", self.PAUSED_KEY)
        await ctx.send("Mini-game scheduler enabled.")

    @minigamescheduler.command(name="off")
    @is_gm()
    async def minigamescheduler_off(self, ctx):
        await self.bot.redis.execute_command("SET", self.PAUSED_KEY, "1")
        await ctx.send("Mini-game scheduler paused.")

    @minigamescheduler.command(name="stop", aliases=["stopcurrent", "emergencystop"])
    @is_gm()
    async def minigamescheduler_stop(self, ctx):
        channel = await self._target_channel()
        if channel is None:
            return await ctx.send("Could not find the scheduled mini-game channel.")

        stopped = False
        chariot = self.bot.get_cog("ChariotRace")
        if chariot and hasattr(chariot, "chariot_tasks_for_channel"):
            race_tasks, task_games = chariot.chariot_tasks_for_channel(channel.id)
            active_game = getattr(chariot, "active_games", {}).pop(channel.id, None)
            if active_game and active_game not in task_games:
                task_games.append(active_game)

            for game in task_games:
                stop = getattr(game, "stop", None)
                if callable(stop):
                    stop(f"Stopped by scheduler command from {ctx.author}.")
                refund_game = getattr(chariot, "refund_game", None)
                if callable(refund_game):
                    await refund_game(game)
                stopped = True

            for task in race_tasks:
                task.cancel()
                stopped = True

        await self.bot.redis.execute_command("DEL", self.LOCK_KEY)
        if stopped:
            await ctx.send("Stopped the active scheduled Chariot Race and cleared the scheduler lock.")
        else:
            await ctx.send("No scheduled Chariot Race task found, but the scheduler lock was cleared.")

    @minigamescheduler.command(name="next")
    @is_gm()
    async def minigamescheduler_next(self, ctx, hours: int = 3):
        hours = max(1, int(hours))
        await self._set_next_run(int(time.time()) + hours * 3600)
        await ctx.send(f"Next scheduled mini-game set for about **{hours}h** from now.")

    @minigamescheduler.command(name="interval")
    @is_gm()
    async def minigamescheduler_interval(
        self,
        ctx,
        amount: Optional[float] = None,
        unit: str = "hours",
    ):
        current = await self._interval_seconds()
        if amount is None:
            return await ctx.send(
                f"Scheduled mini-games currently spawn every **{self._format_duration(current)}**."
            )
        if amount <= 0:
            return await ctx.send("Interval must be greater than 0.")
        try:
            seconds = self._parse_duration_seconds(amount, unit)
        except ValueError as exc:
            return await ctx.send(str(exc))
        seconds = max(MIN_INTERVAL_SECONDS, seconds)
        await self._set_interval_seconds(seconds)
        await self._set_next_run(int(time.time()) + seconds)
        await ctx.send(
            f"Scheduled mini-games will now spawn every **{self._format_duration(seconds)}**. "
            "Next run has been rescheduled from now."
        )

    @minigamescheduler.group(name="roster", invoke_without_command=True)
    @is_gm()
    async def minigamescheduler_roster(self, ctx):
        enabled_games = await self._enabled_games()
        await ctx.send(
            f"Active scheduled mini-game roster: **{self._roster_summary(enabled_games)}**\n"
            f"Available games: {self._available_games_summary()}\n"
            "Use `$mgs roster add <game>` or `$mgs roster remove <game>`."
        )

    @minigamescheduler_roster.command(name="add")
    @is_gm()
    async def minigamescheduler_roster_add(self, ctx, game: str):
        game_key = self._normalize_game(game)
        if not game_key:
            return await ctx.send(
                f"Unknown game. Available games: {self._available_games_summary()}"
            )
        enabled_games = await self._enabled_games()
        if game_key in enabled_games:
            return await ctx.send(f"**{self.GAMES[game_key]['label']}** is already in the scheduler roster.")
        enabled_games.append(game_key)
        await self._set_enabled_games(enabled_games)
        await ctx.send(
            f"Added **{self.GAMES[game_key]['label']}**. "
            f"Roster: **{self._roster_summary(enabled_games)}**"
        )

    @minigamescheduler_roster.command(name="remove", aliases=["rm"])
    @is_gm()
    async def minigamescheduler_roster_remove(self, ctx, game: str):
        game_key = self._normalize_game(game)
        if not game_key:
            return await ctx.send(
                f"Unknown game. Available games: {self._available_games_summary()}"
            )
        enabled_games = await self._enabled_games()
        if game_key not in enabled_games:
            return await ctx.send(f"**{self.GAMES[game_key]['label']}** is not in the scheduler roster.")
        if len(enabled_games) <= 1:
            return await ctx.send("The scheduler roster needs at least one game.")
        enabled_games = [key for key in enabled_games if key != game_key]
        await self._set_enabled_games(enabled_games)
        await ctx.send(
            f"Removed **{self.GAMES[game_key]['label']}**. "
            f"Roster: **{self._roster_summary(enabled_games)}**"
        )

    @minigamescheduler.command(name="force")
    @is_gm()
    async def minigamescheduler_force(self, ctx, game: Optional[str] = None):
        game_key = self._normalize_game(game)
        if game and not game_key:
            return await ctx.send(
                "Unknown game. Use one of: `lykaion`, `rr`, `hg`, `chariot`."
            )
        started = await self._run_game(game_key, manual=True)
        if started:
            return await ctx.send("Scheduled mini-game started.")
        await ctx.send("Could not start a mini-game right now.")


async def setup(bot):
    await bot.add_cog(MinigameScheduler(bot))
