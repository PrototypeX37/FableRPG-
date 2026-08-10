"""
Olympus Dash (OD) mini-game for Echoes of Olympus.
"""

from __future__ import annotations

import asyncio
import datetime
import io
import json
import os
import secrets
from collections import Counter
from typing import Any

import discord
from PIL import Image, ImageDraw, ImageFont

from discord.ext import commands

from classes.bot import Bot
from classes.converters import IntFromTo, IntGreaterThan
from utils.checks import has_char, has_money, user_is_gm
from utils.i18n import _, locale_doc


CM_ROLE_ID = 491353140042530826
MAX_LAPS = 25
MIN_PLAYERS = 3
BOARD_TILE_COUNT = 28

CANVAS_W = 1600
CANVAS_H = 900
BOARD_TOP_MARGIN = 12
BOARD_SIDE_MARGIN = 12
BOARD_BOTTOM_MARGIN = 12
PLACEHOLDER_SIZE = 512

TITLE_FONT_PATH = "assets/fonts/CaesarDressing-Regular.ttf"
TEXT_FONT_PATH = "assets/fonts/GFSDidot-Regular.ttf"

MAP_ASSET_URLS = {
    "background": "https://i.imgur.com/bbxyUfK.png",
    "normal": "https://i.imgur.com/NJhrJNA.png",
    "coin": "https://i.imgur.com/kNhvbA1.png",
    "lucky": "https://i.imgur.com/uNJ0jsk.png",
    "trap": "https://i.imgur.com/F4qQvNJ.png",
    "shop": "https://i.imgur.com/V71CcCK.png",
    "xp": "https://i.imgur.com/IGFwiHn.png",
    "swap": "https://i.imgur.com/iP0Qwe3.png",
    "crate": "https://i.imgur.com/jasIFZx.png",
}

ASSET_FILE_NAMES = {
    "background": "background.png",
    "normal": "normal.png",
    "coin": "coin.png",
    "lucky": "lucky.png",
    "trap": "trap.png",
    "shop": "shop.png",
    "xp": "xp.png",
    "swap": "swap.png",
    "crate": "crate.png",
}

TILE_COLORS = {
    "start": (234, 200, 73, 255),
    "normal": (222, 226, 232, 255),
    "coin": (252, 210, 66, 255),
    "lucky": (107, 221, 136, 255),
    "trap": (232, 94, 94, 255),
    "swap": (120, 160, 244, 255),
    "xp": (186, 132, 255, 255),
    "crate": (245, 167, 94, 255),
    "shop": (89, 198, 201, 255),
}

RANK_MULTIPLIERS = {
    1: 1.00,
    2: 0.75,
    3: 0.50,
}

TILE_EMOJIS = {
    "start": "🏛️",
    "normal": "⬜",
    "coin": "🪙",
    "lucky": "🍀",
    "trap": "🕳️",
    "swap": "🔄",
    "xp": "✨",
    "crate": "📦",
    "shop": "🛒",
}

TILE_LEGEND_DETAILS = [
    ("start", "Lap checkpoint/start tile. Completing the required laps wins the OD."),
    ("normal", "No effect."),
    ("coin", "Gain **1 to 5 OD coins**."),
    ("lucky", "Move forward **1 to 3 tiles**."),
    ("trap", "Move back **2 to 5 tiles** OR lose up to **70%** of OD coins (whole coins only)."),
    ("swap", "Swap spot with one random active opponent."),
    ("xp", "Gain **1,000 to 5,000 XP** in EoO (GM/CM-started OD only)."),
    ("crate", "Gain one random EoO crate (GM/CM-started OD only)."),
    ("shop", "Buy one-use dice: **d8 = 5 OD coins**, **d16 = 8 OD coins**."),
]

CRATE_LABELS = {
    "crates_common": "Common Crate",
    "crates_uncommon": "Uncommon Crate",
    "crates_rare": "Rare Crate",
    "crates_magic": "Magic Crate",
    "crates_legendary": "Legendary Crate",
    "crates_mystery": "Mystery Crate",
}

CRATE_WEIGHTS = [
    ("crates_common", 45.0),
    ("crates_uncommon", 28.0),
    ("crates_rare", 15.0),
    ("crates_magic", 8.0),
    ("crates_legendary", 3.5),
    ("crates_mystery", 0.5),
]

TILE_WEIGHTS_STAFF = [
    ("normal", 42.0),
    ("coin", 25.0),
    ("lucky", 11.0),
    ("trap", 11.0),
    ("shop", 6.0),
    ("xp", 3.0),
    ("swap", 1.5),
    ("crate", 0.5),
]

TILE_WEIGHTS_STANDARD = [
    ("normal", 45.5),
    ("coin", 25.0),
    ("lucky", 11.0),
    ("trap", 11.0),
    ("shop", 6.0),
    ("swap", 1.5),
]


