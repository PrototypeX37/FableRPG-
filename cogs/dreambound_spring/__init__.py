"""
The IdleRPG Discord Bot
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
import datetime as dt
import io
import json
import random
from collections import defaultdict
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands, tasks
from PIL import Image

from classes.converters import IntFromTo, IntGreaterThan
from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils import misc as rpgtools
from utils.checks import has_char, is_gm
from utils.divine_familiars import (
    DIVINE_FAMILIARS,
    award_divine_shards,
    ensure_divine_familiar_tables,
    format_familiar_display_name,
    resolve_familiar_key,
)
from utils.i18n import _, locale_doc

from .data import (
    ALLOWED_PROFILE_CRATE_COLUMNS,
    ASSETS,
    CONFIG,
    CRATE_PROFILE_COLUMNS,
    DEFAULT_PHASE,
    DEFAULT_SEASON_ID,
    EVENT_CURRENCY,
    EVENT_NAME,
    GARDEN_NAME,
    GARDEN_MILESTONE_REWARDS,
    SEEDS,
    SHOP_ITEMS,
)
from .pve_data import (
    SPRING_PVE_CONFIG,
    SPRING_PVE_DIVINE_SHARD_CHANCE_PCT,
    SPRING_PVE_MONSTERS,
    SPRING_PVE_REWARD_DEFAULTS,
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


SPRING_DIVINE_SHARD_PITY_TIERS = (
    (60, 2),
    (80, 3),
    (100, 5),
)


def _get_spring_divine_shard_pity_state(
    base_chance_pct: int,
    no_shard_wins: int,
    pity_tiers=SPRING_DIVINE_SHARD_PITY_TIERS,
):
    base_chance = max(0, min(100, int(base_chance_pct or 0)))
    miss_streak = max(0, int(no_shard_wins or 0))

    pity_multiplier = 1
    active_threshold = None
    next_threshold = None
    next_multiplier = None

    for threshold, multiplier in pity_tiers:
        threshold = max(0, int(threshold))
        multiplier = max(1, int(multiplier))
        if miss_streak >= threshold:
            pity_multiplier = multiplier
            active_threshold = threshold
            continue
        if next_threshold is None:
            next_threshold = threshold
            next_multiplier = multiplier

    return {
        "base_chance_pct": base_chance,
        "effective_chance_pct": min(100, int(base_chance * pity_multiplier)),
        "pity_multiplier": pity_multiplier,
        "pity_active": pity_multiplier > 1,
        "active_threshold": active_threshold,
        "next_threshold": next_threshold,
        "next_multiplier": next_multiplier,
    }


class DreamboundSpring(commands.Cog):
    DIVINE_SHARD_PITY_TIERS = SPRING_DIVINE_SHARD_PITY_TIERS
    SPRING_PVE_RETIRED_MESSAGE = (
        "`springpve` is currently retired while Dreambound Spring is being wound down. "
        "You can still use `garden`, `garden harvest`, and `springshop`."
    )

    def __init__(self, bot):
        self.bot = bot
        self._seed_groups = self._build_seed_groups()
        self._seed_group_aliases = self._build_seed_group_aliases()
        self._seed_aliases = self._build_seed_aliases()
        self._item_aliases = self._build_item_aliases()
        self._init_db.start()

    def cog_unload(self):
        self._init_db.cancel()

    @staticmethod
    def _build_seed_groups() -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for seed_key, seed_data in SEEDS.items():
            group = str(seed_data.get("group", "")).strip().lower()
            if not group:
                continue
            groups.setdefault(group, []).append(seed_key)
        return groups

    def _seed_plant_priority(self, seed_type: str) -> tuple[int, int, str, str]:
        seed_data = SEEDS.get(seed_type, {})
        group = str(seed_data.get("group", "")).strip().lower()
        group_rank = {"normal": 0, "special": 1, "horsemen": 2}.get(group, 99)
        try:
            growth_seconds = int(seed_data.get("growth_seconds", CONFIG["BASE_GROWTH_SECONDS"]))
        except (TypeError, ValueError):
            growth_seconds = int(CONFIG["BASE_GROWTH_SECONDS"])
        display_name = str(seed_data.get("display_name", seed_type)).strip().lower()
        return (group_rank, growth_seconds, display_name, seed_type)

    @staticmethod
    def _build_seed_group_aliases() -> dict[str, str]:
        return {
            "normal": "normal",
            "normal_seed": "normal",
            "normal_seeds": "normal",
            "basic": "normal",
            "basic_seed": "normal",
            "basic_seeds": "normal",
            "special": "special",
            "special_seed": "special",
            "special_seeds": "special",
            "rare": "special",
            "rare_seed": "special",
            "rare_seeds": "special",
            "horsemen": "horsemen",
            "horseman": "horsemen",
            "horsemen_seed": "horsemen",
            "horsemen_seeds": "horsemen",
            "horseman_seed": "horsemen",
            "horseman_seeds": "horsemen",
        }

    @staticmethod
    def _build_seed_aliases() -> dict[str, str]:
        aliases: dict[str, str] = {}
        for seed_key, seed_data in SEEDS.items():
            key_lower = seed_key.lower()
            display = str(seed_data.get("display_name", "")).strip().lower()
            base = key_lower.replace("_seed", "")
            short_base = base.replace("_dream", "").replace("dream_", "")

            aliases[key_lower] = seed_key
            aliases[base] = seed_key
            aliases[key_lower.replace("_", "")] = seed_key
            aliases[base.replace("_", "")] = seed_key
            aliases[short_base] = seed_key
            aliases[short_base.replace("_", "")] = seed_key

            tokens = [tok for tok in short_base.split("_") if tok]
            if tokens:
                aliases[" ".join(tokens)] = seed_key
                aliases["".join(tokens)] = seed_key
                aliases[tokens[0]] = seed_key

            if display:
                aliases[display] = seed_key
                aliases[display.replace(" ", "_")] = seed_key
                aliases[display.replace(" ", "")] = seed_key
                display_no_seed = display.replace(" seed", "")
                aliases[display_no_seed] = seed_key
                aliases[display_no_seed.replace(" ", "_")] = seed_key
                aliases[display_no_seed.replace(" ", "")] = seed_key
        return aliases

    @staticmethod
    def _build_item_aliases() -> dict[str, str]:
        aliases: dict[str, str] = {}
        for key, data in SHOP_ITEMS.items():
            item_id = str(data["item_id"])
            display = str(data.get("display_name", "")).strip().lower()
            aliases[key.lower()] = item_id
            aliases[key.lower().replace("_", "")] = item_id
            aliases[item_id.lower()] = item_id
            if display:
                aliases[display] = item_id
                aliases[display.replace(" ", "_")] = item_id
                aliases[display.replace(" ", "")] = item_id
        aliases["water"] = "ENCHANTED_WATER"
        aliases["tonic"] = "BLOOM_TONIC"
        aliases["rshard"] = "DIVINE_SHARD_RANDOM"
        aliases["randomshard"] = "DIVINE_SHARD_RANDOM"
        aliases["cshard"] = "DIVINE_SHARD_CHOSEN"
        aliases["chosenshard"] = "DIVINE_SHARD_CHOSEN"
        return aliases

    @tasks.loop(count=1)
    async def _init_db(self):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dreambound_spring_state (
                    season_id TEXT PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    phase SMALLINT NOT NULL DEFAULT 2,
                    starts_at TIMESTAMPTZ,
                    ends_at TIMESTAMPTZ
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dreambound_spring_wallet (
                    user_id BIGINT NOT NULL,
                    season_id TEXT NOT NULL,
                    bloomshards BIGINT NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, season_id)
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dreambound_spring_seeds (
                    user_id BIGINT NOT NULL,
                    season_id TEXT NOT NULL,
                    seed_type TEXT NOT NULL,
                    qty INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, season_id, seed_type)
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dreambound_spring_inventory (
                    user_id BIGINT NOT NULL,
                    season_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    qty INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, season_id, item_id)
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dreambound_spring_gardens (
                    user_id BIGINT NOT NULL,
                    season_id TEXT NOT NULL,
                    garden_level SMALLINT NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, season_id)
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dreambound_spring_slots (
                    user_id BIGINT NOT NULL,
                    season_id TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    seed_type TEXT,
                    planted_at TIMESTAMPTZ,
                    boost_seconds INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, season_id, slot_index)
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dreambound_spring_milestones (
                    user_id BIGINT NOT NULL,
                    season_id TEXT NOT NULL,
                    milestone_key TEXT NOT NULL,
                    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, season_id, milestone_key)
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dreambound_spring_snapshots (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    season_id TEXT NOT NULL,
                    snapshot_data JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dreambound_spring_divine_pity (
                    user_id BIGINT NOT NULL,
                    season_id TEXT NOT NULL,
                    no_shard_wins INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (user_id, season_id)
                );
                """
            )

            await conn.execute(
                """
                INSERT INTO dreambound_spring_state (season_id, enabled, phase, starts_at)
                VALUES ($1, TRUE, $2, NOW())
                ON CONFLICT (season_id) DO NOTHING;
                """,
                DEFAULT_SEASON_ID,
                DEFAULT_PHASE,
            )

    @_init_db.before_loop
    async def _before_init_db(self):
        await self.bot.wait_until_ready()

    @property
    def season_id(self) -> str:
        return DEFAULT_SEASON_ID

    def _grid_for_level(self, garden_level: int) -> tuple[int, int]:
        level_data = ASSETS["backgrounds"].get(garden_level)
        if level_data is None:
            level_data = ASSETS["backgrounds"][0]
        return tuple(level_data["grid_size"])

    def _slot_capacity(self, garden_level: int) -> int:
        cols, rows = self._grid_for_level(garden_level)
        return cols * rows

    def _growth_seconds_for_seed(self, seed_type: str) -> int:
        seed_data = SEEDS.get(seed_type, {})
        try:
            value = int(seed_data.get("growth_seconds", CONFIG["BASE_GROWTH_SECONDS"]))
        except (TypeError, ValueError):
            value = int(CONFIG["BASE_GROWTH_SECONDS"])
        return max(60, value)

    def _select_spring_monster(self, player_level: int) -> Optional[dict[str, Any]]:
        if not SPRING_PVE_MONSTERS:
            return None
        candidates = [
            m for m in SPRING_PVE_MONSTERS if int(m.get("min_player_level", 1)) <= player_level
        ]
        if not candidates:
            candidates = list(SPRING_PVE_MONSTERS)
        weights = [max(1, int(m.get("weight", 1))) for m in candidates]
        return random.choices(candidates, weights=weights, k=1)[0]

    def _spring_monster_to_battle_payload(self, monster: dict[str, Any]) -> dict[str, Any]:
        stats = monster.get("stats", {})
        return {
            "name": str(monster.get("name", "Dreambound Monster")),
            "hp": int(stats.get("hp", 100)),
            "attack": int(stats.get("atk", 20)),
            "defense": int(stats.get("def", 10)),
            "element": str(monster.get("element", "Corrupted")),
            "url": str(monster.get("image_url", "")),
            "ispublic": True,
        }

    @staticmethod
    def _spring_monster_text(monster: dict[str, Any], key: str, fallback: str) -> str:
        value = str(monster.get(key, "")).strip()
        return value or fallback

    def _resolve_spring_seed_reward(self, monster: dict[str, Any]) -> tuple[str, bool]:
        reward = monster.get("reward", {})
        seed_type = str(reward.get("seed_type", "")).strip()
        if seed_type in SEEDS:
            return seed_type, False

        difficulty = str(monster.get("difficulty", "easy")).strip().lower()
        fallback_seed = str(
            SPRING_PVE_REWARD_DEFAULTS.get(difficulty, {}).get("seed_type", "")
        ).strip()
        if fallback_seed in SEEDS:
            return fallback_seed, True

        return random.choice(list(SEEDS.keys())), True

    @staticmethod
    def _parse_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _resolve_spring_bloomshards_reward(self, monster: dict[str, Any]) -> tuple[int, bool]:
        reward = monster.get("reward", {})
        parsed = self._parse_int(reward.get("bloomshards"))
        if parsed is not None:
            return max(0, parsed), False

        difficulty = str(monster.get("difficulty", "easy")).strip().lower()
        fallback_val = self._parse_int(
            SPRING_PVE_REWARD_DEFAULTS.get(difficulty, {}).get("bloomshards")
        )
        return max(0, fallback_val or 0), True

    def _resolve_spring_crate_reward(
        self,
        monster: dict[str, Any],
        seed_type: str,
    ) -> tuple[str, int, bool]:
        reward = monster.get("reward", {})
        crate_id = str(reward.get("crate_id", "")).strip()
        parsed_amount = self._parse_int(reward.get("crate_amount"))
        if crate_id and parsed_amount is not None and parsed_amount > 0:
            return crate_id, parsed_amount, False

        fallback_amount = max(1, int(parsed_amount or 1))
        seed_data = SEEDS.get(seed_type, {})
        flower_type = str(seed_data.get("flower_type", "")).strip()
        fallback_crate_id = str(
            ASSETS["flowers"].get(flower_type, {}).get("crate_id", "")
        ).strip()
        if fallback_crate_id:
            return fallback_crate_id, fallback_amount, True

        if crate_id:
            return crate_id, fallback_amount, True
        return "", 0, True

    def _resolve_divine_shard_chance_pct(self, monster: dict[str, Any]) -> int:
        reward = monster.get("reward", {})
        parsed = self._parse_int(reward.get("divine_shard_chance_pct"))
        if parsed is not None:
            return max(0, min(100, int(parsed)))

        difficulty = str(monster.get("difficulty", "easy")).strip().lower()
        fallback = self._parse_int(SPRING_PVE_DIVINE_SHARD_CHANCE_PCT.get(difficulty))
        return max(0, min(100, int(fallback or 5)))

    @staticmethod
    def _format_divine_shard_chance_text(pity_state: dict[str, Any]) -> str:
        if pity_state["pity_active"]:
            return (
                f"{pity_state['effective_chance_pct']}% chance, pity x"
                f"{pity_state['pity_multiplier']} active after "
                f"{pity_state['active_threshold']} misses"
            )
        return f"{pity_state['base_chance_pct']}% chance"

    @staticmethod
    def _format_divine_shard_pity_text(
        pity_state: dict[str, Any], pity_no_shard_wins: int
    ) -> str:
        summary = f"Pity: **{pity_no_shard_wins} misses**"
        if pity_state["next_threshold"] is not None:
            summary += (
                f"\nNext tier: **x{pity_state['next_multiplier']}** at "
                f"**{pity_state['next_threshold']}** misses"
            )
        else:
            summary += (
                f"\nCurrent tier: **x{pity_state['pity_multiplier']}** "
                f"(max pity tier active)"
            )
        return summary

    @staticmethod
    def _crate_display_name(profile_column: str) -> str:
        tier = profile_column.replace("crates_", "").replace("_", " ").title()
        return f"{tier} Crate"

    @staticmethod
    def _crate_id_display_name(crate_id: str) -> str:
        raw = str(crate_id or "").strip()
        if not raw:
            return "Seasonal Crate"
        cleaned = raw.replace("CRATE::", "")
        for suffix in ("_CRATE_PLACEHOLDER", "_CRATE"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break
        label = cleaned.replace("_", " ").strip().title()
        return f"{label} Crate" if label else "Seasonal Crate"

    def _resolve_seed_type(self, seed_input: str) -> Optional[str]:
        if not seed_input:
            return None
        raw = seed_input.strip().lower().replace("-", " ")
        compact = raw.replace(" ", "")
        return (
            self._seed_aliases.get(raw)
            or self._seed_aliases.get(raw.replace(" ", "_"))
            or self._seed_aliases.get(compact)
        )

    def _resolve_seed_group(self, seed_input: str) -> Optional[str]:
        if not seed_input:
            return None
        raw = seed_input.strip().lower().replace("-", " ")
        compact = raw.replace(" ", "")
        return (
            self._seed_group_aliases.get(raw)
            or self._seed_group_aliases.get(raw.replace(" ", "_"))
            or self._seed_group_aliases.get(compact)
        )

    @staticmethod
    def _seed_input_help() -> str:
        return "Use names like `good`, `nightmare`, `war` or groups: `normal`, `special`, `horsemen`."

    def _resolve_item_id(self, item_input: str) -> Optional[str]:
        if not item_input:
            return None
        return self._item_aliases.get(item_input.strip().lower())

    def _item_data_from_id(self, item_id: str) -> Optional[dict[str, Any]]:
        for data in SHOP_ITEMS.values():
            if data["item_id"] == item_id:
                return data
        return None

    @staticmethod
    def _format_duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        if seconds == 0:
            return "0m"
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes or not parts:
            parts.append(f"{minutes}m")
        return " ".join(parts)

    @staticmethod
    def _chunk_lines(lines: list[str], max_chars: int = 980) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        current_len = 0
        for line in lines:
            extra = len(line) + (1 if current else 0)
            if current and current_len + extra > max_chars:
                chunks.append("\n".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += extra
        if current:
            chunks.append("\n".join(current))
        return chunks

    @staticmethod
    def _is_renderable_image_url(value: str) -> bool:
        url = str(value or "").strip()
        if not url:
            return False
        if url.startswith("REPLACE_ME_"):
            return False
        return url.startswith("http://") or url.startswith("https://")

    async def _get_state(self, conn) -> Optional[dict[str, Any]]:
        row = await conn.fetchrow(
            """
            SELECT season_id, enabled, phase, starts_at, ends_at
            FROM dreambound_spring_state
            WHERE season_id = $1;
            """,
            self.season_id,
        )
        if not row:
            await conn.execute(
                """
                INSERT INTO dreambound_spring_state (season_id, enabled, phase, starts_at)
                VALUES ($1, TRUE, $2, NOW())
                ON CONFLICT (season_id) DO NOTHING;
                """,
                self.season_id,
                DEFAULT_PHASE,
            )
            row = await conn.fetchrow(
                """
                SELECT season_id, enabled, phase, starts_at, ends_at
                FROM dreambound_spring_state
                WHERE season_id = $1;
                """,
                self.season_id,
            )
        return dict(row) if row else None

    async def _ensure_event_phase_two(self, ctx, conn) -> Optional[dict[str, Any]]:
        state = await self._get_state(conn)
        if not state:
            await ctx.send(_("Dreambound Spring event state is not initialized yet."))
            return None
        if not state["enabled"]:
            await ctx.send(
                _("{event} is currently disabled.").format(event=EVENT_NAME)
            )
            return None
        return state

    async def _ensure_user_rows(self, conn, user_id: int, season_id: str):
        await conn.execute(
            """
            INSERT INTO dreambound_spring_wallet (user_id, season_id, bloomshards)
            VALUES ($1, $2, 0)
            ON CONFLICT (user_id, season_id) DO NOTHING;
            """,
            user_id,
            season_id,
        )
        await conn.execute(
            """
            INSERT INTO dreambound_spring_gardens (user_id, season_id, garden_level)
            VALUES ($1, $2, 0)
            ON CONFLICT (user_id, season_id) DO NOTHING;
            """,
            user_id,
            season_id,
        )
        await conn.execute(
            """
            INSERT INTO dreambound_spring_divine_pity (user_id, season_id, no_shard_wins)
            VALUES ($1, $2, 0)
            ON CONFLICT (user_id, season_id) DO NOTHING;
            """,
            user_id,
            season_id,
        )

    async def _get_divine_pity_no_shard_wins(
        self, conn, user_id: int, season_id: str
    ) -> int:
        value = await conn.fetchval(
            """
            SELECT no_shard_wins
            FROM dreambound_spring_divine_pity
            WHERE user_id = $1 AND season_id = $2;
            """,
            user_id,
            season_id,
        )
        return int(value or 0)

    async def _set_divine_pity_no_shard_wins(
        self, conn, user_id: int, season_id: str, value: int
    ) -> None:
        normalized = max(0, int(value))
        await conn.execute(
            """
            INSERT INTO dreambound_spring_divine_pity (user_id, season_id, no_shard_wins)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, season_id)
            DO UPDATE
            SET no_shard_wins = EXCLUDED.no_shard_wins,
                updated_at = NOW();
            """,
            user_id,
            season_id,
            normalized,
        )

    async def _get_garden_level(self, conn, user_id: int, season_id: str) -> int:
        level = await conn.fetchval(
            """
            SELECT garden_level
            FROM dreambound_spring_gardens
            WHERE user_id = $1 AND season_id = $2;
            """,
            user_id,
            season_id,
        )
        if level is None:
            await conn.execute(
                """
                INSERT INTO dreambound_spring_gardens (user_id, season_id, garden_level)
                VALUES ($1, $2, 0)
                ON CONFLICT (user_id, season_id) DO NOTHING;
                """,
                user_id,
                season_id,
            )
            return 0
        return int(level)

    async def _get_wallet(self, conn, user_id: int, season_id: str) -> int:
        balance = await conn.fetchval(
            """
            SELECT bloomshards
            FROM dreambound_spring_wallet
            WHERE user_id = $1 AND season_id = $2;
            """,
            user_id,
            season_id,
        )
        if balance is None:
            await conn.execute(
                """
                INSERT INTO dreambound_spring_wallet (user_id, season_id, bloomshards)
                VALUES ($1, $2, 0)
                ON CONFLICT (user_id, season_id) DO NOTHING;
                """,
                user_id,
                season_id,
            )
            return 0
        return int(balance)

    async def _spend_wallet(self, conn, user_id: int, season_id: str, amount: int) -> bool:
        if amount < 0:
            return False
        result = await conn.execute(
            """
            UPDATE dreambound_spring_wallet
            SET bloomshards = bloomshards - $1
            WHERE user_id = $2 AND season_id = $3 AND bloomshards >= $1;
            """,
            amount,
            user_id,
            season_id,
        )
        return result.endswith("UPDATE 1")

    async def _add_wallet(self, conn, user_id: int, season_id: str, amount: int):
        if amount <= 0:
            return
        await conn.execute(
            """
            INSERT INTO dreambound_spring_wallet (user_id, season_id, bloomshards)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, season_id)
            DO UPDATE SET bloomshards = dreambound_spring_wallet.bloomshards + EXCLUDED.bloomshards;
            """,
            user_id,
            season_id,
            amount,
        )

    async def _consume_seed(self, conn, user_id: int, season_id: str, seed_type: str, amount: int) -> bool:
        if amount <= 0:
            return False
        result = await conn.execute(
            """
            UPDATE dreambound_spring_seeds
            SET qty = qty - $1
            WHERE user_id = $2 AND season_id = $3 AND seed_type = $4 AND qty >= $1;
            """,
            amount,
            user_id,
            season_id,
            seed_type,
        )
        if result.endswith("UPDATE 1"):
            await conn.execute(
                """
                DELETE FROM dreambound_spring_seeds
                WHERE user_id = $1 AND season_id = $2 AND seed_type = $3 AND qty <= 0;
                """,
                user_id,
                season_id,
                seed_type,
            )
            return True
        return False

    async def _add_seed(self, conn, user_id: int, season_id: str, seed_type: str, amount: int):
        if amount <= 0:
            return
        await conn.execute(
            """
            INSERT INTO dreambound_spring_seeds (user_id, season_id, seed_type, qty)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, season_id, seed_type)
            DO UPDATE SET qty = dreambound_spring_seeds.qty + EXCLUDED.qty;
            """,
            user_id,
            season_id,
            seed_type,
            amount,
        )

    async def _consume_item(self, conn, user_id: int, season_id: str, item_id: str, amount: int) -> bool:
        if amount <= 0:
            return False
        result = await conn.execute(
            """
            UPDATE dreambound_spring_inventory
            SET qty = qty - $1
            WHERE user_id = $2 AND season_id = $3 AND item_id = $4 AND qty >= $1;
            """,
            amount,
            user_id,
            season_id,
            item_id,
        )
        if result.endswith("UPDATE 1"):
            await conn.execute(
                """
                DELETE FROM dreambound_spring_inventory
                WHERE user_id = $1 AND season_id = $2 AND item_id = $3 AND qty <= 0;
                """,
                user_id,
                season_id,
                item_id,
            )
            return True
        return False

    async def _add_item(self, conn, user_id: int, season_id: str, item_id: str, amount: int):
        if amount <= 0:
            return
        await conn.execute(
            """
            INSERT INTO dreambound_spring_inventory (user_id, season_id, item_id, qty)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id, season_id, item_id)
            DO UPDATE SET qty = dreambound_spring_inventory.qty + EXCLUDED.qty;
            """,
            user_id,
            season_id,
            item_id,
            amount,
        )

    async def _fetch_seed_inventory(self, conn, user_id: int, season_id: str) -> dict[str, int]:
        rows = await conn.fetch(
            """
            SELECT seed_type, qty
            FROM dreambound_spring_seeds
            WHERE user_id = $1 AND season_id = $2;
            """,
            user_id,
            season_id,
        )
        data = {seed: 0 for seed in SEEDS.keys()}
        for row in rows:
            data[str(row["seed_type"])] = int(row["qty"])
        return data

    async def _fetch_item_inventory(self, conn, user_id: int, season_id: str) -> dict[str, int]:
        rows = await conn.fetch(
            """
            SELECT item_id, qty
            FROM dreambound_spring_inventory
            WHERE user_id = $1 AND season_id = $2;
            """,
            user_id,
            season_id,
        )
        data: dict[str, int] = defaultdict(int)
        for row in rows:
            data[str(row["item_id"])] = int(row["qty"])
        return dict(data)

    async def _fetch_slot(self, conn, user_id: int, season_id: str, slot_index: int):
        return await conn.fetchrow(
            """
            SELECT slot_index, seed_type, planted_at, boost_seconds
            FROM dreambound_spring_slots
            WHERE user_id = $1 AND season_id = $2 AND slot_index = $3;
            """,
            user_id,
            season_id,
            slot_index,
        )

    async def _fetch_slots(self, conn, user_id: int, season_id: str, max_slots: int):
        return await conn.fetch(
            """
            SELECT slot_index, seed_type, planted_at, boost_seconds
            FROM dreambound_spring_slots
            WHERE user_id = $1 AND season_id = $2 AND slot_index <= $3
            ORDER BY slot_index ASC;
            """,
            user_id,
            season_id,
            max_slots,
        )

    @staticmethod
    def _milestone_row_key(row_index: int) -> str:
        return f"row_{int(row_index)}"

    @staticmethod
    def _milestone_column_key(column_index: int) -> str:
        return f"column_{int(column_index)}"

    @staticmethod
    def _milestone_full_key(cols: int, rows: int) -> str:
        return f"full_{int(cols)}x{int(rows)}"

    @staticmethod
    def _normalize_milestone_reward(raw_reward: Any) -> dict[str, Any]:
        reward = raw_reward if isinstance(raw_reward, dict) else {}
        bloomshards = 0
        crates: dict[str, int] = {}
        try:
            bloomshards = max(0, int(reward.get("bloomshards", 0)))
        except (TypeError, ValueError):
            bloomshards = 0

        raw_crates = reward.get("crates", {})
        if isinstance(raw_crates, dict):
            for column, amount in raw_crates.items():
                crate_column = str(column).strip()
                if crate_column not in ALLOWED_PROFILE_CRATE_COLUMNS:
                    continue
                try:
                    parsed_amount = max(0, int(amount))
                except (TypeError, ValueError):
                    parsed_amount = 0
                if parsed_amount > 0:
                    crates[crate_column] = parsed_amount
        return {"bloomshards": bloomshards, "crates": crates}

    @staticmethod
    def _has_milestone_reward(reward: dict[str, Any]) -> bool:
        if int(reward.get("bloomshards", 0)) > 0:
            return True
        crates = reward.get("crates", {})
        return isinstance(crates, dict) and any(int(v) > 0 for v in crates.values())

    def _milestone_reward_for(self, kind: str, cols: int, rows: int) -> dict[str, Any]:
        rewards_cfg = GARDEN_MILESTONE_REWARDS if isinstance(GARDEN_MILESTONE_REWARDS, dict) else {}
        if kind == "row":
            return self._normalize_milestone_reward(rewards_cfg.get("row", {}))
        if kind == "column":
            return self._normalize_milestone_reward(rewards_cfg.get("column", {}))
        if kind == "full":
            full_cfg = rewards_cfg.get("full_by_grid", {})
            if isinstance(full_cfg, dict):
                grid_key = f"{int(cols)}x{int(rows)}"
                return self._normalize_milestone_reward(full_cfg.get(grid_key, {}))
            return {"bloomshards": 0, "crates": {}}
        return {"bloomshards": 0, "crates": {}}

    async def _grant_profile_crate_column(
        self,
        conn,
        user_id: int,
        crate_column: str,
        amount: int = 1,
    ) -> int:
        if crate_column not in ALLOWED_PROFILE_CRATE_COLUMNS:
            raise ValueError(f"Unsupported crate column: {crate_column}")
        grant_amount = max(1, int(amount or 1))
        await conn.execute(
            f'UPDATE profile SET "{crate_column}" = COALESCE("{crate_column}", 0) + $1 WHERE "user" = $2;',
            grant_amount,
            user_id,
        )
        return int(
            await conn.fetchval(
                f'SELECT COALESCE("{crate_column}", 0) FROM profile WHERE "user" = $1;',
                user_id,
            )
            or 0
        )

    def _compute_garden_milestones(
        self,
        slot_rows,
        garden_level: int,
    ) -> list[tuple[str, str, dict[str, Any]]]:
        cols, rows = self._grid_for_level(garden_level)
        max_slots = cols * rows
        occupied: set[int] = set()
        for row in slot_rows:
            slot_index = int(row["slot_index"])
            if 1 <= slot_index <= max_slots and row["seed_type"]:
                occupied.add(slot_index)

        milestones: list[tuple[str, str, dict[str, Any]]] = []
        row_reward = self._milestone_reward_for("row", cols, rows)
        for row_index in range(1, rows + 1):
            start_slot = ((row_index - 1) * cols) + 1
            if all((start_slot + offset) in occupied for offset in range(cols)):
                if not self._has_milestone_reward(row_reward):
                    continue
                milestones.append(
                    (
                        self._milestone_row_key(row_index),
                        f"Row {row_index}",
                        row_reward,
                    )
                )

        column_reward = self._milestone_reward_for("column", cols, rows)
        for column_index in range(1, cols + 1):
            if all((((row_index - 1) * cols) + column_index) in occupied for row_index in range(1, rows + 1)):
                if not self._has_milestone_reward(column_reward):
                    continue
                milestones.append(
                    (
                        self._milestone_column_key(column_index),
                        f"Column {column_index}",
                        column_reward,
                    )
                )

        seen_full_keys: set[str] = set()
        for level in range(max(0, int(garden_level)) + 1):
            level_cols, level_rows = self._grid_for_level(level)
            level_max_slots = level_cols * level_rows
            if not all(slot_index in occupied for slot_index in range(1, level_max_slots + 1)):
                continue
            full_reward = self._milestone_reward_for("full", level_cols, level_rows)
            if not self._has_milestone_reward(full_reward):
                continue
            full_key = self._milestone_full_key(level_cols, level_rows)
            if full_key in seen_full_keys:
                continue
            seen_full_keys.add(full_key)
            milestones.append(
                (
                    full_key,
                    f"Full Garden ({level_cols}x{level_rows})",
                    full_reward,
                )
            )

        return milestones

    async def _claim_garden_milestone_rewards(
        self,
        conn,
        user_id: int,
        season_id: str,
        slot_rows,
        garden_level: int,
    ) -> list[dict[str, Any]]:
        claimed: list[dict[str, Any]] = []
        milestones = self._compute_garden_milestones(slot_rows, garden_level)
        if not milestones:
            return claimed

        for milestone_key, label, reward in milestones:
            inserted = await conn.fetchval(
                """
                INSERT INTO dreambound_spring_milestones (user_id, season_id, milestone_key)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, season_id, milestone_key) DO NOTHING
                RETURNING 1;
                """,
                user_id,
                season_id,
                milestone_key,
            )
            if not inserted:
                continue

            bloomshards_reward = int(reward.get("bloomshards", 0) or 0)
            if bloomshards_reward > 0:
                await self._add_wallet(conn, user_id, season_id, bloomshards_reward)

            crate_rewards: list[dict[str, Any]] = []
            reward_crates = reward.get("crates", {})
            if isinstance(reward_crates, dict):
                for crate_column, amount in reward_crates.items():
                    crate_amount = max(0, int(amount or 0))
                    if crate_amount <= 0:
                        continue
                    crate_total = await self._grant_profile_crate_column(
                        conn,
                        user_id,
                        str(crate_column),
                        crate_amount,
                    )
                    crate_rewards.append(
                        {
                            "column": str(crate_column),
                            "amount": crate_amount,
                            "total": crate_total,
                        }
                    )

            claimed.append(
                {
                    "key": milestone_key,
                    "label": label,
                    "bloomshards": bloomshards_reward,
                    "crates": crate_rewards,
                }
            )

        return claimed

    async def _plant_slot(
        self,
        conn,
        user_id: int,
        season_id: str,
        slot_index: int,
        seed_type: str,
    ):
        await conn.execute(
            """
            INSERT INTO dreambound_spring_slots (user_id, season_id, slot_index, seed_type, planted_at, boost_seconds)
            VALUES ($1, $2, $3, $4, NOW(), 0)
            ON CONFLICT (user_id, season_id, slot_index)
            DO UPDATE SET seed_type = EXCLUDED.seed_type, planted_at = EXCLUDED.planted_at, boost_seconds = 0;
            """,
            user_id,
            season_id,
            slot_index,
            seed_type,
        )

    async def _clear_slot(self, conn, user_id: int, season_id: str, slot_index: int):
        await conn.execute(
            """
            UPDATE dreambound_spring_slots
            SET seed_type = NULL, planted_at = NULL, boost_seconds = 0
            WHERE user_id = $1 AND season_id = $2 AND slot_index = $3;
            """,
            user_id,
            season_id,
            slot_index,
        )

    def _slot_runtime(self, slot_row: Optional[dict[str, Any]], now: dt.datetime) -> dict[str, Any]:
        if not slot_row or not slot_row.get("seed_type"):
            return {
                "state": "empty",
                "seed_type": None,
                "flower_type": None,
                "seconds_to_next": None,
                "seconds_to_bloom": None,
                "next_stage": None,
            }

        seed_type = str(slot_row["seed_type"])
        seed_data = SEEDS.get(seed_type)
        if not seed_data:
            return {
                "state": "empty",
                "seed_type": None,
                "flower_type": None,
                "seconds_to_next": None,
                "seconds_to_bloom": None,
                "next_stage": None,
            }

        planted_at = slot_row.get("planted_at") or now
        if planted_at.tzinfo is None:
            planted_at = planted_at.replace(tzinfo=dt.timezone.utc)

        boost_seconds = int(slot_row.get("boost_seconds") or 0)
        elapsed = int((now - planted_at).total_seconds()) + boost_seconds
        elapsed = max(0, elapsed)

        total = self._growth_seconds_for_seed(seed_type)
        if elapsed >= total:
            stage = "bloomed"
            next_stage = None
            seconds_to_next = 0
        else:
            stage = "planted"
            next_stage = "bloomed"
            seconds_to_next = max(0, total - elapsed)

        seconds_to_bloom = max(0, total - elapsed)
        flower_type = str(seed_data["flower_type"])
        flower_data = ASSETS["flowers"].get(flower_type, {})
        stage_sprite = flower_data.get("stages", {}).get(stage, "")

        return {
            "state": stage,
            "seed_type": seed_type,
            "flower_type": flower_type,
            "seconds_to_next": seconds_to_next,
            "seconds_to_bloom": seconds_to_bloom,
            "next_stage": next_stage,
            "crate_id": flower_data.get("crate_id"),
            "stage_sprite": stage_sprite,
        }

    def _compose_slot_views(self, slot_rows, max_slots: int, now: dt.datetime) -> list[dict[str, Any]]:
        by_index: dict[int, dict[str, Any]] = {}
        for row in slot_rows:
            by_index[int(row["slot_index"])] = dict(row)

        slot_views: list[dict[str, Any]] = []
        for slot_index in range(1, max_slots + 1):
            runtime = self._slot_runtime(by_index.get(slot_index), now)
            runtime["slot_index"] = slot_index
            slot_views.append(runtime)
        return slot_views

    def _build_grid_text(self, slot_views: list[dict[str, Any]], garden_level: int) -> str:
        cols, rows = self._grid_for_level(garden_level)
        symbols = {
            "empty": "·",
            "planted": "s",
            "bloomed": "f",
        }
        lines = []
        for r in range(rows):
            parts = []
            for c in range(cols):
                idx = r * cols + c + 1
                state = slot_views[idx - 1]["state"]
                parts.append(f"[{idx:02}{symbols.get(state, '?')}]")
            lines.append(" ".join(parts))
        return "\n".join(lines)

    def _build_slot_lines(self, slot_views: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for slot in slot_views:
            idx = slot["slot_index"]
            state = slot["state"]
            if state == "empty":
                lines.append(f"#{idx}: empty")
                continue
            seed_display = SEEDS[slot["seed_type"]]["display_name"]
            if state == "bloomed":
                lines.append(f"#{idx}: BLOOMED ({seed_display}) - ready to harvest")
                continue
            next_stage = str(slot["next_stage"]).upper()
            eta = self._format_duration(int(slot["seconds_to_next"] or 0))
            lines.append(
                f"#{idx}: {state.upper()} ({seed_display}) -> {next_stage} in {eta}"
            )
        return lines

    def _build_render_payload(self, garden_level: int, slot_views: list[dict[str, Any]]) -> dict[str, Any]:
        bg_data = ASSETS["backgrounds"][garden_level]
        cols, rows = bg_data["grid_size"]
        canvas_w, canvas_h = CONFIG.get("GARDEN_CANVAS_SIZE", (1563, 938))
        target_w, target_h = CONFIG.get("TARGET_FLOWER_SIZE", (300, 300))
        padding_ratio = float(CONFIG.get("CELL_PADDING_RATIO", 0.06))
        padding_ratio = max(0.0, min(0.45, padding_ratio))
        cell_w = canvas_w / cols
        cell_h = canvas_h / rows
        overlays: list[dict[str, Any]] = []

        for slot in slot_views:
            if slot["state"] == "empty":
                continue
            flower_type = slot["flower_type"]
            stage = slot["state"]
            sprite = ASSETS["flowers"][flower_type]["stages"][stage]
            index_zero = slot["slot_index"] - 1
            col = index_zero % cols
            row = index_zero // cols

            slot_x = col * cell_w
            slot_y = row * cell_h
            max_sprite_w = max(1.0, cell_w * (1 - (padding_ratio * 2)))
            max_sprite_h = max(1.0, cell_h * (1 - (padding_ratio * 2)))
            scale = min(max_sprite_w / target_w, max_sprite_h / target_h, 1.0)
            sprite_w = max(1, int(round(target_w * scale)))
            sprite_h = max(1, int(round(target_h * scale)))
            sprite_x = int(round(slot_x + ((cell_w - sprite_w) / 2)))
            sprite_y = int(round(slot_y + ((cell_h - sprite_h) / 2)))

            overlays.append(
                {
                    "slot_index": slot["slot_index"],
                    "row": row,
                    "col": col,
                    "flower_type": flower_type,
                    "stage": stage,
                    "sprite": sprite,
                    "slot_rect": {
                        "x": int(round(slot_x)),
                        "y": int(round(slot_y)),
                        "w": int(round(cell_w)),
                        "h": int(round(cell_h)),
                    },
                    "sprite_rect": {
                        "x": sprite_x,
                        "y": sprite_y,
                        "w": sprite_w,
                        "h": sprite_h,
                    },
                }
            )

        return {
            "background": bg_data["image"],
            "background_name": bg_data["name"],
            "canvas_size": (canvas_w, canvas_h),
            "target_flower_size": (target_w, target_h),
            "grid_size": (cols, rows),
            "cell_size": (round(cell_w, 2), round(cell_h, 2)),
            "overlays": overlays,
        }

    async def _download_image_bytes(self, url: str) -> Optional[bytes]:
        if not self._is_renderable_image_url(url):
            return None

        session = getattr(self.bot, "trusted_session", None) or getattr(self.bot, "session", None)
        if session is None:
            return None

        try:
            timeout = aiohttp.ClientTimeout(total=15)
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
            async with session.get(url, timeout=timeout, headers=headers, allow_redirects=True) as response:
                if response.status != 200:
                    return None
                data = await response.read()
                if not data:
                    return None
                return data
        except Exception:
            return None

    async def _render_garden_file(
        self,
        render_payload: dict[str, Any],
    ) -> Optional[discord.File]:
        background_url = str(render_payload.get("background", ""))
        overlays = list(render_payload.get("overlays") or [])

        urls: set[str] = set()
        if self._is_renderable_image_url(background_url):
            urls.add(background_url)
        for overlay in overlays:
            sprite_url = str(overlay.get("sprite", "")).strip()
            if self._is_renderable_image_url(sprite_url):
                urls.add(sprite_url)
        if not urls:
            return None

        resample_filter = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
        decoded: dict[str, Image.Image] = {}

        async def _fetch_and_decode(url: str):
            raw = await self._download_image_bytes(url)
            if not raw:
                return
            try:
                with Image.open(io.BytesIO(raw)) as img:
                    decoded[url] = img.convert("RGBA")
            except Exception:
                return

        await asyncio.gather(*(_fetch_and_decode(url) for url in urls))

        base = decoded.get(background_url)
        if base is None:
            return None

        canvas_size = render_payload.get("canvas_size", base.size)
        canvas_w = int(canvas_size[0]) if isinstance(canvas_size, (tuple, list)) and len(canvas_size) == 2 else base.width
        canvas_h = int(canvas_size[1]) if isinstance(canvas_size, (tuple, list)) and len(canvas_size) == 2 else base.height
        canvas_w = max(1, canvas_w)
        canvas_h = max(1, canvas_h)
        if base.size != (canvas_w, canvas_h):
            base = base.resize((canvas_w, canvas_h), resample_filter)

        for overlay in overlays:
            sprite_url = str(overlay.get("sprite", "")).strip()
            sprite = decoded.get(sprite_url)
            if sprite is None:
                continue

            rect = dict(overlay.get("sprite_rect") or {})
            width = max(1, int(rect.get("w", sprite.width)))
            height = max(1, int(rect.get("h", sprite.height)))
            x = int(rect.get("x", 0))
            y = int(rect.get("y", 0))

            layer = sprite.resize((width, height), resample_filter)
            base.alpha_composite(layer, dest=(x, y))

        out = io.BytesIO()
        base.save(out, format="PNG")
        out.seek(0)
        return discord.File(fp=out, filename="dreambound_garden.png")

    async def _grant_crate(
        self,
        conn,
        ctx,
        user_id: int,
        season_id: str,
        crate_id: str,
        amount: int = 1,
        subject: str = "dreambound spring harvest",
    ) -> str:
        crate_amount = max(1, int(amount or 1))
        profile_column = CRATE_PROFILE_COLUMNS.get(crate_id)
        if profile_column in ALLOWED_PROFILE_CRATE_COLUMNS:
            await conn.execute(
                f'UPDATE profile SET "{profile_column}"="{profile_column}"+$1 WHERE "user"=$2;',
                crate_amount,
                user_id,
            )
            try:
                await self.bot.log_transaction(
                    ctx,
                    from_=1,
                    to=user_id,
                    subject=subject,
                    data={
                        "CrateID": crate_id,
                        "ProfileColumn": profile_column,
                        "Amount": crate_amount,
                    },
                    conn=conn,
                )
            except Exception:
                pass
            return profile_column

        # Fallback if crate id is not mapped to profile columns yet.
        fallback_item = f"CRATE::{crate_id}"
        await self._add_item(conn, user_id, season_id, fallback_item, crate_amount)
        return fallback_item

    async def _force_bloom_all_slots(
        self,
        conn,
        user_id: int,
        season_id: str,
    ) -> tuple[int, int, int, int]:
        rows = await conn.fetch(
            """
            SELECT slot_index, seed_type, planted_at, boost_seconds
            FROM dreambound_spring_slots
            WHERE user_id = $1 AND season_id = $2 AND seed_type IS NOT NULL
            ORDER BY slot_index ASC;
            """,
            user_id,
            season_id,
        )
        if not rows:
            return 0, 0, 0, 0

        now = utcnow()
        updates: list[tuple[int, int, str, int]] = []
        already_bloomed = 0
        skipped = 0

        for row in rows:
            slot = dict(row)
            slot_state = self._slot_runtime(slot, now)
            if slot_state["state"] == "bloomed":
                already_bloomed += 1
                continue
            if slot_state["state"] != "planted":
                skipped += 1
                continue

            needed = int(slot_state.get("seconds_to_bloom") or 0)
            if needed <= 0:
                already_bloomed += 1
                continue
            updates.append((needed, user_id, season_id, int(slot["slot_index"])))

        if updates:
            await conn.executemany(
                """
                UPDATE dreambound_spring_slots
                SET boost_seconds = COALESCE(boost_seconds, 0) + $1
                WHERE user_id = $2 AND season_id = $3 AND slot_index = $4;
                """,
                updates,
            )

        total = len(rows)
        forced = len(updates)
        return total, forced, already_bloomed, skipped

    async def _try_upgrade(
        self,
        conn,
        user_id: int,
        season_id: str,
        current_level: int,
    ) -> tuple[bool, str, int, Optional[int]]:
        max_level = int(CONFIG["MAX_GARDEN_LEVEL"])
        if current_level >= max_level:
            return False, _("Your garden is already at max level."), current_level, None

        upgrade_costs = CONFIG["UPGRADE_COSTS"]
        if current_level not in upgrade_costs:
            return False, _("No upgrade path exists for your current level."), current_level, None

        cost = int(upgrade_costs[current_level])
        ok = await self._spend_wallet(conn, user_id, season_id, cost)
        if not ok:
            return (
                False,
                _("You need **{cost} {currency}** to upgrade.").format(
                    cost=cost,
                    currency=EVENT_CURRENCY,
                ),
                current_level,
                cost,
            )

        next_level = current_level + 1
        await conn.execute(
            """
            UPDATE dreambound_spring_gardens
            SET garden_level = $1
            WHERE user_id = $2 AND season_id = $3;
            """,
            next_level,
            user_id,
            season_id,
        )
        return True, _("Garden upgraded."), next_level, cost

    async def add_bloomshards(
        self,
        user_id: int,
        amount: int,
        *,
        season_id: Optional[str] = None,
        conn=None,
    ) -> int:
        """Phase 1 hook: grant Bloomshards to a user."""
        if amount <= 0:
            return 0
        season = season_id or self.season_id
        owns_conn = conn is None
        if owns_conn:
            conn = await self.bot.pool.acquire()
        try:
            await self._ensure_user_rows(conn, user_id, season)
            await self._add_wallet(conn, user_id, season, amount)
            return await self._get_wallet(conn, user_id, season)
        finally:
            if owns_conn:
                await self.bot.pool.release(conn)

    async def add_seeds(
        self,
        user_id: int,
        seed_type: str,
        amount: int,
        *,
        season_id: Optional[str] = None,
        conn=None,
    ) -> int:
        """Phase 1 hook: grant seeds to a user."""
        if seed_type not in SEEDS or amount <= 0:
            return 0
        season = season_id or self.season_id
        owns_conn = conn is None
        if owns_conn:
            conn = await self.bot.pool.acquire()
        try:
            await self._add_seed(conn, user_id, season, seed_type, amount)
            qty = await conn.fetchval(
                """
                SELECT qty
                FROM dreambound_spring_seeds
                WHERE user_id = $1 AND season_id = $2 AND seed_type = $3;
                """,
                user_id,
                season,
                seed_type,
            )
            return int(qty or 0)
        finally:
            if owns_conn:
                await self.bot.pool.release(conn)

    async def grant_phase1_rewards(
        self,
        user_id: int,
        *,
        bloomshards: int = 0,
        seeds: Optional[dict[str, int]] = None,
        season_id: Optional[str] = None,
    ):
        """Phase 1 hook: atomically grant Bloomshards + seeds."""
        season = season_id or self.season_id
        async with self.bot.pool.acquire() as conn:
            await self._ensure_user_rows(conn, user_id, season)
            if bloomshards > 0:
                await self._add_wallet(conn, user_id, season, bloomshards)
            if seeds:
                for seed_type, amount in seeds.items():
                    if seed_type in SEEDS and amount > 0:
                        await self._add_seed(conn, user_id, season, seed_type, amount)

    async def _send_garden_view(self, ctx):
        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            garden_level = await self._get_garden_level(conn, ctx.author.id, season_id)
            max_slots = self._slot_capacity(garden_level)
            slots = await self._fetch_slots(conn, ctx.author.id, season_id, max_slots)
            seeds = await self._fetch_seed_inventory(conn, ctx.author.id, season_id)
            items = await self._fetch_item_inventory(conn, ctx.author.id, season_id)
            bloomshards = await self._get_wallet(conn, ctx.author.id, season_id)

        now = utcnow()
        slot_views = self._compose_slot_views(slots, max_slots, now)
        render_payload = self._build_render_payload(garden_level, slot_views)

        cols, rows = self._grid_for_level(garden_level)
        background = ASSETS["backgrounds"][garden_level]["image"]
        seed_text = "\n".join(
            f"{SEEDS[seed]['display_name']}: **{qty}**" for seed, qty in seeds.items()
        )
        if not seed_text:
            seed_text = "No seeds."

        item_lines = []
        for item_id, qty in sorted(items.items()):
            item_data = self._item_data_from_id(item_id)
            display = item_data["display_name"] if item_data else item_id
            item_lines.append(f"{display}: **{qty}**")
        items_text = "\n".join(item_lines) if item_lines else "No spring items."

        slot_lines = self._build_slot_lines(slot_views)
        slot_chunks = self._chunk_lines(slot_lines)

        embed = discord.Embed(
            title=f"{EVENT_NAME} - {GARDEN_NAME}",
            color=discord.Color.green(),
            description=(
                f"Season: `{state['season_id']}` | All features unlocked\n"
                f"Garden Level: **{garden_level}** / {CONFIG['MAX_GARDEN_LEVEL']}\n"
                f"Grid: **{cols}x{rows}** ({max_slots} slots)\n"
                f"Background: `{background}`"
            ),
        )
        render_file = await self._render_garden_file(render_payload)
        if render_file is not None:
            embed.set_image(url="attachment://dreambound_garden.png")
        elif self._is_renderable_image_url(background):
            embed.set_image(url=background)
        embed.add_field(
            name=EVENT_CURRENCY,
            value=f"**{bloomshards}**",
            inline=True,
        )
        embed.add_field(
            name="Slots",
            value=str(max_slots),
            inline=True,
        )
        embed.add_field(
            name="Grid View",
            value=f"```\n{self._build_grid_text(slot_views, garden_level)}\n```",
            inline=False,
        )
        embed.add_field(name="Seeds", value=seed_text, inline=True)
        embed.add_field(name="Items", value=items_text, inline=True)

        for idx, chunk in enumerate(slot_chunks, start=1):
            name = "Slot States" if idx == 1 else f"Slot States (cont. {idx})"
            embed.add_field(name=name, value=chunk, inline=False)

        embed.add_field(
            name="Render Payload",
            value=(
                f"Background: `{render_payload['background']}`\n"
                f"Canvas: `{render_payload['canvas_size'][0]}x{render_payload['canvas_size'][1]}`\n"
                f"Grid: `{render_payload['grid_size'][0]}x{render_payload['grid_size'][1]}`\n"
                f"Cell: `{render_payload['cell_size'][0]}x{render_payload['cell_size'][1]}`\n"
                f"Flower overlays: **{len(render_payload['overlays'])}**"
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"Try next: `{ctx.clean_prefix}garden plant <slot> <seed>` | "
                f"`{ctx.clean_prefix}garden harvest <slot>` | "
                f"`{ctx.clean_prefix}springshop`"
            )
        )
        if render_file is not None:
            await ctx.send(embed=embed, file=render_file)
        else:
            await ctx.send(embed=embed)

    async def _send_snapshot_view(self, ctx, selector: str):
        selector = selector.strip().lower()
        async with self.bot.pool.acquire() as conn:
            if selector in {"latest", "last"}:
                row = await conn.fetchrow(
                    """
                    SELECT id, season_id, snapshot_data, created_at
                    FROM dreambound_spring_snapshots
                    WHERE user_id = $1
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1;
                    """,
                    ctx.author.id,
                )
            else:
                try:
                    snapshot_id = int(selector)
                except ValueError:
                    return await ctx.send(
                        _("Use `$garden snapshot` to save, `$garden snapshot latest` or `$garden snapshot <id>` to view.")
                    )
                row = await conn.fetchrow(
                    """
                    SELECT id, season_id, snapshot_data, created_at
                    FROM dreambound_spring_snapshots
                    WHERE id = $1 AND user_id = $2;
                    """,
                    snapshot_id,
                    ctx.author.id,
                )

        if not row:
            return await ctx.send(_("No matching garden snapshot found."))

        snapshot_data = dict(row["snapshot_data"] or {})
        season_id = str(snapshot_data.get("season_id") or row["season_id"])
        garden_level = int(snapshot_data.get("garden_level", 0))
        background = str(snapshot_data.get("background", ""))
        wallet = int(snapshot_data.get("wallet", 0))

        grid = snapshot_data.get("grid_size") or list(self._grid_for_level(garden_level))
        if (
            not isinstance(grid, list)
            or len(grid) != 2
            or not isinstance(grid[0], int)
            or not isinstance(grid[1], int)
        ):
            cols, rows = self._grid_for_level(garden_level)
        else:
            cols, rows = int(grid[0]), int(grid[1])
        max_slots = cols * rows

        snapshot_slots = snapshot_data.get("slots") or []
        slot_views: list[dict[str, Any]] = []
        if isinstance(snapshot_slots, list):
            by_index: dict[int, dict[str, Any]] = {}
            for slot in snapshot_slots:
                if not isinstance(slot, dict):
                    continue
                idx = int(slot.get("slot_index", 0))
                if 1 <= idx <= max_slots:
                    by_index[idx] = {
                        "slot_index": idx,
                        "state": str(slot.get("state", "empty")),
                        "seed_type": slot.get("seed_type"),
                        "next_stage": slot.get("next_stage"),
                        "seconds_to_next": slot.get("seconds_to_next"),
                        "seconds_to_bloom": slot.get("seconds_to_bloom"),
                    }
            for idx in range(1, max_slots + 1):
                slot_views.append(
                    by_index.get(
                        idx,
                        {
                            "slot_index": idx,
                            "state": "empty",
                            "seed_type": None,
                            "next_stage": None,
                            "seconds_to_next": None,
                            "seconds_to_bloom": None,
                        },
                    )
                )
        else:
            for idx in range(1, max_slots + 1):
                slot_views.append(
                    {
                        "slot_index": idx,
                        "state": "empty",
                        "seed_type": None,
                        "next_stage": None,
                        "seconds_to_next": None,
                        "seconds_to_bloom": None,
                    }
                )

        seeds = snapshot_data.get("seeds", {})
        items = snapshot_data.get("items", {})
        seed_lines = []
        if isinstance(seeds, dict):
            for seed_key, qty in seeds.items():
                display = SEEDS.get(seed_key, {}).get("display_name", seed_key)
                seed_lines.append(f"{display}: **{int(qty)}**")
        seeds_text = "\n".join(seed_lines) if seed_lines else "No seed data in snapshot."

        item_lines = []
        if isinstance(items, dict):
            for item_id, qty in sorted(items.items()):
                item_data = self._item_data_from_id(str(item_id))
                display = item_data["display_name"] if item_data else str(item_id)
                item_lines.append(f"{display}: **{int(qty)}**")
        items_text = "\n".join(item_lines) if item_lines else "No item data in snapshot."

        slot_lines = self._build_slot_lines(slot_views)
        slot_chunks = self._chunk_lines(slot_lines)

        embed = discord.Embed(
            title=f"{EVENT_NAME} Snapshot #{row['id']}",
            color=discord.Color.teal(),
            description=(
                f"Season: `{season_id}`\n"
                f"Saved At: `{row['created_at'].isoformat()}`\n"
                f"Garden Level: **{garden_level}**\n"
                f"Grid: **{cols}x{rows}** ({max_slots} slots)\n"
                f"Background: `{background}`"
            ),
        )
        if self._is_renderable_image_url(background):
            embed.set_image(url=background)
        embed.add_field(name=EVENT_CURRENCY, value=f"**{wallet}**", inline=True)
        embed.add_field(name="Seeds", value=seeds_text, inline=True)
        embed.add_field(name="Items", value=items_text, inline=False)
        embed.add_field(
            name="Grid View",
            value=f"```\n{self._build_grid_text(slot_views, garden_level)}\n```",
            inline=False,
        )
        for idx, chunk in enumerate(slot_chunks, start=1):
            name = "Snapshot Slot States" if idx == 1 else f"Snapshot Slot States (cont. {idx})"
            embed.add_field(name=name, value=chunk, inline=False)
        await ctx.send(embed=embed)

    @has_char()
    @commands.group(
        name="garden",
        invoke_without_command=True,
        brief=_("Manage your Elysian Garden."),
    )
    @locale_doc
    async def garden(self, ctx):
        _(
            """Manage The Dreambound Spring garden phase.

            Use this command group to view and manage your Elysian Garden."""
        )
        await self._send_garden_view(ctx)

    @has_char()
    @garden.command(name="show", brief=_("Show your Elysian Garden."))
    @locale_doc
    async def garden_show(self, ctx):
        _("""Show your Elysian Garden.""")
        await self._send_garden_view(ctx)

    @has_char()
    @garden.command(name="plant", brief=_("Plant a seed in a slot."))
    @locale_doc
    async def garden_plant(self, ctx, slot: IntFromTo(1, 24), *, seed_input: str):
        _(
            """`<slot>` - slot number from 1 to 24
            `<seed_name|seed_group>` - seed key/name (or group: normal/special/horsemen)

            Plant a seed into a slot in your Elysian Garden."""
        )
        seed_type = self._resolve_seed_type(seed_input)
        requested_group = None if seed_type else self._resolve_seed_group(seed_input)
        if not seed_type and not requested_group:
            return await ctx.send(_("Unknown seed type. {hint}").format(hint=self._seed_input_help()))

        milestone_rewards: list[dict[str, Any]] = []
        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            garden_level = await self._get_garden_level(conn, ctx.author.id, season_id)
            max_slots = self._slot_capacity(garden_level)
            if slot > max_slots:
                return await ctx.send(
                    _("Slot {slot} is locked. Your current garden has {max_slots} slots.").format(
                        slot=slot,
                        max_slots=max_slots,
                    )
                )

            slot_row = await self._fetch_slot(conn, ctx.author.id, season_id, slot)
            slot_data = self._slot_runtime(dict(slot_row) if slot_row else None, utcnow())
            if slot_data["state"] != "empty":
                return await ctx.send(_("That slot is already occupied."))

            if not seed_type and requested_group:
                inventory = await self._fetch_seed_inventory(conn, ctx.author.id, season_id)
                for candidate in self._seed_groups.get(requested_group, []):
                    if int(inventory.get(candidate, 0)) > 0:
                        seed_type = candidate
                        break
                if not seed_type:
                    return await ctx.send(
                        _("You do not have any **{group}** seeds.").format(
                            group=requested_group.title()
                        )
                    )

            ok = await self._consume_seed(conn, ctx.author.id, season_id, seed_type, 1)
            if not ok:
                display = SEEDS[seed_type]["display_name"]
                return await ctx.send(
                    _("You do not have enough **{seed}**.").format(seed=display)
                )

            await self._plant_slot(conn, ctx.author.id, season_id, slot, seed_type)
            slots = await self._fetch_slots(conn, ctx.author.id, season_id, max_slots)
            milestone_rewards = await self._claim_garden_milestone_rewards(
                conn,
                ctx.author.id,
                season_id,
                slots,
                garden_level,
            )

        flower_type = SEEDS[seed_type]["flower_type"]
        growth_seconds = self._growth_seconds_for_seed(seed_type)
        response = _(
            "Planted **{seed}** in slot **#{slot}** of your {garden}.\n"
            "Flower type: `{flower}`. Expected full bloom in about **{eta}**."
        ).format(
            seed=SEEDS[seed_type]["display_name"],
            slot=slot,
            garden=GARDEN_NAME,
            flower=flower_type,
            eta=self._format_duration(growth_seconds),
        )
        if milestone_rewards:
            total_bloomshards = 0
            bonus_lines: list[str] = []
            for entry in milestone_rewards:
                line_parts = [f"- {entry['label']}:"]
                bloomshards_gain = int(entry.get("bloomshards", 0) or 0)
                if bloomshards_gain > 0:
                    total_bloomshards += bloomshards_gain
                    line_parts.append(f"+{bloomshards_gain} {EVENT_CURRENCY}")
                for crate_entry in entry.get("crates", []):
                    crate_column = str(crate_entry.get("column", "")).strip()
                    crate_amount = int(crate_entry.get("amount", 0) or 0)
                    if not crate_column or crate_amount <= 0:
                        continue
                    line_parts.append(
                        f"+{crate_amount} {self._crate_display_name(crate_column)}"
                    )
                bonus_lines.append(" ".join(line_parts))
            bonus_text = "\n".join(bonus_lines)
            response += (
                "\n\nGarden milestone reward(s) unlocked:\n"
                f"{bonus_text}"
            )
            if total_bloomshards > 0:
                response += f"\nTotal {EVENT_CURRENCY}: +{total_bloomshards}"
        await ctx.send(response)

    @has_char()
    @garden.command(
        name="plantall",
        aliases=["plant_all"],
        brief=_("Plant all empty slots (lowest seeds first)."),
    )
    @locale_doc
    async def garden_plant_all(self, ctx):
        _(
            """Plant seeds in all empty slots, prioritizing the lowest-tier seeds first.

            Seed order: normal -> special -> horsemen (then by growth time/display name)."""
        )
        milestone_rewards: list[dict[str, Any]] = []
        planted_by_seed: dict[str, int] = defaultdict(int)
        planted_slots: list[int] = []
        empty_before = 0

        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            garden_level = await self._get_garden_level(conn, ctx.author.id, season_id)
            max_slots = self._slot_capacity(garden_level)
            slot_rows = await self._fetch_slots(conn, ctx.author.id, season_id, max_slots)
            now = utcnow()
            by_slot = {int(row["slot_index"]): dict(row) for row in slot_rows}
            empty_slots = [
                slot_index
                for slot_index in range(1, max_slots + 1)
                if self._slot_runtime(by_slot.get(slot_index), now)["state"] == "empty"
            ]
            empty_before = len(empty_slots)
            if empty_before <= 0:
                return await ctx.send(_("Your unlocked garden slots are already occupied."))

            inventory = await self._fetch_seed_inventory(conn, ctx.author.id, season_id)
            ordered_seed_types = sorted(SEEDS.keys(), key=self._seed_plant_priority)
            remaining = {
                seed_type: max(0, int(inventory.get(seed_type, 0)))
                for seed_type in ordered_seed_types
            }

            if sum(remaining.values()) <= 0:
                return await ctx.send(_("You do not have any seeds to plant."))

            for slot_index in empty_slots:
                chosen_seed = None
                for seed_type in ordered_seed_types:
                    if remaining.get(seed_type, 0) <= 0:
                        continue
                    if await self._consume_seed(conn, ctx.author.id, season_id, seed_type, 1):
                        chosen_seed = seed_type
                        remaining[seed_type] -= 1
                        break
                    remaining[seed_type] = 0

                if not chosen_seed:
                    break

                await self._plant_slot(conn, ctx.author.id, season_id, slot_index, chosen_seed)
                planted_slots.append(slot_index)
                planted_by_seed[chosen_seed] += 1

            if not planted_slots:
                return await ctx.send(
                    _("No seeds were planted. Your inventory may have changed; try again.")
                )

            updated_slots = await self._fetch_slots(conn, ctx.author.id, season_id, max_slots)
            milestone_rewards = await self._claim_garden_milestone_rewards(
                conn,
                ctx.author.id,
                season_id,
                updated_slots,
                garden_level,
            )

        seed_lines = "\n".join(
            f"- {SEEDS[seed_type]['display_name']}: {qty}"
            for seed_type, qty in sorted(planted_by_seed.items(), key=lambda entry: self._seed_plant_priority(entry[0]))
            if qty > 0
        )
        remaining_empty = max(0, empty_before - len(planted_slots))
        response = _(
            "Planted **{count}** seed(s) across **{filled}** slot(s) in your {garden}.\n"
            "Order used: **lowest-tier seeds first**.\n"
            "Slots still empty: **{remaining}**.\n\n"
            "Seed usage:\n{seed_lines}"
        ).format(
            count=len(planted_slots),
            filled=len(planted_slots),
            garden=GARDEN_NAME,
            remaining=remaining_empty,
            seed_lines=seed_lines or "- None",
        )

        if milestone_rewards:
            total_bloomshards = 0
            bonus_lines: list[str] = []
            for entry in milestone_rewards:
                line_parts = [f"- {entry['label']}:"]
                bloomshards_gain = int(entry.get("bloomshards", 0) or 0)
                if bloomshards_gain > 0:
                    total_bloomshards += bloomshards_gain
                    line_parts.append(f"+{bloomshards_gain} {EVENT_CURRENCY}")
                for crate_entry in entry.get("crates", []):
                    crate_column = str(crate_entry.get("column", "")).strip()
                    crate_amount = int(crate_entry.get("amount", 0) or 0)
                    if not crate_column or crate_amount <= 0:
                        continue
                    line_parts.append(
                        f"+{crate_amount} {self._crate_display_name(crate_column)}"
                    )
                bonus_lines.append(" ".join(line_parts))
            bonus_text = "\n".join(bonus_lines)
            response += "\n\nGarden completion bonus unlocked:\n" f"{bonus_text}"
            if total_bloomshards > 0:
                response += f"\nTotal {EVENT_CURRENCY}: +{total_bloomshards}"

        await ctx.send(response)

    @has_char()
    @garden.command(name="remove", brief=_("Remove a plant from a slot (uses Shears)."))
    @locale_doc
    async def garden_remove(self, ctx, slot: IntFromTo(1, 24)):
        _(
            """`<slot>` - slot number from 1 to 24

            Remove a plant from a slot. This consumes one Shears item.
            Seed is not refunded."""
        )
        await self._use_item_on_slot(ctx, "SHEARS", slot)

    @has_char()
    @garden.command(name="harvest", brief=_("Harvest a bloomed flower from a slot."))
    @locale_doc
    async def garden_harvest(self, ctx, slot: IntFromTo(1, 24)):
        _(
            """`<slot>` - slot number from 1 to 24

            Harvest a BLOOMED flower, grant one crate, and clear the slot."""
        )
        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            garden_level = await self._get_garden_level(conn, ctx.author.id, season_id)
            max_slots = self._slot_capacity(garden_level)
            if slot > max_slots:
                return await ctx.send(
                    _("Slot {slot} is locked. Your current garden has {max_slots} slots.").format(
                        slot=slot,
                        max_slots=max_slots,
                    )
                )

            slot_row = await self._fetch_slot(conn, ctx.author.id, season_id, slot)
            slot_data = self._slot_runtime(dict(slot_row) if slot_row else None, utcnow())
            if slot_data["state"] == "empty":
                return await ctx.send(_("That slot is empty."))
            if slot_data["state"] != "bloomed":
                return await ctx.send(
                    _("That flower is not BLOOMED yet. Remaining: {time}").format(
                        time=self._format_duration(int(slot_data["seconds_to_bloom"] or 0))
                    )
                )

            crate_id = str(slot_data["crate_id"])
            grant_target = await self._grant_crate(
                conn,
                ctx,
                ctx.author.id,
                season_id,
                crate_id,
            )
            await self._clear_slot(conn, ctx.author.id, season_id, slot)

        if grant_target in ALLOWED_PROFILE_CRATE_COLUMNS:
            crate_name = self._crate_display_name(grant_target)
        else:
            crate_name = self._crate_id_display_name(crate_id)

        await ctx.send(
            _(
                "Harvested slot **#{slot}** in the {garden}.\n"
                "Reward claimed: **1 {crate_name}**."
            ).format(
                slot=slot,
                garden=GARDEN_NAME,
                crate_name=crate_name,
            )
        )

    @has_char()
    @garden.command(
        name="harvestall",
        aliases=["harvest_all"],
        brief=_("Harvest all BLOOMED flowers."),
    )
    @locale_doc
    async def garden_harvest_all(self, ctx):
        _("""Harvest every BLOOMED flower in unlocked slots and claim all crate rewards.""")
        harvested_slots: list[int] = []
        reward_counts: dict[str, int] = defaultdict(int)

        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            garden_level = await self._get_garden_level(conn, ctx.author.id, season_id)
            max_slots = self._slot_capacity(garden_level)
            slot_rows = await self._fetch_slots(conn, ctx.author.id, season_id, max_slots)
            now = utcnow()

            bloomed_slots: list[tuple[int, str]] = []
            planted_count = 0
            for row in slot_rows:
                slot_data = self._slot_runtime(dict(row), now)
                if slot_data["state"] == "empty":
                    continue
                planted_count += 1
                if slot_data["state"] == "bloomed":
                    bloomed_slots.append(
                        (int(row["slot_index"]), str(slot_data.get("crate_id") or ""))
                    )

            if not bloomed_slots:
                if planted_count <= 0:
                    return await ctx.send(_("There are no planted flowers to harvest."))
                return await ctx.send(_("No flowers are BLOOMED yet."))

            for slot_index, crate_id in bloomed_slots:
                grant_target = await self._grant_crate(
                    conn,
                    ctx,
                    ctx.author.id,
                    season_id,
                    crate_id,
                    subject="dreambound spring harvest all",
                )
                await self._clear_slot(conn, ctx.author.id, season_id, slot_index)
                harvested_slots.append(slot_index)
                if grant_target in ALLOWED_PROFILE_CRATE_COLUMNS:
                    reward_name = self._crate_display_name(grant_target)
                else:
                    reward_name = self._crate_id_display_name(crate_id)
                reward_counts[reward_name] += 1

        reward_lines = "\n".join(
            f"- {reward_name}: {count}"
            for reward_name, count in sorted(reward_counts.items(), key=lambda item: item[0].lower())
        )
        slot_list = ", ".join(f"#{slot}" for slot in harvested_slots)
        await ctx.send(
            _(
                "Harvested **{count}** BLOOMED flower(s) in the {garden}.\n"
                "Slots: {slots}\n\n"
                "Rewards:\n{rewards}"
            ).format(
                count=len(harvested_slots),
                garden=GARDEN_NAME,
                slots=slot_list,
                rewards=reward_lines or "- None",
            )
        )

    @has_char()
    @garden.command(name="upgrade", brief=_("Upgrade your Elysian Garden level."))
    @locale_doc
    async def garden_upgrade(self, ctx):
        _("""Upgrade your garden level using Bloomshards.""")
        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            current_level = await self._get_garden_level(conn, ctx.author.id, season_id)
            ok, msg, new_level, cost = await self._try_upgrade(
                conn,
                ctx.author.id,
                season_id,
                current_level,
            )
            if not ok:
                return await ctx.send(msg)

        cols, rows = self._grid_for_level(new_level)
        bg = ASSETS["backgrounds"][new_level]["image"]
        await ctx.send(
            _(
                "{msg}\n"
                "New level: **{level}** | Grid: **{cols}x{rows}** ({slots} slots)\n"
                "Cost paid: **{cost} {currency}**\n"
                "Background: `{bg}`"
            ).format(
                msg=msg,
                level=new_level,
                cols=cols,
                rows=rows,
                slots=cols * rows,
                cost=cost,
                currency=EVENT_CURRENCY,
                bg=bg,
            )
        )

    @has_char()
    @garden.command(name="snapshot", brief=_("Save or view a seasonal garden snapshot."))
    @locale_doc
    async def garden_snapshot(self, ctx, selector: Optional[str] = None):
        _(
            """`[selector]` - optional `latest` or snapshot id

            Save a read-only snapshot of your current seasonal garden layout.
            Use `latest` or a snapshot id to view an existing snapshot."""
        )
        if selector:
            return await self._send_snapshot_view(ctx, selector)

        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            garden_level = await self._get_garden_level(conn, ctx.author.id, season_id)
            max_slots = self._slot_capacity(garden_level)
            slots = await self._fetch_slots(conn, ctx.author.id, season_id, max_slots)
            seeds = await self._fetch_seed_inventory(conn, ctx.author.id, season_id)
            items = await self._fetch_item_inventory(conn, ctx.author.id, season_id)
            bloomshards = await self._get_wallet(conn, ctx.author.id, season_id)

            now = utcnow()
            slot_views = self._compose_slot_views(slots, max_slots, now)
            render_payload = self._build_render_payload(garden_level, slot_views)

            snapshot_payload = {
                "event_name": EVENT_NAME,
                "currency": EVENT_CURRENCY,
                "garden_name": GARDEN_NAME,
                "season_id": season_id,
                "saved_at": now.isoformat(),
                "garden_level": garden_level,
                "grid_size": list(self._grid_for_level(garden_level)),
                "background": ASSETS["backgrounds"][garden_level]["image"],
                "slots": slot_views,
                "seeds": seeds,
                "items": items,
                "wallet": bloomshards,
                "render": render_payload,
            }

            snapshot_id = await conn.fetchval(
                """
                INSERT INTO dreambound_spring_snapshots (user_id, season_id, snapshot_data)
                VALUES ($1, $2, $3::jsonb)
                RETURNING id;
                """,
                ctx.author.id,
                season_id,
                json.dumps(snapshot_payload, ensure_ascii=False),
            )

        await ctx.send(
            _(
                "Saved snapshot **#{snapshot_id}** for season `{season}`.\n"
                "Garden level: **{level}**, slots tracked: **{slots}**."
            ).format(
                snapshot_id=snapshot_id,
                season=season_id,
                level=garden_level,
                slots=max_slots,
            )
        )

    @has_char()
    @commands.group(
        name="springshop",
        invoke_without_command=True,
        brief=_("Shop for Dreambound Spring boosters and items."),
    )
    @locale_doc
    async def springshop(self, ctx):
        _("""Show the seasonal shop for The Dreambound Spring.""")
        await self._send_shop_view(ctx)

    async def _send_shop_view(self, ctx):
        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            bloomshards = await self._get_wallet(conn, ctx.author.id, season_id)
            items = await self._fetch_item_inventory(conn, ctx.author.id, season_id)
            level = await self._get_garden_level(conn, ctx.author.id, season_id)

        lines = []
        for key, data in SHOP_ITEMS.items():
            item_id = data["item_id"]
            qty = items.get(item_id, 0)
            if data["effect"] == "reduce_time":
                effect = f"-{self._format_duration(int(data['seconds']))} on one slot"
            elif data["effect"] == "instant_bloom":
                effect = "Instantly BLOOMED on one slot"
            elif data["effect"] == "clear_slot":
                effect = "Remove plant from one slot (no seed refund)"
            elif data["effect"] == "grant_random_divine_shard":
                effect = "Gain +1 random Divine Familiar shard"
            elif data["effect"] == "grant_chosen_divine_shard":
                effect = "Gain +1 chosen Divine Familiar shard (use with familiar name)"
            else:
                effect = data["effect"]
            lines.append(
                f"`{key}` ({data['display_name']}) - **{data['cost']} {EVENT_CURRENCY}** | Own: {qty} | {effect}"
            )

        upgrade_line: str
        if level >= int(CONFIG["MAX_GARDEN_LEVEL"]):
            upgrade_line = "Garden upgrade: MAX level reached"
        else:
            cost = int(CONFIG["UPGRADE_COSTS"][level])
            next_level = level + 1
            cols, rows = self._grid_for_level(next_level)
            upgrade_line = (
                f"Garden upgrade (`springshop buy upgrade`) - **{cost} {EVENT_CURRENCY}** "
                f"for level {next_level} ({cols}x{rows})"
            )

        embed = discord.Embed(
            title=f"{EVENT_NAME} Shop",
            color=discord.Color.blurple(),
            description=(
                f"Currency: **{bloomshards} {EVENT_CURRENCY}**\n"
                f"Garden: **{GARDEN_NAME}** (level {level})"
            ),
        )
        embed.add_field(name="Items", value="\n".join(lines) if lines else "No items configured.", inline=False)
        embed.add_field(name="Upgrade", value=upgrade_line, inline=False)
        embed.set_footer(text="Use springshop buy <item> [amount] and springshop use <item> <slot|familiar>")
        await ctx.send(embed=embed)

    def _user_is_gm(self, user_id: int) -> bool:
        return user_id in getattr(self.bot.config.game, "game_masters", [])

    async def _send_event_help(self, ctx):
        prefix = ctx.clean_prefix
        player_commands = [
            f"`{prefix}dreambound_spring` - Event overview, rewards, and best commands",
            f"`{prefix}garden` / `{prefix}garden show` - Show your {GARDEN_NAME}",
            f"`{prefix}garden plant <slot> <seed_name|seed_group>` - Plant one seed",
            f"`{prefix}garden plantall` - Plant all empty slots (lowest seeds first)",
            f"`{prefix}garden remove <slot>` - Remove plant (uses Shears)",
            f"`{prefix}garden harvest <slot>` - Harvest BLOOMED flower",
            f"`{prefix}garden harvestall` - Harvest every BLOOMED flower",
            f"`{prefix}garden upgrade` - Upgrade garden layout",
            f"`{prefix}garden plant` milestones - One-time rewards for full rows, columns, and full garden",
            f"`{prefix}garden snapshot` - Save snapshot",
            f"`{prefix}garden snapshot latest` - View latest snapshot",
            f"`{prefix}garden snapshot <id>` - View snapshot by id",
            f"`{prefix}springpve` - Retired while Dreambound Spring is winding down",
            f"`{prefix}springshop` - Show spring shop",
            f"`{prefix}springshop buy <item> [amount]` - Buy boosters/items",
            f"`{prefix}springshop use <item> <slot|familiar>` - Use item on slot, or choose familiar for chosen shard",
        ]
        embed = discord.Embed(
            title=f"{EVENT_NAME} Help",
            color=discord.Color.blurple(),
            description=(
                f"Season command guide for **{GARDEN_NAME}**.\n"
                "Garden and shop features remain available while Spring PvE is retired."
            ),
        )
        embed.add_field(name="Player Commands", value="\n".join(player_commands), inline=False)
        embed.add_field(
            name="Shop Item Keys",
            value=", ".join(sorted(SHOP_ITEMS.keys())),
            inline=False,
        )
        embed.add_field(name="Seed Input", value=self._seed_input_help(), inline=False)

        if self._user_is_gm(ctx.author.id):
            gm_commands = [
                f"`{prefix}springadmin grantshards <@user> <amount>`",
                f"`{prefix}springadmin grantseed <@user> <seed_name|seed_group> <amount>`",
                f"`{prefix}springadmin growall <@user>`",
                f"`{prefix}springspawn <hp> [rarity]`",
                f"`{prefix}springadmin setenabled <true|false>`",
                f"`{prefix}springadmin setseason <season_id>` (info-only helper)",
            ]
            embed.add_field(name="GM Commands", value="\n".join(gm_commands), inline=False)

        embed.set_footer(
            text=(
                f"Tip: use `{prefix}garden help` or `{prefix}springshop help` anytime."
            )
        )
        await ctx.send(embed=embed)

    @commands.command(
        name="springhelp",
        aliases=["dreamboundhelp", "dbspringhelp"],
        brief=_("Show Dreambound Spring commands."),
    )
    @locale_doc
    async def springhelp(self, ctx):
        _(
            """Show Dreambound Spring command help.

            GMs also see Dreambound Spring GM command references."""
        )
        await self._send_event_help(ctx)

    @commands.command(
        name="dreambound_spring",
        aliases=["dreamboundspring", "dbspring", "springevent"],
        brief=_("Show Dreambound Spring event overview."),
    )
    @locale_doc
    async def dreambound_spring(self, ctx):
        _(
            """Show a complete Dreambound Spring event overview, rewards, and commands."""
        )
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title=f"{EVENT_NAME} Overview",
            color=discord.Color.blurple(),
            description=(
                "Spring PvE is retired, but you can still grow flowers already in your "
                "garden, harvest crates, and spend any remaining Bloomshards."
            ),
        )
        embed.add_field(
            name="Core Loop",
            value=(
                f"1) `{prefix}garden show` to review your remaining seeds and planted flowers\n"
                f"2) `{prefix}garden plant <slot> <seed>` to grow flowers\n"
                f"3) `{prefix}garden harvest <slot>` to claim crate rewards\n"
                f"4) `{prefix}springshop` to spend Bloomshards on boosters/upgrades"
            ),
            inline=False,
        )
        embed.add_field(
            name="Garden Milestone Rewards",
            value=(
                "Per completed column: **1 Materials Crate**\n"
                "Per completed line: **1 Divine Crate**\n"
                "Full base garden (5x3): **1 Fortune Crate + 25 Bloomshards**\n"
                "Full + first extension (5x4): **2 Fortune Crates + 25 Bloomshards**\n"
                "Full + second extension (6x4): **1 Fortune Crate + 1 Divine Crate + 25 Bloomshards**"
            ),
            inline=False,
        )
        embed.add_field(
            name="Useful Commands",
            value=(
                f"`{prefix}garden show`, `{prefix}garden snapshot`, `{prefix}springshop buy`, "
                f"`{prefix}springshop use`, `{prefix}pets divineshards`, `{prefix}pets divinecraft <familiar>`"
            ),
            inline=False,
        )
        embed.set_footer(
            text="Garden and shop commands remain open while Spring PvE is retired."
        )
        await ctx.send(embed=embed)

    @has_char()
    @commands.command(
        name="springpve",
        aliases=["spve"],
        brief=_("Spring PvE is retired."),
    )
    @user_cooldown(SPRING_PVE_CONFIG["COOLDOWN_SECONDS"])
    @locale_doc
    async def springpve(self, ctx):
        _(
            """Battle a seasonal monster and earn Dreambound Spring rewards.

            Rewards are data-driven by the spring monster table and can be configured
            per monster in pve_data.py."""
        )
        await self.bot.reset_cooldown(ctx)
        return await ctx.send(_(self.SPRING_PVE_RETIRED_MESSAGE))

        antiscript_cog = self.bot.get_cog("AntiScript")
        if antiscript_cog is not None:
            try:
                daily_limits = getattr(antiscript_cog, "DAILY_LIMITS", {}) or {}
                springpve_limit = int(daily_limits.get("springpve", 36))
            except Exception:
                springpve_limit = 36

            allowed = await antiscript_cog.check_and_increment_command_use(
                user_id=ctx.author.id,
                command_name="springpve",
                limit=springpve_limit,
                increment=0,  # check only; increment happens on successful completion
            )
            if not allowed:
                await self.bot.reset_cooldown(ctx)
                return await ctx.send(
                    _("You've reached the daily threshold for `springpve`. Please try again after reset.")
                )
            setattr(ctx, "_antiscript_track_info", ("springpve", int(springpve_limit)))

        battles_cog = self.bot.get_cog("Battles")
        if not battles_cog or not getattr(battles_cog, "battle_factory", None):
            await ctx.send(_("The Battles system is not loaded right now."))
            await self.bot.reset_cooldown(ctx)
            return

        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                await self.bot.reset_cooldown(ctx)
                return
            season_id = str(state["season_id"])
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

        player_xp = int(ctx.character_data.get("xp", 0))
        player_level = int(rpgtools.xptolevel(player_xp))
        monster = self._select_spring_monster(player_level)
        if monster is None:
            await ctx.send(_("No spring monsters are configured yet."))
            await self.bot.reset_cooldown(ctx)
            return

        searching_embed = discord.Embed(
            title=_("Dreambound Hunt"),
            description=_("You step into the dreamscape and search for a spring foe..."),
            color=self.bot.config.game.primary_colour,
        )
        searching_message = await ctx.send(embed=searching_embed)
        search_min = int(SPRING_PVE_CONFIG["SEARCH_MIN_SECONDS"])
        search_max = int(SPRING_PVE_CONFIG["SEARCH_MAX_SECONDS"])
        if search_max < search_min:
            search_max = search_min
        await asyncio.sleep(random.randint(search_min, search_max))

        monster_name = str(monster.get("name", "Dreambound Monster"))
        monster_level = int(monster.get("level", 1))
        monster_data = self._spring_monster_to_battle_payload(monster)
        discovery_text = self._spring_monster_text(
            monster, "discovery_text", "A dreambound foe emerges from the haze."
        )
        found_embed = discord.Embed(
            title=_("Spring Monster Found!"),
            description=(
                _(
                    "A **{difficulty}** foe appears: **{name}** (Lv.{level}).\n\n"
                ).format(
                    difficulty=str(monster.get("difficulty", "unknown")).title(),
                    name=monster_name,
                    level=monster_level,
                )
                + discovery_text
            ),
            color=self.bot.config.game.primary_colour,
        )
        monster_image = str(monster.get("image_url", "")).strip()
        if monster_image.startswith("http://") or monster_image.startswith("https://"):
            found_embed.set_thumbnail(url=monster_image)
        abilities = monster.get("abilities", [])
        if isinstance(abilities, list) and abilities:
            ability_preview = []
            for ability in abilities[:4]:
                if not isinstance(ability, dict):
                    continue
                ability_name = str(ability.get("name", "Unknown"))
                ability_chance = self._parse_int(ability.get("chance_pct"))
                if ability_chance is None:
                    ability_preview.append(ability_name)
                else:
                    ability_preview.append(f"{ability_name} ({ability_chance}%)")
            if ability_preview:
                found_embed.add_field(
                    name="Known Abilities",
                    value="\n".join(ability_preview),
                    inline=False,
                )
        await searching_message.edit(embed=found_embed)
        await asyncio.sleep(2)

        try:
            battle = await battles_cog.battle_factory.create_battle(
                "pve",
                ctx,
                player=ctx.author,
                monster_data=monster_data,
                monster_level=monster_level,
                monster_abilities=abilities if isinstance(abilities, list) else [],
                special_ai="dreambound_spring",
            )
            await battle.start_battle()
            turn_delay = max(0, int(SPRING_PVE_CONFIG["TURN_DELAY_SECONDS"]))
            while not await battle.is_battle_over():
                await battle.process_turn()
                if turn_delay:
                    await asyncio.sleep(turn_delay)
            result = await battle.end_battle()
        except Exception:
            await ctx.send(_("Spring battle failed to start. Please try again."))
            await self.bot.reset_cooldown(ctx)
            return

        if not result or getattr(result, "name", None) != "Player":
            fail_embed = discord.Embed(
                title=_("You were defeated."),
                description=self._spring_monster_text(
                    monster, "fail_text", "The dream overwhelms you before you can escape."
                ),
                color=discord.Color.red(),
            )
            await ctx.send(embed=fail_embed)
            return

        seed_type, used_seed_fallback = self._resolve_spring_seed_reward(monster)
        drop_seed_display = SEEDS[seed_type]["display_name"]
        flower_type = str(SEEDS[seed_type].get("flower_type", "")).strip()
        flower_data = ASSETS["flowers"].get(flower_type, {})
        drop_flower_display = str(flower_data.get("display_name", "Dream Flower"))
        bloomshards_reward, used_shard_fallback = self._resolve_spring_bloomshards_reward(monster)
        base_divine_shard_chance_pct = self._resolve_divine_shard_chance_pct(monster)
        did_divine_shard_drop = False
        divine_no_shard_wins = 0
        divine_familiar_key = random.choice(list(DIVINE_FAMILIARS.keys()))
        divine_familiar = DIVINE_FAMILIARS[divine_familiar_key]
        defeat_text = self._spring_monster_text(
            monster, "defeat_text", "The dream shatters and you force yourself awake."
        )

        async with self.bot.pool.acquire() as conn:
            await self._ensure_user_rows(conn, ctx.author.id, season_id)
            await ensure_divine_familiar_tables(conn)
            await self._add_seed(conn, ctx.author.id, season_id, seed_type, 1)
            if bloomshards_reward > 0:
                await self._add_wallet(conn, ctx.author.id, season_id, bloomshards_reward)

            current_no_shard_wins = await self._get_divine_pity_no_shard_wins(
                conn,
                ctx.author.id,
                season_id,
            )
            roll_pity_state = _get_spring_divine_shard_pity_state(
                base_divine_shard_chance_pct,
                current_no_shard_wins,
                self.DIVINE_SHARD_PITY_TIERS,
            )
            did_divine_shard_drop = random.random() < (
                roll_pity_state["effective_chance_pct"] / 100.0
            )

            if did_divine_shard_drop:
                divine_shard_total = await award_divine_shards(
                    conn,
                    ctx.author.id,
                    divine_familiar_key,
                    1,
                )
                divine_no_shard_wins = 0
                await self._set_divine_pity_no_shard_wins(
                    conn,
                    ctx.author.id,
                    season_id,
                    0,
                )
            else:
                divine_shard_total = int(
                    await conn.fetchval(
                        """
                        SELECT shards
                        FROM divine_familiar_shards
                        WHERE user_id = $1 AND familiar_key = $2;
                        """,
                        ctx.author.id,
                        divine_familiar_key,
                    )
                    or 0
                )
                divine_no_shard_wins = current_no_shard_wins + 1
                await self._set_divine_pity_no_shard_wins(
                    conn,
                    ctx.author.id,
                    season_id,
                    divine_no_shard_wins,
                )

            current_pity_state = _get_spring_divine_shard_pity_state(
                base_divine_shard_chance_pct,
                divine_no_shard_wins,
                self.DIVINE_SHARD_PITY_TIERS,
            )

            new_seed_qty = int(
                await conn.fetchval(
                    """
                    SELECT qty
                    FROM dreambound_spring_seeds
                    WHERE user_id = $1 AND season_id = $2 AND seed_type = $3;
                    """,
                    ctx.author.id,
                    season_id,
                    seed_type,
                )
                or 0
            )
            wallet_total = await self._get_wallet(conn, ctx.author.id, season_id)

        reward_embed = discord.Embed(
            title=_("Spring PvE Rewards"),
            description=(
                _(
                    "You defeated **{monster}** and claimed seasonal rewards.\n\n"
                ).format(monster=monster_name)
                + defeat_text
            ),
            color=discord.Color.green(),
        )
        reward_embed.add_field(
            name="Dream Drop",
            value=(
                f"Flower: **{drop_flower_display}**\n"
                f"Seed: **{drop_seed_display}** (+1)\n"
                f"Now: **{new_seed_qty}**"
            ),
            inline=True,
        )
        reward_embed.add_field(
            name=EVENT_CURRENCY,
            value=f"+**{bloomshards_reward}**\nNow: **{wallet_total}**",
            inline=True,
        )
        reward_embed.add_field(
            name="Divine Familiar Shard",
            value=(
                (
                    f"**{format_familiar_display_name(divine_familiar)}** +1 shard "
                    + f"({self._format_divine_shard_chance_text(roll_pity_state)})\n"
                )
                if did_divine_shard_drop
                else (
                    f"**{format_familiar_display_name(divine_familiar)}** no shard "
                    + f"({self._format_divine_shard_chance_text(roll_pity_state)})\n"
                )
                + f"Now: **{divine_shard_total}/20**\n"
                + self._format_divine_shard_pity_text(
                    current_pity_state,
                    divine_no_shard_wins,
                )
            ),
            inline=False,
        )
        if used_seed_fallback or used_shard_fallback:
            reward_embed.set_footer(
                text=(
                    f"Next steps: `{ctx.clean_prefix}garden show` | "
                    f"`{ctx.clean_prefix}garden plant <slot> <seed>` | "
                    f"`{ctx.clean_prefix}pets divineshards`"
                )
            )
        else:
            reward_embed.set_footer(
                text=(
                    f"Try: `{ctx.clean_prefix}garden show` | "
                    f"`{ctx.clean_prefix}garden harvest <slot>` | "
                    f"`{ctx.clean_prefix}pets divinecraft <familiar>`"
                )
            )
        await ctx.send(embed=reward_embed)

    @has_char()
    @springshop.command(name="buy", brief=_("Buy a spring shop item."))
    @locale_doc
    async def springshop_buy(self, ctx, item_input: str, amount: IntGreaterThan(0) = 1):
        _(
            """`<item>` - shop item key
            `[amount]` - amount to buy

            Buy an item with Bloomshards."""
        )
        item_key = item_input.strip().lower()
        if item_key in {"upgrade", "garden_upgrade", "gardenupgrade"}:
            if amount != 1:
                return await ctx.send(_("Garden upgrades can only be bought one at a time."))
            return await self.garden_upgrade(ctx)

        item_id = self._resolve_item_id(item_input)
        if not item_id:
            valid = ", ".join(sorted(SHOP_ITEMS.keys()))
            return await ctx.send(_("Unknown shop item. Valid items: {valid}").format(valid=valid))

        item_data = self._item_data_from_id(item_id)
        if not item_data:
            return await ctx.send(_("That item is not configured in the spring shop."))

        total_cost = int(item_data["cost"]) * int(amount)

        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            ok = await self._spend_wallet(conn, ctx.author.id, season_id, total_cost)
            if not ok:
                return await ctx.send(
                    _("You need **{cost} {currency}** for that purchase.").format(
                        cost=total_cost,
                        currency=EVENT_CURRENCY,
                    )
                )

            await self._add_item(conn, ctx.author.id, season_id, item_id, int(amount))

        await ctx.send(
            _(
                "Purchased **{amount}x {item}** for **{cost} {currency}**."
            ).format(
                amount=amount,
                item=item_data["display_name"],
                cost=total_cost,
                currency=EVENT_CURRENCY,
            )
        )

    async def _use_item_on_slot(self, ctx, item_id: str, slot: int):
        item_data = self._item_data_from_id(item_id)
        if not item_data:
            return await ctx.send(_("That item is not configured."))

        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)

            garden_level = await self._get_garden_level(conn, ctx.author.id, season_id)
            max_slots = self._slot_capacity(garden_level)
            if slot > max_slots:
                return await ctx.send(
                    _("Slot {slot} is locked. Your current garden has {max_slots} slots.").format(
                        slot=slot,
                        max_slots=max_slots,
                    )
                )

            slot_row = await self._fetch_slot(conn, ctx.author.id, season_id, slot)
            slot_state = self._slot_runtime(dict(slot_row) if slot_row else None, utcnow())
            effect = item_data["effect"]

            if effect in {"reduce_time", "instant_bloom"} and slot_state["state"] == "empty":
                return await ctx.send(_("That slot is empty."))

            if effect == "reduce_time" and slot_state["state"] == "bloomed":
                return await ctx.send(_("That slot is already BLOOMED."))

            if effect == "instant_bloom" and slot_state["state"] == "bloomed":
                return await ctx.send(_("That slot is already BLOOMED."))

            if effect == "clear_slot" and slot_state["state"] == "empty":
                return await ctx.send(_("That slot is already empty."))

            ok = await self._consume_item(conn, ctx.author.id, season_id, item_id, 1)
            if not ok:
                return await ctx.send(
                    _("You do not own any **{item}**.").format(item=item_data["display_name"])
                )

            if effect == "reduce_time":
                reduction = min(
                    int(item_data["seconds"]),
                    int(slot_state["seconds_to_bloom"] or 0),
                )
                if reduction <= 0:
                    return await ctx.send(_("No reduction was needed for that slot."))
                await conn.execute(
                    """
                    UPDATE dreambound_spring_slots
                    SET boost_seconds = COALESCE(boost_seconds, 0) + $1
                    WHERE user_id = $2 AND season_id = $3 AND slot_index = $4;
                    """,
                    reduction,
                    ctx.author.id,
                    season_id,
                    slot,
                )
                return await ctx.send(
                    _("Used **{item}** on slot **#{slot}**. Time reduced by **{time}**.").format(
                        item=item_data["display_name"],
                        slot=slot,
                        time=self._format_duration(reduction),
                    )
                )

            if effect == "instant_bloom":
                needed = int(slot_state["seconds_to_bloom"] or 0)
                await conn.execute(
                    """
                    UPDATE dreambound_spring_slots
                    SET boost_seconds = COALESCE(boost_seconds, 0) + $1
                    WHERE user_id = $2 AND season_id = $3 AND slot_index = $4;
                    """,
                    needed,
                    ctx.author.id,
                    season_id,
                    slot,
                )
                return await ctx.send(
                    _("Used **{item}** on slot **#{slot}**. The flower is now **BLOOMED**.").format(
                        item=item_data["display_name"],
                        slot=slot,
                    )
                )

            if effect == "clear_slot":
                await self._clear_slot(conn, ctx.author.id, season_id, slot)
                return await ctx.send(
                    _("Used **{item}** on slot **#{slot}**. The plant was removed.").format(
                        item=item_data["display_name"],
                        slot=slot,
                    )
                )

            return await ctx.send(_("This item effect is not supported yet."))

    async def _use_item_non_slot(self, ctx, item_id: str, target: Optional[str] = None):
        item_data = self._item_data_from_id(item_id)
        if not item_data:
            return await ctx.send(_("That item is not configured."))

        effect = str(item_data.get("effect", "")).strip()
        if effect not in {"grant_random_divine_shard", "grant_chosen_divine_shard"}:
            return await ctx.send(_("This item must be used on a slot."))

        async with self.bot.pool.acquire() as conn:
            state = await self._ensure_event_phase_two(ctx, conn)
            if not state:
                return
            season_id = state["season_id"]
            await self._ensure_user_rows(conn, ctx.author.id, season_id)
            await ensure_divine_familiar_tables(conn)

            familiar_key: Optional[str] = None
            if effect == "grant_chosen_divine_shard":
                familiar_key = resolve_familiar_key(str(target or "").strip())
                if not familiar_key or familiar_key not in DIVINE_FAMILIARS:
                    choices = ", ".join(
                        sorted(v["name"] for v in DIVINE_FAMILIARS.values())
                    )
                    return await ctx.send(
                        _("Choose a valid familiar: {choices}.").format(choices=choices)
                    )

            ok = await self._consume_item(conn, ctx.author.id, season_id, item_id, 1)
            if not ok:
                return await ctx.send(
                    _("You do not own any **{item}**.").format(item=item_data["display_name"])
                )

            if effect == "grant_random_divine_shard":
                familiar_key = random.choice(list(DIVINE_FAMILIARS.keys()))

            if not familiar_key:
                return await ctx.send(_("Could not determine divine familiar shard target."))

            new_total = await award_divine_shards(
                conn,
                ctx.author.id,
                familiar_key,
                1,
            )
            familiar = DIVINE_FAMILIARS[familiar_key]

        await ctx.send(
            _(
                "Used **{item}** and gained **+1 shard** for **{familiar}**. "
                "New total: **{total}/20**."
            ).format(
                item=item_data["display_name"],
                familiar=format_familiar_display_name(familiar),
                total=new_total,
            )
        )

    @has_char()
    @springshop.command(name="use", brief=_("Use a spring shop item."))
    @locale_doc
    async def springshop_use(self, ctx, item_input: str, *, target: Optional[str] = None):
        _(
            """`<item>` - shop item key
            `<target>` - slot number for slot items, or familiar name for chosen shard

            Use a purchased spring item."""
        )
        item_id = self._resolve_item_id(item_input)
        if not item_id:
            valid = ", ".join(sorted(SHOP_ITEMS.keys()))
            return await ctx.send(_("Unknown item. Valid items: {valid}").format(valid=valid))

        item_data = self._item_data_from_id(item_id)
        if not item_data:
            return await ctx.send(_("That item is not configured."))

        requires_slot = bool(item_data.get("requires_slot", True))
        if requires_slot:
            if not target:
                return await ctx.send(_("This item requires a slot: `springshop use <item> <slot>`."))
            try:
                slot = int(str(target).strip())
            except (TypeError, ValueError):
                return await ctx.send(_("Slot must be a number from 1 to 24."))
            if slot < 1 or slot > 24:
                return await ctx.send(_("Slot must be between 1 and 24."))
            return await self._use_item_on_slot(ctx, item_id, slot)

        return await self._use_item_non_slot(ctx, item_id, target)

    @has_char()
    @garden.command(name="help", brief=_("Show Dreambound Spring help."))
    @locale_doc
    async def garden_help(self, ctx):
        _("""Show Dreambound Spring command help.""")
        await self._send_event_help(ctx)

    @has_char()
    @springshop.command(name="help", brief=_("Show Dreambound Spring help."))
    @locale_doc
    async def springshop_help(self, ctx):
        _("""Show Dreambound Spring command help.""")
        await self._send_event_help(ctx)

    @commands.group(name="springadmin", aliases=["springgm"], invoke_without_command=True, hidden=True)
    @is_gm()
    async def springadmin(self, ctx):
        await ctx.send(
            "springadmin (GM) commands: grantshards, grantseed, growall, simulate, setenabled, setseason"
        )

    @springadmin.command(name="grantshards", hidden=True)
    @is_gm()
    async def springadmin_grantshards(self, ctx, user: discord.Member, amount: IntGreaterThan(0)):
        async with self.bot.pool.acquire() as conn:
            await self._ensure_user_rows(conn, user.id, self.season_id)
            await self._add_wallet(conn, user.id, self.season_id, int(amount))
            new_total = await self._get_wallet(conn, user.id, self.season_id)
        await ctx.send(
            f"Gave {amount} {EVENT_CURRENCY} to {user.mention}. New balance: {new_total}."
        )

    @springadmin.command(name="grantseed", hidden=True)
    @is_gm()
    async def springadmin_grantseed(
        self,
        ctx,
        user: discord.Member,
        seed_type: str,
        amount: IntGreaterThan(0),
    ):
        seed_key = self._resolve_seed_type(seed_type)
        seed_group = None if seed_key else self._resolve_seed_group(seed_type)
        if not seed_key and not seed_group:
            return await ctx.send(f"Unknown seed. {self._seed_input_help()}")

        async with self.bot.pool.acquire() as conn:
            if seed_key:
                await self._add_seed(conn, user.id, self.season_id, seed_key, int(amount))
                qty = await conn.fetchval(
                    """
                    SELECT qty
                    FROM dreambound_spring_seeds
                    WHERE user_id = $1 AND season_id = $2 AND seed_type = $3;
                    """,
                    user.id,
                    self.season_id,
                    seed_key,
                )
                display = SEEDS[seed_key]["display_name"]
                return await ctx.send(
                    f"Gave {amount}x {display} to {user.mention}. New qty: {int(qty or 0)}."
                )

            group_seeds = self._seed_groups.get(seed_group or "", [])
            if not group_seeds:
                return await ctx.send(f"Unknown seed group: `{seed_group}`.")

            distribution = defaultdict(int)
            amount_int = int(amount)
            for idx in range(amount_int):
                distribution[group_seeds[idx % len(group_seeds)]] += 1

            for group_seed, qty in distribution.items():
                await self._add_seed(conn, user.id, self.season_id, group_seed, qty)

        lines = [
            f"- {SEEDS[group_seed]['display_name']}: {qty}"
            for group_seed, qty in distribution.items()
            if qty > 0
        ]
        await ctx.send(
            f"Gave {amount}x **{seed_group.title()}** seeds to {user.mention}:\n"
            + "\n".join(lines)
        )

    @springadmin.command(name="growall", aliases=["bloomall", "instagrow"], hidden=True)
    @is_gm()
    async def springadmin_growall(self, ctx, user: discord.Member):
        async with self.bot.pool.acquire() as conn:
            await self._ensure_user_rows(conn, user.id, self.season_id)
            total, forced, already, skipped = await self._force_bloom_all_slots(
                conn, user.id, self.season_id
            )

        if total == 0:
            return await ctx.send(
                f"{user.mention} has no planted flowers in `{self.season_id}`."
            )

        await ctx.send(
            (
                f"Force-grown flowers for {user.mention} in `{self.season_id}`.\n"
                f"Slots with plants: **{total}** | Newly bloomed: **{forced}** | "
                f"Already bloomed: **{already}** | Skipped: **{skipped}**."
            )
        )

    @springadmin.command(name="simulate", aliases=["sim", "simpve"], hidden=True)
    @is_gm()
    async def springadmin_simulate(
        self,
        ctx,
        user: discord.Member,
        runs: IntFromTo(1, 10000) = 324,
        starting_pity: IntFromTo(0, 500) = 0,
    ):
        """Simulate Dreambound Spring PvE runs for a user without granting rewards."""
        battles_cog = self.bot.get_cog("Battles")
        if not battles_cog or not getattr(battles_cog, "battle_factory", None):
            return await ctx.send("Battles system is not available.")

        async with self.bot.pool.acquire() as conn:
            profile = await conn.fetchrow(
                'SELECT "xp" FROM profile WHERE "user" = $1;',
                user.id,
            )
            if not profile:
                return await ctx.send(f"{user.mention} does not have a character profile.")

        player_level = int(rpgtools.xptolevel(int(profile["xp"] or 0)))
        total_runs = int(runs)
        pity_no_shard_wins = int(starting_pity)

        wins = 0
        losses = 0
        shards_gained = 0
        pity_tier_rolls = {2: 0, 3: 0, 5: 0}
        total_turns = 0
        familiar_shards = {key: 0 for key in DIVINE_FAMILIARS.keys()}
        win_shard_base_chances: list[int] = []

        for _ in range(total_runs):
            monster = self._select_spring_monster(player_level)
            if monster is None:
                return await ctx.send("No spring monsters are configured.")

            monster_data = self._spring_monster_to_battle_payload(monster)
            monster_level = int(monster.get("level", 1))
            abilities = monster.get("abilities", [])

            battle = await battles_cog.battle_factory.create_battle(
                "pve",
                ctx,
                player=user,
                monster_data=monster_data,
                monster_level=monster_level,
                monster_abilities=abilities if isinstance(abilities, list) else [],
                special_ai="dreambound_spring",
                simulation_mode=True,
                max_duration=dt.timedelta(minutes=2),
            )

            await battle.start_battle()

            turn_guard = 0
            while not await battle.is_battle_over():
                progressed = await battle.process_turn()
                turn_guard += 1
                if not progressed or turn_guard >= 1000:
                    break

            total_turns += turn_guard
            player_alive = any(c.is_alive() for c in battle.player_team.combatants)
            monster_alive = any(c.is_alive() for c in battle.monster_team.combatants)
            did_win = bool(player_alive and not monster_alive)

            if did_win:
                wins += 1
                base_chance = self._resolve_divine_shard_chance_pct(monster)
                win_shard_base_chances.append(base_chance)
                pity_state = _get_spring_divine_shard_pity_state(
                    base_chance,
                    pity_no_shard_wins,
                    self.DIVINE_SHARD_PITY_TIERS,
                )
                if pity_state["pity_multiplier"] > 1:
                    pity_tier_rolls[pity_state["pity_multiplier"]] += 1

                did_drop = random.random() < (
                    pity_state["effective_chance_pct"] / 100.0
                )

                if did_drop:
                    shards_gained += 1
                    pity_no_shard_wins = 0
                    familiar_key = random.choice(list(DIVINE_FAMILIARS.keys()))
                    familiar_shards[familiar_key] += 1
                else:
                    pity_no_shard_wins += 1
            else:
                losses += 1

        win_rate = (wins / total_runs * 100.0) if total_runs else 0.0
        shard_per_win = (shards_gained / wins * 100.0) if wins else 0.0
        shard_per_run = (shards_gained / total_runs * 100.0) if total_runs else 0.0

        estimate_trials = 1000
        estimate_totals: list[int] = []
        if win_shard_base_chances:
            for _ in range(estimate_trials):
                pity_sim = int(starting_pity)
                simulated_shards = 0
                for base_chance in win_shard_base_chances:
                    pity_state = _get_spring_divine_shard_pity_state(
                        base_chance,
                        pity_sim,
                        self.DIVINE_SHARD_PITY_TIERS,
                    )
                    did_drop = random.random() < (
                        pity_state["effective_chance_pct"] / 100.0
                    )

                    if did_drop:
                        simulated_shards += 1
                        pity_sim = 0
                    else:
                        pity_sim += 1
                estimate_totals.append(simulated_shards)

        if estimate_totals:
            sorted_estimates = sorted(estimate_totals)
            p10_idx = max(0, int((len(sorted_estimates) - 1) * 0.10))
            p90_idx = max(0, int((len(sorted_estimates) - 1) * 0.90))
            unlucky_estimate = int(sorted_estimates[p10_idx])
            lucky_estimate = int(sorted_estimates[p90_idx])
            expected_estimate = (sum(sorted_estimates) / len(sorted_estimates))
        else:
            unlucky_estimate = 0
            lucky_estimate = 0
            expected_estimate = 0.0

        lines = []
        for familiar_key, count in familiar_shards.items():
            familiar = DIVINE_FAMILIARS[familiar_key]
            lines.append(
                f"- {format_familiar_display_name(familiar)}: **{count}**"
            )

        summary = (
            f"Dreambound Spring simulation for {user.mention}\n"
            f"Runs: **{total_runs}** | Wins: **{wins}** | Losses: **{losses}** | Win rate: **{win_rate:.2f}%**\n"
            f"Shards gained: **{shards_gained}** | Shards / win: **{shard_per_win:.2f}%** | Shards / run: **{shard_per_run:.2f}%**\n"
            f"Shard estimate (same profile): unlucky ~**{unlucky_estimate}**, expected ~**{expected_estimate:.2f}**, lucky ~**{lucky_estimate}**\n"
            f"Starting pity: **{starting_pity}** | Ending pity: **{pity_no_shard_wins}**\n"
            f"Pity tier rolls: **x2={pity_tier_rolls[2]}**, **x3={pity_tier_rolls[3]}**, **x5={pity_tier_rolls[5]}**\n"
            f"Total simulated turns: **{total_turns}**\n\n"
            f"Shards by familiar:\n" + "\n".join(lines)
        )
        await ctx.send(summary)

    @springadmin.command(name="setphase", hidden=True)
    @is_gm()
    async def springadmin_setphase(self, ctx, phase: IntFromTo(1, 3)):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO dreambound_spring_state (season_id, enabled, phase, starts_at)
                VALUES ($1, TRUE, $2, NOW())
                ON CONFLICT (season_id)
                DO UPDATE SET phase = EXCLUDED.phase;
                """,
                self.season_id,
                int(phase),
            )
        await ctx.send(
            f"Set Dreambound Spring phase to {phase} for `{self.season_id}`. "
            "Phase no longer gates any event commands."
        )

    @springadmin.command(name="setenabled", hidden=True)
    @is_gm()
    async def springadmin_setenabled(self, ctx, enabled: bool):
        async with self.bot.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO dreambound_spring_state (season_id, enabled, phase, starts_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (season_id)
                DO UPDATE SET enabled = EXCLUDED.enabled;
                """,
                self.season_id,
                enabled,
                DEFAULT_PHASE,
            )
        await ctx.send(
            f"Set Dreambound Spring enabled={enabled} for `{self.season_id}`."
        )

    @springadmin.command(name="setseason", hidden=True)
    @is_gm()
    async def springadmin_setseason(self, ctx, season_id: str):
        await ctx.send(
            "Season id is configured in `cogs/dreambound_spring/data.py` via DEFAULT_SEASON_ID."
            f" Current: `{self.season_id}` | Requested: `{season_id}`"
        )


async def setup(bot):
    await bot.add_cog(DreamboundSpring(bot))