class ODShopView(discord.ui.View):
    def __init__(self, cog: "OlympusDash", session_id: str, player_id: int, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.session_id = str(session_id).upper()
        self.player_id = int(player_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message(
                "Only the current player can use these shop buttons.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Buy d8 (5)", style=discord.ButtonStyle.primary)
    async def buy_d8(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog._buy_from_interaction(interaction, self.session_id, "d8")

    @discord.ui.button(label="Buy d16 (8)", style=discord.ButtonStyle.success)
    async def buy_d16(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog._buy_from_interaction(interaction, self.session_id, "d16")


class _ODCommandProxy:
    def __init__(self, qualified_name: str):
        self.qualified_name = str(qualified_name)


class ODCtxProxy:
    def __init__(self, guild: Any, channel: Any, author: Any, command_name: str):
        self.guild = guild
        self.channel = channel
        self.author = author
        self.command = _ODCommandProxy(command_name)


class ODLobbyView(discord.ui.View):
    def __init__(self, cog: "OlympusDash", session_id: str, host_id: int, timeout: float = 300.0):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.session_id = str(session_id).upper()
        self.host_id = int(host_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.guild is not None

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        log_ctx = ODCtxProxy(interaction.guild, interaction.channel, interaction.user, "od join")
        ok, message, row_data, state = await self.cog._join_session_internal(
            session_id=self.session_id,
            guild_id=interaction.guild.id,
            member=interaction.user,
            log_ctx=log_ctx,
        )
        if not ok or row_data is None or state is None:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await interaction.response.edit_message(embed=self.cog._lobby_embed(row_data, state), view=self)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        log_ctx = ODCtxProxy(interaction.guild, interaction.channel, interaction.user, "od leave")
        ok, message, row_data, state = await self.cog._leave_session_internal(
            session_id=self.session_id,
            guild_id=interaction.guild.id,
            member=interaction.user,
            log_ctx=log_ctx,
        )
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        if row_data is None or state is None or str(row_data.get("status", "")) == "CANCELLED":
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(content=message, embed=None, view=self)
            return
        await interaction.response.edit_message(embed=self.cog._lobby_embed(row_data, state), view=self)

    @discord.ui.button(label="Begin Now", style=discord.ButtonStyle.primary)
    async def begin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        actor_is_staff = await self.cog._is_staff_member(interaction.user, interaction.guild)
        ok, message, row_data, state = await self.cog._begin_session_internal(
            session_id=self.session_id,
            guild_id=interaction.guild.id,
            actor_id=interaction.user.id,
            actor_is_staff=actor_is_staff,
        )
        if not ok or row_data is None or state is None:
            await interaction.response.send_message(message, ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=self.cog._lobby_embed(row_data, state),
            view=self,
        )
        if interaction.channel is not None:
            await interaction.channel.send(message)
            await self.cog._send_turn_visual(
                interaction.channel,
                row_data,
                state,
                extra_lines=["Game started. Use action buttons below for this turn."],
                status_override="ACTIVE",
                arm_timeout=True,
                send_turn_ping=True,
            )


class ODTurnView(discord.ui.View):
    def __init__(self, cog: "OlympusDash", session_id: str, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.session_id = str(session_id).upper()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return False
        async with self.cog.bot.pool.acquire() as conn:
            row, state = await self.cog._fetch_session(conn, self.session_id, for_update=False)
        if row is None or state is None:
            await interaction.response.send_message("Unknown OD session ID.", ephemeral=True)
            return False
        if str(row["status"]) != "ACTIVE":
            await interaction.response.send_message("This OD session is not active.", ephemeral=True)
            return False
        current_id = self.cog._get_current_player_id(state)
        if current_id is None:
            await interaction.response.send_message("No active player found.", ephemeral=True)
            return False
        if int(interaction.user.id) != int(current_id):
            await interaction.response.send_message("It is not your turn.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Roll d6", style=discord.ButtonStyle.primary)
    async def roll_d6(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog._roll_from_interaction(interaction, self.session_id, "d6")

    @discord.ui.button(label="Roll d8", style=discord.ButtonStyle.secondary)
    async def roll_d8(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog._roll_from_interaction(interaction, self.session_id, "d8")

    @discord.ui.button(label="Roll d16", style=discord.ButtonStyle.secondary)
    async def roll_d16(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog._roll_from_interaction(interaction, self.session_id, "d16")

    @discord.ui.button(label="Buy d8 (5)", style=discord.ButtonStyle.success)
    async def buy_d8(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog._buy_from_interaction(interaction, self.session_id, "d8")

    @discord.ui.button(label="Buy d16 (8)", style=discord.ButtonStyle.success)
    async def buy_d16(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog._buy_from_interaction(interaction, self.session_id, "d16")


class OlympusDash(commands.Cog):
    """Olympus Dash turn-based race mini-game."""

    def __init__(self, bot: Bot):
        self.bot = bot
        self._rng = secrets.SystemRandom()
        self._map_asset_cache: dict[str, Any] = {}
        self._font_cache: dict[tuple[str, int], Any] = {}
        self._turn_timeout_tasks: dict[str, asyncio.Task] = {}
        self._round_visual_messages: dict[str, tuple[int, int, int]] = {}

    async def cog_load(self) -> None:
        await self._ensure_schema()
        os.makedirs(self._asset_dir(), exist_ok=True)

    def cog_unload(self) -> None:
        for task in self._turn_timeout_tasks.values():
            task.cancel()
        self._turn_timeout_tasks.clear()
        self._round_visual_messages.clear()

    async def _ensure_schema(self) -> None:
        await self.bot.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS olympus_dash_sessions (
                session_id VARCHAR(32) PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                host_id BIGINT NOT NULL,
                started_by_staff BOOLEAN NOT NULL DEFAULT FALSE,
                laps_to_win SMALLINT NOT NULL,
                min_players SMALLINT NOT NULL DEFAULT 3,
                max_players SMALLINT NOT NULL,
                entry_fee BIGINT NOT NULL DEFAULT 0,
                sponsor_reward BIGINT NOT NULL DEFAULT 0,
                status VARCHAR(16) NOT NULL DEFAULT 'LOBBY',
                winner_user_id BIGINT NULL,
                game_state JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        await self.bot.pool.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_od_sessions_guild_status
                ON olympus_dash_sessions (guild_id, status);
            """
        )

    @staticmethod
    def _max_players_for_laps(laps: int) -> int:
        if laps >= 21:
            return 8
        if laps >= 16:
            return 10
        if laps >= 11:
            return 12
        return 15

    @staticmethod
    def _board_size_for_laps(laps: int) -> int:
        _ = laps
        return BOARD_TILE_COUNT

    def _weights_for_mode(self, started_by_staff: bool) -> list[tuple[str, float]]:
        return TILE_WEIGHTS_STAFF if started_by_staff else TILE_WEIGHTS_STANDARD

    def _weighted_choice(self, weighted_values: list[tuple[str, float]]) -> str:
        total = sum(weight for _, weight in weighted_values)
        roll = self._rng.random() * total
        upto = 0.0
        for value, weight in weighted_values:
            upto += weight
            if roll <= upto:
                return value
        return weighted_values[-1][0]

    def _required_tiles_for_mode(self, started_by_staff: bool) -> list[str]:
        return [tile for tile, _weight in self._weights_for_mode(started_by_staff)]

    def _enforce_required_tiles(self, tiles: list[str], started_by_staff: bool) -> int:
        if len(tiles) <= 1:
            return 0

        required = self._required_tiles_for_mode(started_by_staff)
        non_start_indices = list(range(1, len(tiles)))
        counts = Counter(tiles[1:])
        missing = [tile for tile in required if counts.get(tile, 0) <= 0]
        fixes = 0

        for missing_tile in missing:
            # Prefer replacing a duplicated tile to preserve current diversity.
            candidates = [
                idx for idx in non_start_indices
                if counts.get(tiles[idx], 0) > 1 and tiles[idx] != missing_tile
            ]
            if not candidates:
                candidates = [idx for idx in non_start_indices if tiles[idx] != missing_tile]
            if not candidates:
                continue

            idx = self._rng.choice(candidates)
            old_tile = tiles[idx]
            if old_tile == missing_tile:
                continue
            counts[old_tile] = max(0, int(counts.get(old_tile, 0)) - 1)
            tiles[idx] = missing_tile
            counts[missing_tile] = int(counts.get(missing_tile, 0)) + 1
            fixes += 1

        return fixes

    def _generate_map(self, board_size: int, started_by_staff: bool) -> list[str]:
        tiles = ["start"]
        weights = self._weights_for_mode(started_by_staff)
        for _ in range(board_size - 1):
            tiles.append(self._weighted_choice(weights))

        self._enforce_required_tiles(tiles, started_by_staff)

        return tiles

    def _mutate_map(self, state: dict[str, Any], started_by_staff: bool) -> int:
        tiles = state["tiles"]
        if len(tiles) <= 2:
            return 0

        weights = self._weights_for_mode(started_by_staff)
        mutable_slots = list(range(1, len(tiles)))
        mutate_count = max(1, int((len(tiles) - 1) * 0.20))
        picks = self._rng.sample(mutable_slots, k=min(mutate_count, len(mutable_slots)))

        for idx in picks:
            tiles[idx] = self._weighted_choice(weights)

        fix_count = self._enforce_required_tiles(tiles, started_by_staff)

        state["board_version"] = int(state.get("board_version", 1)) + 1
        return len(picks) + fix_count

    def _is_cm(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            return False
        if ctx.guild.id != self.bot.config.game.support_server_id:
            return False
        return any(getattr(role, "id", 0) == CM_ROLE_ID for role in getattr(ctx.author, "roles", []))

    async def _is_staff_member(self, member: Any, guild: Any) -> bool:
        is_cm = False
        if guild is not None and int(getattr(guild, "id", 0)) == int(self.bot.config.game.support_server_id):
            is_cm = any(getattr(role, "id", 0) == CM_ROLE_ID for role in getattr(member, "roles", []))
        return is_cm or await user_is_gm(self.bot, member)

    async def _is_staff_host(self, ctx: commands.Context) -> bool:
        return await self._is_staff_member(ctx.author, ctx.guild)

    def _new_session_id(self) -> str:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%y%m%d%H%M%S")
        return f"OD-{stamp}-{secrets.token_hex(2).upper()}"

    @staticmethod
    def _new_player(member: discord.Member | discord.User) -> dict[str, Any]:
        return {
            "user_id": int(member.id),
            "name": str(member.display_name),
            "pos": 0,
            "laps": 0,
            "od_coins": 0,
            "d8_uses": 0,
            "d16_uses": 0,
            "xp_gained": 0,
            "timeout_count": 0,
            "crates": {
                "crates_common": 0,
                "crates_uncommon": 0,
                "crates_rare": 0,
                "crates_magic": 0,
                "crates_legendary": 0,
                "crates_mystery": 0,
            },
            "eliminated": False,
        }

    @staticmethod
    def _parse_state(raw_state: Any) -> dict[str, Any]:
        if isinstance(raw_state, dict):
            return raw_state
        if isinstance(raw_state, str):
            try:
                parsed = json.loads(raw_state)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {}

    def _normalize_state(self, state: dict[str, Any], row: Any) -> dict[str, Any]:
        if (
            "tiles" not in state
            or not isinstance(state.get("tiles"), list)
            or not state["tiles"]
            or len(state.get("tiles", [])) != BOARD_TILE_COUNT
        ):
            board_size = self._board_size_for_laps(int(row["laps_to_win"]))
            state["tiles"] = self._generate_map(board_size, bool(row["started_by_staff"]))
        state.setdefault("board_version", 1)
        state.setdefault("players", [])
        state.setdefault("order", [])
        state.setdefault("turn_index", 0)
        state.setdefault("pot", 0)
        state.setdefault("player_tags", {})
        state.setdefault("round_number", 1)
        state.setdefault("round_roll_count", 0)
        if "map_lap_target" not in state:
            active_laps = [
                int(player.get("laps", 0))
                for player in state.get("players", [])
                if not player.get("eliminated", False)
            ]
            state["map_lap_target"] = max(1, (min(active_laps) + 1) if active_laps else 1)

        for player in state["players"]:
            player.setdefault("name", "Unknown")
            player.setdefault("pos", 0)
            player.setdefault("laps", 0)
            player.setdefault("od_coins", 0)
            player.setdefault("d8_uses", 0)
            player.setdefault("d16_uses", 0)
            player.setdefault("xp_gained", 0)
            player.setdefault("timeout_count", 0)
            player.setdefault("crates", {})
            player.setdefault("eliminated", False)
            for crate_key in CRATE_LABELS:
                player["crates"].setdefault(crate_key, 0)

        if state.get("order") and not state.get("player_tags"):
            self._assign_player_tags(state)

        return state

    @staticmethod
    def _player_progress(player: dict[str, Any], board_size: int) -> int:
        return int(player["laps"]) * board_size + int(player["pos"])

    @staticmethod
    def _split_progress(progress: int, board_size: int) -> tuple[int, int]:
        laps, pos = divmod(max(0, progress), board_size)
        return int(laps), int(pos)

    @staticmethod
    def _find_player(state: dict[str, Any], user_id: int) -> dict[str, Any] | None:
        for player in state["players"]:
            if int(player["user_id"]) == int(user_id):
                return player
        return None

    def _active_racers(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return [p for p in state["players"] if not p.get("eliminated", False)]

    def _get_current_player_id(self, state: dict[str, Any]) -> int | None:
        order = [int(uid) for uid in state.get("order", [])]
        if not order:
            return None

        turn_index = int(state.get("turn_index", 0)) % len(order)
        for offset in range(len(order)):
            idx = (turn_index + offset) % len(order)
            uid = int(order[idx])
            player = self._find_player(state, uid)
            if player and not player.get("eliminated", False):
                state["turn_index"] = idx
                return uid
        return None

    def _advance_turn(self, state: dict[str, Any]) -> int | None:
        order = [int(uid) for uid in state.get("order", [])]
        if not order:
            return None

        state["turn_index"] = (int(state.get("turn_index", 0)) + 1) % len(order)
        return self._get_current_player_id(state)

    def _move_player(self, player: dict[str, Any], delta: int, board_size: int) -> int:
        old_progress = self._player_progress(player, board_size)
        new_progress = max(0, old_progress + int(delta))
        new_laps, new_pos = self._split_progress(new_progress, board_size)
        lap_gain = int(new_laps) - int(player["laps"])
        player["laps"] = new_laps
        player["pos"] = new_pos
        return lap_gain

    def _format_map_preview(self, state: dict[str, Any], limit: int = 42) -> str:
        tiles = state.get("tiles", [])
        if not tiles:
            return "No map available."
        visible = tiles[:limit]
        text = " ".join(TILE_EMOJIS.get(tile, "⬜") for tile in visible)
        if len(tiles) > limit:
            text += " ..."
        return text

    def _format_map_rows(self, tiles: list[str], per_row: int = 7) -> list[str]:
        rows: list[str] = []
        for start in range(0, len(tiles), per_row):
            chunk = tiles[start:start + per_row]
            row = " ".join(
                f"{idx:02d}{TILE_EMOJIS.get(tile, '⬜')}"
                for idx, tile in enumerate(chunk, start=start)
            )
            rows.append(row)
        return rows

    def _weight_lines(self, started_by_staff: bool) -> list[str]:
        lines = []
        for tile, weight in self._weights_for_mode(started_by_staff):
            lines.append(f"{tile} {weight}%")
        return lines

    @staticmethod
    def _asset_dir() -> str:
        return os.path.join("assets", "od")

    def _asset_path(self, key: str) -> str:
        filename = ASSET_FILE_NAMES.get(key, f"{key}.png")
        return os.path.join(self._asset_dir(), filename)

    def _tile_coordinates(self) -> tuple[list[tuple[int, int]], int]:
        coords: list[tuple[int, int]] = []
        cols = 7
        rows = (BOARD_TILE_COUNT + cols - 1) // cols
        available_w = max(1, CANVAS_W - (BOARD_SIDE_MARGIN * 2))
        available_h = max(1, CANVAS_H - BOARD_TOP_MARGIN - BOARD_BOTTOM_MARGIN)
        tile_size = max(48, int(min(available_w // cols, available_h // rows)))
        board_w = cols * tile_size
        board_h = rows * tile_size
        origin_x = BOARD_SIDE_MARGIN + max(0, (available_w - board_w) // 2)
        origin_y = BOARD_TOP_MARGIN + max(0, (available_h - board_h) // 2)
        for idx in range(BOARD_TILE_COUNT):
            row = idx // cols
            offset = idx % cols
            col = offset if row % 2 == 0 else (cols - 1 - offset)
            x = origin_x + (col * tile_size)
            y = origin_y + (row * tile_size)
            coords.append((x, y))
        return coords, tile_size

    def _load_font(self, path: str, size: int) -> Any:
        cache_key = (path, int(size))
        if cache_key in self._font_cache:
            return self._font_cache[cache_key]

        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            font = ImageFont.load_default()
        self._font_cache[cache_key] = font
        return font

    async def _get_map_asset(self, key: str) -> Any:
        normalized = str(key).strip().lower()
        cached = self._map_asset_cache.get(normalized)
        if cached is not None:
            return cached.copy()

        image = self._read_local_asset(normalized)
        if image is None:
            image = await self._download_remote_asset(normalized)

        if image is None:
            image = self._placeholder_tile(normalized)
        self._map_asset_cache[normalized] = image
        return image.copy()

    def _asset_candidates(self, key: str) -> list[str]:
        main_path = self._asset_path(key)
        root, _ext = os.path.splitext(main_path)
        candidates = [main_path, f"{root}.png", f"{root}.webp", f"{root}.jpg", f"{root}.jpeg"]
        ordered: list[str] = []
        for path in candidates:
            if path not in ordered:
                ordered.append(path)
        return ordered

    def _read_local_asset(self, key: str) -> Any | None:
        for path in self._asset_candidates(key):
            if not os.path.isfile(path):
                continue
            try:
                with Image.open(path) as local_img:
                    return local_img.convert("RGBA")
            except Exception:
                continue
        return None

    async def _download_remote_asset(self, key: str) -> Any | None:
        url = MAP_ASSET_URLS.get(key)
        if not url:
            return None

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }

        sessions = [
            getattr(self.bot, "session", None),
            getattr(self.bot, "trusted_session", None),
        ]

        image = None
        for session in sessions:
            if session is None:
                continue
            try:
                async with session.get(url, headers=headers, timeout=20) as response:
                    if response.status != 200:
                        continue
                    raw = await response.read()
                    with Image.open(io.BytesIO(raw)) as fetched:
                        image = fetched.convert("RGBA")
                        break
            except Exception:
                continue

        if image is None:
            return None

        try:
            os.makedirs(self._asset_dir(), exist_ok=True)
            image.save(self._asset_path(key), format="PNG")
        except Exception:
            pass
        return image

    def _placeholder_tile(self, tile: str) -> Any:
        color = TILE_COLORS.get(tile, (198, 198, 198, 255))
        image = Image.new("RGBA", (PLACEHOLDER_SIZE, PLACEHOLDER_SIZE), color)
        draw = ImageDraw.Draw(image)
        font = self._load_font(TEXT_FONT_PATH, 72)
        label = tile.upper()[:4]
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text(
            ((PLACEHOLDER_SIZE - tw) / 2, (PLACEHOLDER_SIZE - th) / 2),
            label,
            font=font,
            fill=(22, 22, 22, 255),
        )
        return image

    @staticmethod
    def _resample_lanczos() -> Any:
        return Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

    def _assign_player_tags(self, state: dict[str, Any]) -> None:
        order = [int(uid) for uid in state.get("order", [])]
        order_idx = {uid: idx for idx, uid in enumerate(order)}
        seed_groups: dict[str, list[int]] = {}
        for player in state.get("players", []):
            uid = int(player["user_id"])
            clean = "".join(ch for ch in str(player.get("name", "")).upper() if ch.isalnum())
            if len(clean) >= 2:
                seed = clean[:2]
            elif len(clean) == 1:
                seed = f"{clean}X"
            else:
                seed = f"P{uid % 10}"
            seed_groups.setdefault(seed, []).append(uid)

        tags: dict[str, str] = {}
        for seed, users in seed_groups.items():
            if len(users) == 1:
                tags[str(users[0])] = seed
                continue
            sorted_users = sorted(users, key=lambda uid: order_idx.get(uid, 10_000))
            for idx, uid in enumerate(sorted_users, start=1):
                tags[str(uid)] = f"{seed}{idx}"

        state["player_tags"] = tags

    def _player_tag(self, state: dict[str, Any], user_id: int) -> str:
        tags = state.get("player_tags", {})
        if isinstance(tags, dict):
            existing = tags.get(str(int(user_id)))
            if existing:
                return str(existing)
        return f"P{int(user_id) % 100}"

    def _player_color(self, user_id: int) -> tuple[int, int, int, int]:
        palette = [
            (247, 134, 106, 255),
            (112, 193, 179, 255),
            (255, 209, 102, 255),
            (168, 139, 250, 255),
            (100, 149, 237, 255),
            (245, 135, 165, 255),
            (124, 224, 154, 255),
            (255, 172, 120, 255),
        ]
        return palette[int(user_id) % len(palette)]

    async def _render_turn_board_file(
        self,
        row: Any,
        state: dict[str, Any],
        current_turn_id: int | None,
    ) -> discord.File:
        bg = await self._get_map_asset("background")
        canvas = bg.resize((CANVAS_W, CANVAS_H), self._resample_lanczos())
        draw = ImageDraw.Draw(canvas)
        coords, tile_size = self._tile_coordinates()
        tiny_font = self._load_font(TEXT_FONT_PATH, max(14, tile_size // 8))

        for idx, tile in enumerate(state.get("tiles", [])[:BOARD_TILE_COUNT]):
            asset_key = "normal" if tile == "start" else tile
            tile_image = await self._get_map_asset(asset_key)
            tile_image = tile_image.resize((tile_size, tile_size), self._resample_lanczos())
            x, y = coords[idx]
            canvas.alpha_composite(tile_image, (x, y))
            outline = (255, 218, 120, 255) if tile == "start" else (248, 248, 248, 245)
            radius = max(12, tile_size // 8)
            border_w = max(2, tile_size // 42)
            draw.rounded_rectangle(
                (x, y, x + tile_size, y + tile_size),
                radius=radius,
                outline=outline,
                width=border_w,
            )
            label = "START" if tile == "start" else str(idx)
            draw.text((x + max(6, tile_size // 20), y + max(6, tile_size // 20)), label, font=tiny_font, fill=(250, 250, 250, 255))

        token_gap = max(18, tile_size // 4)
        slot_offsets = [
            (-token_gap, -token_gap),
            (token_gap, -token_gap),
            (-token_gap, token_gap),
            (token_gap, token_gap),
            (0, 0),
        ]
        tile_players: dict[int, list[int]] = {}
        for player in state.get("players", []):
            if player.get("eliminated", False):
                continue
            tile_players.setdefault(int(player["pos"]), []).append(int(player["user_id"]))

        for tile_idx, users in tile_players.items():
            if tile_idx >= len(coords):
                continue
            center_x = coords[tile_idx][0] + (tile_size // 2)
            center_y = coords[tile_idx][1] + (tile_size // 2)
            for idx, uid in enumerate(users):
                off = slot_offsets[idx % len(slot_offsets)]
                cx = center_x + off[0]
                cy = center_y + off[1]
                color = self._player_color(uid)
                border_color = (255, 248, 193, 255) if uid == current_turn_id else (248, 248, 248, 255)
                border_width = max(2, tile_size // 40) + (1 if uid == current_turn_id else 0)
                radius = max(12, tile_size // 9)
                draw.ellipse(
                    (cx - radius, cy - radius, cx + radius, cy + radius),
                    fill=color,
                    outline=border_color,
                    width=border_width,
                )

        output = io.BytesIO()
        canvas.convert("RGB").save(output, format="PNG", optimize=True)
        output.seek(0)
        return discord.File(output, filename=f"{row['session_id'].lower()}_board.png")

    async def _send_turn_visual(
        self,
        destination: Any,
        row: Any,
        state: dict[str, Any],
        extra_lines: list[str] | None = None,
        status_override: str | None = None,
        include_image: bool = True,
        arm_timeout: bool = False,
        send_turn_ping: bool = False,
    ) -> None:
        display_status = status_override or str(row["status"])
        current_turn_id = self._get_current_player_id(state) if display_status == "ACTIVE" else None
        render_row = dict(row)
        render_row["status"] = display_status
        file = None
        if include_image:
            file = await self._render_turn_board_file(render_row, state, current_turn_id)
        embed = discord.Embed(
            title=f"Olympus Dash {row['session_id']}",
            description=(
                f"Status: **{display_status}**\n"
                f"Laps to win: **{row['laps_to_win']}**\n"
                f"Map version: **{state.get('board_version', 1)}**"
            ),
            color=0xD4AF37,
        )
        if current_turn_id is not None:
            embed.add_field(name="Current Turn", value=f"<@{current_turn_id}>", inline=True)
        if display_status == "ACTIVE":
            active_count = max(1, len(self._active_racers(state)))
            embed.add_field(name="Pot", value=f"${int(state.get('pot', 0))}", inline=True)
            embed.add_field(
                name="Round",
                value=(
                    f"{int(state.get('round_number', 1))} "
                    f"({int(state.get('round_roll_count', 0))}/{active_count} rolls)"
                ),
                inline=True,
            )
            embed.add_field(
                name="Summary",
                value="\n".join(
                    self._active_player_summary_lines(
                        state,
                        int(row["laps_to_win"]),
                        current_turn_id=current_turn_id,
                    )
                )[:1024],
                inline=False,
            )

        tags = state.get("player_tags", {})
        if include_image and isinstance(tags, dict) and tags:
            key_lines = []
            order = [int(uid) for uid in state.get("order", [])]
            if order:
                ordered_ids = [uid for uid in order if str(uid) in tags]
                ordered_ids.extend(
                    int(uid) for uid in tags.keys()
                    if int(uid) not in ordered_ids
                )
            else:
                ordered_ids = sorted(int(uid) for uid in tags.keys())
            for uid in ordered_ids:
                key_lines.append(f"<@{uid}> = **{tags[str(uid)]}**")
            embed.add_field(name="Tag Key", value="\n".join(key_lines)[:1024], inline=False)

        if not include_image:
            embed.add_field(
                name="Board Image",
                value="Image not refreshed this turn (refreshes each completed round).",
                inline=False,
            )

        if extra_lines:
            embed.add_field(name="Turn Log", value="\n".join(extra_lines)[:1024], inline=False)

        view = None
        if display_status == "ACTIVE" and current_turn_id is not None:
            view = ODTurnView(self, str(row["session_id"]))
            current_player = self._find_player(state, current_turn_id)
            if current_player is not None:
                current_tile = state["tiles"][int(current_player["pos"])]
                if current_tile == "shop":
                    action_text = (
                        "Current player can buy dice or roll using the buttons below."
                    )
                else:
                    action_text = (
                        "Current player should use Roll buttons below. "
                        "Buy buttons work only on shop tiles."
                    )
                embed.add_field(name="Action Buttons", value=action_text, inline=False)
                if current_tile == "shop":
                    embed.add_field(name="Shop", value="You are on a shop tile.", inline=False)

        channel = destination.channel if hasattr(destination, "channel") else destination
        if channel is None or not hasattr(channel, "send"):
            return

        session_id = str(row["session_id"]).strip().upper()
        if display_status == "ACTIVE" and current_turn_id is not None:
            sent_message = None
            channel_id = int(getattr(channel, "id", 0) or 0)
            round_number = int(state.get("round_number", 1))
            ref = self._round_visual_messages.get(session_id)
            if (
                not include_image
                and ref is not None
                and int(ref[0]) == channel_id
                and int(ref[2]) == round_number
                and hasattr(channel, "fetch_message")
            ):
                try:
                    sent_message = await channel.fetch_message(int(ref[1]))
                    await sent_message.edit(embed=embed, view=view)
                except Exception:
                    sent_message = None

            if sent_message is None:
                if file is not None:
                    sent_message = await channel.send(embed=embed, file=file, view=view)
                else:
                    sent_message = await channel.send(embed=embed, view=view)

            if channel_id > 0:
                self._round_visual_messages[session_id] = (channel_id, int(sent_message.id), round_number)

            if arm_timeout:
                self._schedule_turn_timeout(row, int(current_turn_id))

            if send_turn_ping:
                await channel.send(
                    f"🎯 <@{int(current_turn_id)}> your turn now. You have **60 seconds** to roll."
                )
            return

        self._cancel_turn_timeout(session_id)
        self._round_visual_messages.pop(session_id, None)
        if file is not None:
            await channel.send(embed=embed, file=file, view=view)
        else:
            await channel.send(embed=embed, view=view)

    def _leaderboard_lines(
        self,
        state: dict[str, Any],
        laps_to_win: int,
        current_turn_id: int | None = None,
    ) -> list[str]:
        board_size = len(state["tiles"])
        players = list(state["players"])
        players.sort(key=lambda p: self._player_progress(p, board_size), reverse=True)

        lines: list[str] = []
        for idx, player in enumerate(players, start=1):
            uid = int(player["user_id"])
            tag = self._player_tag(state, uid)
            marker = "🎯 " if current_turn_id == uid else ""
            eliminated = " (forfeit)" if player.get("eliminated", False) else ""
            lines.append(
                f"{idx}. {marker}<@{uid}> [{tag}] - lap {player['laps']}/{laps_to_win}, "
                f"tile {player['pos']}, OD coins {player['od_coins']}, "
                f"d8 x{player['d8_uses']}, d16 x{player['d16_uses']}{eliminated}"
            )
        return lines

    def _choose_swap_target(
        self,
        state: dict[str, Any],
        player: dict[str, Any],
    ) -> dict[str, Any] | None:
        current_id = int(player["user_id"])
        choices = [
            racer
            for racer in self._active_racers(state)
            if int(racer["user_id"]) != current_id
        ]
        if not choices:
            return None
        return self._rng.choice(choices)

    def _choose_crate(self) -> str:
        return self._weighted_choice(CRATE_WEIGHTS)

    def _resolve_tile_effects(
        self,
        state: dict[str, Any],
        player: dict[str, Any],
        started_by_staff: bool,
        laps_to_win: int,
    ) -> list[str]:
        messages: list[str] = []
        board_size = len(state["tiles"])
        max_chain = 8
        chain_count = 0

        while chain_count < max_chain:
            if int(player["laps"]) >= int(laps_to_win):
                messages.append("🏁 Lap target reached.")
                break

            tile = state["tiles"][int(player["pos"])]
            if tile == "start":
                messages.append("🏛️ Start tile.")
                break

            if tile == "normal":
                messages.append("⬜ Normal tile. No effect.")
                break

            if tile == "coin":
                gain = self._rng.randint(1, 5)
                player["od_coins"] = max(0, int(player["od_coins"]) + gain)
                messages.append(f"🪙 Coin tile: +{gain} OD coins.")
                break

            if tile == "lucky":
                jump = self._rng.randint(1, 3)
                self._move_player(player, jump, board_size)
                chain_count += 1
                messages.append(f"🍀 Lucky tile: move forward {jump} tiles.")
                if int(player["laps"]) >= int(laps_to_win):
                    messages.append("🏁 Lap target reached.")
                    break
                continue

            if tile == "trap":
                coins = int(player["od_coins"])
                do_burn = coins > 0 and (self._rng.random() < 0.5)
                if do_burn:
                    max_burn = max(1, int(coins * 0.70))
                    burned = self._rng.randint(1, max_burn)
                    player["od_coins"] = max(0, coins - burned)
                    messages.append(f"🕳️ Trap tile: burned {burned} OD coins.")
                    break
                backward = self._rng.randint(2, 5)
                self._move_player(player, -backward, board_size)
                chain_count += 1
                messages.append(f"🕳️ Trap tile: move backward {backward} tiles.")
                if int(player["laps"]) >= int(laps_to_win):
                    messages.append("🏁 Lap target reached.")
                    break
                continue

            if tile == "swap":
                target = self._choose_swap_target(state, player)
                if target is None:
                    messages.append("🔄 Swap tile: no valid target.")
                    break
                my_progress = self._player_progress(player, board_size)
                target_progress = self._player_progress(target, board_size)
                my_laps, my_pos = self._split_progress(my_progress, board_size)
                target_laps, target_pos = self._split_progress(target_progress, board_size)

                player["laps"], player["pos"] = target_laps, target_pos
                target["laps"], target["pos"] = my_laps, my_pos
                messages.append(
                    f"🔄 Swap tile: randomly swapped position with <@{int(target['user_id'])}>."
                )
                if int(player["laps"]) >= int(laps_to_win):
                    messages.append("🏁 Lap target reached.")
                break

            if tile == "xp":
                if started_by_staff:
                    xp_gain = self._rng.randint(1000, 5000)
                    player["xp_gained"] = max(0, int(player["xp_gained"]) + xp_gain)
                    messages.append(f"✨ XP tile: +{xp_gain} XP banked.")
                else:
                    messages.append("✨ XP tile disabled in non-staff OD sessions.")
                break

            if tile == "crate":
                if started_by_staff:
                    crate_key = self._choose_crate()
                    player["crates"][crate_key] = max(
                        0, int(player["crates"].get(crate_key, 0)) + 1
                    )
                    messages.append(f"📦 Crate tile: +1 {CRATE_LABELS[crate_key]} banked for final payout.")
                else:
                    messages.append("📦 Crate tile disabled in non-staff OD sessions.")
                break

            if tile == "shop":
                messages.append("🛒 Shop tile: use Buy buttons (or `$od buy <OD_ID> d8|d16`).")
                break

            messages.append("⬜ Unknown tile treated as normal.")
            break

        if chain_count >= max_chain:
            messages.append("⚠️ Tile chain capped to prevent loops.")

        return messages

    @staticmethod
    def _normalize_dice_selection(raw: str | None) -> tuple[str, int] | None:
        if raw is None:
            return "d6", 6
        parsed = str(raw).strip().lower()
        mapping = {
            "6": ("d6", 6),
            "d6": ("d6", 6),
            "8": ("d8", 8),
            "d8": ("d8", 8),
            "16": ("d16", 16),
            "d16": ("d16", 16),
        }
        return mapping.get(parsed)

    async def _apply_final_rewards(
        self,
        ctx: commands.Context,
        conn: Any,
        row: Any,
        state: dict[str, Any],
        winner_id: int,
    ) -> tuple[list[str], int]:
        board_size = len(state["tiles"])
        racers = list(state["players"])
        racers.sort(key=lambda p: self._player_progress(p, board_size), reverse=True)

        # Ensure winner appears first for final ranking.
        racers.sort(key=lambda p: int(p["user_id"]) != int(winner_id))

        result_lines: list[str] = []
        for rank, player in enumerate(racers, start=1):
            mult = RANK_MULTIPLIERS.get(rank, 0.10)
            xp_raw = int(player.get("xp_gained", 0))
            xp_award = int(xp_raw * mult)

            crates_raw = player.get("crates", {})
            crates_award = {
                key: int(int(crates_raw.get(key, 0)) * mult)
                for key in CRATE_LABELS
            }

            uid = int(player["user_id"])
            await conn.execute(
                """
                UPDATE profile
                   SET "xp" = COALESCE("xp", 0) + $1,
                       "crates_common" = COALESCE("crates_common", 0) + $2,
                       "crates_uncommon" = COALESCE("crates_uncommon", 0) + $3,
                       "crates_rare" = COALESCE("crates_rare", 0) + $4,
                       "crates_magic" = COALESCE("crates_magic", 0) + $5,
                       "crates_legendary" = COALESCE("crates_legendary", 0) + $6,
                       "crates_mystery" = COALESCE("crates_mystery", 0) + $7
                 WHERE "user" = $8;
                """,
                xp_award,
                crates_award["crates_common"],
                crates_award["crates_uncommon"],
                crates_award["crates_rare"],
                crates_award["crates_magic"],
                crates_award["crates_legendary"],
                crates_award["crates_mystery"],
                uid,
            )

            crate_text = ", ".join(
                f"{CRATE_LABELS[key]} x{value}"
                for key, value in crates_award.items()
                if value > 0
            )
            if not crate_text:
                crate_text = "no crates"

            result_lines.append(
                f"{rank}. <@{uid}> — XP {xp_award}, {crate_text} (x{mult:.2f})"
            )

        winner_money = int(state.get("pot", 0))
        winner_money += int(row["sponsor_reward"] or 0)
        if winner_money > 0:
            await conn.execute(
                'UPDATE profile SET "money" = COALESCE("money", 0) + $1 WHERE "user" = $2;',
                winner_money,
                int(winner_id),
            )
            await self.bot.log_transaction(
                ctx,
                from_=1,
                to=winner_id,
                subject="money",
                data={"Amount": winner_money, "Reason": f"Olympus Dash win {row['session_id']}"},
                conn=conn,
            )

        return result_lines, winner_money

    async def _fetch_session(
        self,
        conn: Any,
        session_id: str,
        for_update: bool = False,
    ) -> tuple[Any | None, dict[str, Any] | None]:
        normalized_id = str(session_id).strip().upper()
        query = "SELECT * FROM olympus_dash_sessions WHERE session_id = $1"
        if for_update:
            query += " FOR UPDATE"
        row = await conn.fetchrow(query, normalized_id)
        if row is None:
            return None, None
        state = self._normalize_state(self._parse_state(row["game_state"]), row)
        return row, state

    async def _save_session(
        self,
        conn: Any,
        session_id: str,
        state: dict[str, Any],
        status: str,
        host_id: int,
        winner_user_id: int | None,
    ) -> None:
        await conn.execute(
            """
            UPDATE olympus_dash_sessions
               SET game_state = $2::jsonb,
                   status = $3,
                   host_id = $4,
                   winner_user_id = $5,
                   updated_at = NOW()
             WHERE session_id = $1;
            """,
            str(session_id).strip().upper(),
            json.dumps(state),
            status,
            int(host_id),
            winner_user_id,
        )

    @staticmethod
    def _row_value(row: Any, key: str, default: Any = None) -> Any:
        if isinstance(row, dict):
            return row.get(key, default)
        try:
            return row[key]
        except Exception:
            return default

    def _active_player_summary_lines(
        self,
        state: dict[str, Any],
        laps_to_win: int,
        current_turn_id: int | None = None,
    ) -> list[str]:
        lines: list[str] = []
        board_size = len(state.get("tiles", []))
        players = list(self._active_racers(state))
        players.sort(key=lambda p: self._player_progress(p, board_size), reverse=True)
        for player in players:
            uid = int(player["user_id"])
            marker = "🎯 " if current_turn_id == uid else ""
            tile_idx = int(player.get("pos", 0))
            tiles = state.get("tiles", [])
            tile_name = str(tiles[tile_idx]).upper() if 0 <= tile_idx < len(tiles) else "N/A"
            lines.append(
                f"{marker}<@{uid}> • tile {tile_idx} ({tile_name}) • lap {int(player['laps'])}/{laps_to_win} • "
                f"coins {int(player.get('od_coins', 0))} • timeouts {int(player.get('timeout_count', 0))}/3"
            )
        if not lines:
            lines.append("No active racers.")
        return lines

    def _all_active_reached_map_target(self, state: dict[str, Any]) -> bool:
        racers = self._active_racers(state)
        if not racers:
            return False
        target_lap = int(state.get("map_lap_target", 1))
        return all(int(player.get("laps", 0)) >= target_lap for player in racers)

    def _cancel_turn_timeout(self, session_id: str) -> None:
        sid = str(session_id).strip().upper()
        task = self._turn_timeout_tasks.pop(sid, None)
        if task is not None:
            task.cancel()

    def _schedule_turn_timeout(
        self,
        row: Any,
        current_turn_id: int,
    ) -> None:
        session_id = str(self._row_value(row, "session_id", "")).strip().upper()
        guild_id = int(self._row_value(row, "guild_id", 0) or 0)
        channel_id = int(self._row_value(row, "channel_id", 0) or 0)
        if not session_id or guild_id <= 0 or channel_id <= 0:
            return

        self._cancel_turn_timeout(session_id)
        task = asyncio.create_task(
            self._turn_timeout_worker(
                session_id=session_id,
                guild_id=guild_id,
                channel_id=channel_id,
                expected_user_id=int(current_turn_id),
            )
        )
        self._turn_timeout_tasks[session_id] = task

    async def _turn_timeout_worker(
        self,
        *,
        session_id: str,
        guild_id: int,
        channel_id: int,
        expected_user_id: int,
    ) -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

        guild = self.bot.get_guild(int(guild_id))
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except Exception:
                channel = None

        actor = guild.me if guild is not None and getattr(guild, "me", None) is not None else self.bot.user
        log_ctx = ODCtxProxy(guild, channel, actor, "od autoroll")
        ok, body, row_data, state, status, messages, image_refresh = await self._roll_internal(
            session_id=session_id,
            guild_id=int(guild_id),
            user_id=int(expected_user_id),
            die="d6",
            ctx_for_log=log_ctx,
            timeout_mode=True,
        )
        if not ok or row_data is None or state is None or status is None:
            return

        if channel is None:
            return

        await channel.send(
            f"⏱️ <@{int(expected_user_id)}> timed out after 60s, bot rolled automatically (d6)."
        )
        await channel.send(body)
        await self._send_turn_visual(
            channel,
            row_data,
            state,
            extra_lines=messages[-6:],
            status_override=status,
            include_image=image_refresh,
            arm_timeout=(status == "ACTIVE"),
            send_turn_ping=(status == "ACTIVE"),
        )

    def _lobby_embed(self, row: Any, state: dict[str, Any]) -> discord.Embed:
        status = str(row["status"])
        started_by_staff = bool(row["started_by_staff"])
        entry_fee = int(row["entry_fee"])
        sponsor_reward = int(row["sponsor_reward"])
        max_players = int(row["max_players"])
        min_players = int(row["min_players"])
        players = list(state.get("players", []))
        host_id = int(row["host_id"])

        mode_text = (
            f"GM/CM mode: winner gets bot reward **${sponsor_reward}**."
            if started_by_staff
            else f"Entry mode: each player pays **${entry_fee}** (pot to winner)."
        )
        embed = discord.Embed(
            title=f"Olympus Dash Lobby {row['session_id']}",
            description=(
                f"Status: **{status}**\n"
                f"Host: <@{host_id}>\n"
                f"Laps to win: **{int(row['laps_to_win'])}**\n"
                f"Players: **{len(players)}/{max_players}** (min {min_players})\n"
                f"{mode_text}\n"
                f"Current pot: **${int(state.get('pot', 0))}**"
            ),
            color=0xD4AF37,
        )
        if players:
            lines = [f"{idx}. <@{int(p['user_id'])}>" for idx, p in enumerate(players, start=1)]
            embed.add_field(name="Lobby Players", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="Lobby Players", value="No players.", inline=False)
        embed.add_field(name="Map Preview", value=self._format_map_preview(state), inline=False)
        embed.set_footer(text="Use the buttons below to join, leave, or begin.")
        return embed

    async def _join_session_internal(
        self,
        *,
        session_id: str,
        guild_id: int,
        member: Any,
        log_ctx: Any,
    ) -> tuple[bool, str, dict[str, Any] | None, dict[str, Any] | None]:
        session_id = str(session_id).strip().upper()
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                row, state = await self._fetch_session(conn, session_id, for_update=True)
                if row is None:
                    return False, "Unknown OD session ID.", None, None
                if int(row["guild_id"]) != int(guild_id):
                    return False, "That OD session belongs to another server.", None, None
                if str(row["status"]) != "LOBBY":
                    return False, "You can only join sessions in lobby state.", None, None

                players = state["players"]
                if self._find_player(state, int(member.id)):
                    return False, "You already joined this session.", None, None
                if len(players) >= int(row["max_players"]):
                    return False, "This lobby is full.", None, None

                entry_fee = int(row["entry_fee"])
                if entry_fee > 0:
                    if not await has_money(self.bot, int(member.id), entry_fee, conn=conn):
                        return False, "You don't have enough money to join this OD.", None, None
                    await conn.execute(
                        'UPDATE profile SET "money" = "money" - $1 WHERE "user" = $2;',
                        entry_fee,
                        int(member.id),
                    )
                    await self.bot.log_transaction(
                        log_ctx,
                        from_=int(member.id),
                        to=2,
                        subject="money",
                        data={"Amount": entry_fee, "Reason": f"Olympus Dash entry {session_id}"},
                        conn=conn,
                    )

                players.append(self._new_player(member))
                state["pot"] = max(0, int(state.get("pot", 0)) + entry_fee)
                await self._save_session(
                    conn=conn,
                    session_id=session_id,
                    state=state,
                    status="LOBBY",
                    host_id=int(row["host_id"]),
                    winner_user_id=row["winner_user_id"],
                )
                row_data = dict(row)
                row_data["status"] = "LOBBY"

        return (
            True,
            f"✅ <@{int(member.id)}> joined **{session_id}**. "
            f"Players: **{len(state['players'])}/{int(row_data['max_players'])}**.",
            row_data,
            state,
        )

    async def _leave_session_internal(
        self,
        *,
        session_id: str,
        guild_id: int,
        member: Any,
        log_ctx: Any,
    ) -> tuple[bool, str, dict[str, Any] | None, dict[str, Any] | None]:
        session_id = str(session_id).strip().upper()
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                row, state = await self._fetch_session(conn, session_id, for_update=True)
                if row is None:
                    return False, "Unknown OD session ID.", None, None
                if int(row["guild_id"]) != int(guild_id):
                    return False, "That OD session belongs to another server.", None, None
                if str(row["status"]) != "LOBBY":
                    return False, "You can only leave before the match begins.", None, None

                player = self._find_player(state, int(member.id))
                if not player:
                    return False, "You are not in this session.", None, None

                state["players"] = [p for p in state["players"] if int(p["user_id"]) != int(member.id)]
                entry_fee = int(row["entry_fee"])
                if entry_fee > 0:
                    await conn.execute(
                        'UPDATE profile SET "money" = "money" + $1 WHERE "user" = $2;',
                        entry_fee,
                        int(member.id),
                    )
                    state["pot"] = max(0, int(state.get("pot", 0)) - entry_fee)
                    await self.bot.log_transaction(
                        log_ctx,
                        from_=1,
                        to=int(member.id),
                        subject="money",
                        data={"Amount": entry_fee, "Reason": f"Olympus Dash leave refund {session_id}"},
                        conn=conn,
                    )

                host_id = int(row["host_id"])
                status = "LOBBY"
                if not state["players"]:
                    status = "CANCELLED"
                elif host_id == int(member.id):
                    host_id = int(state["players"][0]["user_id"])

                await self._save_session(
                    conn=conn,
                    session_id=session_id,
                    state=state,
                    status=status,
                    host_id=host_id,
                    winner_user_id=row["winner_user_id"],
                )
                row_data = dict(row)
                row_data["status"] = status
                row_data["host_id"] = host_id

        if not state["players"]:
            return True, f"Session **{session_id}** is now cancelled (no players left).", row_data, state
        return (
            True,
            f"↩️ <@{int(member.id)}> left **{session_id}**. "
            f"Players now: **{len(state['players'])}/{int(row_data['max_players'])}**.",
            row_data,
            state,
        )

    async def _begin_session_internal(
        self,
        *,
        session_id: str,
        guild_id: int,
        actor_id: int,
        actor_is_staff: bool,
    ) -> tuple[bool, str, dict[str, Any] | None, dict[str, Any] | None]:
        session_id = str(session_id).strip().upper()
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                row, state = await self._fetch_session(conn, session_id, for_update=True)
                if row is None:
                    return False, "Unknown OD session ID.", None, None
                if int(row["guild_id"]) != int(guild_id):
                    return False, "That OD session belongs to another server.", None, None
                if str(row["status"]) != "LOBBY":
                    return False, "This session is not in lobby state.", None, None
                if int(row["host_id"]) != int(actor_id) and not actor_is_staff:
                    return False, "Only the host (or GM/CM) can begin the match.", None, None

                if len(state["players"]) < int(row["min_players"]):
                    return (
                        False,
                        f"Need at least {int(row['min_players'])} players to begin.",
                        None,
                        None,
                    )

                order = [int(player["user_id"]) for player in state["players"] if not player.get("eliminated", False)]
                self._rng.shuffle(order)
                state["order"] = order
                state["turn_index"] = 0
                state["round_number"] = 1
                state["round_roll_count"] = 0
                state["map_lap_target"] = 1
                self._assign_player_tags(state)

                await self._save_session(
                    conn=conn,
                    session_id=session_id,
                    state=state,
                    status="ACTIVE",
                    host_id=int(row["host_id"]),
                    winner_user_id=row["winner_user_id"],
                )
                row_data = dict(row)
                row_data["status"] = "ACTIVE"

        first_player = self._get_current_player_id(state)
        order_text = ", ".join(f"<@{uid}>" for uid in state["order"])
        message = (
            f"🎲 **{session_id} started**\n"
            f"Turn order (CSPRNG shuffle): {order_text}\n"
            f"Current turn: <@{first_player}>"
        )
        return True, message, row_data, state

    async def _roll_internal(
        self,
        *,
        session_id: str,
        guild_id: int,
        user_id: int,
        die: str | None,
        ctx_for_log: Any,
        timeout_mode: bool = False,
    ) -> tuple[
        bool,
        str,
        dict[str, Any] | None,
        dict[str, Any] | None,
        str | None,
        list[str],
        bool,
    ]:
        session_id = str(session_id).strip().upper()
        if timeout_mode:
            die_name, die_sides = ("d6", 6)
        else:
            die_selection = self._normalize_dice_selection(die)
            if die_selection is None:
                return False, "Invalid die. Use d6, d8, or d16.", None, None, None, [], False
            die_name, die_sides = die_selection

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                row, state = await self._fetch_session(conn, session_id, for_update=True)
                if row is None:
                    return False, "Unknown OD session ID.", None, None, None, [], False
                if int(row["guild_id"]) != int(guild_id):
                    return False, "That OD session belongs to another server.", None, None, None, [], False
                if str(row["status"]) != "ACTIVE":
                    return False, "This session is not active.", None, None, None, [], False

                current_id = self._get_current_player_id(state)
                if current_id is None:
                    return False, "No active player found in this session.", None, None, None, [], False
                if int(current_id) != int(user_id):
                    return False, "It is not your turn.", None, None, None, [], False

                player = self._find_player(state, user_id)
                if player is None or player.get("eliminated", False):
                    return False, "You are not an active racer in this session.", None, None, None, [], False

                messages: list[str] = []
                timeout_count = int(player.get("timeout_count", 0))
                if timeout_mode:
                    timeout_count += 1
                    player["timeout_count"] = timeout_count
                    messages.append(f"⏱️ Timeout: bot auto-roll triggered ({timeout_count}/3).")

                spend_msg = None
                if not timeout_mode and die_name == "d8":
                    if int(player.get("d8_uses", 0)) <= 0:
                        return False, "You have no d8 uses left.", None, None, None, [], False
                    player["d8_uses"] = int(player["d8_uses"]) - 1
                    spend_msg = "Used one **d8**."
                elif not timeout_mode and die_name == "d16":
                    if int(player.get("d16_uses", 0)) <= 0:
                        return False, "You have no d16 uses left.", None, None, None, [], False
                    player["d16_uses"] = int(player["d16_uses"]) - 1
                    spend_msg = "Used one **d16**."

                laps_to_win = int(row["laps_to_win"])
                board_size = len(state["tiles"])
                roll_value = self._rng.randint(1, die_sides)
                self._move_player(player, roll_value, board_size)

                if spend_msg:
                    messages.append(spend_msg)
                messages.append(f"🎲 Rolled **{roll_value}** on {die_name}.")
                messages.append(f"Moved to tile {player['pos']} (lap {player['laps']}/{laps_to_win}).")

                winner_id: int | None = None
                status = "ACTIVE"
                if int(player["laps"]) >= laps_to_win:
                    winner_id = int(player["user_id"])
                else:
                    tile_messages = self._resolve_tile_effects(
                        state=state,
                        player=player,
                        started_by_staff=bool(row["started_by_staff"]),
                        laps_to_win=laps_to_win,
                    )
                    messages.extend(tile_messages)

                    if int(player["laps"]) >= laps_to_win:
                        winner_id = int(player["user_id"])
                    else:
                        if self._all_active_reached_map_target(state):
                            reached_lap = int(state.get("map_lap_target", 1))
                            changed = self._mutate_map(state=state, started_by_staff=bool(row["started_by_staff"]))
                            state["map_lap_target"] = reached_lap + 1
                            messages.append(
                                f"🌀 Map shifted (v{state['board_version']}, changed {changed} tiles) "
                                f"because all active racers reached lap {reached_lap}."
                            )

                        if timeout_mode and timeout_count >= 3:
                            player["eliminated"] = True
                            messages.append("🚪 Removed for AFK after 3 timeouts.")

                        next_player = self._advance_turn(state)
                        if next_player is None:
                            status = "CANCELLED"
                            messages.append("Session cancelled: no active racers left.")
                        else:
                            messages.append(f"Next turn: <@{next_player}>.")

                result_lines: list[str] = []
                winner_money = 0
                if winner_id is not None:
                    status = "FINISHED"
                    result_lines, winner_money = await self._apply_final_rewards(
                        ctx=ctx_for_log,
                        conn=conn,
                        row=row,
                        state=state,
                        winner_id=winner_id,
                    )
                    messages.append(f"🏆 <@{winner_id}> wins **{session_id}**!")
                    if winner_money > 0:
                        messages.append(f"💰 Winner money reward: **${winner_money}**.")

                round_complete = False
                if status == "ACTIVE":
                    state["round_roll_count"] = int(state.get("round_roll_count", 0)) + 1
                    active_count = max(1, len(self._active_racers(state)))
                    completed_round = int(state.get("round_number", 1))
                    if int(state["round_roll_count"]) >= active_count:
                        round_complete = True
                        state["round_roll_count"] = 0
                        state["round_number"] = completed_round + 1
                        messages.append(f"🔁 Round {completed_round} complete.")

                await self._save_session(
                    conn=conn,
                    session_id=session_id,
                    state=state,
                    status=status,
                    host_id=int(row["host_id"]),
                    winner_user_id=winner_id if winner_id is not None else row["winner_user_id"],
                )
                row_data = dict(row)
                row_data["status"] = status

        body = [f"**{session_id} turn result**"] + messages
        if result_lines:
            body.append("")
            body.append("Final payout (XP/Crates after rank multipliers):")
            body.extend(result_lines)

        image_refresh = (status != "ACTIVE") or round_complete
        return True, "\n".join(body), row_data, state, status, messages, image_refresh

    async def _buy_die_internal(
        self,
        session_id: str,
        guild_id: int,
        user_id: int,
        die: str,
    ) -> tuple[bool, str, Any | None, dict[str, Any] | None, dict[str, Any] | None]:
        normalized_die = "d8" if str(die).lower() in {"8", "d8"} else "d16"
        cost = 5 if normalized_die == "d8" else 8

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                row, state = await self._fetch_session(conn, session_id, for_update=True)
                if row is None:
                    return False, "Unknown OD session ID.", None, None, None
                if int(row["guild_id"]) != int(guild_id):
                    return False, "This OD session belongs to another server.", None, None, None
                if str(row["status"]) != "ACTIVE":
                    return False, "You can only buy during an active game.", None, None, None

                current_id = self._get_current_player_id(state)
                if current_id is None:
                    return False, "No active player found in this session.", None, None, None
                if int(current_id) != int(user_id):
                    return False, "It is not your turn.", None, None, None

                player = self._find_player(state, user_id)
                if player is None or player.get("eliminated", False):
                    return False, "You are not an active racer in this session.", None, None, None

                tile = state["tiles"][int(player["pos"])]
                if tile != "shop":
                    return False, "You can only buy dice while standing on a shop tile.", None, None, None
                if int(player["od_coins"]) < cost:
                    return False, "Not enough OD coins for that die.", None, None, None

                player["od_coins"] = max(0, int(player["od_coins"]) - cost)
                uses_key = "d8_uses" if normalized_die == "d8" else "d16_uses"
                player[uses_key] = int(player.get(uses_key, 0)) + 1

                await self._save_session(
                    conn=conn,
                    session_id=session_id,
                    state=state,
                    status="ACTIVE",
                    host_id=int(row["host_id"]),
                    winner_user_id=row["winner_user_id"],
                )

                return (
                    True,
                    (
                        f"Bought **{normalized_die}** for **{cost} OD coins**. "
                        f"Inventory: d8 x{player['d8_uses']}, d16 x{player['d16_uses']}. "
                        f"Coins left: {player['od_coins']}."
                    ),
                    row,
                    state,
                    player,
                )

    async def _buy_from_interaction(
        self,
        interaction: discord.Interaction,
        session_id: str,
        die: str,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        ok, message, row, state, _player = await self._buy_die_internal(
            session_id=session_id,
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
            die=die,
        )
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await interaction.response.send_message(message, ephemeral=True)
        if interaction.channel is not None:
            await self._send_turn_visual(
                interaction.channel,
                row,
                state,
                extra_lines=[message],
                status_override="ACTIVE",
                include_image=False,
                arm_timeout=True,
                send_turn_ping=True,
            )

    async def _roll_from_interaction(
        self,
        interaction: discord.Interaction,
        session_id: str,
        die: str | None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This can only be used in a server.", ephemeral=True)
            return
        log_ctx = ODCtxProxy(interaction.guild, interaction.channel, interaction.user, "od roll")
        ok, body, row_data, state, status, messages, image_refresh = await self._roll_internal(
            session_id=session_id,
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
            die=die,
            ctx_for_log=log_ctx,
        )
        if not ok or row_data is None or state is None or status is None:
            await interaction.response.send_message(body, ephemeral=True)
            return

        await interaction.response.send_message(f"Action accepted: {die or 'd6'}", ephemeral=True)
        if interaction.channel is not None:
            await interaction.channel.send(body)
            await self._send_turn_visual(
                interaction.channel,
                row_data,
                state,
                extra_lines=messages[-6:],
                status_override=status,
                include_image=image_refresh,
                arm_timeout=(status == "ACTIVE"),
                send_turn_ping=(status == "ACTIVE"),
            )

    @commands.group(name="od", invoke_without_command=True, brief=_("Olympus Dash"))
    @commands.guild_only()
    async def od(self, ctx: commands.Context) -> None:
        await ctx.send(
            "**Olympus Dash commands**\n"
            "`$od help` - Full OD help.\n"
            "`$od start <laps 1-25> [value]` - Start a lobby.\n"
            "`$od join <od_id>` - Join a lobby.\n"
            "`$od leave <od_id>` - Leave a lobby (before start).\n"
            "`$od begin <od_id>` - Start match with joined players.\n"
            "`$od roll <od_id> [d6|d8|d16]` - Roll on your turn.\n"
            "`$od buy <od_id> <d8|d16>` - Buy one-use dice on shop tile.\n"
            "`$od stop <od_id>` - Host cancels an open session.\n"
            "`$od pause <od_id>` / `$od resume <od_id>` - Pause or resume.\n"
            "`$od status <od_id>` - Show session state.\n"
            "`$od list` - List open OD sessions.\n"
            "`$od legend` - Tile meanings and effects.\n"
            "`$od assetcheck` - Check OD visual assets status.\n"
            "`$od assetsync` - Re-download OD visual assets from Imgur.\n"
            "`$od simulate [laps] [normal|staff] [mutations]` - Preview map generation.\n\n"
            "UI:\n"
            "- Lobby posts Join/Leave/Begin buttons.\n"
            "- Active turn embeds include Roll/Buy action buttons for the current player.\n\n"
            "Turn timer:\n"
            "- Current player has 60s to roll.\n"
            "- On timeout, bot auto-rolls d6.\n"
            "- At 3 timeouts, player is marked AFK and removed.\n\n"
            "Host mode:\n"
            "- GM/CM host: second value is bot-funded winner money reward.\n"
            "- Normal host: second value is entry fee; winner takes the pot.\n"
            "- Host can cancel with `$od stop <od_id>` (entry fees refunded)."
        )

    @od.command(name="help", aliases=["h"])
    @commands.guild_only()
    async def od_help(self, ctx: commands.Context) -> None:
        normal_rates = ", ".join(self._weight_lines(False))
        staff_rates = ", ".join(self._weight_lines(True))
        await ctx.send(
            "**Olympus Dash Help**\n"
            "`$od start <laps 1-25> [value]`\n"
            "- GM/CM host: `[value]` = bot-funded winner money reward.\n"
            "- Normal host: `[value]` = entry fee; winner takes the pot.\n"
            "`$od join <od_id>` / `$od leave <od_id>` / `$od begin <od_id>`\n"
            "`$od roll <od_id> [d6|d8|d16]`\n"
            "`$od buy <od_id> <d8|d16>` (d8 costs 5 OD coins, d16 costs 8)\n"
            "`$od stop <od_id>` (host-only)\n"
            "`$od pause <od_id>` / `$od resume <od_id>` / `$od status <od_id>` / `$od list`\n"
            "`$od legend`\n"
            "`$od assetcheck` / `$od assetsync`\n"
            "`$od simulate [laps] [normal|staff] [mutations]`\n\n"
            "**UI**\n"
            "- Lobby uses Join/Leave/Begin buttons.\n"
            "- Active turn embeds provide Roll/Buy action buttons for current player.\n\n"
            "**Turn Timer**\n"
            "- Current player has 60s to roll.\n"
            "- Timeout triggers auto-roll d6.\n"
            "- 3 timeouts => AFK removal.\n\n"
            "**Player caps by laps**\n"
            "- 1-10 laps: max 15 players\n"
            "- 11-15 laps: max 12 players\n"
            "- 16-20 laps: max 10 players\n"
            "- 21-25 laps: max 8 players\n\n"
            "**Tile spawn rates (normal sessions)**\n"
            f"{normal_rates}\n\n"
            "**Tile spawn rates (GM/CM sessions)**\n"
            f"{staff_rates}\n\n"
            "**Dynamic map**\n"
            "- When a player gains a lap, 20% of non-start tiles reroll."
        )

    @od.command(name="legend")
    @commands.guild_only()
    async def od_legend(self, ctx: commands.Context) -> None:
        embeds: list[discord.Embed] = []
        for tile_key, detail in TILE_LEGEND_DETAILS:
            emoji = TILE_EMOJIS.get(tile_key, "⬜")
            asset_key = "normal" if tile_key == "start" else tile_key
            image_url = MAP_ASSET_URLS.get(asset_key)
            description = detail
            if tile_key == "start":
                description += "\nUses the Normal tile art with a gold START border in OD renders."

            embed = discord.Embed(
                title=f"{emoji} {tile_key.title()} Tile",
                description=description,
                color=0xD4AF37,
            )
            if image_url:
                embed.set_image(url=image_url)
            embed.set_footer(text="OD coins are session-only and burned at game end.")
            embeds.append(embed)

        await ctx.send(embeds=embeds)

    @od.command(name="assetcheck")
    @commands.guild_only()
    async def od_assetcheck(self, ctx: commands.Context) -> None:
        lines = ["**OD Asset Check**"]
        missing = 0
        for key in [
            "background",
            "normal",
            "coin",
            "lucky",
            "trap",
            "shop",
            "xp",
            "swap",
            "crate",
        ]:
            local = self._read_local_asset(key) is not None
            expected = self._asset_path(key)
            if local:
                lines.append(f"✅ `{key}` -> `{expected}`")
            else:
                missing += 1
                lines.append(f"❌ `{key}` missing (will fallback placeholder)")
        if missing:
            lines.append("")
            lines.append("Run `$od assetsync` to pull assets from Imgur.")
        await ctx.send("\n".join(lines))

    @od.command(name="assetsync")
    @commands.guild_only()
    async def od_assetsync(self, ctx: commands.Context) -> None:
        os.makedirs(self._asset_dir(), exist_ok=True)
        ok = 0
        fail = 0
        lines = ["**OD Asset Sync**"]
        for key in [
            "background",
            "normal",
            "coin",
            "lucky",
            "trap",
            "shop",
            "xp",
            "swap",
            "crate",
        ]:
            image = await self._download_remote_asset(key)
            if image is None:
                fail += 1
                lines.append(f"❌ `{key}` failed")
            else:
                ok += 1
                self._map_asset_cache.pop(key, None)
                lines.append(f"✅ `{key}` saved to `{self._asset_path(key)}`")
        lines.append("")
        lines.append(f"Done: {ok} ok, {fail} failed.")
        await ctx.send("\n".join(lines))

    @od.command(name="simulate", aliases=["sim", "mappreview"])
    @commands.guild_only()
    async def od_simulate(
        self,
        ctx: commands.Context,
        laps: IntFromTo(1, MAX_LAPS) = 10,
        mode: str = "normal",
        mutations: IntFromTo(0, 5) = 0,
    ) -> None:
        mode_normalized = str(mode).strip().lower()
        if mode_normalized in {"staff", "gm", "cm", "event"}:
            started_by_staff = True
        elif mode_normalized in {"normal", "member", "public"}:
            started_by_staff = False
        else:
            return await ctx.send("Mode must be `normal` or `staff`.")

        board_size = self._board_size_for_laps(int(laps))
        state: dict[str, Any] = {
            "tiles": self._generate_map(board_size, started_by_staff),
            "board_version": 1,
            "players": [],
            "order": [],
            "turn_index": 0,
            "pot": 0,
        }
        counts = Counter(state["tiles"])
        lines = [
            f"Mode: **{'staff' if started_by_staff else 'normal'}**",
            f"Board: **{board_size} tiles** (7x4 snake path)",
            "Spawn rates: " + ", ".join(self._weight_lines(started_by_staff)),
            "Tile counts v1: " + ", ".join(
                f"{tile}:{counts.get(tile, 0)}"
                for tile in ["start", "normal", "coin", "lucky", "trap", "shop", "swap", "xp", "crate"]
                if counts.get(tile, 0) > 0
            ),
            f"Map v{state['board_version']}: {self._format_map_preview(state)}",
        ]

        for _ in range(int(mutations)):
            changed = self._mutate_map(state, started_by_staff)
            counts = Counter(state["tiles"])
            lines.extend(
                [
                    f"Map v{state['board_version']}: changed **{changed}** tiles",
                    "Tile counts: " + ", ".join(
                        f"{tile}:{counts.get(tile, 0)}"
                        for tile in ["start", "normal", "coin", "lucky", "trap", "shop", "swap", "xp", "crate"]
                        if counts.get(tile, 0) > 0
                    ),
                    f"Preview: {self._format_map_preview(state)}",
                ]
            )
        if len(lines) > 12:
            lines = lines[:11] + [f"... and {len(lines) - 11} more simulation lines."]

        preview_row = {
            "session_id": "OD-SIM",
            "status": "SIMULATION",
            "laps_to_win": int(laps),
        }
        await self._send_turn_visual(
            ctx,
            preview_row,
            state,
            extra_lines=lines,
            status_override="SIMULATION",
            include_image=True,
        )

    @od.command(name="start")
    @commands.guild_only()
    @has_char()
    @locale_doc
    async def od_start(
        self,
        ctx: commands.Context,
        laps: IntFromTo(1, MAX_LAPS),
        value: IntGreaterThan(-1) = 0,
    ) -> None:
        _(
            """Start an Olympus Dash lobby.

`<laps>`: 1 to 25.
`[value]`: for GM/CM this is winner money reward; for others this is entry fee.
"""
        )
        laps = int(laps)
        value = int(value)
        started_by_staff = await self._is_staff_host(ctx)
        entry_fee = 0 if started_by_staff else value
        sponsor_reward = value if started_by_staff else 0
        max_players = self._max_players_for_laps(laps)
        board_size = self._board_size_for_laps(laps)
        session_id = self._new_session_id()

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                if entry_fee > 0 and not await has_money(self.bot, ctx.author.id, entry_fee, conn=conn):
                    return await ctx.send(_("You don't have enough money for the entry fee."))

                if entry_fee > 0:
                    await conn.execute(
                        'UPDATE profile SET "money" = "money" - $1 WHERE "user" = $2;',
                        entry_fee,
                        ctx.author.id,
                    )
                    await self.bot.log_transaction(
                        ctx,
                        from_=ctx.author.id,
                        to=2,
                        subject="money",
                        data={"Amount": entry_fee, "Reason": f"Olympus Dash entry {session_id}"},
                        conn=conn,
                    )

                host_player = self._new_player(ctx.author)
                tiles = self._generate_map(board_size, started_by_staff)
                state = {
                    "tiles": tiles,
                    "board_version": 1,
                    "players": [host_player],
                    "order": [],
                    "turn_index": 0,
                    "pot": int(entry_fee),
                    "round_number": 1,
                    "round_roll_count": 0,
                    "map_lap_target": 1,
                }

                await conn.execute(
                    """
                    INSERT INTO olympus_dash_sessions (
                        session_id, guild_id, channel_id, host_id, started_by_staff,
                        laps_to_win, min_players, max_players, entry_fee, sponsor_reward,
                        status, game_state
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'LOBBY', $11::jsonb);
                    """,
                    session_id,
                    ctx.guild.id,
                    ctx.channel.id,
                    ctx.author.id,
                    started_by_staff,
                    laps,
                    MIN_PLAYERS,
                    max_players,
                    entry_fee,
                    sponsor_reward,
                    json.dumps(state),
                )

        row_data = {
            "session_id": session_id,
            "status": "LOBBY",
            "host_id": int(ctx.author.id),
            "started_by_staff": started_by_staff,
            "laps_to_win": laps,
            "min_players": MIN_PLAYERS,
            "max_players": max_players,
            "entry_fee": entry_fee,
            "sponsor_reward": sponsor_reward,
        }
        view = ODLobbyView(self, session_id=session_id, host_id=int(ctx.author.id))
        await ctx.send(embed=self._lobby_embed(row_data, state), view=view)

    @od.command(name="join")
    @commands.guild_only()
    @has_char()
    async def od_join(self, ctx: commands.Context, session_id: str) -> None:
        ok, message, _row_data, _state = await self._join_session_internal(
            session_id=session_id,
            guild_id=ctx.guild.id,
            member=ctx.author,
            log_ctx=ctx,
        )
        await ctx.send(message)

    @od.command(name="leave")
    @commands.guild_only()
    @has_char()
    async def od_leave(self, ctx: commands.Context, session_id: str) -> None:
        _ok, message, _row_data, _state = await self._leave_session_internal(
            session_id=session_id,
            guild_id=ctx.guild.id,
            member=ctx.author,
            log_ctx=ctx,
        )
        await ctx.send(message)

    @od.command(name="begin")
    @commands.guild_only()
    @has_char()
    async def od_begin(self, ctx: commands.Context, session_id: str) -> None:
        caller_is_staff = await self._is_staff_host(ctx)
        ok, message, row_data, state = await self._begin_session_internal(
            session_id=session_id,
            guild_id=ctx.guild.id,
            actor_id=ctx.author.id,
            actor_is_staff=caller_is_staff,
        )
        if not ok or row_data is None or state is None:
            return await ctx.send(message)

        await ctx.send(message)
        await self._send_turn_visual(
            ctx,
            row_data,
            state,
            extra_lines=["Game started. Use action buttons below for this turn."],
            status_override="ACTIVE",
            arm_timeout=True,
            send_turn_ping=True,
        )

    @od.command(name="buy")
    @commands.guild_only()
    @has_char()
    async def od_buy(self, ctx: commands.Context, session_id: str, die: str) -> None:
        session_id = str(session_id).strip().upper()
        die = str(die).strip().lower()
        if die not in {"d8", "8", "d16", "16"}:
            return await ctx.send(_("Choose `d8` or `d16`."))
        ok, message, row, state, _player = await self._buy_die_internal(
            session_id=session_id,
            guild_id=ctx.guild.id,
            user_id=ctx.author.id,
            die=die,
        )
        if not ok:
            return await ctx.send(message)
        await ctx.send(f"🛒 {message}")
        await self._send_turn_visual(
            ctx,
            row,
            state,
            extra_lines=[message],
            status_override="ACTIVE",
            include_image=False,
            arm_timeout=True,
            send_turn_ping=True,
        )

    @od.command(name="roll")
    @commands.guild_only()
    @has_char()
    async def od_roll(self, ctx: commands.Context, session_id: str, die: str | None = None) -> None:
        ok, body, row_data, state, status, messages, image_refresh = await self._roll_internal(
            session_id=session_id,
            guild_id=ctx.guild.id,
            user_id=ctx.author.id,
            die=die,
            ctx_for_log=ctx,
        )
        if not ok or row_data is None or state is None or status is None:
            return await ctx.send(body)

        await ctx.send(body)
        await self._send_turn_visual(
            ctx,
            row_data,
            state,
            extra_lines=messages[-6:],
            status_override=status,
            include_image=image_refresh,
            arm_timeout=(status == "ACTIVE"),
            send_turn_ping=(status == "ACTIVE"),
        )

    @od.command(name="stop")
    @commands.guild_only()
    @has_char()
    async def od_stop(self, ctx: commands.Context, session_id: str) -> None:
        session_id = str(session_id).strip().upper()
        refunds_total = 0
        refunded_players = 0

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                row, state = await self._fetch_session(conn, session_id, for_update=True)
                if row is None:
                    return await ctx.send(_("Unknown OD session ID."))
                if int(row["guild_id"]) != int(ctx.guild.id):
                    return await ctx.send(_("That OD session belongs to another server."))
                if str(row["status"]) in {"CANCELLED", "FINISHED"}:
                    return await ctx.send(_("This session is already closed."))
                if int(row["host_id"]) != int(ctx.author.id):
                    return await ctx.send(_("Only the session host can stop this OD."))

                entry_fee = int(row["entry_fee"])
                if entry_fee > 0:
                    for player in state.get("players", []):
                        uid = int(player["user_id"])
                        await conn.execute(
                            'UPDATE profile SET "money" = COALESCE("money", 0) + $1 WHERE "user" = $2;',
                            entry_fee,
                            uid,
                        )
                        await self.bot.log_transaction(
                            ctx,
                            from_=1,
                            to=uid,
                            subject="money",
                            data={"Amount": entry_fee, "Reason": f"Olympus Dash stop refund {session_id}"},
                            conn=conn,
                        )
                        refunds_total += entry_fee
                        refunded_players += 1
                    state["pot"] = 0

                await self._save_session(
                    conn=conn,
                    session_id=session_id,
                    state=state,
                    status="CANCELLED",
                    host_id=int(row["host_id"]),
                    winner_user_id=row["winner_user_id"],
                )
                row_data = dict(row)
                row_data["status"] = "CANCELLED"

        self._cancel_turn_timeout(session_id)
        self._round_visual_messages.pop(session_id, None)

        lines = [f"🛑 Session **{session_id}** was stopped by host <@{ctx.author.id}>."]
        if refunded_players > 0:
            lines.append(
                f"Refunded **${int(row_data['entry_fee'])}** to {refunded_players} players "
                f"(total **${refunds_total}**)."
            )
        await ctx.send("\n".join(lines))
        await self._send_turn_visual(
            ctx,
            row_data,
            state,
            extra_lines=lines,
            status_override="CANCELLED",
            include_image=True,
        )

    @od.command(name="pause")
    @commands.guild_only()
    async def od_pause(self, ctx: commands.Context, session_id: str) -> None:
        await self._toggle_pause(ctx, session_id, pause=True)

    @od.command(name="resume")
    @commands.guild_only()
    async def od_resume(self, ctx: commands.Context, session_id: str) -> None:
        await self._toggle_pause(ctx, session_id, pause=False)

    async def _toggle_pause(self, ctx: commands.Context, session_id: str, pause: bool) -> None:
        session_id = str(session_id).strip().upper()
        caller_is_staff = await self._is_staff_host(ctx)
        wanted = "PAUSED" if pause else "ACTIVE"
        expected = "ACTIVE" if pause else "PAUSED"

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                row, state = await self._fetch_session(conn, session_id, for_update=True)
                if row is None:
                    return await ctx.send(_("Unknown OD session ID."))
                if int(row["guild_id"]) != int(ctx.guild.id):
                    return await ctx.send(_("That OD session belongs to another server."))
                if str(row["status"]) != expected:
                    return await ctx.send(f"Session must be {expected} to switch to {wanted}.")
                if int(row["host_id"]) != int(ctx.author.id) and not caller_is_staff:
                    return await ctx.send(_("Only the host (or GM/CM) can do that."))

                await self._save_session(
                    conn=conn,
                    session_id=session_id,
                    state=state,
                    status=wanted,
                    host_id=int(row["host_id"]),
                    winner_user_id=row["winner_user_id"],
                )
                row_data = dict(row)
                row_data["status"] = wanted

        action = "paused" if pause else "resumed"
        await ctx.send(f"⏯️ Session **{session_id}** {action}.")
        if pause:
            self._cancel_turn_timeout(session_id)
            return

        await self._send_turn_visual(
            ctx,
            row_data,
            state,
            extra_lines=[f"Session resumed. Current player has 60 seconds to roll."],
            status_override="ACTIVE",
            include_image=False,
            arm_timeout=True,
            send_turn_ping=True,
        )

    @od.command(name="status")
    @commands.guild_only()
    async def od_status(self, ctx: commands.Context, session_id: str) -> None:
        session_id = str(session_id).strip().upper()
        async with self.bot.pool.acquire() as conn:
            row, state = await self._fetch_session(conn, session_id, for_update=False)

        if row is None:
            return await ctx.send(_("Unknown OD session ID."))
        if int(row["guild_id"]) != int(ctx.guild.id):
            return await ctx.send(_("That OD session belongs to another server."))

        current_id = self._get_current_player_id(state) if str(row["status"]) == "ACTIVE" else None
        mode_text = (
            f"GM/CM mode, bot reward ${int(row['sponsor_reward'])}"
            if bool(row["started_by_staff"])
            else f"Entry mode, fee ${int(row['entry_fee'])}, current pot ${int(state.get('pot', 0))}"
        )
        lines = [
            f"**Olympus Dash {row['session_id']}**",
            f"Status: **{row['status']}**",
            f"Host: <@{int(row['host_id'])}>",
            f"Laps: **{int(row['laps_to_win'])}**",
            f"Players: **{len(state['players'])}/{int(row['max_players'])}** (min {int(row['min_players'])})",
            mode_text,
            f"Map v{int(state.get('board_version', 1))}: {self._format_map_preview(state)}",
        ]
        if current_id is not None:
            lines.append(f"Current turn: <@{current_id}>")

        lines.append("")
        lines.append("Leaderboard:")
        lines.extend(self._leaderboard_lines(state, int(row["laps_to_win"]), current_turn_id=current_id))
        await ctx.send("\n".join(lines))
        await self._send_turn_visual(ctx, row, state, status_override=str(row["status"]), include_image=False)

    @od.command(name="list")
    @commands.guild_only()
    async def od_list(self, ctx: commands.Context) -> None:
        async with self.bot.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT session_id, status, host_id, laps_to_win, max_players, entry_fee, sponsor_reward, started_by_staff, game_state
                  FROM olympus_dash_sessions
                 WHERE guild_id = $1
                   AND status IN ('LOBBY', 'ACTIVE', 'PAUSED')
                 ORDER BY created_at DESC
                 LIMIT 15;
                """,
                ctx.guild.id,
            )

        if not rows:
            return await ctx.send(_("No open Olympus Dash sessions in this server."))

        lines = ["**Open Olympus Dash Sessions**"]
        for row in rows:
            state = self._parse_state(row["game_state"])
            players_count = len(state.get("players", [])) if isinstance(state, dict) else 0
            mode = (
                f"GM/CM reward ${int(row['sponsor_reward'])}"
                if bool(row["started_by_staff"])
                else f"entry ${int(row['entry_fee'])}"
            )
            lines.append(
                f"`{row['session_id']}` - {row['status']} | "
                f"laps {int(row['laps_to_win'])} | players {players_count}/{int(row['max_players'])} | "
                f"host <@{int(row['host_id'])}> | {mode}"
            )
        await ctx.send("\n".join(lines))


async def setup(bot: Bot) -> None:
    await bot.add_cog(OlympusDash(bot))
